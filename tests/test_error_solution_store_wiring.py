"""ErrorSolutionStore 单元测试。

验证 ErrorSolutionStore 自身的核心接口：
1. record_error + record_solution 配对入库
2. search / get_guidance_text 语义检索
3. 语义去重（相似错误不重复入库）
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest


def _make_store(*, top_k: int = 3, threshold: float = 0.35):
    """构建一个使用 mock embedding client 的 ErrorSolutionStore。"""
    import tempfile

    from excelmanus.embedding.error_solution_store import ErrorSolutionStore

    client = AsyncMock()
    client.dimensions = 8
    # 默认返回随机但稳定的向量
    _call_count = 0

    async def _embed_single(text: str):
        nonlocal _call_count
        _call_count += 1
        rng = np.random.RandomState(hash(text) % (2**31))
        vec = rng.randn(8).astype(np.float32)
        return vec / np.linalg.norm(vec)

    client.embed_single = AsyncMock(side_effect=_embed_single)

    with tempfile.TemporaryDirectory() as tmpdir:
        store = ErrorSolutionStore(
            embedding_client=client,
            store_dir=tmpdir,
            top_k=top_k,
            threshold=threshold,
        )
        yield store


@pytest.fixture
def store():
    yield from _make_store()


@pytest.mark.asyncio
async def test_record_error_and_solution(store):
    """record_error + record_solution 应成功配对入库。"""
    await store.record_error("call_1", "write_cell", "Permission denied")
    added = await store.record_solution("call_1", "write_cell", "chmod 修复权限后重试成功", success=True)
    assert added is True
    assert store.size == 1


@pytest.mark.asyncio
async def test_record_solution_without_error(store):
    """无前置 record_error 时，record_solution 应返回 False。"""
    added = await store.record_solution("call_x", "read_cell", "some result", success=True)
    assert added is False
    assert store.size == 0


@pytest.mark.asyncio
async def test_get_guidance_text_empty_store(store):
    """空 store 时 get_guidance_text 应返回空字符串。"""
    result = await store.get_guidance_text("some error")
    assert result == ""


@pytest.mark.asyncio
async def test_get_guidance_text_with_data():
    """有数据时 get_guidance_text 应返回非空的格式化文本。"""
    import tempfile
    from excelmanus.embedding.error_solution_store import ErrorSolutionStore

    client = AsyncMock()
    client.dimensions = 4

    # 让所有向量都非常接近（高相似度），确保能搜索到
    _fixed_vec = np.array([0.5, 0.5, 0.5, 0.5], dtype=np.float32)
    _fixed_vec = _fixed_vec / np.linalg.norm(_fixed_vec)
    client.embed_single = AsyncMock(return_value=_fixed_vec)

    with tempfile.TemporaryDirectory() as tmpdir:
        store = ErrorSolutionStore(
            embedding_client=client,
            store_dir=tmpdir,
            top_k=3,
            threshold=0.01,  # 极低阈值确保能匹配
        )

        await store.record_error("c1", "write_cell", "Permission denied")
        await store.record_solution("c1", "write_cell", "chmod 修复", success=True)

        result = await store.get_guidance_text("Permission denied on file")
        assert "历史解决方案" in result
        assert "chmod 修复" in result


@pytest.mark.asyncio
async def test_dedup_similar_error_not_added():
    """语义去重：cosine > 0.92 的相似错误不应重复入库，而是更新已有方案。"""
    import tempfile
    from excelmanus.embedding.error_solution_store import ErrorSolutionStore

    client = AsyncMock()
    client.dimensions = 4

    # 所有文本返回几乎相同的向量（cosine ≈ 1.0），触发去重
    _fixed_vec = np.array([0.5, 0.5, 0.5, 0.5], dtype=np.float32)
    _fixed_vec = _fixed_vec / np.linalg.norm(_fixed_vec)
    client.embed_single = AsyncMock(return_value=_fixed_vec)

    with tempfile.TemporaryDirectory() as tmpdir:
        store = ErrorSolutionStore(
            embedding_client=client,
            store_dir=tmpdir,
            top_k=3,
            threshold=0.01,
        )

        # 第一条：正常入库
        await store.record_error("c1", "write_cell", "Permission denied: /path/a.xlsx")
        added1 = await store.record_solution("c1", "write_cell", "chmod 修复权限", success=True)
        assert added1 is True
        assert store.size == 1

        # 第二条：几乎相同的错误，应被去重（cosine ≈ 1.0 > 0.92）
        await store.record_error("c2", "write_cell", "Permission denied: /path/b.xlsx")
        added2 = await store.record_solution("c2", "write_cell", "sudo 修复权限", success=True)
        assert added2 is False  # 去重，未新增
        assert store.size == 1  # 仍然只有 1 条


@pytest.mark.asyncio
async def test_different_errors_both_added():
    """语义不相似的错误应各自入库。"""
    import tempfile
    from excelmanus.embedding.error_solution_store import ErrorSolutionStore

    client = AsyncMock()
    client.dimensions = 4

    _call_count = 0

    async def _embed_orthogonal(text: str):
        """每次调用返回不同方向的向量（cosine ≈ 0），确保不触发去重。"""
        nonlocal _call_count
        _call_count += 1
        vec = np.zeros(4, dtype=np.float32)
        vec[_call_count % 4] = 1.0
        return vec

    client.embed_single = AsyncMock(side_effect=_embed_orthogonal)

    with tempfile.TemporaryDirectory() as tmpdir:
        store = ErrorSolutionStore(
            embedding_client=client,
            store_dir=tmpdir,
            top_k=3,
            threshold=0.01,
        )

        await store.record_error("c1", "write_cell", "Permission denied")
        added1 = await store.record_solution("c1", "write_cell", "chmod 修复", success=True)
        assert added1 is True

        await store.record_error("c2", "read_excel", "FileNotFoundError")
        added2 = await store.record_solution("c2", "read_excel", "检查路径", success=True)
        assert added2 is True
        assert store.size == 2
