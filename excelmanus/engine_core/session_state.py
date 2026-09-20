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
from copy import deepcopy

_WIRE_EPOCH_FIELDS = (
    "key",
    "session_id",
    "model",
    "protocol",
    "call_config_digest",
    "tools_digest",
    "system_digest",
    "catalog_digest",
    "wire_digest",
)


def normalize_wire_epoch_dict(raw: Any) -> dict[str, str] | None:
    """把快照里的 wire_epoch 收成可往返的 dict；缺字段给空串，坏数据当缺失。"""
    if not isinstance(raw, dict):
        return None
    normalized = {
        name: str(raw.get(name, "") or "")
        for name in _WIRE_EPOCH_FIELDS
    }
    if not any(normalized.values()):
        return None
    return normalized


def snapshot_wire_epoch(epoch: Any) -> dict[str, str] | None:
    """把内存中的 EpochIdentity（或等价 dict）写成快照字段。恢复时原样读回，不重算。"""
    if epoch is None:
        return None
    if isinstance(epoch, dict):
        return normalize_wire_epoch_dict(epoch)
    data = {
        "session_id": str(getattr(epoch, "session_id", "") or ""),
        "model": str(getattr(epoch, "model", "") or ""),
        "protocol": str(getattr(epoch, "protocol", "") or ""),
        "call_config_digest": str(getattr(epoch, "call_config_digest", "") or ""),
        "tools_digest": str(getattr(epoch, "tools_digest", "") or ""),
        "system_digest": str(getattr(epoch, "system_digest", "") or ""),
        "catalog_digest": str(getattr(epoch, "catalog_digest", "") or ""),
        "wire_digest": str(getattr(epoch, "wire_digest", "") or ""),
    }
    key_fn = getattr(epoch, "key", None)
    if callable(key_fn):
        try:
            data["key"] = str(key_fn() or "")
        except Exception:
            data["key"] = str(getattr(epoch, "key", "") or "")
    else:
        data["key"] = str(getattr(epoch, "key", "") or "")
    return normalize_wire_epoch_dict(data)


def epoch_identity_from_dict(raw: Any) -> Any:
    """从快照 dict 恢复 EpochIdentity。接口未就绪时返回 None，不重算 digest。"""
    normalized = normalize_wire_epoch_dict(raw)
    if normalized is None:
        return None
    try:
        from excelmanus.prompt.envelope import EpochIdentity
    except ImportError:
        return None
    try:
        return EpochIdentity(
            session_id=normalized["session_id"],
            model=normalized["model"],
            protocol=normalized["protocol"],
            call_config_digest=normalized["call_config_digest"],
            tools_digest=normalized["tools_digest"],
            system_digest=normalized["system_digest"],
            catalog_digest=normalized["catalog_digest"],
            wire_digest=normalized["wire_digest"],
        )
    except Exception:
        return None


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

        # 写入操作日志
        # 每条: {tool_name, file_path, sheet, range, summary}
        self.write_operations_log: list[dict[str, str]] = []

        # FileRegistry 引用（由 engine 注入，唯一接口）
        self._file_registry: Any = None

        # 提示词注入快照（每轮完整文本，供 /save 导出）
        self.prompt_injection_snapshots: list[dict[str, Any]] = []

        # 上次真正发给模型的动态快照指纹；相同则本步不再重注
        self.injected_context_fingerprint: str | None = None
        # 图片出网 pin 序列；与 engine._image_wire_pin_seq 同一语义，随快照恢复
        self.image_wire_pin_seq: tuple[str, ...] = ()
        # 压缩代数；与 engine._compaction_generation 同一语义，随快照恢复
        self.compaction_generation: int = 0
        self.compaction_handoff: dict[str, Any] = {}
        # 上次出网 epoch（key + 各 digest）。恢复后直接使用，不重算。
        self.wire_epoch: dict[str, str] | None = None
        # RequestSeries 快照。无 header 时恢复为 restore/migrate，不作空前缀通行证。
        self.request_series: dict[str, Any] | None = None
        # Driver/InBox 可恢复运行态（callbacks 不进入快照）。
        self.runtime_state: dict[str, Any] = {}

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
        self.image_wire_pin_seq = ()
        self.wire_epoch = None
        self.request_series = None
        self.runtime_state = {}
        self.compaction_handoff = {}
        self.compaction_generation = 0
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
        """记录一次写入操作的结构化摘要。"""
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
        """将写入操作日志渲染为可读文本。"""
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
            "image_wire_pin_seq": list(self.image_wire_pin_seq or ()),
            "injected_context_fingerprint": self.injected_context_fingerprint,
            "compaction_generation": int(self.compaction_generation or 0),
            "compaction_handoff": deepcopy(self.compaction_handoff),
            "file_content_versions": dict(self.file_content_versions),
            "write_operations_log": deepcopy(self.write_operations_log),
            "wire_epoch": (
                dict(self.wire_epoch) if isinstance(self.wire_epoch, dict) else None
            ),
            "request_series": (
                dict(self.request_series) if isinstance(self.request_series, dict) else None
            ),
            "runtime_state": dict(self.runtime_state) if isinstance(self.runtime_state, dict) else {},
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
        raw_fp = data.get("injected_context_fingerprint", None)
        state.injected_context_fingerprint = raw_fp if isinstance(raw_fp, str) else None
        raw_pins = data.get("image_wire_pin_seq", ())
        if isinstance(raw_pins, (list, tuple)):
            state.image_wire_pin_seq = tuple(str(item) for item in raw_pins)
        else:
            state.image_wire_pin_seq = ()
        try:
            state.compaction_generation = int(data.get("compaction_generation", 0) or 0)
        except (TypeError, ValueError):
            state.compaction_generation = 0
        state.wire_epoch = normalize_wire_epoch_dict(data.get("wire_epoch"))
        raw_series = data.get("request_series")
        state.request_series = dict(raw_series) if isinstance(raw_series, dict) else None
        raw_runtime = data.get("runtime_state")
        state.runtime_state = dict(raw_runtime) if isinstance(raw_runtime, dict) else {}
        handoff = data.get("compaction_handoff")
        state.compaction_handoff = deepcopy(handoff) if isinstance(handoff, dict) else {}
        versions = data.get("file_content_versions")
        state.file_content_versions = dict(versions) if isinstance(versions, dict) else {}
        writes = data.get("write_operations_log")
        state.write_operations_log = deepcopy(writes) if isinstance(writes, list) else []
        return state
