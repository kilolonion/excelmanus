"""SessionState — 从 AgentEngine 解耦的会话状态追踪组件。

负责管理：
- 轮次计数（session_turn）
- 工具调用统计（iteration/tool_call/success/failure counts）
- 每轮迭代诊断快照（turn_diagnostics）
- 会话级诊断累积（session_diagnostics）
- 写入追踪（has_write_tool_call / affected_files）
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

        self.has_write_tool_call: bool = False

        # 每轮迭代诊断快照
        self.turn_diagnostics: list[Any] = []
        # 会话级诊断累积
        self.session_diagnostics: list[dict[str, Any]] = []

        # 自动追踪写入工具涉及的文件路径（替代 finish_task 的 affected_files）
        self.affected_files: list[str] = []
        # 本会话最近读到/写到的内容版本（path → sha256:...）
        self.file_content_versions: dict[str, str] = {}

        # 写入操作日志（当前仅供 Playbook 反思注入）
        # 每条: {tool_name, file_path, sheet, range, summary}
        self.write_operations_log: list[dict[str, str]] = []

        # FileRegistry 引用（由 engine 注入，唯一接口）
        self._file_registry: Any = None

        # 提示词注入快照（每轮完整文本，供 /save 导出）
        self.prompt_injection_snapshots: list[dict[str, Any]] = []
        # Code Mode：native | code。随快照持久化，plan/read 由 present_as_of 强制 native。
        self.present_as: str = "native"

        # 上次真正发给模型的动态快照指纹；相同则本步不再重注
        self.injected_context_fingerprint: str | None = None

    def increment_turn(self) -> None:
        """递增会话轮次。"""
        self.session_turn += 1

    def reset_loop_stats(self) -> None:
        """重置单次 followup 的循环统计。"""
        self.last_iteration_count = 0
        self.last_tool_call_count = 0
        self.last_success_count = 0
        self.last_failure_count = 0
        self.has_write_tool_call = False
        self.turn_diagnostics = []
        self.affected_files = []
        self.write_operations_log = []

    def reset_session(self) -> None:
        """重置全部会话级状态（跨对话边界调用）。"""
        self.session_turn = 0
        self.has_write_tool_call = False
        self.last_iteration_count = 0
        self.last_tool_call_count = 0
        self.last_success_count = 0
        self.last_failure_count = 0
        self.turn_diagnostics = []
        self.session_diagnostics = []
        self.prompt_injection_snapshots = []
        self.injected_context_fingerprint = None
        self.affected_files = []
        self.file_content_versions = {}
        self.write_operations_log = []

    def record_write_action(self) -> None:
        """记录一次实质写入操作。"""
        self.has_write_tool_call = True

    def record_affected_file(self, path: str) -> None:
        """记录被写入工具修改的文件路径（canonical public identity）。"""
        from excelmanus.workspace.identity import public_identity, workspace_root_of

        public = public_identity(path, workspace_root_of(self))
        if public and public not in self.affected_files:
            self.affected_files.append(public)

    def remember_file_version(self, path: str, version: str) -> None:
        key = path.replace("\\", "/").lstrip("./").strip()
        if key and version:
            self.file_content_versions[key] = version

    def peek_file_version(self, path: str) -> str | None:
        key = path.replace("\\", "/").lstrip("./").strip()
        return self.file_content_versions.get(key)

    def record_write_operation(
        self,
        *,
        tool_name: str,
        file_path: str = "",
        sheet: str = "",
        cell_range: str = "",
        summary: str = "",
    ) -> None:
        """记录一次写入操作的结构化摘要，供 Playbook 反思注入。"""
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
        """将写入操作日志渲染为可读文本（调试/测试用；生产消费者为 Playbook）。"""
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

    # ── 序列化 / 反序列化（状态持久化） ──────────────────────────

    def to_dict(self) -> dict[str, Any]:
        """将可恢复的会话状态序列化为 dict，供持久化存储。

        仅保存恢复执行所需的核心状态，不保存临时性运行时数据
        （如 prompt_injection_snapshots 等）。旧会话里的 current_write_hint 会被忽略。
        """
        return {
            "session_turn": self.session_turn,
            "last_iteration_count": self.last_iteration_count,
            "last_tool_call_count": self.last_tool_call_count,
            "last_success_count": self.last_success_count,
            "last_failure_count": self.last_failure_count,
            "has_write_tool_call": self.has_write_tool_call,
            "affected_files": list(self.affected_files),
            "session_diagnostics": list(self.session_diagnostics),
            "present_as": self.present_as if self.present_as in {"native", "code"} else "native",
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
        # 旧会话可能仍带 current_write_hint，P1 已删除该状态机，忽略即可。
        state.has_write_tool_call = data.get("has_write_tool_call", False)
        state.affected_files = data.get("affected_files", [])
        state.session_diagnostics = data.get("session_diagnostics", [])
        raw_present = data.get("present_as", "native")
        state.present_as = "code" if raw_present in {"code", "both"} else "native"
        return state
