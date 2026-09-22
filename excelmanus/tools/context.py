"""ToolCallContext：一次工具调用的工作区、权限与观察版本。

缺失时 fail-closed，禁止回退 cwd / 模块单例 / MCP 闭包根。
"""

from __future__ import annotations

import contextvars
import hashlib
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from excelmanus.security.guard import FileAccessGuard
from excelmanus.workspace.identity import resolve_canonical
from excelmanus.workspace.refs import FileRef, WorkspaceRef

CatalogMode = Literal["read", "plan", "write"]
ToolAccess = Literal["may_write", "read_only"]
ApprovalPolicy = Literal["ask", "never"]
Actor = Literal["host", "child", "http"]


class ToolContextMissing(RuntimeError):
    """正式执行路径没有 ToolCallContext。"""

    error_code = "TOOL_CONTEXT_MISSING"


@dataclass(frozen=True)
class CallerCapability:
    catalog_mode: CatalogMode = "write"
    tool_access: ToolAccess = "may_write"
    approval: ApprovalPolicy = "ask"
    full_access: bool = False
    allowed_tools: frozenset[str] | None = None
    disallowed_tools: frozenset[str] = frozenset()


@dataclass(frozen=True)
class SessionBinding:
    session_id: str
    workspace: WorkspaceRef
    capability: CallerCapability
    actor: Actor = "host"
    parent_session_id: str | None = None


@dataclass(frozen=True)
class ToolCallContext:
    binding: SessionBinding
    call_id: str = ""
    tool_name: str = ""
    arguments: Mapping[str, Any] = field(default_factory=dict)
    parent_call_id: str | None = None
    observed_versions: Mapping[str, str] = field(default_factory=dict)
    durable_attachment_ids: frozenset[str] = field(default_factory=frozenset)
    # 宿主提供的本轮披露集合；不代表授权，编译时仍与有效目录求交。
    loaded_tool_names: set[str] | None = field(default=None, compare=False, repr=False)


_current_call: contextvars.ContextVar[ToolCallContext | None] = contextvars.ContextVar(
    "excelmanus_tool_call",
    default=None,
)


def current_call() -> ToolCallContext | None:
    return _current_call.get()


def call_has_full_access() -> bool:
    """返回当前调用是否由宿主签发了「跳过审批」能力。"""
    ctx = current_call()
    return bool(ctx is not None and ctx.binding.capability.full_access)


def operation_id_for(path: str | None = None) -> str | None:
    """Stable workspace mutation key for the current dispatched tool call."""
    ctx = current_call()
    if ctx is None or not ctx.call_id:
        return None
    if not ctx.binding.session_id:
        return None
    if ctx.call_id == "bind_workspace" and not ctx.arguments:
        return None
    material = "|".join(
        str(item or "") for item in (
            ctx.binding.session_id,
            ctx.call_id,
            ctx.tool_name,
            str(path or "").replace("\\", "/").strip().lower(),
        )
    )
    return "emop_" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def bind_call(ctx: ToolCallContext) -> contextvars.Token[ToolCallContext | None]:
    return _current_call.set(ctx)


def reset_call(token: contextvars.Token[ToolCallContext | None]) -> None:
    _current_call.reset(token)


def clear_call() -> None:
    _current_call.set(None)


def require_call() -> ToolCallContext:
    ctx = current_call()
    if ctx is None:
        raise ToolContextMissing("工具调用缺少工作区上下文；入口必须显式 bind ToolCallContext。")
    return ctx


def require_guard() -> FileAccessGuard:
    ctx = require_call()
    return FileAccessGuard(str(ctx.binding.workspace.root))


def bind_workspace(
    root: str | Path,
    *,
    workspace_id: str | None = None,
    session_id: str = "test",
    catalog_mode: CatalogMode = "write",
) -> contextvars.Token[ToolCallContext | None]:
    """测试与非分发入口：绑定最小调用上下文。"""
    binding = SessionBinding(
        session_id=session_id,
        workspace=WorkspaceRef.from_root(root, workspace_id=workspace_id),
        capability=CallerCapability(catalog_mode=catalog_mode),
        actor="host",
    )
    return bind_call(ToolCallContext(binding=binding, call_id="bind_workspace"))


@contextmanager
def use_workspace(
    root: str | Path,
    *,
    workspace_id: str | None = None,
) -> Iterator[ToolCallContext]:
    token = bind_workspace(root, workspace_id=workspace_id)
    try:
        ctx = require_call()
        yield ctx
    finally:
        reset_call(token)


def resolve_file_ref(raw: str, *, observed_version: str | None = None) -> FileRef:
    ctx = require_call()
    guard = FileAccessGuard(str(ctx.binding.workspace.root))
    resolved = guard.resolve_and_validate(raw)
    relative = resolve_canonical(ctx.binding.workspace.root, str(resolved)).relative
    return FileRef(
        workspace=ctx.binding.workspace,
        relative=relative,
        observed_version=observed_version,
    )


_ALL_FAMILIES: frozenset[str] = frozenset({"xlsx", "csv", "docx", "mcp"})


