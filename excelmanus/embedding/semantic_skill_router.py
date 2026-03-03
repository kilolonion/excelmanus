"""语义技能路由：为 SkillRouter 提供 embedding 语义匹配能力。

根据用户自然语言查询，语义匹配最相关的 Skillpack，
替代全量 all_tools 模式下的盲目暴露。

设计原则：
- 不侵入 SkillRouter 内部逻辑，作为独立增强层
- 懒加载向量索引，首次查询时构建
- API 不可用时降级为空结果（由调用方决定 fallback 策略）
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import numpy as np

from excelmanus.embedding.search import cosine_top_k

if TYPE_CHECKING:
    from excelmanus.embedding.client import EmbeddingClient
    from excelmanus.skillpacks.models import Skillpack

logger = logging.getLogger(__name__)


def _skill_to_text(skill: "Skillpack") -> str:
    """将 Skillpack 元数据转换为用于 embedding 的文本描述。"""
    parts = [skill.name, skill.description]
    # 取 instructions 前 200 字符作为语义补充
    inst = (skill.instructions or "").strip()
    if inst:
        parts.append(inst[:200])
    return " | ".join(parts)


class SemanticSkillRouter:
    """语义技能路由增强层。

    在 SkillRouter 之上叠加 embedding 索引，
    支持按用户查询语义搜索最相关的 Skillpack。
    """

    def __init__(
        self,
        embedding_client: "EmbeddingClient",
        *,
        top_k: int = 3,
        threshold: float = 0.3,
    ) -> None:
        self._client = embedding_client
        self._top_k = top_k
        self._threshold = threshold
        # 缓存：向量矩阵 + 对应的 skill 列表
        self._skill_vectors: np.ndarray | None = None
        self._skill_list: list["Skillpack"] = []
        self._indexed_skill_names: frozenset[str] = frozenset()

    async def index_skills(
        self, skillpacks: dict[str, "Skillpack"],
    ) -> int:
        """为 skillpacks 建立向量索引。

        使用 skill 名称集合作为缓存键，避免重复索引。
        返回新增的向量数量。
        """
        current_names = frozenset(skillpacks.keys())
        if current_names == self._indexed_skill_names and self._skill_vectors is not None:
            return 0

        skills = [
            s for s in skillpacks.values()
            if not s.disable_model_invocation and s.user_invocable
        ]

        if not skills:
            self._skill_vectors = np.empty((0, self._client.dimensions), dtype=np.float32)
            self._skill_list = []
            self._indexed_skill_names = current_names
            return 0

        texts = [_skill_to_text(s) for s in skills]

        try:
            vectors = await self._client.embed(texts)
        except Exception:
            logger.warning("Skillpack 向量化失败", exc_info=True)
            self._indexed_skill_names = current_names
            return 0

        self._skill_vectors = vectors
        self._skill_list = skills
        self._indexed_skill_names = current_names

        logger.debug("语义技能索引完成: %d 个 skill", len(skills))
        return len(skills)

    async def match(
        self,
        query: str,
        skillpacks: dict[str, "Skillpack"],
        k: int | None = None,
        threshold: float | None = None,
    ) -> list[tuple["Skillpack", float]]:
        """语义匹配，返回 (Skillpack, score) 列表（按相关性降序）。"""
        await self.index_skills(skillpacks)

        if self._skill_vectors is None or self._skill_vectors.shape[0] == 0:
            return []

        try:
            query_vec = await self._client.embed_single(query)
        except Exception:
            logger.warning("查询向量化失败", exc_info=True)
            return []

        results = cosine_top_k(
            query_vec,
            self._skill_vectors,
            k=k or self._top_k,
            threshold=threshold or self._threshold,
        )

        output: list[tuple["Skillpack", float]] = []
        for r in results:
            if r.index < len(self._skill_list):
                output.append((self._skill_list[r.index], r.score))

        return output

    async def get_best_skill(
        self,
        query: str,
        skillpacks: dict[str, "Skillpack"],
        min_score: float = 0.45,
    ) -> tuple["Skillpack", float] | None:
        """返回最佳匹配的单个 skill（score >= min_score），否则返回 None。

        用于决定是否激活特定 skill 而不是 all_tools 模式。
        """
        results = await self.match(query, skillpacks, k=1, threshold=min_score)
        return results[0] if results else None

    async def get_relevant_skill_hints(
        self,
        query: str,
        skillpacks: dict[str, "Skillpack"],
    ) -> str:
        """返回语义匹配到的技能提示文本，用于注入 system prompt。

        即使不激活 skill，也可以告诉 agent 哪些 skill 与当前任务最相关。
        """
        results = await self.match(query, skillpacks)
        if not results:
            return ""

        lines = ["## 语义匹配技能提示"]
        lines.append(f"与当前请求最相关的 {len(results)} 个技能：")
        for skill, score in results:
            lines.append(f"- **{skill.name}**（相关度 {score:.2f}）：{skill.description}")
        lines.append(
            "如果用户的请求与上述技能相关，可以建议使用 `/技能名` 斜杠命令激活。"
        )
        return "\n".join(lines)
