"""Skillpack 路由器：预筛、快速路径与 LLM 确认。"""

from __future__ import annotations

import fnmatch
import re
from typing import Awaitable, Callable

from excelmanus.config import ExcelManusConfig
from excelmanus.logger import get_logger
from excelmanus.skillpacks.context_builder import build_contexts_with_budget
from excelmanus.skillpacks.loader import SkillpackLoader
from excelmanus.skillpacks.models import SkillMatchResult, Skillpack

logger = get_logger("skillpacks.router")

ConfirmWithLLM = Callable[[str, list[Skillpack]], Awaitable[list[str]]]


class SkillRouter:
    """Skillpack 路由器。"""

    def __init__(self, config: ExcelManusConfig, loader: SkillpackLoader) -> None:
        self._config = config
        self._loader = loader

    async def route(
        self,
        user_message: str,
        *,
        skill_hints: list[str] | None = None,
        file_paths: list[str] | None = None,
        confirm_with_llm: ConfirmWithLLM | None = None,
        blocked_skillpacks: set[str] | None = None,
    ) -> SkillMatchResult:
        """执行本轮技能路由。"""
        skillpacks = self._loader.get_skillpacks()
        if not skillpacks:
            skillpacks = self._loader.load_all()

        blocked = set(blocked_skillpacks or [])
        if blocked:
            skillpacks = {
                name: skill
                for name, skill in skillpacks.items()
                if name not in blocked
            }

        if not skillpacks:
            return self._build_fallback_result(
                selected=[],
                route_mode="no_skillpack",
                all_skillpacks={},
            )

        # ── 1. 用户显式 hint 直连（手动调用）──
        hints = [hint.strip() for hint in (skill_hints or []) if hint and hint.strip()]
        if hints:
            hinted = self._pick_by_hints(hints=hints, skillpacks=skillpacks)
            if hinted:
                selected = self._apply_selection_limit(hinted)
                return self._build_result(selected=selected, route_mode="hint_direct")
            return self._build_fallback_result(
                selected=[],
                route_mode="hint_not_found",
                all_skillpacks=skillpacks,
            )

        # ── 2. 模型自动路由前过滤（禁用自动调用 / 禁止用户手动调用）──
        auto_skillpacks = self._filter_auto_routable_skillpacks(skillpacks)
        if not auto_skillpacks:
            return self._build_fallback_result(
                selected=[],
                route_mode="no_skillpack",
                all_skillpacks=skillpacks,
            )

        # ── 3. 预筛选 ──
        candidates = self._prefilter_candidates(
            user_message=user_message,
            skillpacks=auto_skillpacks,
            file_paths=file_paths or [],
        )

        # ── 4. 零候选：让 LLM 从可自动路由 skillpacks 中判断，否则 fallback ──
        if not candidates:
            if not self._config.skills_skip_llm_confirm and confirm_with_llm is not None:
                all_skillpacks = list(auto_skillpacks.values())
                selected = await self._llm_select(
                    user_message=user_message,
                    confirm_with_llm=confirm_with_llm,
                    candidate_skillpacks=all_skillpacks,
                    fallback_skillpacks=[],
                    skillpacks=auto_skillpacks,
                )
                if selected:
                    return self._build_result(selected=selected, route_mode="llm_confirm")
            fallback = self._fallback_skillpack(skillpacks=auto_skillpacks)
            return self._build_fallback_result(
                selected=[fallback] if fallback else [],
                route_mode="fallback",
                all_skillpacks=auto_skillpacks,
            )

        # ── 5. 高分领先：confident_direct ──
        top1, top1_score = candidates[0]
        top2_score = candidates[1][1] if len(candidates) > 1 else 0
        if (
            top1_score >= self._config.skills_fastpath_min_score
            and (top1_score - top2_score) >= self._config.skills_fastpath_min_gap
        ):
            selected = self._apply_selection_limit([top1])
            return self._build_result(selected=selected, route_mode="confident_direct")

        # ── 6. 分数不够突出 + skip_llm → 直接用 topK ──
        topk_skillpacks = [skill for skill, _ in candidates[: self._config.skills_prefilter_topk]]
        if self._config.skills_skip_llm_confirm or confirm_with_llm is None:
            selected = self._apply_selection_limit(topk_skillpacks)
            if not selected:
                fallback = self._fallback_skillpack(skillpacks=auto_skillpacks)
                selected = [fallback] if fallback else []
            return self._build_result(selected=selected, route_mode="topk_direct")

        # ── 7. 调 LLM 从候选中确认，失败则降级 topK ──
        selected = await self._llm_select(
            user_message=user_message,
            confirm_with_llm=confirm_with_llm,
            candidate_skillpacks=topk_skillpacks,
            fallback_skillpacks=topk_skillpacks,
            skillpacks=auto_skillpacks,
        )
        return self._build_result(selected=selected, route_mode="llm_confirm")

    async def _llm_select(
        self,
        *,
        user_message: str,
        confirm_with_llm: ConfirmWithLLM,
        candidate_skillpacks: list[Skillpack],
        fallback_skillpacks: list[Skillpack],
        skillpacks: dict[str, Skillpack],
    ) -> list[Skillpack]:
        """调用 LLM 从候选中选择，失败时降级到 fallback_skillpacks 或 general_excel。"""
        try:
            selected_names = await confirm_with_llm(user_message, candidate_skillpacks)
        except Exception:
            logger.warning("LLM 路由确认失败，降级到预筛结果", exc_info=True)
            selected_names = []

        selected_by_llm = [
            skillpacks[name]
            for name in selected_names
            if name in skillpacks
        ]
        selected = self._apply_selection_limit(selected_by_llm)
        if selected:
            return selected

        # LLM 返回空 → 降级到 fallback_skillpacks（通常是 topK 候选）
        if fallback_skillpacks:
            return self._apply_selection_limit(fallback_skillpacks)

        # 连 fallback_skillpacks 也为空 → general_excel
        fb = self._fallback_skillpack(skillpacks=skillpacks)
        return [fb] if fb else []

    def _pick_by_hints(
        self,
        hints: list[str],
        skillpacks: dict[str, Skillpack],
    ) -> list[Skillpack]:
        if not hints:
            return []
        by_lower = {name.lower(): skill for name, skill in skillpacks.items()}
        selected: list[Skillpack] = []
        seen: set[str] = set()
        for hint in hints:
            skill = by_lower.get(hint.lower())
            if skill is None:
                continue
            if skill.name in seen:
                continue
            selected.append(skill)
            seen.add(skill.name)
        return selected

    @staticmethod
    def _filter_auto_routable_skillpacks(
        skillpacks: dict[str, Skillpack],
    ) -> dict[str, Skillpack]:
        """过滤可参与模型自动路由的 Skillpack。"""
        return {
            name: skill
            for name, skill in skillpacks.items()
            if not skill.disable_model_invocation and skill.user_invocable
        }

    def _prefilter_candidates(
        self,
        user_message: str,
        skillpacks: dict[str, Skillpack],
        file_paths: list[str],
    ) -> list[tuple[Skillpack, int]]:
        query = user_message.lower()
        tokens = set(re.findall(r"[a-zA-Z0-9_\.\-/]+", query))
        candidates: list[tuple[Skillpack, int]] = []

        for skill in skillpacks.values():
            score = skill.priority
            score += self._score_triggers(query=query, skill=skill)
            score += self._score_file_patterns(
                query=query, tokens=tokens, file_paths=file_paths, skill=skill
            )
            # 基于 description 的词汇交集评分
            score += self._score_description(query=user_message, skill=skill)
            if score > 0:
                candidates.append((skill, score))

        candidates.sort(key=lambda item: (-item[1], item[0].name))
        return candidates

    @staticmethod
    def _score_triggers(query: str, skill: Skillpack) -> int:
        score = 0
        for trigger in skill.triggers:
            trigger_lower = trigger.lower()
            if trigger_lower and trigger_lower in query:
                score += 3
        return score

    @staticmethod
    def _tokenize(text: str) -> set[str]:
        """分词：英文按空格/标点分割并小写化，中文按字符级 bigram + 单字分割。"""
        # 英文 token（小写）
        tokens = set(re.findall(r"[a-zA-Z0-9_]+", text.lower()))
        # 中文字符提取
        chinese_chars = re.findall(r"[\u4e00-\u9fff]", text)
        # 中文 bigram
        for i in range(len(chinese_chars) - 1):
            tokens.add(chinese_chars[i] + chinese_chars[i + 1])
        # 单个中文字符也加入（支持单字匹配）
        for ch in chinese_chars:
            tokens.add(ch)
        return tokens

    @staticmethod
    def _score_description(query: str, skill: Skillpack) -> int:
        """基于 description 的词汇交集评分，每个交集词 +1 分。"""
        if not skill.description:
            return 0
        query_tokens = SkillRouter._tokenize(query)
        desc_tokens = SkillRouter._tokenize(skill.description)
        overlap = query_tokens & desc_tokens
        return len(overlap)


    @staticmethod
    def _score_file_patterns(
        query: str,
        tokens: set[str],
        file_paths: list[str],
        skill: Skillpack,
    ) -> int:
        if not skill.file_patterns:
            return 0

        score = 0
        for pattern in skill.file_patterns:
            pattern_lower = pattern.lower()
            ext_hit = pattern_lower.startswith("*.") and pattern_lower[1:] in query
            if ext_hit:
                score += 1

            for token in tokens:
                if fnmatch.fnmatch(token, pattern_lower):
                    score += 2
                    break

            for path in file_paths:
                if fnmatch.fnmatch(path.lower(), pattern_lower):
                    score += 2
                    break
        return score

    def _apply_selection_limit(self, selected: list[Skillpack]) -> list[Skillpack]:
        if not selected:
            return []
        ordered = sorted(selected, key=lambda skill: (-skill.priority, skill.name))
        return ordered[: self._config.skills_max_selected]

    @staticmethod
    def _fallback_skillpack(skillpacks: dict[str, Skillpack]) -> Skillpack | None:
        if "general_excel" in skillpacks:
            return skillpacks["general_excel"]
        if not skillpacks:
            return None
        return sorted(skillpacks.values(), key=lambda skill: (-skill.priority, skill.name))[0]

    def _build_result(self, selected: list[Skillpack], route_mode: str) -> SkillMatchResult:
        skills_used = [skill.name for skill in selected]
        tool_scope: list[str] = []
        seen_tools: set[str] = set()
        for skill in selected:
            for tool in skill.allowed_tools:
                if tool in seen_tools:
                    continue
                seen_tools.add(tool)
                tool_scope.append(tool)
        contexts = build_contexts_with_budget(
            selected, self._config.skills_context_char_budget
        )
        return SkillMatchResult(
            skills_used=skills_used,
            tool_scope=tool_scope,
            route_mode=route_mode,
            system_contexts=contexts,
        )

    def _build_fallback_result(
        self,
        selected: list[Skillpack],
        route_mode: str,
        all_skillpacks: dict[str, Skillpack],
    ) -> SkillMatchResult:
        """构建 fallback 路由结果：注入技能目录摘要 + list_skills 工具。"""
        result = self._build_result(selected=selected, route_mode=route_mode)

        # 将 list_skills 加入 tool_scope
        tool_scope = list(result.tool_scope)
        if "list_skills" not in tool_scope:
            tool_scope.append("list_skills")

        # 生成技能目录摘要并注入 system_contexts
        catalog = SkillRouter._build_skill_catalog(all_skillpacks)
        contexts = list(result.system_contexts)
        if catalog:
            contexts.append(catalog)

        return SkillMatchResult(
            skills_used=result.skills_used,
            tool_scope=tool_scope,
            route_mode=result.route_mode,
            system_contexts=contexts,
        )

    @staticmethod
    def _build_skill_catalog(skillpacks: dict[str, Skillpack]) -> str:
        """生成所有 skillpack 的摘要目录文本。"""
        if not skillpacks:
            return ""
        lines = [
            "[技能目录] 当前未匹配到明确的技能包。以下是所有可用技能，"
            "你可以根据用户需求判断是否需要调用 list_skills 工具获取详情：\n",
        ]
        for name, skill in sorted(skillpacks.items()):
            lines.append(f"- {name}：{skill.description}")
        return "\n".join(lines)