def capability_from_engine(engine: Any) -> CallerCapability:
    """权限目录由 chat_mode 决定，不受 schema 按需披露影响。

    ``allowed_tools`` 承载 mode 级权限投影（read/plan 不含写入工具），供
    child ``restrict()`` / 交集检查收窄；文件族与 MCP 是否可用按实时工作区
    与注册表在执行目录推导时计算——此处传全集 families，不把文件族投影
    冻结进名单（否则 docx/MCP 工具会被执行目录永久排除）。
    """
    from excelmanus.tools.catalog import derive_effective_catalog

    chat = str(getattr(engine, "_current_chat_mode", "write") or "write")
    if chat == "read":
        mode: CatalogMode = "read"
    elif chat == "plan":
        mode = "plan"
    else:
        mode = "write"
    registry = getattr(engine, "registry", None) or getattr(engine, "_registry", None)
    names: frozenset[str] | None = None
    if registry is not None:
        getter = getattr(registry, "get_all_tools", None)
        if callable(getter):
            raw = getter()
            if isinstance(raw, (list, tuple)):
                from excelmanus.tools.catalog import _allow_run_code_of

                catalog = derive_effective_catalog(
                    tools=list(raw),
                    mode=mode,
                    families=_ALL_FAMILIES,
                    allow_run_code=_allow_run_code_of(engine, mode),
                )
                names = frozenset(catalog.names())
    from excelmanus.security.policy import resolve_approval_policy
    from excelmanus.self_management import disallowed_tools

    return CallerCapability(
        catalog_mode=mode,
        approval=resolve_approval_policy(engine),
        full_access=bool(getattr(engine, "_full_access_enabled", False)),
        allowed_tools=names,
        disallowed_tools=frozenset(disallowed_tools(engine)),
    )


def binding_from_engine(engine: Any) -> SessionBinding:
    """生产引擎返回 SessionBinding；测试 double 从 workspace 派生。"""
    raw = getattr(engine, "session_binding", None)
    if isinstance(raw, SessionBinding):
        return raw
    root = None
    ws = getattr(engine, "workspace", None) or getattr(engine, "_workspace", None)
    if ws is not None:
        root = getattr(ws, "root_dir", None)
    if root is None:
        cfg = getattr(engine, "config", None) or getattr(engine, "_config", None)
        root = getattr(cfg, "workspace_root", None)
    if root is None:
        raise ToolContextMissing("引擎没有工作区，无法构造 SessionBinding。")
    ws_ref = getattr(engine, "_workspace_ref", None)
    if not isinstance(ws_ref, WorkspaceRef):
        ws_ref = WorkspaceRef.from_root(root)
    actor: Actor = "child" if not getattr(engine, "_is_host_session", True) else "host"
    return SessionBinding(
        session_id=str(getattr(engine, "_session_id", None) or ""),
        workspace=ws_ref,
        capability=getattr(engine, "_fixed_capability", None) or capability_from_engine(engine),
        actor=actor,
    )


def intersect_capability(
    parent: CallerCapability,
    *,
    permission_mode: str,
    allowed_tools: Sequence[str] | None,
    disallowed_tools: Sequence[str] = (),
    extra_disallowed: Sequence[str] = (),
) -> CallerCapability:
    """child = 父约束 ∩ 子配置，只能收窄。"""
    if permission_mode == "readOnly" or parent.catalog_mode == "read" or parent.tool_access == "read_only":
        child_mode: CatalogMode = "read"
    elif parent.catalog_mode == "plan":
        child_mode = "plan"
    else:
        child_mode = "write"
    parent_names = parent.allowed_tools
    requested = [str(n) for n in (allowed_tools or ()) if str(n).strip()]
    if requested:
        child_names = set(requested)
        if parent_names is not None:
            child_names &= set(parent_names)
    else:
        child_names = set(parent_names) if parent_names is not None else None
    blocked = {str(n) for n in disallowed_tools if str(n)}
    blocked.update(str(n) for n in extra_disallowed if str(n))
    blocked |= set(parent.disallowed_tools)
    if child_names is not None:
        child_names -= blocked
        allowed: frozenset[str] | None = frozenset(child_names)
    else:
        allowed = None
    access: ToolAccess = "read_only" if child_mode == "read" else "may_write"
    return CallerCapability(
        catalog_mode=child_mode,
        tool_access=access,
        approval="never",
        full_access=parent.full_access,
        allowed_tools=allowed,
        disallowed_tools=frozenset(blocked),
    )


def execution_catalog_tools(engine: Any) -> list[Any]:
    """SDK / 子调用可见的完整执行目录：按 chat_mode 和调用方授权投影。

    统一走 ``execution_catalog_from_engine``——与 wire 目录、introspect、
    能力地图、策略段共享同一组输入（capability、文件族、CSV profile、MCP
    注册、元工具 schema 刷新），避免各入口各自推导产生偏差。
    """
    from excelmanus.tools.catalog import execution_catalog_from_engine

    catalog = execution_catalog_from_engine(engine)
    if catalog is None:
        return []
    return list(catalog.tools)
