"""语义记忆：为 PersistentMemory 提供 embedding 语义检索能力。

设计原则：
- 不侵入 PersistentMemory 内部逻辑，作为独立增强层
- 向量存储与 MEMORY.md 同目录，自动同步
- API 不可用时降级到 PersistentMemory.load_core() 原有行为
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import numpy as np

from excelmanus.embedding.search import SearchResult, cosine_top_k
from excelmanus.embedding.store import VectorStore
from excelmanus.memory_models import MemoryEntry

if TYPE_CHECKING:
    from excelmanus.embedding.client import EmbeddingClient
    from excelmanus.persistent_memory import PersistentMemory

logger = logging.getLogger(__name__)


class SemanticMemory:
    """语义记忆增强层。

    在 PersistentMemory 之上叠加 embedding 向量索引，
    支持按用户查询语义检索最相关的记忆条目。
    """

    def __init__(
        self,
        persistent_memory: "PersistentMemory",
        embedding_client: "EmbeddingClient",
        *,
        top_k: int = 10,
        threshold: float = 0.3,
        fallback_recent: int = 5,
    ) -> None:
        self._pm = persistent_memory
        self._client = embedding_client
        self._top_k = top_k
        self._threshold = threshold
        self._fallback_recent = fallback_recent

        # 向量存储目录：与记忆目录同级的 .vectors/ 子目录
        vectors_dir = self._pm.memory_dir / ".vectors"
        self._store = VectorStore(
            store_dir=vectors_dir,
            dimensions=embedding_client.dimensions,
        )
        self._synced = False

    @property
    def store(self) -> VectorStore:
        """暴露底层 VectorStore，供测试或高级用例使用。"""
        return self._store

    async def sync_index(self) -> int:
        """将 PersistentMemory 中的所有条目同步到向量索引。

        仅处理尚未向量化的条目（通过 content_hash 去重）。
        返回新增的向量数量。
        """
        all_entries = self._load_all_entries()
        if not all_entries:
            self._synced = True
            return 0

        # 找出需要向量化的新条目
        new_texts: list[str] = []
        new_metadata: list[dict[str, Any]] = []
        for entry in all_entries:
            if not self._store.has(entry.content):
                new_texts.append(entry.content)
                new_metadata.append({
                    "category": entry.category.value,
                    "timestamp": entry.timestamp.isoformat(),
                })

        if not new_texts:
            self._synced = True
            return 0

        try:
            vectors = await self._client.embed(new_texts)
            added = self._store.add_batch(new_texts, vectors, new_metadata)
            self._store.save()
            self._synced = True
            logger.info("语义记忆索引同步完成: 新增 %d 条向量", added)
            return added
        except Exception:
            logger.warning("语义记忆索引同步失败，将降级到传统加载", exc_info=True)
            self._synced = True  # 标记已尝试，避免重复失败
            return 0

    async def index_entries(self, entries: list[MemoryEntry]) -> int:
        """为新写入的记忆条目增量建立向量索引。

        在 PersistentMemory.save_entries() 之后调用。
        返回新增的向量数量。
        """
        if not entries:
            return 0

        new_texts: list[str] = []
        new_metadata: list[dict[str, Any]] = []
        for entry in entries:
            if not self._store.has(entry.content):
                new_texts.append(entry.content)
                new_metadata.append({
                    "category": entry.category.value,
                    "timestamp": entry.timestamp.isoformat(),
                })

        if not new_texts:
            return 0

        try:
            vectors = await self._client.embed(new_texts)
            added = self._store.add_batch(new_texts, vectors, new_metadata)
            self._store.save()
            return added
        except Exception:
            logger.warning("增量向量索引失败", exc_info=True)
            return 0

    async def search(self, query: str) -> str:
        """语义检索记忆，返回格式化的 Markdown 文本。

        混合策略：embedding top-k + 最近 N 条兜底，避免遗漏。
        API 不可用时降级到 PersistentMemory.load_core()。
        """
        # 确保索引已同步
        if not self._synced:
            await self.sync_index()

        if self._store.size == 0:
            return self._pm.load_core()

        try:
            query_vec = await self._client.embed_single(query)
        except Exception:
            logger.warning("查询向量化失败，降级到传统加载", exc_info=True)
            return self._pm.load_core()

        # 语义检索 top-k
        results = cosine_top_k(
            query_vec,
            self._store.matrix,
            k=self._top_k,
            threshold=self._threshold,
        )

        # 收集语义命中的文本
        semantic_texts: list[str] = []
        semantic_indices: set[int] = set()
        for r in results:
            record = self._store.get_record(r.index)
            if record:
                semantic_texts.append(record.text)
                semantic_indices.add(r.index)

        # 兜底：追加最近 N 条（不重复）
        all_entries = self._load_all_entries()
        recent_entries = all_entries[-self._fallback_recent:] if all_entries else []
        recent_texts: list[str] = []
        for entry in recent_entries:
            if entry.content not in semantic_texts:
                recent_texts.append(entry.content)

        # 组装输出
        parts: list[str] = []
        if semantic_texts:
            parts.append("### 语义相关记忆")
            for text in semantic_texts:
                parts.append(f"- {text}")
        if recent_texts:
            parts.append("\n### 最近记忆")
            for text in recent_texts:
                parts.append(f"- {text}")

        return "\n".join(parts) if parts else self._pm.load_core()

    async def search_entries(
        self,
        query: str,
        k: int | None = None,
        threshold: float | None = None,
    ) -> list[tuple[MemoryEntry, float]]:
        """语义检索，返回 (MemoryEntry, score) 列表。

        供高级用例使用（如 compaction 中的相关性筛选）。
        """
        if not self._synced:
            await self.sync_index()

        if self._store.size == 0:
            return []

        try:
            query_vec = await self._client.embed_single(query)
        except Exception:
            return []

        results = cosine_top_k(
            query_vec,
            self._store.matrix,
            k=k or self._top_k,
            threshold=threshold or self._threshold,
        )

        all_entries = self._load_all_entries()
        entry_by_content: dict[str, MemoryEntry] = {}
        for entry in all_entries:
            entry_by_content[entry.content] = entry

        output: list[tuple[MemoryEntry, float]] = []
        for r in results:
            record = self._store.get_record(r.index)
            if record and record.text in entry_by_content:
                output.append((entry_by_content[record.text], r.score))

        return output

    def _load_all_entries(self) -> list[MemoryEntry]:
        """从 MEMORY.md 加载所有条目。"""
        from excelmanus.persistent_memory import CORE_MEMORY_FILE

        filepath = self._pm.memory_dir / CORE_MEMORY_FILE
        if not filepath.exists():
            return []
        try:
            content = filepath.read_text(encoding="utf-8")
            return self._pm.parse_entries(content)
        except OSError:
            logger.warning("读取记忆文件失败", exc_info=True)
            return []
