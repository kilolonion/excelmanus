"""单元测试：PlaybookStore 持久化存储。"""

from __future__ import annotations

import numpy as np
import pytest

from excelmanus.playbook.store import PlaybookBullet, PlaybookStore


@pytest.fixture
def store(tmp_path):
    """创建临时 PlaybookStore。"""
    s = PlaybookStore(tmp_path / "playbook.db")
    yield s
    s.close()


def _make_bullet(
    *,
    category: str = "general",
    content: str = "测试策略",
    helpful: int = 0,
    harmful: int = 0,
    embedding: np.ndarray | None = None,
    bullet_id: str = "",
) -> PlaybookBullet:
    return PlaybookBullet(
        id=bullet_id,
        category=category,
        content=content,
        helpful_count=helpful,
        harmful_count=harmful,
        embedding=embedding,
        source_task_tags=["test"],
        origin_summary="测试摘要",
    )


class TestPlaybookStoreCRUD:
    """基础增删改查。"""

    def test_add_and_get(self, store: PlaybookStore) -> None:
        bullet = _make_bullet(content="跨表匹配前先 strip key 列")
        bullet_id = store.add(bullet)
        assert bullet_id
        retrieved = store.get(bullet_id)
        assert retrieved is not None
        assert retrieved.content == "跨表匹配前先 strip key 列"
        assert retrieved.category == "general"

    def test_add_auto_generates_id(self, store: PlaybookStore) -> None:
        bullet = _make_bullet()
        bullet_id = store.add(bullet)
        assert len(bullet_id) == 16

    def test_add_with_explicit_id(self, store: PlaybookStore) -> None:
        bullet = _make_bullet(bullet_id="my_custom_id")
        bullet_id = store.add(bullet)
        assert bullet_id == "my_custom_id"

    def test_get_nonexistent_returns_none(self, store: PlaybookStore) -> None:
        assert store.get("nonexistent") is None

    def test_delete(self, store: PlaybookStore) -> None:
        bullet = _make_bullet()
        bullet_id = store.add(bullet)
        assert store.delete(bullet_id) is True
        assert store.get(bullet_id) is None

    def test_delete_nonexistent_returns_false(self, store: PlaybookStore) -> None:
        assert store.delete("nonexistent") is False

    def test_list_all(self, store: PlaybookStore) -> None:
        for i in range(5):
            store.add(_make_bullet(content=f"策略{i}", helpful=i))
        bullets = store.list_all()
        assert len(bullets) == 5
        # 按 helpful_count 降序
        assert bullets[0].helpful_count >= bullets[-1].helpful_count

    def test_list_all_with_limit(self, store: PlaybookStore) -> None:
        for i in range(10):
            store.add(_make_bullet(content=f"策略{i}"))
        bullets = store.list_all(limit=3)
        assert len(bullets) == 3

    def test_count(self, store: PlaybookStore) -> None:
        assert store.count() == 0
        store.add(_make_bullet())
        assert store.count() == 1
        store.add(_make_bullet())
        assert store.count() == 2


class TestPlaybookStoreScoring:
    """评分功能。"""

    def test_mark_helpful(self, store: PlaybookStore) -> None:
        bullet = _make_bullet()
        bullet_id = store.add(bullet)
        store.mark_helpful(bullet_id)
        store.mark_helpful(bullet_id)
        retrieved = store.get(bullet_id)
        assert retrieved is not None
        assert retrieved.helpful_count == 2
        assert retrieved.last_used_at is not None

    def test_mark_harmful(self, store: PlaybookStore) -> None:
        bullet = _make_bullet()
        bullet_id = store.add(bullet)
        store.mark_harmful(bullet_id)
        retrieved = store.get(bullet_id)
        assert retrieved is not None
        assert retrieved.harmful_count == 1


