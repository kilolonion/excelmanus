"""基于身份的窗口定位器。"""

from __future__ import annotations

from .identity import ExplorerIdentity, SheetIdentity, WindowIdentity

WINDOW_KIND_CONFLICT = "WINDOW_KIND_CONFLICT"
WINDOW_IDENTITY_CONFLICT = "WINDOW_IDENTITY_CONFLICT"


class LocatorReject(ValueError):
    """显式身份/定位器拒绝。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class WindowLocator:
    """将稳定身份映射到窗口 id。"""

    def __init__(self) -> None:
        self._index: dict[WindowIdentity, str] = {}
        self._window_to_identity: dict[str, WindowIdentity] = {}

    def register(self, window_id: str, identity: WindowIdentity) -> None:
        existing = self._index.get(identity)
        if existing is not None and existing != window_id:
            raise LocatorReject(
                WINDOW_IDENTITY_CONFLICT,
                f"identity already bound: identity={identity!r} existing={existing} incoming={window_id}",
            )
        previous = self._window_to_identity.get(window_id)
        if previous is not None and previous != identity:
            self._index.pop(previous, None)
        self._index[identity] = window_id
        self._window_to_identity[window_id] = identity

    def find(self, identity: WindowIdentity, *, expected_kind: str | None = None) -> str | None:
        if expected_kind:
            actual_kind = _identity_kind(identity)
            if actual_kind != expected_kind:
                raise LocatorReject(
                    WINDOW_KIND_CONFLICT,
                    f"identity kind mismatch: expected={expected_kind} actual={actual_kind}",
                )
        return self._index.get(identity)


def _identity_kind(identity: WindowIdentity) -> str:
    if isinstance(identity, ExplorerIdentity):
        return "explorer"
    if isinstance(identity, SheetIdentity):
        return "sheet"
    return "unknown"
