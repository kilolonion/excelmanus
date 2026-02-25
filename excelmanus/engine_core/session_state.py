"""SessionState — 从 AgentEngine 解耦的会话状态追踪组件。

负责管理：
- 轮次计数（session_turn）
- 工具调用统计（iteration/tool_call/success/failure counts）
- write_hint 状态追踪
- 每轮迭代诊断快照（turn_diagnostics）
- 会话级诊断累积（session_diagnostics）
- 执行守卫状态（execution_guard_fired, vba_exempt）
- Stuck Detection：检测重复工具调用和冗余读取模式
"""

from __future__ import annotations

from collections import deque
from typing import Any

# Stuck Detection 参数
_STUCK_WINDOW_SIZE = 6
_ACTION_REPEAT_THRESHOLD = 3
_READ_ONLY_LOOP_THRESHOLD = 5


class SessionState:
    """会话级状态容器，集中管理原 AgentEngine 中分散的运行时状态。"""

    def __init__(self) -> None:
        # 会话轮次计数器（每次 chat 调用递增）
        self.session_turn: int = 0

        # 执行统计（每次 chat 调用后更新）
        self.last_iteration_count: int = 0
        self.last_tool_call_count: int = 0
        self.last_success_count: int = 0
        self.last_failure_count: int = 0

        # write_hint 状态
        self.current_write_hint: str = "unknown"
        self.has_write_tool_call: bool = False

        # 每轮迭代诊断快照
        self.turn_diagnostics: list[Any] = []
        # 会话级诊断累积
        self.session_diagnostics: list[dict[str, Any]] = []

        # 执行守卫状态
        self.execution_guard_fired: bool = False
        self.last_text_reply: str | None = None  # 上一次文本回复（用于重复检测）
        self.vba_exempt: bool = False
        self.finish_task_warned: bool = False

        # 自动追踪写入工具涉及的文件路径（替代 finish_task 的 affected_files）
        self.affected_files: list[str] = []

        # CoW 路径映射注册表（会话级累积）
        # key: 原始相对路径, value: outputs/ 下的副本相对路径
        # 注意：当 _fvm 可用时，cow_path_registry 从 _fvm 的 staging 索引派生
        self.cow_path_registry: dict[str, str] = {}

        # 统一文件版本管理器引用（由 engine 注入，可选）
        self._fvm: Any = None

        # 备份沙盒：首次写入工具成功后是否已注入备份路径提示
        self.backup_write_notice_shown: bool = False

        # 提示词注入快照（每轮完整文本，供 /save 导出）
        self.prompt_injection_snapshots: list[dict[str, Any]] = []

        # ── Stuck Detection ──────────────────────────────────
        # 滑动窗口：记录最近 N 次工具调用的 (tool_name, args_fingerprint)
        self._recent_tool_calls: deque[tuple[str, str]] = deque(
            maxlen=_STUCK_WINDOW_SIZE,
        )
        # 当前轮次内是否已触发过 stuck 警告（避免重复注入）
        self.stuck_warning_fired: bool = False

    def increment_turn(self) -> None:
        """递增会话轮次。"""
        self.session_turn += 1

    def reset_loop_stats(self) -> None:
        """重置单次 chat 调用的循环统计（每次 _tool_calling_loop 开始时调用）。"""
        self.last_iteration_count = 0
        self.last_tool_call_count = 0
        self.last_success_count = 0
        self.last_failure_count = 0
        self.has_write_tool_call = False
        self.turn_diagnostics = []
        self.finish_task_warned = False
        self._recent_tool_calls.clear()
        self.stuck_warning_fired = False
        self.affected_files = []

    def reset_session(self) -> None:
        """重置全部会话级状态（跨对话边界调用）。"""
        self.session_turn = 0
        self.current_write_hint = "unknown"
        self.execution_guard_fired = False
        self.vba_exempt = False
        self.has_write_tool_call = False
        self.finish_task_warned = False
        self.last_iteration_count = 0
        self.last_tool_call_count = 0
        self.last_success_count = 0
        self.last_failure_count = 0
        self.turn_diagnostics = []
        self.session_diagnostics = []
        self.cow_path_registry = {}
        self.backup_write_notice_shown = False
        self.prompt_injection_snapshots = []
        self._recent_tool_calls.clear()
        self.stuck_warning_fired = False
        self.affected_files = []

    def record_write_action(self) -> None:
        """记录一次实质写入操作。"""
        self.has_write_tool_call = True
        self.current_write_hint = "may_write"

    def record_affected_file(self, path: str) -> None:
        """记录被写入工具修改的文件路径。"""
        normalized = path.strip()
        if normalized and normalized not in self.affected_files:
            self.affected_files.append(normalized)

    def record_tool_success(self) -> None:
        """记录一次工具调用成功。"""
        self.last_tool_call_count += 1
        self.last_success_count += 1

    def record_tool_failure(self) -> None:
        """记录一次工具调用失败。"""
        self.last_tool_call_count += 1
        self.last_failure_count += 1

    # ── CoW 路径注册表 ──────────────────────────────────────

    def register_cow_mappings(self, mapping: dict[str, str]) -> None:
        """合并新的 CoW 路径映射到会话级注册表。

        当 _fvm 可用时，同时委托给 FileVersionManager 记录版本。
        """
        if not mapping:
            return
        self.cow_path_registry.update(mapping)
        if self._fvm is not None:
            for src_rel, dst_rel in mapping.items():
                self._fvm.register_cow_mapping(src_rel, dst_rel)

    def lookup_cow_redirect(self, rel_path: str) -> str | None:
        """查找相对路径是否有 CoW 副本，返回副本路径或 None。"""
        # 优先从 _fvm 查询（统一来源）
        if self._fvm is not None:
            redirect = self._fvm.lookup_cow_redirect(rel_path)
            if redirect is not None:
                return redirect
        return self.cow_path_registry.get(rel_path)

    # ── Stuck Detection ──────────────────────────────────────

    @staticmethod
    def _args_fingerprint(arguments: dict[str, Any]) -> str:
        """生成工具参数的紧凑指纹，用于检测重复调用。"""
        import hashlib
        import json

        try:
            canonical = json.dumps(arguments, sort_keys=True, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            canonical = str(arguments)
        return hashlib.md5(canonical.encode()).hexdigest()[:8]

    def record_tool_call_for_stuck_detection(
        self, tool_name: str, arguments: dict[str, Any],
    ) -> None:
        """记录工具调用到滑动窗口，供 stuck detection 使用。"""
        fp = self._args_fingerprint(arguments)
        self._recent_tool_calls.append((tool_name, fp))

    def detect_stuck_pattern(self) -> str | None:
        """检测退化模式，返回警告消息或 None。

        灵感来源：OpenHands Stuck Detection。
        检测两类模式：
        1. Action Repeat：连续 N 次调用同一工具 + 相同参数指纹
        2. Read-Only Loop：连续 N 次只读工具但 write_hint 为 may_write（应写未写）
        """
        if self.stuck_warning_fired:
            return None

        calls = list(self._recent_tool_calls)
        if len(calls) < _ACTION_REPEAT_THRESHOLD:
            return None

        # Pattern 1: Action Repeat（连续相同工具+相同参数）
        tail = calls[-_ACTION_REPEAT_THRESHOLD:]
        if len(set(tail)) == 1:
            tool_name = tail[0][0]
            self.stuck_warning_fired = True
            return (
                f"⚠️ 检测到重复操作：工具 `{tool_name}` 已连续调用 "
                f"{_ACTION_REPEAT_THRESHOLD} 次且参数相同。"
                "请更换策略：1) 检查参数是否正确 2) 尝试不同方法 "
                "3) 调用 ask_user 寻求用户帮助。"
            )

        # Pattern 2: Read-Only Loop（write_hint=may_write 时持续只读）
        from excelmanus.tools.policy import READ_ONLY_SAFE_TOOLS

        if (
            self.current_write_hint == "may_write"
            and len(calls) >= _READ_ONLY_LOOP_THRESHOLD
        ):
            recent = calls[-_READ_ONLY_LOOP_THRESHOLD:]
            all_read_only = all(name in READ_ONLY_SAFE_TOOLS for name, _ in recent)
            if all_read_only and not self.has_write_tool_call:
                self.stuck_warning_fired = True
                return (
                    "⚠️ 检测到只读循环：任务需要写入操作，但最近 "
                    f"{_READ_ONLY_LOOP_THRESHOLD} 次调用均为只读工具。"
                    "请立即执行写入操作（如 run_code），或调用 ask_user 确认任务意图。"
                )

        return None
