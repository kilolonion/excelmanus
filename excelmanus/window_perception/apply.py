"""Single entrypoint for domain mutation."""

from __future__ import annotations

from .delta import ExplorerDelta, SheetReadDelta, WindowDelta
from .domain import BaseWindow, ExplorerWindow


class DeltaReject(ValueError):
    """Raised when a delta cannot be applied to a window."""


def apply_delta(window: BaseWindow, delta: WindowDelta) -> BaseWindow:
    """Apply a delta through kind-checked mutation pipeline."""

    if window.kind != delta.kind:
        raise DeltaReject(f"kind mismatch: window={window.kind} delta={delta.kind}")

    _append_audit(window, delta)

    if isinstance(window, ExplorerWindow) and isinstance(delta, ExplorerDelta) and delta.directory is not None:
        window.data.directory = delta.directory
    if isinstance(delta, SheetReadDelta):
        # Sheet read deltas are accepted through the entrypoint; task 3 only enforces contract.
        pass
    return window


def _append_audit(window: BaseWindow, delta: WindowDelta) -> None:
    audit = getattr(window, "audit_log", None)
    if audit is None:
        audit = []
        setattr(window, "audit_log", audit)
    audit.append(delta)
