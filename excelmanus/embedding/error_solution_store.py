"""错误解决方案语义存储：将历史错误→解决方案对建立向量索引。

当工具执行遇到新错误时，通过 embedding 语义检索相似历史案例，
将匹配到的解决方案注入 system prompt，帮助 agent 更快修复。

设计原则：
- 自动从工具执行失败中提取 error→solution 对
- 向量索引持久化到 VectorStore
- API 不可用时降级为空结果（零影响）
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from excelmanus.embedding.search import cosine_top_k
from excelmanus.embedding.store import VectorStore

if TYPE_CHECKING:
    from pathlib import Path

    from excelmanus.embedding.client import EmbeddingClient

logger = logging.getLogger(__name__)

# 语义去重阈值：新错误与已有错误的相似度超过此值则视为已有
_ERROR_DEDUP_THRESHOLD = 0.92


@dataclass
class ErrorSolution:
    """单条错误→解决方案记录。"""

    error_text: str
    solution_text: str
    tool_name: str = ""
    success: bool = False  # 解决方案是否最终成功


class ErrorSolutionStore:
    """错误解决方案语义存储。

    自动从工具执行结果中学习 error→solution 对，
    遇到新错误时语义检索最相关的历史解决方案。
    """

    def __init__(
        self,
        embedding_client: "EmbeddingClient",
        store_dir: "str | Path",
        *,
        top_k: int = 3,
        threshold: float = 0.35,
    ) -> None:
        self._client = embedding_client
        self._top_k = top_k
        self._threshold = threshold
        self._store = VectorStore(
            store_dir=store_dir,
            dimensions=embedding_client.dimensions,
        )
        # 内存中维护 error→solution 映射（与 VectorStore 索引对齐）
        self._solutions: list[ErrorSolution] = []
        self._pending_errors: dict[str, str] = {}  # tool_call_id → error_text

    @property
    def size(self) -> int:
        return self._store.size

    async def record_error(
        self,
        tool_call_id: str,
        tool_name: str,
        error_text: str,
    ) -> None:
        """记录一次工具执行错误（等待后续 record_solution 配对）。"""
        if not error_text or not error_text.strip():
            return
        key = f"{tool_call_id}:{tool_name}"
        self._pending_errors[key] = error_text.strip()[:500]

    async def record_solution(
        self,
        tool_call_id: str,
        tool_name: str,
        solution_text: str,
        success: bool = True,
    ) -> bool:
        """记录错误的解决方案（与之前的 record_error 配对）。

        返回 True 表示成功入库。
        """
        key = f"{tool_call_id}:{tool_name}"
        error_text = self._pending_errors.pop(key, None)
        if not error_text:
            return False

        if not solution_text or not solution_text.strip():
            return False

        # 检查是否已有相似错误
        try:
            error_vec = await self._client.embed_single(error_text)
        except Exception:
            logger.debug("错误向量化失败，跳过入库", exc_info=True)
            return False

        if self._store.size > 0:
            results = cosine_top_k(
                error_vec, self._store.matrix,
                k=1, threshold=_ERROR_DEDUP_THRESHOLD,
            )
            if results:
                # 已有高度相似的错误记录，更新解决方案（如果新方案成功）
                idx = results[0].index
                if idx < len(self._solutions) and success:
                    self._solutions[idx].solution_text = solution_text.strip()[:500]
                    self._solutions[idx].success = True
                return False

        # 新错误→解决方案对，入库
        record = ErrorSolution(
            error_text=error_text,
            solution_text=solution_text.strip()[:500],
            tool_name=tool_name,
            success=success,
        )

        added = self._store.add(
            text=error_text,
            vector=error_vec,
            metadata={
                "tool_name": tool_name,
                "solution": solution_text.strip()[:500],
                "success": success,
            },
        )
        if added:
            self._solutions.append(record)
            self._store.save()
        return added

    async def search(
        self,
        error_text: str,
        k: int | None = None,
        threshold: float | None = None,
    ) -> list[ErrorSolution]:
        """语义搜索相似错误，返回匹配的解决方案列表。"""
        if self._store.size == 0 or not error_text.strip():
            return []

        try:
            query_vec = await self._client.embed_single(error_text)
        except Exception:
            logger.debug("错误查询向量化失败", exc_info=True)
            return []

        results = cosine_top_k(
            query_vec,
            self._store.matrix,
            k=k or self._top_k,
            threshold=threshold or self._threshold,
        )

        output: list[ErrorSolution] = []
        for r in results:
            if r.index < len(self._solutions):
                output.append(self._solutions[r.index])
            else:
                # 从 VectorStore metadata 重建
                meta = self._store.get_metadata(r.index)
                record = self._store.get_record(r.index)
                if record and meta:
                    output.append(ErrorSolution(
                        error_text=record.text,
                        solution_text=meta.get("solution", ""),
                        tool_name=meta.get("tool_name", ""),
                        success=meta.get("success", False),
                    ))

        return output

    async def get_guidance_text(self, error_text: str) -> str:
        """语义检索错误解决方案，返回格式化的注入文本。

        无匹配时返回空字符串（零 token 开销）。
        """
        solutions = await self.search(error_text)
        if not solutions:
            return ""

        # 优先展示成功的解决方案
        solutions.sort(key=lambda s: (not s.success, 0))

        lines = ["## 💡 相似错误的历史解决方案"]
        for sol in solutions:
            status = "✅" if sol.success else "⚠️"
            tool_hint = f" ({sol.tool_name})" if sol.tool_name else ""
            lines.append(f"- {status} **错误**{tool_hint}：{sol.error_text[:100]}")
            lines.append(f"  **方案**：{sol.solution_text[:200]}")

        return "\n".join(lines)
