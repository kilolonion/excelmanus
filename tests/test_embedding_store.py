"""VectorStore 单元测试。"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import numpy as np
import pytest

from excelmanus.embedding.store import VectorStore


@pytest.fixture
def tmp_store_dir(tmp_path: Path) -> Path:
    """临时存储目录。"""
    return tmp_path / "vectors"


class TestVectorStore:
    """VectorStore 基础功能测试。"""

    def test_add_and_size(self, tmp_store_dir: Path):
        store = VectorStore(tmp_store_dir, dimensions=4)
        assert store.size == 0

        vec = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)
        added = store.add("hello", vec)
        assert added is True
        assert store.size == 1

    def test_dedup_by_content(self, tmp_store_dir: Path):
        store = VectorStore(tmp_store_dir, dimensions=4)
        vec = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)
        store.add("hello", vec)
        added = store.add("hello", vec)
        assert added is False
        assert store.size == 1

    def test_has(self, tmp_store_dir: Path):
        store = VectorStore(tmp_store_dir, dimensions=4)
        assert store.has("hello") is False
        store.add("hello", np.zeros(4, dtype=np.float32))
        assert store.has("hello") is True

    def test_matrix_shape(self, tmp_store_dir: Path):
        store = VectorStore(tmp_store_dir, dimensions=3)
        store.add("a", np.array([1, 0, 0], dtype=np.float32))
        store.add("b", np.array([0, 1, 0], dtype=np.float32))
        assert store.matrix.shape == (2, 3)

    def test_empty_matrix(self, tmp_store_dir: Path):
        store = VectorStore(tmp_store_dir, dimensions=3)
        assert store.matrix.shape == (0, 3)

    def test_get_texts(self, tmp_store_dir: Path):
        store = VectorStore(tmp_store_dir, dimensions=2)
        store.add("alpha", np.zeros(2, dtype=np.float32))
        store.add("beta", np.zeros(2, dtype=np.float32))
        assert store.get_texts() == ["alpha", "beta"]

    def test_get_record(self, tmp_store_dir: Path):
        store = VectorStore(tmp_store_dir, dimensions=2)
        store.add("text1", np.array([1.0, 2.0], dtype=np.float32), {"key": "val"})
        record = store.get_record(0)
        assert record is not None
        assert record.text == "text1"
        assert record.metadata == {"key": "val"}
        assert record.vector[0] == pytest.approx(1.0)

    def test_get_record_out_of_range(self, tmp_store_dir: Path):
        store = VectorStore(tmp_store_dir, dimensions=2)
        assert store.get_record(0) is None
        assert store.get_record(-1) is None

    def test_add_batch(self, tmp_store_dir: Path):
        store = VectorStore(tmp_store_dir, dimensions=2)
        texts = ["a", "b", "c"]
        vecs = np.array([[1, 0], [0, 1], [1, 1]], dtype=np.float32)
        added = store.add_batch(texts, vecs)
        assert added == 3
        assert store.size == 3

    def test_add_batch_dedup(self, tmp_store_dir: Path):
        store = VectorStore(tmp_store_dir, dimensions=2)
        store.add("a", np.zeros(2, dtype=np.float32))
        texts = ["a", "b"]
        vecs = np.array([[1, 0], [0, 1]], dtype=np.float32)
        added = store.add_batch(texts, vecs)
        assert added == 1  # "a" 已存在
        assert store.size == 2

    def test_clear(self, tmp_store_dir: Path):
        store = VectorStore(tmp_store_dir, dimensions=2)
        store.add("a", np.zeros(2, dtype=np.float32))
        store.clear()
        assert store.size == 0
        assert store.matrix.shape == (0, 2)

    def test_metadata(self, tmp_store_dir: Path):
        store = VectorStore(tmp_store_dir, dimensions=2)
        store.add("a", np.zeros(2, dtype=np.float32), {"cat": "test"})
        assert store.get_metadata(0) == {"cat": "test"}
        assert store.get_metadata(99) == {}


class TestVectorStorePersistence:
    """VectorStore 持久化与加载测试。"""

    def test_save_and_load(self, tmp_store_dir: Path):
        # 写入
        store1 = VectorStore(tmp_store_dir, dimensions=3)
        store1.add("hello", np.array([1, 0, 0], dtype=np.float32), {"k": "v"})
        store1.add("world", np.array([0, 1, 0], dtype=np.float32))
        store1.save()

        # 重新加载
        store2 = VectorStore(tmp_store_dir, dimensions=3)
        assert store2.size == 2
        assert store2.get_texts() == ["hello", "world"]
        assert store2.get_metadata(0) == {"k": "v"}
        # 验证向量恢复
        assert store2.matrix[0, 0] == pytest.approx(1.0)
        assert store2.matrix[1, 1] == pytest.approx(1.0)

    def test_save_idempotent(self, tmp_store_dir: Path):
        store = VectorStore(tmp_store_dir, dimensions=2)
        store.add("a", np.zeros(2, dtype=np.float32))
        store.save()
        store.save()  # 二次保存不应出错
        assert store.size == 1

    def test_load_empty_dir(self, tmp_store_dir: Path):
        store = VectorStore(tmp_store_dir, dimensions=2)
        assert store.size == 0

    def test_corrupted_jsonl_partial_load(self, tmp_store_dir: Path):
        """JSONL 部分损坏时跳过损坏行。"""
        tmp_store_dir.mkdir(parents=True, exist_ok=True)
        jsonl = tmp_store_dir / "vectors.jsonl"
        lines = [
            json.dumps({"content_hash": "abc123", "text": "good", "metadata": {}}),
            "INVALID JSON LINE",
            json.dumps({"content_hash": "def456", "text": "also good", "metadata": {}}),
        ]
        jsonl.write_text("\n".join(lines))

        store = VectorStore(tmp_store_dir, dimensions=2)
        assert store.size == 2  # 跳过损坏行，仍加载有效行


class TestVectorStoreHashConsistency:
    """content_hash 一致性测试。"""

    def test_whitespace_normalization(self, tmp_store_dir: Path):
        """空白归一后相同的文本被视为同一条。"""
        store = VectorStore(tmp_store_dir, dimensions=2)
        store.add("hello  world", np.zeros(2, dtype=np.float32))
        added = store.add("hello world", np.zeros(2, dtype=np.float32))
        assert added is False  # 空白归一后相同
        assert store.size == 1
