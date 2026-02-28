"""PlaybookCurator — 策略整合器。

将 Reflector 产出的 PlaybookDelta 智能整合到 PlaybookStore，
处理语义去重、合并、评分和淘汰。
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import numpy as np

from excelmanus.playbook.reflector import PlaybookDelta
from excelmanus.playbook.store import (
    DEDUP_SIMILARITY_THRESHOLD,
    PlaybookBullet,
    PlaybookStore,
    _utc_now_iso,
)

if TYPE_CHECKING:
    from excelmanus.embedding.client import EmbeddingClient

logger = logging.getLogger(__name__)


@dataclass
class CuratorReport:
    """整合报告。"""

    new_count: int = 0
    merged_count: int = 0
    pruned_count: int = 0
    total_bullets: int = 0
    errors: list[str] | None = None


class PlaybookCurator:
    """将新策略教训整合到 PlaybookStore。"""

    def __init__(
        self,
        store: PlaybookStore,
        embedding_client: "EmbeddingClient | None" = None,
        *,
        max_bullets: int = 500,
    ) -> None:
        self._store = store
        self._embedding_client = embedding_client
        self._max_bullets = max_bullets

    async def integrate(
        self,
        deltas: list[PlaybookDelta],
        session_id: str = "",
    ) -> CuratorReport:
        """将 PlaybookDelta 列表整合到 store。

        流程:
        1. 为每条 delta 生成 embedding
        2. 与现有 playbook 做语义去重（cosine > threshold → 合并）
        3. 合并: helpful_count += 1，content 取更精炼版本
        4. 新增: 直接入库
        5. 超限时淘汰低质量条目
        """
        report = CuratorReport()
        errors: list[str] = []

        if not deltas:
            report.total_bullets = self._store.count()
            return report

        # 1. 生成 embeddings
        embeddings = await self._compute_embeddings(
            [d.content for d in deltas]
        )

        # 2+3. 逐条去重/合并/新增
        for i, delta in enumerate(deltas):
            emb = embeddings[i] if i < len(embeddings) else None
            try:
                merged = self._try_merge(delta, emb, session_id)
                if merged:
                    report.merged_count += 1
                else:
                    self._add_new(delta, emb, session_id)
                    report.new_count += 1
            except Exception as exc:
                errors.append(f"delta[{i}] 整合失败: {exc}")
                logger.debug("Curator delta 整合失败", exc_info=True)

        # 4. 超限淘汰
        report.pruned_count = self._store.prune(max_bullets=self._max_bullets)
        report.total_bullets = self._store.count()
        if errors:
            report.errors = errors

        return report

    def _try_merge(
        self,
        delta: PlaybookDelta,
        embedding: np.ndarray | None,
        session_id: str,
    ) -> bool:
        """尝试与已有条目合并。返回 True 表示已合并。"""
        if embedding is None:
            return False

        existing = self._store.find_similar(
            embedding, threshold=DEDUP_SIMILARITY_THRESHOLD,
        )
        if existing is None:
            return False

        # 合并策略: helpful +1, content 选择更短的版本
        self._store.mark_helpful(existing.id)
        if len(delta.content) < len(existing.content):
            self._store.update_content(existing.id, delta.content)

        logger.debug(
            "Curator: 合并到已有条目 %s (similarity > %.2f)",
            existing.id, DEDUP_SIMILARITY_THRESHOLD,
        )
        return True

    def _add_new(
        self,
        delta: PlaybookDelta,
        embedding: np.ndarray | None,
        session_id: str,
    ) -> str:
        """新增条目到 store。"""
        bullet = PlaybookBullet(
            id=uuid.uuid4().hex[:16],
            category=delta.category,
            content=delta.content[:500],
            source_task_tags=[],
            helpful_count=1,
            harmful_count=0,
            embedding=embedding,
            created_at=_utc_now_iso(),
            origin_session_id=session_id,
            origin_summary=delta.source_summary[:100],
        )
        return self._store.add(bullet)

    async def _compute_embeddings(
        self, texts: list[str],
    ) -> list[np.ndarray | None]:
        """为文本列表生成 embeddings。无 client 时返回全 None。"""
        if self._embedding_client is None or not texts:
            return [None] * len(texts)

        try:
            matrix = await self._embedding_client.embed(texts)
            return [matrix[i] for i in range(matrix.shape[0])]
        except Exception as exc:
            logger.debug("Curator embedding 生成失败: %s", exc)
            return [None] * len(texts)
