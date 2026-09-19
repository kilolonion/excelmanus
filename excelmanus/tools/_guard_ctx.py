"""兼容薄包装：委托 ToolCallContext，不再作为路径权威。"""

from __future__ import annotations

import contextvars
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from excelmanus.security import FileAccessGuard


def set_guard(guard: "FileAccessGuard") -> contextvars.Token:
    """从 guard 根绑定 ToolCallContext。"""
    from excelmanus.tools.context import bind_workspace

    root = getattr(guard, "workspace_root", None)
    return bind_workspace(str(root))


def get_guard() -> "FileAccessGuard | None":
    """从当前调用上下文派生守卫；缺失则 None。"""
    from excelmanus.security.guard import FileAccessGuard
    from excelmanus.tools.context import current_call

    call = current_call()
    if call is None:
        return None
    return FileAccessGuard(str(call.binding.workspace.root))


def reset_guard(token: contextvars.Token) -> None:
    from excelmanus.tools.context import reset_call

    reset_call(token)
