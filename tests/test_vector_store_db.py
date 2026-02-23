"""VectorStoreDB SQLite BLOB 存储测试。"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from excelmanus.database import Database
from excelmanus.stores.vector_store_db import VectorStoreDB


@pytest.fixture()
def store(tmp_path: Path) -> VectorStoreDB:
    db = Database(str(tmp_path / "test.db"))
    return VectorStoreDB(db, dimensions=4)


def _rand_vec(dim: int = 4) -> np.ndarray:
    return np.random.randn(dim).astype(np.float32)


class TestVectorStoreDBAdd:
    def test_add_single(self, store: VectorStoreDB) -> None:
        added = store.add("hello", _rand_vec())
        assert added is True
        assert store.size == 1

    def test_add_deduplicates(self, store: VectorStoreDB) -> None:
        vec = _rand_vec()
        store.add("hello", vec)
        added = store.add("hello", vec)
        assert added is False
        assert store.size == 1

    def test_add_batch(self, store: VectorStoreDB) -> None:
        texts = ["a", "b", "c"]
        vecs = np.random.randn(3, 4).astype(np.float32)
        count = store.add_batch(texts, vecs)
        assert count == 3
        assert store.size == 3

    def test_add_batch_dedup(self, store: VectorStoreDB) -> None:
        vec = _rand_vec()
        store.add("a", vec)
        texts = ["a", "b"]
        vecs = np.random.randn(2, 4).astype(np.float32)
        count = store.add_batch(texts, vecs)
        assert count == 1  # "a" 已存在
        assert store.size == 2


class TestVectorStoreDBHas:
    def test_has_existing(self, store: VectorStoreDB) -> None:
        store.add("exists", _rand_vec())
        assert store.has("exists") is True

    def test_has_missing(self, store: VectorStoreDB) -> None:
        assert store.has("nope") is False


class TestVectorStoreDBLoad:
    def test_load_all_preserves_vector_precision(self, store: VectorStoreDB) -> None:
        vec = np.array([1.5, -2.3, 0.0, 4.7], dtype=np.float32)
        store.add("test", vec, metadata={"key": "val"})
        records = store.load_all()
        assert len(records) == 1
        r = records[0]
        assert r["text"] == "test"
        assert r["metadata"] == {"key": "val"}
        np.testing.assert_allclose(r["vector"], vec, atol=1e-7)

    def test_load_all_empty(self, store: VectorStoreDB) -> None:
        assert store.load_all() == []

    def test_get_texts(self, store: VectorStoreDB) -> None:
        store.add("alpha", _rand_vec())
        store.add("beta", _rand_vec())
        texts = store.get_texts()
        assert set(texts) == {"alpha", "beta"}

    def test_get_metadata(self, store: VectorStoreDB) -> None:
        store.add("x", _rand_vec(), metadata={"foo": "bar"})
        meta = store.get_metadata("x")
        assert meta == {"foo": "bar"}

    def test_get_metadata_missing(self, store: VectorStoreDB) -> None:
        assert store.get_metadata("missing") == {}


class TestVectorStoreDBMatrix:
    def test_build_matrix(self, store: VectorStoreDB) -> None:
        v1 = np.array([1, 0, 0, 0], dtype=np.float32)
        v2 = np.array([0, 1, 0, 0], dtype=np.float32)
        store.add("a", v1)
        store.add("b", v2)
        matrix = store.build_matrix()
        assert matrix.shape == (2, 4)
        np.testing.assert_allclose(matrix[0], v1)
        np.testing.assert_allclose(matrix[1], v2)

    def test_build_matrix_empty(self, store: VectorStoreDB) -> None:
        matrix = store.build_matrix()
        assert matrix.shape == (0, 4)
