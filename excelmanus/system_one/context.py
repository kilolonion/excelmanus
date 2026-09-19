"""Small host/session predicates shared by Jev integration points."""

from __future__ import annotations

from typing import Any


def is_child_session(engine: Any) -> bool:
    """Return whether Jev must stay inert for a child/subagent engine."""
    if getattr(engine, "_subagent_config", None) is not None:
        return True
    return getattr(engine, "_is_host_session", True) is False


def is_host_session(engine: Any) -> bool:
    return engine is not None and not is_child_session(engine)
