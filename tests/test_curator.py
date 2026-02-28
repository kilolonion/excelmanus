"""单元测试：PlaybookCurator 策略整合器。"""

from __future__ import annotations

import asyncio

import numpy as np
import pytest

from excelmanus.playbook.curator import CuratorReport, PlaybookCurator
from excelmanus.playbook.reflector import PlaybookDelta
from excelmanus.playbook.store import PlaybookBullet, PlaybookStore


@pytest.fixture
def store(tmp_path):
    s = PlaybookStore(tmp_path / "playbook.db")
    yield s
    s.close()


def _run(coro):
    """同步运行 async 函数。"""
    return asyncio.run(coro)


class TestCuratorIntegrateNewItems:
    """新增条目（无去重场景）。"""

    def test_add_single_delta(self, store: PlaybookStore) -> None:
        curator = PlaybookCurator(store, embedding_client=None)
        deltas = [PlaybookDelta(
            category="formula",
            content="VLOOKUP 前先检查 key 列数据类型",
            confidence=0.9,
            source_summary="公式任务",
        )]
        report = _run(curator.integrate(deltas, session_id="s1"))
        assert report.new_count == 1
        assert report.merged_count == 0
        assert report.total_bullets == 1

        bullets = store.list_all()
        assert len(bullets) == 1
        assert bullets[0].category == "formula"
        assert bullets[0].origin_session_id == "s1"

    def test_add_multiple_deltas(self, store: PlaybookStore) -> None:
        curator = PlaybookCurator(store, embedding_client=None)
        deltas = [
            PlaybookDelta("cross_sheet", "跨表匹配前 strip key 列", 0.8, "汇总"),
            PlaybookDelta("error_recovery", "BadZipFile 时从备份恢复", 0.7, "恢复"),
        ]
        report = _run(curator.integrate(deltas))
        assert report.new_count == 2
        assert report.total_bullets == 2

    def test_empty_deltas(self, store: PlaybookStore) -> None:
        curator = PlaybookCurator(store, embedding_client=None)
        report = _run(curator.integrate([]))
        assert report.new_count == 0
        assert report.total_bullets == 0


class TestCuratorMerge:
    """语义去重合并（需要 embedding）。"""

    def test_merge_similar_bullet(self, store: PlaybookStore) -> None:
        """相似度 > 阈值时应合并到已有条目。"""
        # 先手动添加一个带 embedding 的 bullet
        emb = np.array([1.0, 0.0, 0.0], dtype=np.float32)
        store.add(PlaybookBullet(
            id="existing",
            category="formula",
            content="VLOOKUP 前先检查 key 列的数据类型是否一致",
            embedding=emb,
            helpful_count=3,
        ))

        # 创建一个 mock embedding client
        class MockEmbeddingClient:
            async def embed(self, texts):
                # 返回非常接近 [1,0,0] 的向量
                return np.array([[0.99, 0.01, 0.0]], dtype=np.float32)

        curator = PlaybookCurator(store, embedding_client=MockEmbeddingClient())
        deltas = [PlaybookDelta(
            category="formula",
            content="VLOOKUP 前检查 key 列类型",  # 更精炼
            confidence=0.9,
            source_summary="",
        )]
        report = _run(curator.integrate(deltas))
        assert report.merged_count == 1
        assert report.new_count == 0

        # helpful_count 应 +1
        existing = store.get("existing")
        assert existing is not None
        assert existing.helpful_count == 4
        # content 应更新为更精炼的版本
        assert existing.content == "VLOOKUP 前检查 key 列类型"

    def test_no_merge_dissimilar(self, store: PlaybookStore) -> None:
        """相似度低时应新增。"""
        emb = np.array([1.0, 0.0, 0.0], dtype=np.float32)
        store.add(PlaybookBullet(
            id="existing",
            category="formula",
            content="VLOOKUP 前检查类型",
            embedding=emb,
        ))

        class MockEmbeddingClient:
            async def embed(self, texts):
                # 返回完全不同方向的向量
                return np.array([[0.0, 1.0, 0.0]], dtype=np.float32)

        curator = PlaybookCurator(store, embedding_client=MockEmbeddingClient())
        deltas = [PlaybookDelta("formatting", "边框用 thin", 0.8, "")]
        report = _run(curator.integrate(deltas))
        assert report.new_count == 1
        assert report.merged_count == 0
        assert report.total_bullets == 2


class TestCuratorPrune:
    """超限淘汰。"""

    def test_prune_on_overflow(self, store: PlaybookStore) -> None:
        # 预填充 5 个
        for i in range(5):
            store.add(PlaybookBullet(
                id=f"b{i}",
                category="general",
                content=f"策略{i}",
                helpful_count=i,
            ))

        curator = PlaybookCurator(store, embedding_client=None, max_bullets=4)
        deltas = [PlaybookDelta("general", "新策略", 0.9, "")]
        report = _run(curator.integrate(deltas))
        # 6 个总共，max=4，应淘汰 2 个
        assert report.total_bullets <= 4
        assert report.pruned_count >= 2


class TestCuratorReport:
    """CuratorReport 基本功能。"""

    def test_report_fields(self) -> None:
        r = CuratorReport(new_count=2, merged_count=1, pruned_count=0, total_bullets=3)
        assert r.new_count == 2
        assert r.errors is None
