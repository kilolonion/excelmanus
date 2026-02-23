"""MemoryStore SQLite CRUD 测试。"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from excelmanus.database import Database
from excelmanus.memory_models import MemoryCategory, MemoryEntry
from excelmanus.stores.memory_store import MemoryStore


@pytest.fixture()
def store(tmp_path: Path) -> MemoryStore:
    db = Database(str(tmp_path / "test.db"))
    return MemoryStore(db)


def _entry(
    content: str,
    category: MemoryCategory = MemoryCategory.GENERAL,
    source: str = "",
) -> MemoryEntry:
    return MemoryEntry(
        content=content,
        category=category,
        timestamp=datetime(2026, 1, 15, 10, 30),
        source=source,
    )


class TestMemoryStoreSave:
    def test_save_single_entry(self, store: MemoryStore) -> None:
        store.save_entries([_entry("hello world")])
        assert store.count() == 1

    def test_save_deduplicates_by_category_and_hash(self, store: MemoryStore) -> None:
        e = _entry("duplicate content")
        store.save_entries([e])
        store.save_entries([e])
        assert store.count() == 1

    def test_same_content_different_category_not_deduped(self, store: MemoryStore) -> None:
        store.save_entries([
            _entry("shared text", MemoryCategory.GENERAL),
            _entry("shared text", MemoryCategory.USER_PREF),
        ])
        assert store.count() == 2

    def test_save_multiple_entries(self, store: MemoryStore) -> None:
        entries = [
            _entry("entry 1", MemoryCategory.GENERAL),
            _entry("entry 2", MemoryCategory.FILE_PATTERN),
            _entry("entry 3", MemoryCategory.ERROR_SOLUTION),
        ]
        store.save_entries(entries)
        assert store.count() == 3


class TestMemoryStoreLoad:
    def test_load_core_returns_formatted_text(self, store: MemoryStore) -> None:
        store.save_entries([_entry("important info")])
        text = store.load_core(limit=10)
        assert "important info" in text

    def test_load_core_respects_limit(self, store: MemoryStore) -> None:
        for i in range(10):
            store.save_entries([_entry(f"entry {i}")])
        text = store.load_core(limit=3)
        # 应只包含最近 3 条
        assert "entry 9" in text
        assert "entry 0" not in text

    def test_load_by_category(self, store: MemoryStore) -> None:
        store.save_entries([
            _entry("general note", MemoryCategory.GENERAL),
            _entry("file pattern", MemoryCategory.FILE_PATTERN),
            _entry("another general", MemoryCategory.GENERAL),
        ])
        entries = store.load_by_category(MemoryCategory.GENERAL)
        assert len(entries) == 2
        assert all(e.category == MemoryCategory.GENERAL for e in entries)

    def test_load_by_category_empty(self, store: MemoryStore) -> None:
        entries = store.load_by_category(MemoryCategory.ERROR_SOLUTION)
        assert entries == []

    def test_load_all_entries(self, store: MemoryStore) -> None:
        store.save_entries([
            _entry("a", MemoryCategory.GENERAL),
            _entry("b", MemoryCategory.FILE_PATTERN),
        ])
        all_entries = store.load_all()
        assert len(all_entries) == 2


class TestMemoryStoreCapacity:
    def test_enforce_capacity_removes_oldest(self, store: MemoryStore) -> None:
        for i in range(10):
            store.save_entries([_entry(f"item {i}")])
        store.enforce_capacity(max_entries=5)
        assert store.count() == 5
        # 最新的应保留
        text = store.load_core(limit=10)
        assert "item 9" in text

    def test_enforce_capacity_noop_when_under_limit(self, store: MemoryStore) -> None:
        store.save_entries([_entry("solo")])
        store.enforce_capacity(max_entries=100)
        assert store.count() == 1


class TestMemoryStoreCount:
    def test_count_by_category(self, store: MemoryStore) -> None:
        store.save_entries([
            _entry("a", MemoryCategory.GENERAL),
            _entry("b", MemoryCategory.GENERAL),
            _entry("c", MemoryCategory.FILE_PATTERN),
        ])
        assert store.count_by_category(MemoryCategory.GENERAL) == 2
        assert store.count_by_category(MemoryCategory.FILE_PATTERN) == 1
        assert store.count_by_category(MemoryCategory.ERROR_SOLUTION) == 0
