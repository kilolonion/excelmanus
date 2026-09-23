"""Accept 门禁与变更审计。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from difflib import unified_diff
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
from typing import TYPE_CHECKING, Any, Callable, Sequence

if TYPE_CHECKING:
    from excelmanus.database import Database
    from excelmanus.stores.approval_store import ApprovalStore

from excelmanus.json_typed import em_json_default, revive_typed_args
from excelmanus.logger import get_logger

logger = get_logger("approval")

from excelmanus.tools.policy import (
    AUDIT_TARGET_ARG_RULES_ALL,
    AUDIT_TARGET_ARG_RULES_FIRST,
    MUTATING_ALL_TOOLS,
    MUTATING_AUDIT_ONLY_TOOLS,
    MUTATING_CONFIRM_TOOLS,
    READ_ONLY_SAFE_TOOLS,
    WORKSPACE_SCAN_EXCLUDE_PREFIXES,
    WORKSPACE_SCAN_MAX_FILES,
    WORKSPACE_SCAN_MAX_HASH_BYTES,
    normalize_write_effect,
    write_effect_for_call as policy_write_effect_for_call,
)


@dataclass
class FileChangeRecord:
    path: str
    before_exists: bool
    after_exists: bool
    before_hash: str | None
    after_hash: str | None
    before_size: int | None
    after_size: int | None
    is_binary: bool
    text_diff_file: str | None = None
    before_snapshot_file: str | None = None


@dataclass
class BinarySnapshotRecord:
    path: str
    snapshot_file: str
    hash_sha256: str
    size_bytes: int


@dataclass
class PendingApproval:
    approval_id: str
    tool_name: str
    arguments: dict[str, Any]
    tool_scope: list[str]
    created_at_utc: str
    parent_call_id: str | None = None


@dataclass
class AppliedApprovalRecord:
    approval_id: str
    tool_name: str
    arguments: dict[str, Any]
    tool_scope: list[str]
    created_at_utc: str
    applied_at_utc: str
    undoable: bool
    manifest_file: str
    audit_dir: str
    result_preview: str
    execution_status: str = "success"
    error_type: str | None = None
    error_message: str | None = None
    partial_scan: bool = False
    patch_file: str | None = None
    changes: list[FileChangeRecord] = field(default_factory=list)
    binary_snapshots: list[BinarySnapshotRecord] = field(default_factory=list)
    repo_diff_before_file: str | None = None
    repo_diff_after_file: str | None = None
    session_turn: int | None = None
    session_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["id"] = d.pop("approval_id")
        return d


@dataclass
class _FileSnapshot:
    exists: bool
    content: bytes | None

    @property
    def size(self) -> int | None:
        return len(self.content) if self.content is not None else None

    @property
    def sha256(self) -> str | None:
        if self.content is None:
            return None
        return hashlib.sha256(self.content).hexdigest()


class ApprovalManager:
    """审批状态与审计管理器。"""

    # 默认策略（私有类常量，仅作为实例初始化模板）
    _READ_ONLY_SAFE_TOOLS: frozenset[str] = frozenset(READ_ONLY_SAFE_TOOLS)
    _CONFIRM_TOOLS: frozenset[str] = frozenset(MUTATING_CONFIRM_TOOLS)
    _AUDIT_ONLY_TOOLS: frozenset[str] = frozenset(MUTATING_AUDIT_ONLY_TOOLS)
    _MUTATING_TOOLS: frozenset[str] = frozenset(MUTATING_ALL_TOOLS)

    def __init__(
        self,
        workspace_root: str,
        audit_root: str = "outputs/approvals",
        *,
        database: "Database | None" = None,
        session_id: str | None = None,
    ) -> None:
        self.workspace_root = Path(workspace_root).expanduser().resolve()
        self.audit_root = (self.workspace_root / audit_root).resolve()
        self.audit_root.mkdir(parents=True, exist_ok=True)
        self._pending: PendingApproval | None = None
        self._applied: dict[str, AppliedApprovalRecord] = {}
        self._mcp_auto_approved: set[str] = set()
        self._db_store: "ApprovalStore | None" = None
        self._session_id: str | None = session_id
        # FileRegistry 引用（由 engine 注入，唯一接口）
        self._file_registry: Any = None
        if database is not None:
            from excelmanus.stores.approval_store import ApprovalStore as _AS
            self._db_store = _AS(database)

        # 会话实例级副本，避免类级可变状态污染。
        self._read_only_safe_tools: set[str] = set(self._READ_ONLY_SAFE_TOOLS)
        self._confirm_tools: set[str] = set(self._CONFIRM_TOOLS)
        self._audit_only_tools: set[str] = set(self._AUDIT_ONLY_TOOLS)
        self._mutating_tools: set[str] = set(self._MUTATING_TOOLS)
        # ToolDef 是运行时副作用能力的唯一来源。静态集合保留作未注册工具的
        # 兼容回退，但已注册工具一律通过这里的快照参与审批、审计和撤销判定。
        self._tool_definitions: dict[str, Any] = {}

    def bind_tool_definitions(self, tools: Sequence[Any]) -> None:
        """绑定当前 registry 的 ToolDef 快照。

        MCP 不需要增加任何 provider 字段；其现有 ToolDef（含宿主启发式
        ``write_effect``）在这里统一进入审批/审计判定。
        """
        self._tool_definitions = {
            str(tool.name): tool
            for tool in tools
            if getattr(tool, "name", None)
        }

    def _tool_definition(self, tool_name: str) -> Any | None:
        return self._tool_definitions.get(str(tool_name))

    def write_effect_for_call(
        self,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
    ) -> str:
        tool = self._tool_definition(tool_name)
        if tool is None:
            # 未绑定 registry 时保持旧的静态策略行为；一旦绑定，未知声明
            # 仍回退 unknown，交给外部一致性/审计兜底。
            if tool_name in self._confirm_tools or tool_name in self._audit_only_tools:
                return "workspace_write"
            return "unknown"
        effective = getattr(tool, "effective_capability", None)
        if callable(effective):
            try:
                effect = str(effective(arguments or {}).effect)
                # 第三方/旧版 ToolDef 常省略 write_effect。会话实例可能
                # 已通过 confirm/audit 覆盖其等级，此时不能让 unknown 的
                # 保守默认吞掉本地文件工具的 undo/快照语义。
                if effect == "unknown" and tool_name in (
                    self._confirm_tools | self._audit_only_tools
                ):
                    return "workspace_write"
                return effect
            except Exception:
                logger.debug("ToolCapability 派生失败，回退旧策略: %s", tool_name, exc_info=True)
        declared = normalize_write_effect(getattr(tool, "write_effect", "unknown"))
        # 第三方/测试 registry 可能仍使用旧 ToolDef（缺省 unknown）。
        # 对宿主已知的本地写工具保留原静态能力，避免升级时意外失去 undo。
        if declared == "unknown" and tool_name in (self._confirm_tools | self._audit_only_tools):
            declared = "workspace_write"
        actions = getattr(tool, "actions", None)
        return policy_write_effect_for_call(
            tool_name,
            arguments,
            declared=declared,
            actions=actions if isinstance(actions, dict) else None,
        )

    def register_mcp_auto_approve(self, prefixed_names: Sequence[str]) -> None:
        """注册 MCP 工具白名单（自动批准，无需用户确认）。

        全量替换：每次同步使用最新列表，旧条目自动清除。
        """
        self._mcp_auto_approved = set(prefixed_names)

    def register_read_only_safe_tools(self, tool_names: Sequence[str]) -> None:
        """注册额外只读安全工具（仅影响当前实例）。"""
        self._read_only_safe_tools.update(
            str(name).strip() for name in tool_names if str(name).strip()
        )

    @property
    def pending(self) -> PendingApproval | None:
        return self._pending

    def snapshot_pending(self) -> dict[str, Any] | None:
        """Return the pending approval in a session-snapshot-friendly shape."""
        if self._pending is None:
            return None
        return {
            "approval_id": self._pending.approval_id,
            "tool_name": self._pending.tool_name,
            "arguments": dict(self._pending.arguments),
            "tool_scope": list(self._pending.tool_scope),
            "created_at_utc": self._pending.created_at_utc,
            "parent_call_id": self._pending.parent_call_id,
        }

    def restore_pending(self, raw: dict[str, Any] | None) -> None:
        """Restore a pending approval after a session snapshot reload."""
        if not isinstance(raw, dict) or not raw.get("approval_id"):
            self._pending = None
            return
        self._pending = PendingApproval(
            approval_id=str(raw.get("approval_id")),
            tool_name=str(raw.get("tool_name") or ""),
            arguments=dict(raw.get("arguments") or {}),
            tool_scope=[str(item) for item in raw.get("tool_scope") or []],
            created_at_utc=str(raw.get("created_at_utc") or ""),
            parent_call_id=(
                str(raw.get("parent_call_id"))
                if raw.get("parent_call_id") is not None
                else None
            ),
        )

    def has_pending(self) -> bool:
        return self._pending is not None

    def is_read_only_safe_tool(self, tool_name: str) -> bool:
        return tool_name in self._read_only_safe_tools

    def is_audit_only_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
    ) -> bool:
        if tool_name in self._confirm_tools and tool_name != "manage_skills":
            return False
        if self.is_mcp_tool(tool_name):
            tool = self._tool_definition(tool_name)
            if tool is None:
                return False
        effect = self.write_effect_for_call(tool_name, arguments)
        tool = self._tool_definition(tool_name)
        if tool is not None:
            capability = getattr(tool, "effective_capability", None)
            if callable(capability):
                try:
                    cap = capability(arguments or {})
                    if not (
                        cap.effect == "unknown"
                        and tool_name in (self._confirm_tools | self._audit_only_tools)
                    ):
                        if tool_name == "memory_save" and cap.effect == "external_write":
                            return True
                        if tool_name == "manage_skills":
                            # It is both auditable and confirmation-gated;
                            # the dispatcher asks the high-risk handler first
                            # when capability.approval == confirm.
                            return bool(cap.audit)
                        return cap.audit and cap.approval == "audit"
                except Exception:
                    pass
        if effect == "none" and self._tool_definition(tool_name) is not None:
            return False
        if effect in {"workspace_write", "external_write"}:
            return tool_name not in {"run_code", "run_shell"}
        return tool_name in self._audit_only_tools

    def is_mutating_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
    ) -> bool:
        tool = self._tool_definition(tool_name)
        if tool is not None:
            capability = getattr(tool, "effective_capability", None)
            if callable(capability):
                try:
                    cap = capability(arguments or {})
                    if not (
                        cap.effect == "unknown"
                        and tool_name in (self._confirm_tools | self._audit_only_tools)
                    ):
                        return not cap.is_read_only
                except Exception:
                    pass
        effect = self.write_effect_for_call(tool_name, arguments)
        if effect == "none" and self._tool_definition(tool_name) is not None:
            return False
        if effect in {"workspace_write", "external_write", "dynamic", "unknown"}:
            return True
        return tool_name in self._mutating_tools

    def is_confirm_required_tool(self, tool_name: str) -> bool:
        # Agent self-management is an explicitly host-scoped, session-local
        # control surface.  Its own access check enforces host/session/skill
        # identity and it cannot change credentials, approval policy, or
        # global defaults, so routing it through the file/external mutation
        # confirmation gate only turns a settings update into a stuck tool
        # call.  Keep it auditable through the normal runtime path.
        if tool_name == "configure_agent":
            return False
        if self.is_read_only_safe_tool(tool_name):
            return False
        if tool_name in self._confirm_tools:
            return True
        tool = self._tool_definition(tool_name)
        if tool is not None:
            capability = getattr(tool, "effective_capability", None)
            if callable(capability):
                try:
                    cap = capability({})
                    if cap.effect == "unknown" and tool_name not in self._confirm_tools:
                        # 兼容旧的第三方普通工具：unknown 宿主工具维持
                        # 默认直通；MCP unknown 则由 fail-closed 分支确认。
                        return self.is_mcp_tool(tool_name)
                    if not (
                        cap.effect == "unknown"
                        and tool_name in (self._confirm_tools | self._audit_only_tools)
                    ):
                        if tool_name == "memory_save" and cap.effect == "external_write":
                            return False
                        return cap.approval == "confirm"
                except Exception:
                    pass
        if self.is_mcp_tool(tool_name):
            # 未绑定声明时保持兼容；绑定后的 MCP ToolDef 走上面的
            # ToolCapability，unknown/external 会 fail-closed。
            return False
        if self.is_audit_only_tool(tool_name):
            return False
        return tool_name in self._confirm_tools

    def is_high_risk_tool(self, tool_name: str) -> bool:
        return self.is_confirm_required_tool(tool_name)

    def is_undoable_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
    ) -> bool:
        """判断工具执行结果是否支持自动回滚。"""
        if self.is_read_only_safe_tool(tool_name):
            return False
        if tool_name in {"run_code", "run_shell"}:
            return False
        tool = self._tool_definition(tool_name)
        if tool is not None:
            capability = getattr(tool, "effective_capability", None)
            if callable(capability):
                try:
                    cap = capability(arguments or {})
                    if not (
                        cap.effect == "unknown"
                        and tool_name in (self._confirm_tools | self._audit_only_tools)
                    ):
                        return bool(cap.undoable)
                except Exception:
                    pass
        effect = self.write_effect_for_call(tool_name, arguments)
        if effect == "none" and self._tool_definition(tool_name) is not None:
            return False
        if self.is_mcp_tool(tool_name) or effect in {"external_write", "dynamic", "unknown"}:
            return False
        return True

    def is_mcp_tool(self, tool_name: str) -> bool:
        """判断工具名是否为 MCP 远程工具（以 mcp_ 前缀开头）。"""
        return tool_name.startswith("mcp_")

    def is_mcp_auto_approved(self, tool_name: str) -> bool:
        """判断 MCP 工具是否在白名单中（自动批准）。"""
        return tool_name in self._mcp_auto_approved

    def create_pending(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        tool_scope: Sequence[str] | None = None,
        parent_call_id: str | None = None,
    ) -> PendingApproval:
        if self._pending is not None:
            raise ValueError("存在待确认操作，请先执行 `/accept <id>` 或 `/reject <id>`。")
        if not parent_call_id:
            try:
                from excelmanus.tools.context import current_call

                ctx = current_call()
                raw = getattr(ctx, "parent_call_id", None) if ctx is not None else None
                parent_call_id = str(raw) if raw else None
            except Exception:
                parent_call_id = None
        pending = PendingApproval(
            approval_id=self._new_approval_id(),
            tool_name=tool_name,
            arguments=dict(arguments),
            tool_scope=list(tool_scope) if tool_scope is not None else [],
            created_at_utc=self._utc_now(),
            parent_call_id=parent_call_id,
        )
        self._pending = pending
        return pending

    def new_approval_id(self) -> str:
        return self._new_approval_id()

    def utc_now(self) -> str:
        return self._utc_now()

    def reject_pending(self, approval_id: str, *, timeout: bool = False) -> str:
        if self._pending is None:
            return "当前没有待确认操作。"
        if self._pending.approval_id != approval_id:
            return f"待确认 ID 不匹配。当前待确认 ID 为 `{self._pending.approval_id}`。"
        tool_name = self._pending.tool_name
        self._pending = None
        from excelmanus.engine_core.error_payload import (
            APPROVAL_DENIED,
            APPROVAL_TIMEOUT,
            dumps_error_payload,
            make_error_payload,
        )

        if timeout:
            message = (
                f"已拒绝待确认操作 `{approval_id}`（工具：{tool_name}）：审批等待超时。"
            )
            error_code = APPROVAL_TIMEOUT
        else:
            message = f"已拒绝待确认操作 `{approval_id}`（工具：{tool_name}）。"
            error_code = APPROVAL_DENIED
        return dumps_error_payload(
            make_error_payload(
                message,
                error_code=error_code,
                approval_id=approval_id,
                tool=tool_name,
            )
        )

    def clear_pending(self) -> None:
        self._pending = None

    def get_applied(self, approval_id: str) -> AppliedApprovalRecord | None:
        cached = self._applied.get(approval_id)
        if cached is not None:
            return cached

        # 优先从 DB 查询
        if self._db_store is not None:
            try:
                db_record = self._db_store.get(approval_id)
                if db_record is not None:
                    loaded = self._dict_to_applied_record(db_record)
                    if loaded is not None:
                        self._applied[approval_id] = loaded
                        return loaded
            except Exception:
                logger.debug("从 DB 加载审批记录失败: %s", approval_id, exc_info=True)

        # 回退到 manifest.json
        loaded = self._load_applied_from_manifest(approval_id)
        if loaded is None:
            return None
        self._applied[approval_id] = loaded
        return loaded

    def list_applied(
        self,
        *,
        limit: int = 50,
        undoable_only: bool = False,
        session_id: str | None = None,
    ) -> list[AppliedApprovalRecord]:
        """列出已执行的审批记录（最近在前）。

        优先从 DB 查询；无 DB 时回退到内存缓存 + 文件系统扫描。
        当 session_id 不为 None 时，仅返回该会话产生的记录。
        """
        records: dict[str, AppliedApprovalRecord] = {}

        # 1) DB 查询
        if self._db_store is not None:
            try:
                rows = self._db_store.list_approvals(
                    limit=limit, session_id=session_id,
                )
                for row in rows:
                    rec = self._dict_to_applied_record(row)
                    if rec is not None:
                        records[rec.approval_id] = rec
            except Exception:
                logger.debug("list_applied: DB 查询失败", exc_info=True)

        # 2) 内存缓存补充
        for aid, rec in self._applied.items():
            if aid not in records:
                if session_id is not None and rec.session_id != session_id:
                    continue
                records[aid] = rec

        # 3) 文件系统扫描（audit_root 下每个子目录即一个 approval_id）
        if self.audit_root.is_dir():
            for entry in sorted(self.audit_root.iterdir(), reverse=True):
                if not entry.is_dir():
                    continue
                aid = entry.name
                if aid in records:
                    continue
                loaded = self._load_applied_from_manifest(aid)
                if loaded is not None:
                    if session_id is not None and loaded.session_id != session_id:
                        continue
                    records[aid] = loaded
                if len(records) >= limit:
                    break

        result = sorted(
            records.values(),
            key=lambda r: r.applied_at_utc or r.created_at_utc,
            reverse=True,
        )
        if undoable_only:
            result = [r for r in result if r.undoable and r.changes]
        return result[:limit]

    def pending_block_message(self) -> str:
        if self._pending is None:
            return "当前没有待确认操作。"
        return (
            "存在待确认操作，已暂停新的高风险执行。\n"
            f"- ID: `{self._pending.approval_id}`\n"
            f"- 工具: `{self._pending.tool_name}`\n"
            "请先执行 `/accept <id>` 或 `/reject <id>`。"
        )

    def set_session_id(self, session_id: str | None) -> None:
        """更新当前关联的会话 ID（由 engine 在 session_id 确定后调用）。"""
        self._session_id = session_id

    def execute_and_audit(
        self,
        *,
        approval_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        tool_scope: Sequence[str],
        execute: Callable[[str, dict[str, Any], Sequence[str]], Any],
        undoable: bool,
        created_at_utc: str | None = None,
        code_policy_info: dict[str, Any] | None = None,
        session_turn: int | None = None,
        session_id: str | None = None,
    ) -> tuple[Any, AppliedApprovalRecord]:
        audit_dir = self.audit_root / approval_id
        audit_dir.mkdir(parents=True, exist_ok=True)

        targets = self._resolve_target_paths(tool_name, arguments)
        # 只有本地工作区/动态宿主写入需要扫描无显式目标的全局变更。外部
        # 写入、MCP unknown 和技能管理没有可靠的本地目标，不能把整个
        # workspace 当作假想目标；它们仍会留下统一的外部审计记录。
        effect = self.write_effect_for_call(tool_name, arguments)
        use_workspace_scan = (not targets) and (
            effect in {"workspace_write", "dynamic"}
            or (effect == "unknown" and not self.is_mcp_tool(tool_name))
        )

        before_partial = False
        after_partial = False
        if use_workspace_scan:
            before, before_partial, _, _ = self._collect_workspace_snapshots()
        else:
            before = self._collect_file_snapshots(targets)

        repo_diff_before = self._git_diff_text()

        result_payload: Any = ""
        result_text = ""
        execute_error: Exception | None = None
        error_type: str | None = None
        error_message: str | None = None
        from excelmanus.engine_core.tool_result import ToolResult as _ToolResult

        try:
            result_payload = execute(tool_name, arguments, tool_scope)

            if isinstance(result_payload, _ToolResult):
                result_text = result_payload.model_text
                if not result_payload.success:
                    error_message = (
                        result_payload.error.message
                        if result_payload.error is not None
                        else result_payload.model_text
                    )
                    error_type = (
                        result_payload.error.code
                        if result_payload.error is not None
                        else "TOOL_ERROR"
                    )
                    if error_type == "TOOL_EXECUTION_ERROR":
                        error_type = "ToolExecutionError"
            else:
                result_text = str(result_payload)
                result_payload = result_text
        except Exception as exc:  # noqa: BLE001
            execute_error = exc
            error_type = type(exc).__name__
            error_message = str(exc)

        # 字符串错误 JSON（非 ToolResult）仍记失败并抛出，便于旧调用方。
        # 已是 ToolResult 的契约失败必须原样返回，不能改写成 RuntimeError，
        # 否则 dispatcher 会 compact 掉 VERSION_CONFLICT / PATH_INVALID。
        if (
            execute_error is None
            and not isinstance(result_payload, _ToolResult)
            and result_text.startswith('{"status": "error"')
        ):
            try:
                _err_payload = json.loads(result_text)
                if isinstance(_err_payload, dict) and _err_payload.get("status") == "error":
                    _error_code = _err_payload.get("error_code", "")
                    if _error_code == "TOOL_EXECUTION_ERROR":
                        error_type = "ToolExecutionError"
                    else:
                        error_type = _err_payload.get("exception") or "ToolExecutionError"
                    error_message = _err_payload.get("message") or result_text
                    execute_error = RuntimeError(error_message)
            except (json.JSONDecodeError, AttributeError):
                pass

        if use_workspace_scan:
            after, after_partial, _, _ = self._collect_workspace_snapshots()
        else:
            after = self._collect_file_snapshots(targets)
        repo_diff_after = self._git_diff_text()

        if use_workspace_scan:
            changes, patch_text, binary_snapshots = self._build_change_records_from_snapshot_maps(
                before=before,
                after=after,
            )
        else:
            changes, patch_text, binary_snapshots = self._build_change_records(
                target_paths=targets,
                before=before,
                after=after,
            )

        patch_rel = None
        if patch_text:
            patch_file = audit_dir / "changes.patch"
            patch_file.write_text(patch_text, encoding="utf-8")
            patch_rel = str(patch_file.relative_to(self.workspace_root))
            for change in changes:
                if change.text_diff_file is None and not change.is_binary:
                    change.text_diff_file = patch_rel

        repo_before_file = audit_dir / "repo_diff_before.txt"
        repo_after_file = audit_dir / "repo_diff_after.txt"
        repo_before_file.write_text(repo_diff_before, encoding="utf-8")
        repo_after_file.write_text(repo_diff_after, encoding="utf-8")

        from excelmanus.engine_core.tool_result import ToolResult as _ToolResultStatus

        structured_failed = isinstance(result_payload, _ToolResultStatus) and not result_payload.success
        execution_status = "failed" if execute_error is not None or structured_failed else "success"
        preview_src = result_text
        if execute_error is not None:
            preview_src = f"{error_type}: {error_message}" if error_type else (error_message or "")

        _effective_session_id = session_id or self._session_id
        record = AppliedApprovalRecord(
            approval_id=approval_id,
            tool_name=tool_name,
            arguments=dict(arguments),
            tool_scope=list(tool_scope) if tool_scope is not None else [],
            created_at_utc=created_at_utc or self._utc_now(),
            applied_at_utc=self._utc_now(),
            undoable=undoable,
            manifest_file=str((audit_dir / "manifest.json").relative_to(self.workspace_root)),
            audit_dir=str(audit_dir.relative_to(self.workspace_root)),
            result_preview=self._shorten(preview_src, 300),
            execution_status=execution_status,
            error_type=error_type,
            error_message=error_message,
            partial_scan=(before_partial or after_partial),
            patch_file=patch_rel,
            changes=changes,
            binary_snapshots=binary_snapshots,
            repo_diff_before_file=str(repo_before_file.relative_to(self.workspace_root)),
            repo_diff_after_file=str(repo_after_file.relative_to(self.workspace_root)),
            session_turn=session_turn,
            session_id=_effective_session_id,
        )

        manifest = self._build_manifest_v2(record, code_policy_info=code_policy_info)
        (audit_dir / "manifest.json").write_text(
            # arguments 可携带 datetime 等非 JSON 值（如 Code Mode 桥还原的类型）；
            # 用 $em_type 标记编码保类型，读回经 revive_typed_args 还原。
            json.dumps(manifest, ensure_ascii=False, indent=2, default=em_json_default),
            encoding="utf-8",
        )

        self._applied[approval_id] = record

        # 同步写入 DB（如果可用）
        if self._db_store is not None:
            try:
                self._db_store.save(record.to_dict())
            except Exception:
                logger.warning("审批记录写入 DB 失败: %s", approval_id, exc_info=True)

        if execute_error is not None:
            raise execute_error
        return result_payload, record

    def record_completed_call(
        self,
        *,
        approval_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        tool_scope: Sequence[str] | None,
        result: Any,
        undoable: bool = False,
        created_at_utc: str | None = None,
        session_turn: int | None = None,
        session_id: str | None = None,
    ) -> AppliedApprovalRecord:
        """为已由专用 handler 完成的副作用写入统一审计记录。

        ``manage_skills`` 等工具必须保留专用异步 handler，不能把 handler
        本身递归塞回 registry；该入口让它们和普通/已批准执行共享同一份
        manifest/DB 审计格式，同时不伪造不存在的本地文件快照。
        """
        existing = self.get_applied(approval_id)
        if existing is not None:
            return existing

        audit_dir = self.audit_root / approval_id
        audit_dir.mkdir(parents=True, exist_ok=True)
        from excelmanus.engine_core.tool_result import (
            ToolResult as _ToolResult,
            coerce_legacy_result,
        )

        structured_result = result if isinstance(result, _ToolResult) else coerce_legacy_result(result)
        result_text = structured_result.model_text
        execution_status = "success" if structured_result.success else "failed"
        error_type = structured_result.error.code if structured_result.error is not None else None
        error_message = structured_result.error.message if structured_result.error is not None else None

        # 外部/技能变更没有可验证的 before/after 文件快照，但仍记录当前
        # repo 状态，便于排查宿主侧发生了什么。
        repo_diff = self._git_diff_text()
        repo_before_file = audit_dir / "repo_diff_before.txt"
        repo_after_file = audit_dir / "repo_diff_after.txt"
        repo_before_file.write_text("", encoding="utf-8")
        repo_after_file.write_text(repo_diff, encoding="utf-8")
        record = AppliedApprovalRecord(
            approval_id=approval_id,
            tool_name=tool_name,
            arguments=dict(arguments),
            tool_scope=list(tool_scope) if tool_scope is not None else [],
            created_at_utc=created_at_utc or self._utc_now(),
            applied_at_utc=self._utc_now(),
            undoable=bool(undoable),
            manifest_file=str((audit_dir / "manifest.json").relative_to(self.workspace_root)),
            audit_dir=str(audit_dir.relative_to(self.workspace_root)),
            result_preview=self._shorten(result_text, 300),
            execution_status=execution_status,
            error_type=error_type,
            error_message=error_message,
            repo_diff_before_file=str(repo_before_file.relative_to(self.workspace_root)),
            repo_diff_after_file=str(repo_after_file.relative_to(self.workspace_root)),
            session_turn=session_turn,
            session_id=session_id or self._session_id,
        )
        manifest = self._build_manifest_v2(record)
        (audit_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, default=em_json_default),
            encoding="utf-8",
        )
        self._applied[approval_id] = record
        if self._db_store is not None:
            try:
                self._db_store.save(record.to_dict())
            except Exception:
                logger.warning("副作用审计记录写入 DB 失败: %s", approval_id, exc_info=True)
        return record

    def mark_non_undoable_for_paths(self, rel_paths: set[str]) -> int:
        """将涉及指定路径的审批记录标记为不可回滚。

        在备份应用后调用，防止 undo 操作在原始文件已被覆盖的情况下
        使用过期的备份副本。

        Returns the number of records affected.
        """
        count = 0
        for record in self._applied.values():
            if not record.undoable or not record.changes:
                continue
            if any(change.path in rel_paths for change in record.changes):
                record.undoable = False
                self._persist_undoable_flag(record)
                count += 1
        return count

    def _persist_undoable_flag(self, record: AppliedApprovalRecord) -> None:
        """将 record.undoable 同步写回磁盘 manifest.json 和 DB（best-effort）。"""
        manifest_path = self.workspace_root / record.manifest_file
        if not manifest_path.exists():
            return
        try:
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
            if isinstance(raw.get("approval"), dict):
                raw["approval"]["undoable"] = record.undoable
                manifest_path.write_text(
                    json.dumps(raw, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
        except (OSError, json.JSONDecodeError, TypeError):
            pass
        if self._db_store is not None:
            try:
                self._db_store.update_undoable(record.approval_id, record.undoable)
            except Exception:
                logger.debug("DB undoable 标记同步失败: %s", record.approval_id, exc_info=True)

    def undo(self, approval_id: str) -> str:
        """Restore workbook files to this transaction's beforeEdit when present."""
        record = self.get_applied(approval_id)
        if record is None:
            return f"未找到已执行记录 `{approval_id}`。"
        if not record.undoable:
            return f"记录 `{approval_id}` 不支持自动回滚（工具：{record.tool_name}）。"
        if not record.changes:
            return f"记录 `{approval_id}` 没有可回滚的文件变更。"

        from excelmanus.workbook_commit import CommitError

        try:
            restored = self._restore_revision_before(record)
        except (CommitError, ValueError, OSError) as exc:
            logger.warning("approval undo failed: %s", approval_id, exc_info=True)
            return f"未回滚 `{approval_id}`：{exc}。请检查文件版本后重试。"
        if restored:
            record.undoable = False
            self._persist_undoable_flag(record)
            names = ", ".join(restored)
            return (
                f"已回滚 `{approval_id}`：已 restore {len(restored)} 个文件到 beforeEdit"
                f"（{names}）。"
            )
        return (
            f"未回滚 `{approval_id}`：找不到与本次操作完整对应的编辑前后快照。"
            "文件回退请用 manage_spreadsheet_versions restore。"
        )

    def _restore_revision_before(self, record: AppliedApprovalRecord) -> list[str]:
        from excelmanus.workspace.file_service import TargetSpec, WorkspaceFileService
        from excelmanus.workspace.revisions import RevisionStore

        store = RevisionStore(self.workspace_root)
        targets: list[TargetSpec] = []
        for change in record.changes:
            rel = str(change.path or "").replace("\\", "/").removeprefix("./").strip()
            if not rel or not change.after_exists:
                return []
            after_hash = str(change.after_hash or "").replace("sha256:", "")
            if not after_hash:
                return []

            # 创建文件没有 beforeEdit 快照。只要当前版本仍然精确等于本次
            # afterEdit，就用同一个受版本保护的事务入口删除它；任何后续
            # 修改都会触发 VERSION_CONFLICT，避免误删用户的新内容。
            if not change.before_exists:
                targets.append(
                    TargetSpec(
                        op="delete",
                        path=rel,
                        expected_version=f"sha256:{after_hash}",
                    )
                )
                continue

            if not change.after_exists:
                return []
            before_hash = str(change.before_hash or "").replace("sha256:", "")
            if not before_hash:
                return []
            records = store.list(rel)
            matching_transactions = {
                rec.transaction_id for rec in records
                if rec.reason == "afterEdit" and rec.sha256 == after_hash
            }
            before = next(
                (
                    rec
                    for rec in reversed(records)
                    if rec.transaction_id in matching_transactions
                    and rec.reason == "beforeEdit" and rec.sha256 == before_hash
                ),
                None,
            )
            if before is None:
                return []
            targets.append(TargetSpec(op="restore", path=rel, restore_revision_id=before.id,
                                      expected_version=f"sha256:{after_hash}"))
        if not targets:
            return []
        # Validate every file under the same transaction lock before publishing.
        # A newer edit to any target must prevent undoing the other targets too.
        service = WorkspaceFileService(self.workspace_root)
        service.raise_if_failed(service.apply_batch(targets))
        return [target.path for target in targets]

    def _new_approval_id(self) -> str:
        now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        return f"apv_{now}_{secrets.token_hex(3)}"

    @staticmethod
    def _utc_now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _shorten(text: str, limit: int) -> str:
        return text if len(text) <= limit else f"{text[:limit]}...(truncated)"

    def _resolve_target_paths(self, tool_name: str, arguments: dict[str, Any]) -> list[Path]:
        path_args: list[str] = []

        # edit_spreadsheet's cross-file mode keeps target paths inside the
        # workbooks array.  Include every destination in the same approval and
        # audit record so the batch cannot hide a secondary write target.
        if tool_name == "edit_spreadsheet":
            batch = arguments.get("workbooks")
            if isinstance(batch, str):
                try:
                    batch = json.loads(batch)
                except (TypeError, ValueError):
                    batch = None
            if isinstance(batch, list):
                for item in batch:
                    if isinstance(item, dict):
                        raw = item.get("file_path") or item.get("path")
                        if raw:
                            path_args.append(str(raw).strip())

        all_fields = AUDIT_TARGET_ARG_RULES_ALL.get(tool_name)
        if all_fields is not None:
            for field_name in all_fields:
                raw = arguments.get(field_name)
                text = str(raw).strip() if raw is not None else ""
                if text:
                    path_args.append(text)
        else:
            first_fields = AUDIT_TARGET_ARG_RULES_FIRST.get(tool_name)
            if first_fields is not None:
                for field_name in first_fields:
                    raw = arguments.get(field_name)
                    text = str(raw).strip() if raw is not None else ""
                    if text:
                        path_args = [text]
                        break

        resolved: list[Path] = []
        seen: set[str] = set()
        for raw in path_args:
            path = self._resolve_workspace_path(raw)
            if path is None:
                continue
            key = str(path)
            if key in seen:
                continue
            seen.add(key)
            resolved.append(path)
        return resolved

    def _resolve_workspace_path(self, value: str) -> Path | None:
        if not value:
            return None
        raw = Path(value).expanduser()
        path = raw if raw.is_absolute() else self.workspace_root / raw
        resolved = path.resolve(strict=False)
        try:
            resolved.relative_to(self.workspace_root)
        except ValueError:
            return None
        return resolved

    def _collect_file_snapshots(self, paths: list[Path]) -> dict[str, _FileSnapshot]:
        snapshots: dict[str, _FileSnapshot] = {}
        for path in paths:
            rel = str(path.relative_to(self.workspace_root))
            if path.exists() and path.is_file():
                snapshots[rel] = _FileSnapshot(exists=True, content=path.read_bytes())
            else:
                snapshots[rel] = _FileSnapshot(exists=False, content=None)
        return snapshots

    def _collect_workspace_snapshots(self) -> tuple[dict[str, _FileSnapshot], bool, int, int]:
        snapshots: dict[str, _FileSnapshot] = {}
        partial_scan = False
        scanned_files = 0
        hashed_bytes = 0

        exclude_prefixes = tuple(
            self._normalize_rel_prefix(prefix)
            for prefix in WORKSPACE_SCAN_EXCLUDE_PREFIXES
            if self._normalize_rel_prefix(prefix)
        )

        for root, dirs, files in os.walk(self.workspace_root):
            root_path = Path(root)
            root_rel = root_path.relative_to(self.workspace_root)

            # 原地裁剪目录，降低遍历开销。
            kept_dirs: list[str] = []
            for name in dirs:
                rel_path = (
                    (root_rel / name).as_posix() if str(root_rel) != "." else Path(name).as_posix()
                )
                if self._is_excluded_rel(rel_path, exclude_prefixes):
                    continue
                kept_dirs.append(name)
            dirs[:] = kept_dirs

            for name in files:
                rel_path = (
                    (root_rel / name).as_posix() if str(root_rel) != "." else Path(name).as_posix()
                )
                if self._is_excluded_rel(rel_path, exclude_prefixes):
                    continue

                path = root_path / name
                if path.is_symlink() or not path.is_file():
                    continue

                scanned_files += 1
                if scanned_files > WORKSPACE_SCAN_MAX_FILES:
                    partial_scan = True
                    return snapshots, partial_scan, scanned_files - 1, hashed_bytes

                try:
                    size = path.stat().st_size
                except OSError:
                    continue

                if (hashed_bytes + size) > WORKSPACE_SCAN_MAX_HASH_BYTES:
                    partial_scan = True
                    return snapshots, partial_scan, scanned_files - 1, hashed_bytes

                try:
                    content = path.read_bytes()
                except OSError:
                    continue

                hashed_bytes += size
                snapshots[rel_path] = _FileSnapshot(exists=True, content=content)

        return snapshots, partial_scan, scanned_files, hashed_bytes

    @staticmethod
    def _normalize_rel_prefix(prefix: str) -> str:
        return prefix.replace("\\", "/").strip("/")

    @staticmethod
    def _is_excluded_rel(rel_path: str, prefixes: Sequence[str]) -> bool:
        normalized = rel_path.replace("\\", "/").strip("/")
        if not normalized:
            return False
        for prefix in prefixes:
            if normalized == prefix or normalized.startswith(f"{prefix}/"):
                return True
        return False

    def _build_change_records(
        self,
        *,
        target_paths: list[Path],
        before: dict[str, _FileSnapshot],
        after: dict[str, _FileSnapshot],
    ) -> tuple[list[FileChangeRecord], str, list[BinarySnapshotRecord]]:
        before_subset: dict[str, _FileSnapshot] = {}
        after_subset: dict[str, _FileSnapshot] = {}
        for abs_path in target_paths:
            rel = str(abs_path.relative_to(self.workspace_root))
            before_subset[rel] = before.get(rel, _FileSnapshot(exists=False, content=None))
            after_subset[rel] = after.get(rel, _FileSnapshot(exists=False, content=None))
        return self._build_change_records_from_snapshot_maps(
            before=before_subset,
            after=after_subset,
        )

    def _build_change_records_from_snapshot_maps(
        self,
        *,
        before: dict[str, _FileSnapshot],
        after: dict[str, _FileSnapshot],
    ) -> tuple[list[FileChangeRecord], str, list[BinarySnapshotRecord]]:
        changes: list[FileChangeRecord] = []
        patches: list[str] = []

        for rel in sorted(set(before) | set(after)):
            before_snap = before.get(rel, _FileSnapshot(exists=False, content=None))
            after_snap = after.get(rel, _FileSnapshot(exists=False, content=None))
            if (
                before_snap.exists == after_snap.exists
                and before_snap.content == after_snap.content
            ):
                continue

            base_content = (
                before_snap.content if before_snap.content is not None else after_snap.content
            )
            is_binary = self._is_binary_content(base_content)

            if not is_binary:
                patch = self._build_unified_diff(
                    rel_path=rel,
                    before_content=before_snap.content,
                    after_content=after_snap.content,
                )
                if patch:
                    patches.append(patch)

            changes.append(
                FileChangeRecord(
                    path=rel,
                    before_exists=before_snap.exists,
                    after_exists=after_snap.exists,
                    before_hash=before_snap.sha256,
                    after_hash=after_snap.sha256,
                    before_size=before_snap.size,
                    after_size=after_snap.size,
                    is_binary=is_binary,
                )
            )

        patch_text = "\n".join(patches).strip()
        if patch_text:
            patch_text += "\n"
        return changes, patch_text, []

    def _build_manifest_v2(
        self,
        record: AppliedApprovalRecord,
        *,
        code_policy_info: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "version": 2,
            "approval": {
                "approval_id": record.approval_id,
                "tool_name": record.tool_name,
                "arguments": record.arguments,
                "tool_scope": record.tool_scope,
                "created_at_utc": record.created_at_utc,
                "applied_at_utc": record.applied_at_utc,
                "undoable": record.undoable,
                "session_turn": record.session_turn,
                "session_id": record.session_id,
            },
            "execution": {
                "status": record.execution_status,
                "result_preview": record.result_preview,
                "error_type": record.error_type,
                "error_message": record.error_message,
                "partial_scan": record.partial_scan,
            },
            "artifacts": {
                "audit_dir": record.audit_dir,
                "manifest_file": record.manifest_file,
                "repo_diff_before_file": record.repo_diff_before_file,
                "repo_diff_after_file": record.repo_diff_after_file,
                "patch_file": record.patch_file,
            },
            "changes": {
                "files": [asdict(change) for change in record.changes],
                "binary_snapshots": [asdict(item) for item in record.binary_snapshots],
            },
        }
        if code_policy_info is not None:
            result["code_policy"] = code_policy_info
        return result

    def _load_applied_from_manifest(self, approval_id: str) -> AppliedApprovalRecord | None:
        manifest_path = self.audit_root / approval_id / "manifest.json"
        if not manifest_path.exists():
            return None

        try:
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

        if raw.get("version") != 2:
            return None

        approval = raw.get("approval") if isinstance(raw.get("approval"), dict) else {}
        execution = raw.get("execution") if isinstance(raw.get("execution"), dict) else {}
        artifacts = raw.get("artifacts") if isinstance(raw.get("artifacts"), dict) else {}
        changes_block = raw.get("changes") if isinstance(raw.get("changes"), dict) else {}

        changes_raw = changes_block.get("files")
        binary_raw = changes_block.get("binary_snapshots")

        changes: list[FileChangeRecord] = []
        if isinstance(changes_raw, list):
            for item in changes_raw:
                if not isinstance(item, dict):
                    continue
                changes.append(
                    FileChangeRecord(
                        path=str(item.get("path", "")),
                        before_exists=bool(item.get("before_exists", False)),
                        after_exists=bool(item.get("after_exists", False)),
                        before_hash=(
                            str(item.get("before_hash"))
                            if item.get("before_hash") is not None
                            else None
                        ),
                        after_hash=(
                            str(item.get("after_hash"))
                            if item.get("after_hash") is not None
                            else None
                        ),
                        before_size=(
                            int(item.get("before_size"))
                            if item.get("before_size") is not None
                            else None
                        ),
                        after_size=(
                            int(item.get("after_size"))
                            if item.get("after_size") is not None
                            else None
                        ),
                        is_binary=bool(item.get("is_binary", False)),
                        text_diff_file=(
                            str(item.get("text_diff_file"))
                            if item.get("text_diff_file") is not None
                            else None
                        ),
                        before_snapshot_file=(
                            str(item.get("before_snapshot_file"))
                            if item.get("before_snapshot_file") is not None
                            else None
                        ),
                    )
                )

        binary_snapshots: list[BinarySnapshotRecord] = []
        if isinstance(binary_raw, list):
            for item in binary_raw:
                if not isinstance(item, dict):
                    continue
                binary_snapshots.append(
                    BinarySnapshotRecord(
                        path=str(item.get("path", "")),
                        snapshot_file=str(item.get("snapshot_file", "")),
                        hash_sha256=str(item.get("hash_sha256", "")),
                        size_bytes=int(item.get("size_bytes", 0)),
                    )
                )

        applied_id = str(approval.get("approval_id") or approval_id)
        audit_dir = self.audit_root / applied_id

        return AppliedApprovalRecord(
            approval_id=applied_id,
            tool_name=str(approval.get("tool_name", "")),
            arguments=(
                revive_typed_args(dict(approval.get("arguments")))
                if isinstance(approval.get("arguments"), dict)
                else {}
            ),
            tool_scope=(
                [str(value) for value in approval.get("tool_scope", [])]
                if isinstance(approval.get("tool_scope"), list)
                else []
            ),
            created_at_utc=str(approval.get("created_at_utc", "")),
            applied_at_utc=str(approval.get("applied_at_utc", "")),
            undoable=bool(approval.get("undoable", False)),
            session_turn=approval.get("session_turn"),
            session_id=approval.get("session_id"),
            manifest_file=str(manifest_path.relative_to(self.workspace_root)),
            audit_dir=str(audit_dir.relative_to(self.workspace_root)),
            result_preview=str(execution.get("result_preview", "")),
            execution_status=str(execution.get("status", "success")),
            error_type=(
                str(execution.get("error_type"))
                if execution.get("error_type") is not None
                else None
            ),
            error_message=(
                str(execution.get("error_message"))
                if execution.get("error_message") is not None
                else None
            ),
            partial_scan=bool(execution.get("partial_scan", False)),
            patch_file=(
                str(artifacts.get("patch_file"))
                if artifacts.get("patch_file") is not None
                else None
            ),
            changes=changes,
            binary_snapshots=binary_snapshots,
            repo_diff_before_file=(
                str(artifacts.get("repo_diff_before_file"))
                if artifacts.get("repo_diff_before_file") is not None
                else None
            ),
            repo_diff_after_file=(
                str(artifacts.get("repo_diff_after_file"))
                if artifacts.get("repo_diff_after_file") is not None
                else None
            ),
        )

    def _dict_to_applied_record(self, d: dict[str, Any]) -> AppliedApprovalRecord | None:
        """将 ApprovalStore 返回的 dict 转为 AppliedApprovalRecord。"""
        try:
            changes_raw = d.get("changes", [])
            changes: list[FileChangeRecord] = []
            if isinstance(changes_raw, list):
                for item in changes_raw:
                    if isinstance(item, dict):
                        changes.append(FileChangeRecord(
                            path=str(item.get("path", "")),
                            before_exists=bool(item.get("before_exists", False)),
                            after_exists=bool(item.get("after_exists", False)),
                            before_hash=item.get("before_hash"),
                            after_hash=item.get("after_hash"),
                            before_size=item.get("before_size"),
                            after_size=item.get("after_size"),
                            is_binary=bool(item.get("is_binary", False)),
                            text_diff_file=item.get("text_diff_file"),
                            before_snapshot_file=item.get("before_snapshot_file"),
                        ))

            binary_raw = d.get("binary_snapshots", [])
            binary_snapshots: list[BinarySnapshotRecord] = []
            if isinstance(binary_raw, list):
                for item in binary_raw:
                    if isinstance(item, dict):
                        binary_snapshots.append(BinarySnapshotRecord(
                            path=str(item.get("path", "")),
                            snapshot_file=str(item.get("snapshot_file", "")),
                            hash_sha256=str(item.get("hash_sha256", "")),
                            size_bytes=int(item.get("size_bytes", 0)),
                        ))

            return AppliedApprovalRecord(
                approval_id=str(d.get("id", "")),
                tool_name=str(d.get("tool_name", "")),
                arguments=d.get("arguments", {}),
                tool_scope=d.get("tool_scope", []),
                created_at_utc=str(d.get("created_at_utc", "")),
                applied_at_utc=str(d.get("applied_at_utc", "")),
                undoable=bool(d.get("undoable", False)),
                manifest_file=str(d.get("manifest_file", "")),
                audit_dir=str(d.get("audit_dir", "")),
                result_preview=str(d.get("result_preview", "")),
                execution_status=str(d.get("execution_status", "success")),
                error_type=d.get("error_type"),
                error_message=d.get("error_message"),
                partial_scan=bool(d.get("partial_scan", False)),
                patch_file=d.get("patch_file"),
                changes=changes,
                binary_snapshots=binary_snapshots,
                repo_diff_before_file=d.get("repo_diff_before"),
                repo_diff_after_file=d.get("repo_diff_after"),
                session_turn=d.get("session_turn"),
                session_id=d.get("session_id"),
            )
        except Exception:
            logger.debug("DB 审批记录转换失败", exc_info=True)
            return None

    @staticmethod
    def _sha256(content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()

    @staticmethod
    def _is_binary_content(content: bytes | None) -> bool:
        if content is None:
            return False
        if b"\x00" in content:
            return True
        try:
            content.decode("utf-8")
            return False
        except UnicodeDecodeError:
            return True

    def _build_unified_diff(
        self,
        *,
        rel_path: str,
        before_content: bytes | None,
        after_content: bytes | None,
    ) -> str:
        before_text = before_content.decode("utf-8", errors="replace") if before_content is not None else ""
        after_text = after_content.decode("utf-8", errors="replace") if after_content is not None else ""
        from_file = f"a/{rel_path}" if before_content is not None else "/dev/null"
        to_file = f"b/{rel_path}" if after_content is not None else "/dev/null"
        diff = unified_diff(
            before_text.splitlines(keepends=True),
            after_text.splitlines(keepends=True),
            fromfile=from_file,
            tofile=to_file,
            lineterm="",
        )
        return "\n".join(diff).strip()

    def _git_diff_text(self) -> str:
        if not (self.workspace_root / ".git").exists():
            return "git diff 不可用：当前工作区不是 git 仓库。"
        try:
            completed = subprocess.run(
                ["git", "-C", str(self.workspace_root), "diff", "--no-color"],
                capture_output=True,
                text=True,
                check=False,
            )
            if completed.returncode != 0:
                stderr = completed.stderr.strip()
                return stderr or f"git diff 执行失败，退出码 {completed.returncode}"
            return completed.stdout
        except Exception as exc:  # noqa: BLE001
            return f"git diff 捕获异常：{exc}"
