"""RefCache 测试。"""
from __future__ import annotations

import time

import pytest

from excelmanus.reference_graph.cache import RefCache
from excelmanus.reference_graph.models import WorkbookRefIndex


def _make_index(path: str = "test.xlsx") -> WorkbookRefIndex:
    return WorkbookRefIndex(
        file_path=path, sheets={}, cross_sheet_edges=[],
        external_refs=[], named_ranges={}, built_at=time.time(),
    )


class TestRefCache:
    def test_put_and_get(self) -> None:
        cache = RefCache()
        idx = _make_index("a.xlsx")
        cache.put_tier1("a.xlsx", idx)
        assert cache.get_tier1("a.xlsx") is idx

    def test_miss_returns_none(self) -> None:
        cache = RefCache()
        assert cache.get_tier1("missing.xlsx") is None

    def test_invalidate(self) -> None:
        cache = RefCache()
        cache.put_tier1("a.xlsx", _make_index())
        cache.invalidate("a.xlsx")
        assert cache.get_tier1("a.xlsx") is None

    def test_invalidate_all(self) -> None:
        cache = RefCache()
        cache.put_tier1("a.xlsx", _make_index("a.xlsx"))
        cache.put_tier1("b.xlsx", _make_index("b.xlsx"))
        cache.invalidate_all()
        assert cache.get_tier1("a.xlsx") is None
        assert cache.get_tier1("b.xlsx") is None

    def test_tier2_cache(self) -> None:
        cache = RefCache()
        from excelmanus.reference_graph.models import CellNode

        node = CellNode(sheet="S1", address="A1")
        cache.put_tier2("f.xlsx", "S1", "A1", node)
        assert cache.get_tier2("f.xlsx", "S1", "A1") is node

    def test_tier2_invalidate_by_file(self) -> None:
        cache = RefCache()
        from excelmanus.reference_graph.models import CellNode

        node = CellNode(sheet="S1", address="A1")
        cache.put_tier2("f.xlsx", "S1", "A1", node)
        cache.invalidate("f.xlsx")
        assert cache.get_tier2("f.xlsx", "S1", "A1") is None


class TestSessionCacheIsolation:
    """contextvar 多会话隔离测试。"""

    def test_different_contexts_get_different_caches(self) -> None:
        import contextvars
        from excelmanus.reference_graph.cache import (
            get_session_cache,
            set_session_cache,
            reset_session_cache,
        )

        cache_a = RefCache()
        cache_b = RefCache()
        cache_a.put_tier1("a.xlsx", _make_index("a.xlsx"))

        token_a = set_session_cache(cache_a)
        assert get_session_cache() is cache_a
        assert get_session_cache().get_tier1("a.xlsx") is not None

        reset_session_cache(token_a)

        token_b = set_session_cache(cache_b)
        assert get_session_cache() is cache_b
        assert get_session_cache().get_tier1("a.xlsx") is None

        reset_session_cache(token_b)

    def test_fallback_to_module_singleton(self) -> None:
        from excelmanus.reference_graph.cache import get_session_cache

        c1 = get_session_cache()
        c2 = get_session_cache()
        assert c1 is c2

    def test_set_and_reset(self) -> None:
        from excelmanus.reference_graph.cache import (
            get_session_cache,
            set_session_cache,
            reset_session_cache,
        )

        original = get_session_cache()
        custom = RefCache()
        token = set_session_cache(custom)
        assert get_session_cache() is custom
        reset_session_cache(token)
        assert get_session_cache() is original