class TestPlaybookStoreEmbedding:
    """Embedding 存储与检索。"""

    def test_embedding_roundtrip(self, store: PlaybookStore) -> None:
        emb = np.random.randn(64).astype(np.float32)
        bullet = _make_bullet(embedding=emb)
        bullet_id = store.add(bullet)
        retrieved = store.get(bullet_id)
        assert retrieved is not None
        assert retrieved.embedding is not None
        np.testing.assert_array_almost_equal(retrieved.embedding, emb)

    def test_none_embedding(self, store: PlaybookStore) -> None:
        bullet = _make_bullet()
        bullet_id = store.add(bullet)
        retrieved = store.get(bullet_id)
        assert retrieved is not None
        assert retrieved.embedding is None

    def test_search_by_embedding(self, store: PlaybookStore) -> None:
        """语义检索基本功能。"""
        # 创建 3 个 bullet，embedding 方向不同
        e1 = np.array([1.0, 0.0, 0.0], dtype=np.float32)
        e2 = np.array([0.0, 1.0, 0.0], dtype=np.float32)
        e3 = np.array([0.9, 0.1, 0.0], dtype=np.float32)  # 接近 e1

        store.add(_make_bullet(content="策略A", embedding=e1, bullet_id="a"))
        store.add(_make_bullet(content="策略B", embedding=e2, bullet_id="b"))
        store.add(_make_bullet(content="策略C", embedding=e3, bullet_id="c"))

        # 查询接近 e1 的
        query = np.array([1.0, 0.0, 0.0], dtype=np.float32)
        results = store.search(query, top_k=2, min_score=0.1)
        assert len(results) == 2
        # 策略A 和 策略C 应排在前面
        result_ids = {r.id for r in results}
        assert "a" in result_ids
        assert "c" in result_ids

    def test_search_with_category_filter(self, store: PlaybookStore) -> None:
        e1 = np.array([1.0, 0.0], dtype=np.float32)
        store.add(_make_bullet(content="A", category="formula", embedding=e1, bullet_id="a"))
        store.add(_make_bullet(content="B", category="formatting", embedding=e1, bullet_id="b"))

        results = store.search(e1, top_k=5, category="formula", min_score=0.1)
        assert len(results) == 1
        assert results[0].category == "formula"

    def test_search_empty_store(self, store: PlaybookStore) -> None:
        query = np.array([1.0, 0.0], dtype=np.float32)
        results = store.search(query, top_k=5)
        assert results == []

    def test_find_similar(self, store: PlaybookStore) -> None:
        e1 = np.array([1.0, 0.0, 0.0], dtype=np.float32)
        store.add(_make_bullet(content="原始", embedding=e1, bullet_id="orig"))

        # 非常相似的向量
        similar = np.array([0.99, 0.01, 0.0], dtype=np.float32)
        found = store.find_similar(similar, threshold=0.9)
        assert found is not None
        assert found.id == "orig"

    def test_find_similar_no_match(self, store: PlaybookStore) -> None:
        e1 = np.array([1.0, 0.0, 0.0], dtype=np.float32)
        store.add(_make_bullet(content="原始", embedding=e1))

        # 完全不同的方向
        different = np.array([0.0, 1.0, 0.0], dtype=np.float32)
        found = store.find_similar(different, threshold=0.9)
        assert found is None


class TestPlaybookStorePrune:
    """清理功能。"""

    def test_prune_harmful_bullets(self, store: PlaybookStore) -> None:
        store.add(_make_bullet(content="好的", helpful=5, harmful=1))
        store.add(_make_bullet(content="坏的", helpful=1, harmful=3))
        pruned = store.prune()
        assert pruned == 1
        assert store.count() == 1
        remaining = store.list_all()
        assert remaining[0].content == "好的"

    def test_prune_excess(self, store: PlaybookStore) -> None:
        for i in range(10):
            store.add(_make_bullet(content=f"策略{i}", helpful=i))
        pruned = store.prune(max_bullets=5)
        assert store.count() == 5
        # 应保留 helpful_count 最高的
        remaining = store.list_all()
        assert all(b.helpful_count >= 5 for b in remaining)

    def test_clear(self, store: PlaybookStore) -> None:
        for i in range(5):
            store.add(_make_bullet(content=f"策略{i}"))
        deleted = store.clear()
        assert deleted == 5
        assert store.count() == 0


class TestPlaybookStoreStats:
    """统计信息。"""

    def test_stats_empty(self, store: PlaybookStore) -> None:
        stats = store.stats()
        assert stats["total"] == 0

    def test_stats_with_data(self, store: PlaybookStore) -> None:
        store.add(_make_bullet(category="formula", helpful=10))
        store.add(_make_bullet(category="formula", helpful=20))
        store.add(_make_bullet(category="formatting", helpful=5))
        stats = store.stats()
        assert stats["total"] == 3
        assert stats["categories"]["formula"] == 2
        assert stats["categories"]["formatting"] == 1
        assert stats["avg_helpful"] == pytest.approx(11.67, abs=0.01)


class TestPlaybookStoreUpdateContent:
    """update_content 方法。"""

    def test_update_content(self, store: PlaybookStore) -> None:
        bullet = _make_bullet(content="原始内容")
        bullet_id = store.add(bullet)
        store.update_content(bullet_id, "更新后内容")
        retrieved = store.get(bullet_id)
        assert retrieved is not None
        assert retrieved.content == "更新后内容"

    def test_update_content_truncates(self, store: PlaybookStore) -> None:
        bullet = _make_bullet(content="短")
        bullet_id = store.add(bullet)
        store.update_content(bullet_id, "x" * 1000)
        retrieved = store.get(bullet_id)
        assert retrieved is not None
        assert len(retrieved.content) == 500  # _MAX_CONTENT_LENGTH


class TestPlaybookBulletSerialization:
    """PlaybookBullet 序列化。"""

    def test_to_dict(self) -> None:
        b = _make_bullet(content="测试", helpful=5)
        b.id = "test_id"
        b.created_at = "2026-01-01T00:00:00"
        d = b.to_dict()
        assert d["id"] == "test_id"
        assert d["content"] == "测试"
        assert d["helpful_count"] == 5
        assert "embedding" not in d  # embedding 不包含在 to_dict 中

    def test_source_task_tags_persisted(self, store: PlaybookStore) -> None:
        bullet = _make_bullet()
        bullet.source_task_tags = ["cross_sheet", "formula"]
        bullet_id = store.add(bullet)
        retrieved = store.get(bullet_id)
        assert retrieved is not None
        assert retrieved.source_task_tags == ["cross_sheet", "formula"]
