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
