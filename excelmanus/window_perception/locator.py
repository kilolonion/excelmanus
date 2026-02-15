"""Identity-based window locator."""

from __future__ import annotations

from .identity import WindowIdentity


class WindowLocator:
    """Maps stable identities to window ids."""

    def __init__(self) -> None:
        self._index: dict[WindowIdentity, str] = {}

    def register(self, window_id: str, identity: WindowIdentity) -> None:
        self._index[identity] = window_id

    def find(self, identity: WindowIdentity) -> str | None:
        return self._index.get(identity)
