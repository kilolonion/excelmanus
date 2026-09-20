"""Bounded caches for immutable read products, with concurrent miss coalescing."""

from collections import OrderedDict
from concurrent.futures import Future
from threading import Lock
from typing import Callable, Generic, Hashable, TypeVar

T = TypeVar("T")


class ReadCache(Generic[T]):
    def __init__(self, *, max_bytes: int, max_entries: int = 64) -> None:
        self.max_bytes = max_bytes
        self.max_entries = max_entries
        self._entries: OrderedDict[Hashable, tuple[T, int]] = OrderedDict()
        self._pending: dict[Hashable, Future[T]] = {}
        self._bytes = 0
        self._lock = Lock()

    def get_or_create(self, key: Hashable, build: Callable[[], tuple[T, int]]) -> T:
        with self._lock:
            cached = self._entries.get(key)
            if cached is not None:
                self._entries.move_to_end(key)
                return cached[0]
            pending = self._pending.get(key)
            owner = pending is None
            if pending is None:
                pending = Future()
                self._pending[key] = pending
        if not owner:
            return pending.result()
        try:
            value, size = build()
            with self._lock:
                if size <= self.max_bytes:
                    self._entries[key] = (value, size)
                    self._bytes += size
                    while self._bytes > self.max_bytes or len(self._entries) > self.max_entries:
                        _, (_, removed_size) = self._entries.popitem(last=False)
                        self._bytes -= removed_size
            pending.set_result(value)
            return value
        except BaseException as exc:
            pending.set_exception(exc)
            raise
        finally:
            with self._lock:
                self._pending.pop(key, None)
