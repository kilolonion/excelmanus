"""Embedding 客户端：封装 OpenAI Embedding API 调用。"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import numpy as np

from excelmanus.config import (
    DEFAULT_EMBEDDING_DIMENSIONS,
    DEFAULT_EMBEDDING_MODEL,
)

if TYPE_CHECKING:
    import openai

logger = logging.getLogger(__name__)

# 单次 API 调用最大输入条数（OpenAI 限制 2048）
_MAX_BATCH_SIZE = 256


class EmbeddingClient:
    """异步 Embedding 客户端，封装 openai.embeddings.create。

    支持自动分批、超时控制和错误降级。
    """

    def __init__(
        self,
        client: "openai.AsyncOpenAI",
        model: str = DEFAULT_EMBEDDING_MODEL,
        dimensions: int = DEFAULT_EMBEDDING_DIMENSIONS,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._client = client
        self._model = model
        self._dimensions = dimensions
        self._timeout = timeout_seconds

    @property
    def model(self) -> str:
        return self._model

    @property
    def dimensions(self) -> int:
        return self._dimensions

    async def embed(self, texts: list[str]) -> np.ndarray:
        """将文本列表向量化，返回 (N, dimensions) 的 numpy 数组。

        自动分批处理，单批最多 _MAX_BATCH_SIZE 条。
        空输入返回 shape (0, dimensions) 的空数组。
        """
        if not texts:
            return np.empty((0, self._dimensions), dtype=np.float32)

        # 过滤空文本，记录原始索引映射
        valid_indices: list[int] = []
        valid_texts: list[str] = []
        for i, text in enumerate(texts):
            stripped = (text or "").strip()
            if stripped:
                valid_indices.append(i)
                valid_texts.append(stripped)

        if not valid_texts:
            return np.zeros((len(texts), self._dimensions), dtype=np.float32)

        # 分批调用
        all_vectors: list[np.ndarray] = []
        for start in range(0, len(valid_texts), _MAX_BATCH_SIZE):
            batch = valid_texts[start : start + _MAX_BATCH_SIZE]
            batch_vectors = await self._embed_batch(batch)
            all_vectors.append(batch_vectors)

        valid_matrix = np.vstack(all_vectors) if len(all_vectors) > 1 else all_vectors[0]

        # 将有效向量回填到完整矩阵（空文本对应零向量）
        if len(valid_indices) == len(texts):
            return valid_matrix

        result = np.zeros((len(texts), self._dimensions), dtype=np.float32)
        for idx, valid_idx in enumerate(valid_indices):
            result[valid_idx] = valid_matrix[idx]
        return result

    async def embed_single(self, text: str) -> np.ndarray:
        """向量化单条文本，返回 (dimensions,) 的一维数组。"""
        matrix = await self.embed([text])
        return matrix[0]

    async def _embed_batch(self, texts: list[str]) -> np.ndarray:
        """单批 API 调用。"""
        try:
            response = await asyncio.wait_for(
                self._client.embeddings.create(
                    input=texts,
                    model=self._model,
                    dimensions=self._dimensions,
                ),
                timeout=self._timeout,
            )
            vectors = [item.embedding for item in response.data]
            return np.array(vectors, dtype=np.float32)
        except asyncio.TimeoutError:
            logger.warning(
                "Embedding API 超时 (model=%s, batch_size=%d, timeout=%.1fs)",
                self._model,
                len(texts),
                self._timeout,
            )
            raise
        except Exception:
            logger.warning(
                "Embedding API 调用失败 (model=%s, batch_size=%d)",
                self._model,
                len(texts),
                exc_info=True,
            )
            raise
