"""语义 Manifest：为 FileRegistry 提供 embedding 语义搜索能力。

根据用户查询语义匹配最相关的 Excel 文件，替代全量注入 manifest。

.. versionchanged::
    数据源统一由 FileRegistry 提供。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import numpy as np

from excelmanus.embedding.search import SearchResult, cosine_top_k
from excelmanus.embedding.store import VectorStore

if TYPE_CHECKING:
    from excelmanus.embedding.client import EmbeddingClient
    from excelmanus.file_registry import FileEntry, FileRegistry

logger = logging.getLogger(__name__)


def _entry_to_text(entry: "FileEntry") -> str:
    """将 FileEntry 元数据转换为用于 embedding 的文本描述。"""
    parts = [entry.original_name, entry.canonical_path]
    for sm in entry.sheet_meta:
        sheet_desc = f"sheet:{sm.get('name', '')}"
        headers = sm.get("headers", [])
        if headers:
            sheet_desc += f" columns:{','.join(headers[:10])}"
        parts.append(sheet_desc)
    return " | ".join(parts)


class SemanticManifest:
    """语义 Manifest 增强层。

    在 FileRegistry 之上叠加 embedding 索引，
    支持按用户查询语义搜索最相关的文件。
    """

    def __init__(
        self,
        embedding_client: "EmbeddingClient",
        *,
        top_k: int = 5,
        threshold: float = 0.25,
    ) -> None:
        self._client = embedding_client
        self._top_k = top_k
        self._threshold = threshold
        self._store: VectorStore | None = None
        self._file_entries: list["FileEntry"] = []
        self._indexed_registry_id: int | None = None

    async def index_registry(self, registry: "FileRegistry") -> int:
        """为 registry 中的所有文件建立向量索引。

        使用 registry 的 id() 作为缓存键，避免重复索引。
        返回新增的向量数量。
        """
        registry_id = id(registry)
        if self._indexed_registry_id == registry_id and self._store is not None:
            return 0

        entries = [
            e for e in registry.list_all()
            if e.file_type in ("excel", "csv") and e.deleted_at is None
        ]

        if not entries:
            self._store = VectorStore(
                store_dir="/tmp/excelmanus_manifest_vectors",
                dimensions=self._client.dimensions,
            )
            self._file_entries = []
            self._indexed_registry_id = registry_id
            return 0

        texts: list[str] = []
        kept_entries: list["FileEntry"] = []
        for entry in entries:
            text = _entry_to_text(entry)
            if text.strip():
                texts.append(text)
                kept_entries.append(entry)

        if not texts:
            self._indexed_registry_id = registry_id
            return 0

        try:
            vectors = await self._client.embed(texts)
        except Exception:
            logger.warning("Registry 向量化失败", exc_info=True)
            self._indexed_registry_id = registry_id
            return 0

        self._store = VectorStore(
            store_dir="/tmp/excelmanus_manifest_vectors",
            dimensions=self._client.dimensions,
        )
        self._store.clear()
        added = self._store.add_batch(texts, vectors)
        self._file_entries = kept_entries
        self._indexed_registry_id = registry_id

        logger.debug("Registry 语义索引完成: %d 文件", added)
        return added

    async def search(
        self,
        query: str,
        registry: "FileRegistry",
        k: int | None = None,
        threshold: float | None = None,
    ) -> list[tuple["FileEntry", float]]:
        """语义搜索，返回 (FileEntry, score) 列表。"""
        await self.index_registry(registry)

        if self._store is None or self._store.size == 0:
            return []

        try:
            query_vec = await self._client.embed_single(query)
        except Exception:
            logger.warning("查询向量化失败", exc_info=True)
            return []

        results = cosine_top_k(
            query_vec,
            self._store.matrix,
            k=k or self._top_k,
            threshold=threshold or self._threshold,
        )

        output: list[tuple["FileEntry", float]] = []
        for r in results:
            if r.index < len(self._file_entries):
                output.append((self._file_entries[r.index], r.score))

        return output

    async def get_relevant_summary(
        self,
        query: str,
        registry: "FileRegistry",
    ) -> str:
        """返回与查询最相关的文件摘要文本，用于注入 system prompt。"""
        results = await self.search(query, registry)

        if not results:
            return ""

        lines = [
            "## 工作区相关文件（语义匹配）",
            f"与当前请求最相关的 {len(results)} 个文件：",
        ]
        for entry, score in results:
            sheet_parts: list[str] = []
            for sm in entry.sheet_meta:
                name = sm.get("name", "")
                rows = sm.get("rows", 0)
                cols = sm.get("columns", 0)
                headers = sm.get("headers", [])
                header_hint = ""
                if headers:
                    cols_str = ", ".join(headers[:6])
                    if len(headers) > 6:
                        cols_str += f" +{len(headers) - 6}列"
                    header_hint = f" [{cols_str}]"
                sheet_parts.append(f"{name}({rows}×{cols}){header_hint}")
            sheets_str = " | ".join(sheet_parts) if sheet_parts else "(空)"
            lines.append(f"- `{entry.canonical_path}` → {sheets_str}")

        return "\n".join(lines)
