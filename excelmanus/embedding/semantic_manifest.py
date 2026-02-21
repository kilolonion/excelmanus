"""语义 Manifest：为 WorkspaceManifest 提供 embedding 语义搜索能力。

根据用户查询语义匹配最相关的 Excel 文件，替代全量注入 manifest。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import numpy as np

from excelmanus.embedding.search import SearchResult, cosine_top_k
from excelmanus.embedding.store import VectorStore

if TYPE_CHECKING:
    from excelmanus.embedding.client import EmbeddingClient
    from excelmanus.workspace_manifest import ExcelFileMeta, WorkspaceManifest

logger = logging.getLogger(__name__)


def _file_to_text(fm: "ExcelFileMeta") -> str:
    """将文件元数据转换为用于 embedding 的文本描述。"""
    parts = [fm.name, fm.path]
    for sm in fm.sheets:
        sheet_desc = f"sheet:{sm.name}"
        if sm.headers:
            sheet_desc += f" columns:{','.join(sm.headers[:10])}"
        parts.append(sheet_desc)
    return " | ".join(parts)


class SemanticManifest:
    """语义 Manifest 增强层。

    在 WorkspaceManifest 之上叠加 embedding 索引，
    支持按用户查询语义搜索最相关的 Excel 文件。
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
        self._file_indices: list[int] = []  # store index → manifest file index
        self._indexed_manifest_id: int | None = None

    async def index_manifest(self, manifest: "WorkspaceManifest") -> int:
        """为 manifest 中的所有文件建立向量索引。

        使用 manifest 的 id() 作为缓存键，避免重复索引。
        返回新增的向量数量。
        """
        manifest_id = id(manifest)
        if self._indexed_manifest_id == manifest_id and self._store is not None:
            return 0

        if not manifest.files:
            self._store = VectorStore(
                store_dir="/tmp/excelmanus_manifest_vectors",
                dimensions=self._client.dimensions,
            )
            self._file_indices = []
            self._indexed_manifest_id = manifest_id
            return 0

        texts: list[str] = []
        file_indices: list[int] = []
        for i, fm in enumerate(manifest.files):
            text = _file_to_text(fm)
            if text.strip():
                texts.append(text)
                file_indices.append(i)

        if not texts:
            self._indexed_manifest_id = manifest_id
            return 0

        try:
            vectors = await self._client.embed(texts)
        except Exception:
            logger.warning("Manifest 向量化失败", exc_info=True)
            self._indexed_manifest_id = manifest_id
            return 0

        # 创建临时内存级 store（manifest 是会话级的，不需要持久化）
        self._store = VectorStore(
            store_dir="/tmp/excelmanus_manifest_vectors",
            dimensions=self._client.dimensions,
        )
        self._store.clear()
        added = self._store.add_batch(texts, vectors)
        self._file_indices = file_indices
        self._indexed_manifest_id = manifest_id

        logger.debug("Manifest 语义索引完成: %d 文件", added)
        return added

    async def search(
        self,
        query: str,
        manifest: "WorkspaceManifest",
        k: int | None = None,
        threshold: float | None = None,
    ) -> list[tuple["ExcelFileMeta", float]]:
        """语义搜索，返回 (ExcelFileMeta, score) 列表。"""
        await self.index_manifest(manifest)

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

        output: list[tuple["ExcelFileMeta", float]] = []
        for r in results:
            if r.index < len(self._file_indices):
                file_idx = self._file_indices[r.index]
                if file_idx < len(manifest.files):
                    output.append((manifest.files[file_idx], r.score))

        return output

    async def get_relevant_summary(
        self,
        query: str,
        manifest: "WorkspaceManifest",
    ) -> str:
        """返回与查询最相关的文件摘要文本，用于注入 system prompt。"""
        results = await self.search(query, manifest)

        if not results:
            return ""

        lines = [
            "## 工作区相关文件（语义匹配）",
            f"与当前请求最相关的 {len(results)} 个文件：",
        ]
        for fm, score in results:
            sheet_parts: list[str] = []
            for sm in fm.sheets:
                header_hint = ""
                if sm.headers:
                    cols_str = ", ".join(sm.headers[:6])
                    if len(sm.headers) > 6:
                        cols_str += f" +{len(sm.headers) - 6}列"
                    header_hint = f" [{cols_str}]"
                sheet_parts.append(
                    f"{sm.name}({sm.rows}×{sm.columns}){header_hint}"
                )
            sheets_str = " | ".join(sheet_parts) if sheet_parts else "(空)"
            lines.append(f"- `{fm.path}` → {sheets_str}")

        return "\n".join(lines)
