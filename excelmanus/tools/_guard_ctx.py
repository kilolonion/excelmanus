"""通过 contextvar 实现每会话的 FileAccessGuard。

工具分发器在每次工具调用前设置 contextvar，
工具函数即可自动获得对应用户的 guard。
模块级 _guard 单例仍作为 CLI 模式下的回退。
"""

from __future__ import annotations

import contextvars
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from excelmanus.security import FileAccessGuard

_current_guard: contextvars.ContextVar["FileAccessGuard | None"] = contextvars.ContextVar(
    "_current_guard", default=None,
)


def set_guard(guard: "FileAccessGuard") -> contextvars.Token:
    """设置每会话的 FileAccessGuard，返回用于恢复的 token。"""
    return _current_guard.set(guard)


def get_guard() -> "FileAccessGuard | None":
    """获取每会话的 FileAccessGuard，为 None 时使用模块级回退。"""
    return _current_guard.get(None)


def reset_guard(token: contextvars.Token) -> None:
    """将 contextvar 恢复为先前值。"""
    _current_guard.reset(token)
