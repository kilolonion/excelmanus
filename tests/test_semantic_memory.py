"""SemanticMemory 单元测试。"""

from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest

from excelmanus.embedding.client import EmbeddingClient
from excelmanus.embedding.semantic_memory import SemanticMemory
from excelmanus.memory_models import MemoryCategory, MemoryEntry
from excelmanus.persistent_memory import PersistentMemory


def _make_mock_embedding_client(dimensions: int = 4) -> EmbeddingClient:
    """构建 mock EmbeddingClient，返回基于文本哈希的伪向量。"""
    client = MagicMock()

    async def _create(**kwargs):
        texts = kwargs.get("input", [])
        data = []
        for text in texts:
            # 基于文本内容生成确定性伪向量
            h = hash(text) % 10000
            vec = [0.0] * dimensions
            vec[h % dimensions] = 1.0
            data.append(SimpleNamespace(embedding=vec))
        return SimpleNamespace(data=data)

    client.embeddings = MagicMock()
    client.embeddings.create = _create
    ec = EmbeddingClient(client=client, model="test", dimensions=dimensions)
    return ec


@pytest.fixture
def memory_dir(tmp_path: Path) -> Path:
    return tmp_path / "memory"


@pytest.fixture
def persistent_memory(memory_dir: Path) -> PersistentMemory:
    return PersistentMemory(str(memory_dir), auto_load_lines=200)


@pytest.fixture
def embedding_client() -> EmbeddingClient:
    return _make_mock_embedding_client(dimensions=8)


class TestSemanticMemorySync:
    """索引同步测试。"""

    @pytest.mark.asyncio
    async def test_sync_empty_memory(self, persistent_memory, embedding_client):
        sm = SemanticMemory(persistent_memory, embedding_client)
        added = await sm.sync_index()
        assert added == 0

    @pytest.mark.asyncio
    async def test_sync_with_entries(self, persistent_memory, embedding_client):
        # 先写入一些记忆
        entries = [
            MemoryEntry(content="销售数据有5列", category=MemoryCategory.FILE_PATTERN, timestamp=datetime.now()),
            MemoryEntry(content="用户偏好蓝色图表", category=MemoryCategory.USER_PREF, timestamp=datetime.now()),
        ]
        persistent_memory.save_entries(entries)

        sm = SemanticMemory(persistent_memory, embedding_client)
        added = await sm.sync_index()
        assert added == 2
        assert sm.store.size == 2

    @pytest.mark.asyncio
    async def test_sync_idempotent(self, persistent_memory, embedding_client):
        entries = [
            MemoryEntry(content="测试内容", category=MemoryCategory.GENERAL, timestamp=datetime.now()),
        ]
        persistent_memory.save_entries(entries)

        sm = SemanticMemory(persistent_memory, embedding_client)
        added1 = await sm.sync_index()
        added2 = await sm.sync_index()
        assert added1 == 1
        assert added2 == 0  # 已同步，不再新增


class TestSemanticMemorySearch:
    """语义检索测试。"""

    @pytest.mark.asyncio
    async def test_search_returns_text(self, persistent_memory, embedding_client):
        entries = [
            MemoryEntry(content="销售报表结构：日期、产品、数量", category=MemoryCategory.FILE_PATTERN, timestamp=datetime.now()),
            MemoryEntry(content="错误：openpyxl 无法读取加密文件", category=MemoryCategory.ERROR_SOLUTION, timestamp=datetime.now()),
        ]
        persistent_memory.save_entries(entries)

        sm = SemanticMemory(persistent_memory, embedding_client, top_k=5, threshold=0.0)
        result = await sm.search("销售数据")
        assert isinstance(result, str)
        assert len(result) > 0

    @pytest.mark.asyncio
    async def test_search_empty_memory_fallback(self, persistent_memory, embedding_client):
        sm = SemanticMemory(persistent_memory, embedding_client)
        result = await sm.search("任意查询")
        # 空记忆时降级到 load_core()，返回空字符串
        assert result == ""

    @pytest.mark.asyncio
    async def test_search_includes_recent_fallback(self, persistent_memory, embedding_client):
        entries = [
            MemoryEntry(content=f"记忆条目{i}", category=MemoryCategory.GENERAL, timestamp=datetime.now())
            for i in range(10)
        ]
        persistent_memory.save_entries(entries)

        sm = SemanticMemory(
            persistent_memory, embedding_client,
            top_k=3, threshold=0.0, fallback_recent=2,
        )
        result = await sm.search("查询")
        assert "最近记忆" in result or "语义相关记忆" in result


class TestSemanticMemoryIndexEntries:
    """增量索引测试。"""

    @pytest.mark.asyncio
    async def test_index_new_entries(self, persistent_memory, embedding_client):
        sm = SemanticMemory(persistent_memory, embedding_client)
        await sm.sync_index()

        new_entries = [
            MemoryEntry(content="新增记忆A", category=MemoryCategory.GENERAL, timestamp=datetime.now()),
            MemoryEntry(content="新增记忆B", category=MemoryCategory.FILE_PATTERN, timestamp=datetime.now()),
        ]
        added = await sm.index_entries(new_entries)
        assert added == 2
        assert sm.store.size == 2

    @pytest.mark.asyncio
    async def test_index_empty_entries(self, persistent_memory, embedding_client):
        sm = SemanticMemory(persistent_memory, embedding_client)
        added = await sm.index_entries([])
        assert added == 0


class TestSemanticMemorySearchEntries:
    """search_entries 高级接口测试。"""

    @pytest.mark.asyncio
    async def test_search_entries_returns_scored_tuples(self, persistent_memory, embedding_client):
        entries = [
            MemoryEntry(content="财务报表分析", category=MemoryCategory.FILE_PATTERN, timestamp=datetime.now()),
            MemoryEntry(content="图表样式偏好", category=MemoryCategory.USER_PREF, timestamp=datetime.now()),
        ]
        persistent_memory.save_entries(entries)

        sm = SemanticMemory(persistent_memory, embedding_client, top_k=5, threshold=0.0)
        results = await sm.search_entries("财务数据")
        assert isinstance(results, list)
        for entry, score in results:
            assert isinstance(entry, MemoryEntry)
            assert isinstance(score, float)
