"""Per-session FileAccessGuard via contextvar.

The tool dispatcher sets the contextvar before each tool call,
so tool functions automatically pick up the correct per-user guard.
Module-level ``_guard`` singletons remain as fallback for CLI mode.
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
    """Set per-session FileAccessGuard. Returns reset token."""
    return _current_guard.set(guard)


def get_guard() -> "FileAccessGuard | None":
    """Get per-session FileAccessGuard, or None to use module fallback."""
    return _current_guard.get(None)


def reset_guard(token: contextvars.Token) -> None:
    """Reset contextvar to previous value."""
    _current_guard.reset(token)
