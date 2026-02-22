"""EmbeddingClient 单元测试。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest

from excelmanus.config import (
    DEFAULT_EMBEDDING_DIMENSIONS,
    DEFAULT_EMBEDDING_MODEL,
)
from excelmanus.embedding.client import EmbeddingClient


def _make_mock_client(vectors: list[list[float]] | None = None):
    """构建 mock openai.AsyncOpenAI，返回指定向量。"""
    client = MagicMock()
    dims = len(vectors[0]) if vectors else 4

    async def _create(**kwargs):
        texts = kwargs.get("input", [])
        if vectors is not None:
            data = [
                SimpleNamespace(embedding=vectors[i % len(vectors)])
                for i in range(len(texts))
            ]
        else:
            data = [
                SimpleNamespace(embedding=[0.1] * dims)
                for _ in texts
            ]
        return SimpleNamespace(data=data)

    client.embeddings = MagicMock()
    client.embeddings.create = _create
    return client


class TestEmbeddingClient:
    """EmbeddingClient 基础功能测试。"""

    @pytest.mark.asyncio
    async def test_defaults_follow_config_constants(self):
        mock = _make_mock_client()
        ec = EmbeddingClient(client=mock)
        assert ec.model == DEFAULT_EMBEDDING_MODEL
        assert ec.dimensions == DEFAULT_EMBEDDING_DIMENSIONS

    @pytest.mark.asyncio
    async def test_embed_single(self):
        mock = _make_mock_client([[1.0, 0.0, 0.0, 0.0]])
        ec = EmbeddingClient(client=mock, model="test", dimensions=4)
        result = await ec.embed_single("hello")
        assert result.shape == (4,)
        assert result[0] == pytest.approx(1.0)

    @pytest.mark.asyncio
    async def test_embed_batch(self):
        vecs = [[1.0, 0.0], [0.0, 1.0]]
        mock = _make_mock_client(vecs)
        ec = EmbeddingClient(client=mock, model="test", dimensions=2)
        result = await ec.embed(["a", "b"])
        assert result.shape == (2, 2)
        assert result[0, 0] == pytest.approx(1.0)
        assert result[1, 1] == pytest.approx(1.0)

    @pytest.mark.asyncio
    async def test_embed_empty_input(self):
        mock = _make_mock_client()
        ec = EmbeddingClient(client=mock, model="test", dimensions=4)
        result = await ec.embed([])
        assert result.shape == (0, 4)

    @pytest.mark.asyncio
    async def test_embed_filters_empty_strings(self):
        vecs = [[1.0, 0.0]]
        mock = _make_mock_client(vecs)
        ec = EmbeddingClient(client=mock, model="test", dimensions=2)
        result = await ec.embed(["hello", "", "  "])
        assert result.shape == (3, 2)
        # 空字符串对应零向量
        assert result[1, 0] == pytest.approx(0.0)
        assert result[2, 0] == pytest.approx(0.0)
        # 非空字符串有值
        assert result[0, 0] == pytest.approx(1.0)

    @pytest.mark.asyncio
    async def test_properties(self):
        mock = _make_mock_client()
        ec = EmbeddingClient(client=mock, model="my-model", dimensions=8)
        assert ec.model == "my-model"
        assert ec.dimensions == 8

    @pytest.mark.asyncio
    async def test_embed_api_error_raises(self):
        client = MagicMock()

        async def _fail(**kwargs):
            raise RuntimeError("API error")

        client.embeddings = MagicMock()
        client.embeddings.create = _fail
        ec = EmbeddingClient(client=client, model="test", dimensions=4)
        with pytest.raises(RuntimeError, match="API error"):
            await ec.embed(["hello"])
