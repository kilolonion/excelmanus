"""SessionState — 从 AgentEngine 解耦的会话状态追踪组件。

负责管理：
- 轮次计数（session_turn）
- 工具调用统计（iteration/tool_call/success/failure counts）
- write_hint 状态追踪
- 每轮迭代诊断快照（turn_diagnostics）
- 会话级诊断累积（session_diagnostics）
- 执行守卫状态（execution_guard_fired, vba_exempt）
- 卡住检测：检测重复工具调用和冗余读取模式
"""

from __future__ import annotations

from collections import deque
from typing import Any

# 卡住检测参数
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
        self.verification_attempt_count: int = 0

        # 自动追踪写入工具涉及的文件路径（替代 finish_task 的 affected_files）
        self.affected_files: list[str] = []

        # 写入操作日志（供 verifier delta 注入）
        # 每条: {tool_name, file_path, sheet, range, summary}
        self.write_operations_log: list[dict[str, str]] = []

        # explorer 结构化报告缓存（供 context_builder 注入 system prompt）
        self.explorer_reports: list[dict[str, Any]] = []

        # FileRegistry 引用（由 engine 注入，唯一接口）
        self._file_registry: Any = None

        # 备份沙盒：首次写入工具成功后是否已注入备份路径提示
        self.backup_write_notice_shown: bool = False

        # 提示词注入快照（每轮完整文本，供 /save 导出）
        self.prompt_injection_snapshots: list[dict[str, Any]] = []

        # ── 卡住检测 ──────────────────────────────────
        # 滑动窗口：记录最近 N 次工具调用的 (tool_name, args_fingerprint)
        self._recent_tool_calls: deque[tuple[str, str]] = deque(
            maxlen=_STUCK_WINDOW_SIZE,
        )
        # 当前轮次内是否已触发过 stuck 警告（避免重复注入）
        self.stuck_warning_fired: bool = False

        # ── Think-Act 推理检测 ─────────────────────────────────
        self.silent_call_count: int = 0
        self.reasoned_call_count: int = 0
        self.reasoning_chars_total: int = 0

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
        self.verification_attempt_count = 0
        self._recent_tool_calls.clear()
        self.stuck_warning_fired = False
        self.affected_files = []
        self.write_operations_log = []
        # 注意：explorer_reports 是跨轮次缓存，不在此处清空
        self.silent_call_count = 0
        self.reasoned_call_count = 0
        self.reasoning_chars_total = 0

    def reset_session(self) -> None:
        """重置全部会话级状态（跨对话边界调用）。"""
        self.session_turn = 0
        self.current_write_hint = "unknown"
        self.execution_guard_fired = False
        self.vba_exempt = False
        self.has_write_tool_call = False
        self.finish_task_warned = False
        self.verification_attempt_count = 0
        self.last_iteration_count = 0
        self.last_tool_call_count = 0
        self.last_success_count = 0
        self.last_failure_count = 0
        self.turn_diagnostics = []
        self.session_diagnostics = []
        self.backup_write_notice_shown = False
        self.prompt_injection_snapshots = []
        self._recent_tool_calls.clear()
        self.stuck_warning_fired = False
        self.affected_files = []
        self.write_operations_log = []
        self.explorer_reports = []
        self.silent_call_count = 0
        self.reasoned_call_count = 0
        self.reasoning_chars_total = 0

    def record_write_action(self) -> None:
        """记录一次实质写入操作。"""
        self.has_write_tool_call = True
        self.current_write_hint = "may_write"

    def record_affected_file(self, path: str) -> None:
        """记录被写入工具修改的文件路径。"""
        normalized = path.strip()
        if normalized and normalized not in self.affected_files:
            self.affected_files.append(normalized)

    def record_write_operation(
        self,
        *,
        tool_name: str,
        file_path: str = "",
        sheet: str = "",
        cell_range: str = "",
        summary: str = "",
    ) -> None:
        """记录一次写入操作的结构化摘要，供 verifier delta 注入。"""
        entry: dict[str, str] = {"tool_name": tool_name}
        if file_path:
            entry["file_path"] = file_path
        if sheet:
            entry["sheet"] = sheet
        if cell_range:
            entry["range"] = cell_range
        if summary:
            entry["summary"] = summary
        self.write_operations_log.append(entry)

    def render_write_operations_log(self) -> str:
        """将写入操作日志渲染为可读文本，供 verifier prompt 注入。"""
        if not self.write_operations_log:
            return ""
        lines: list[str] = ["## 本轮写入操作记录"]
        for i, entry in enumerate(self.write_operations_log, 1):
            parts = [entry.get("tool_name", "unknown")]
            fp = entry.get("file_path", "")
            if fp:
                parts.append(fp)
            sheet = entry.get("sheet", "")
            if sheet:
                parts[-1] = f"{parts[-1]} / {sheet}"
            cr = entry.get("range", "")
            if cr:
                parts[-1] = f"{parts[-1]} / {cr}"
            summary = entry.get("summary", "")
            desc = " → ".join(parts)
            if summary:
                desc = f"{desc} — {summary}"
            lines.append(f"{i}. {desc}")
        return "\n".join(lines)

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
        """合并新的 CoW 路径映射到 FileRegistry。"""
        if not mapping:
            return
        if self._file_registry is not None and self._file_registry.has_versions:
            for src_rel, dst_rel in mapping.items():
                self._file_registry.register_cow_mapping(src_rel, dst_rel)
        
    def get_cow_mappings(self) -> dict[str, str]:
        """返回当前 CoW 映射（仅来自 FileRegistry）。"""
        if self._file_registry is not None and self._file_registry.has_versions:
            mappings = self._file_registry.get_cow_mappings()
            if isinstance(mappings, dict):
                return {
                    str(k): str(v)
                    for k, v in mappings.items()
                    if isinstance(k, str) and isinstance(v, str)
                }
        return {}

    def lookup_cow_redirect(self, rel_path: str) -> str | None:
        """查找相对路径是否有 CoW 副本，返回副本路径或 None。"""
        if self._file_registry is not None and self._file_registry.has_versions:
            redirect = self._file_registry.lookup_cow_redirect(rel_path)
            if isinstance(redirect, str):
                return redirect
        return None

    # ── 卡住检测 ──────────────────────────────────────

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
        """记录工具调用到滑动窗口，供卡住检测使用。"""
        fp = self._args_fingerprint(arguments)
        self._recent_tool_calls.append((tool_name, fp))

    def detect_stuck_pattern(self) -> str | None:
        """检测退化模式，返回警告消息或 None。

        灵感来源：OpenHands 卡住检测。
        检测两类模式：
        1. 动作重复：连续 N 次调用同一工具且参数指纹相同
        2. 只读循环：连续 N 次只读工具但 write_hint 为 may_write（应写未写）
        """
        if self.stuck_warning_fired:
            return None

        calls = list(self._recent_tool_calls)
        if len(calls) < _ACTION_REPEAT_THRESHOLD:
            return None

        # 模式 1：动作重复（连续相同工具+相同参数）
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

        # 模式 2：只读循环（write_hint=may_write 时持续只读）
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

    # ── 序列化 / 反序列化（状态持久化） ──────────────────────────

    def to_dict(self) -> dict[str, Any]:
        """将可恢复的会话状态序列化为 dict，供持久化存储。

        仅保存恢复执行所需的核心状态，不保存临时性运行时数据
        （如 _recent_tool_calls、prompt_injection_snapshots 等）。
        """
        return {
            "session_turn": self.session_turn,
            "last_iteration_count": self.last_iteration_count,
            "last_tool_call_count": self.last_tool_call_count,
            "last_success_count": self.last_success_count,
            "last_failure_count": self.last_failure_count,
            "current_write_hint": self.current_write_hint,
            "has_write_tool_call": self.has_write_tool_call,
            "execution_guard_fired": self.execution_guard_fired,
            "vba_exempt": self.vba_exempt,
            "finish_task_warned": self.finish_task_warned,
            "verification_attempt_count": self.verification_attempt_count,
            "affected_files": list(self.affected_files),
            "backup_write_notice_shown": self.backup_write_notice_shown,
            "session_diagnostics": list(self.session_diagnostics),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SessionState":
        """从 dict 恢复会话状态。"""
        state = cls()
        state.session_turn = data.get("session_turn", 0)
        state.last_iteration_count = data.get("last_iteration_count", 0)
        state.last_tool_call_count = data.get("last_tool_call_count", 0)
        state.last_success_count = data.get("last_success_count", 0)
        state.last_failure_count = data.get("last_failure_count", 0)
        state.current_write_hint = data.get("current_write_hint", "unknown")
        state.has_write_tool_call = data.get("has_write_tool_call", False)
        state.execution_guard_fired = data.get("execution_guard_fired", False)
        state.vba_exempt = data.get("vba_exempt", False)
        state.finish_task_warned = data.get("finish_task_warned", False)
        state.verification_attempt_count = data.get("verification_attempt_count", 0)
        state.affected_files = data.get("affected_files", [])
        state.backup_write_notice_shown = data.get("backup_write_notice_shown", False)
        state.session_diagnostics = data.get("session_diagnostics", [])
        return state
