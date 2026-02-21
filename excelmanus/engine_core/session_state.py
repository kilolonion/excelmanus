"""SessionState — 从 AgentEngine 解耦的会话状态追踪组件。

负责管理：
- 轮次计数（session_turn）
- 工具调用统计（iteration/tool_call/success/failure counts）
- write_hint 状态追踪
- 每轮迭代诊断快照（turn_diagnostics）
- 会话级诊断累积（session_diagnostics）
- 执行守卫状态（execution_guard_fired, vba_exempt）
"""

from __future__ import annotations

from typing import Any


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
        self.vba_exempt: bool = False
        self.finish_task_warned: bool = False

        # CoW 路径映射注册表（会话级累积）
        # key: 原始相对路径, value: outputs/ 下的副本相对路径
        self.cow_path_registry: dict[str, str] = {}

        # 提示词注入快照（每轮完整文本，供 /save 导出）
        self.prompt_injection_snapshots: list[dict[str, Any]] = []

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
        self.finish_task_warned = False
        self.turn_diagnostics = []

    def reset_session(self) -> None:
        """重置全部会话级状态（跨对话边界调用）。"""
        self.session_turn = 0
        self.current_write_hint = "unknown"
        self.execution_guard_fired = False
        self.vba_exempt = False
        self.finish_task_warned = False
        self.has_write_tool_call = False
        self.last_iteration_count = 0
        self.last_tool_call_count = 0
        self.last_success_count = 0
        self.last_failure_count = 0
        self.turn_diagnostics = []
        self.session_diagnostics = []
        self.cow_path_registry = {}
        self.prompt_injection_snapshots = []

    def record_write_action(self) -> None:
        """记录一次实质写入操作。"""
        self.has_write_tool_call = True
        self.current_write_hint = "may_write"

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
        """合并新的 CoW 路径映射到会话级注册表。"""
        if mapping:
            self.cow_path_registry.update(mapping)

    def lookup_cow_redirect(self, rel_path: str) -> str | None:
        """查找相对路径是否有 CoW 副本，返回副本路径或 None。"""
        return self.cow_path_registry.get(rel_path)
