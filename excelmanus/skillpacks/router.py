"""Skillpack 路由器：斜杠直连与全量工具。"""

from __future__ import annotations

from dataclasses import replace

from excelmanus.config import ExcelManusConfig
from excelmanus.skillpacks.arguments import parse_arguments, substitute
from excelmanus.skillpacks.context_builder import build_contexts_with_budget
from excelmanus.skillpacks.loader import SkillpackLoader
from excelmanus.skillpacks.models import SkillMatchResult, Skillpack


class SkillRouter:
    """斜杠命令按技能直连；非斜杠默认全量工具。"""

    def __init__(self, config: ExcelManusConfig, loader: SkillpackLoader) -> None:
        self._config = config
        self._loader = loader

    async def route(
        self,
        user_message: str,
        *,
        slash_command: str | None = None,
        raw_args: str | None = None,
        file_paths: list[str] | None = None,
        blocked_skillpacks: set[str] | None = None,
        chat_mode: str = "write",
        on_event: object | None = None,
        images: list[dict] | None = None,
    ) -> SkillMatchResult:
        """执行路由：斜杠命令按技能直连；非斜杠默认全量工具。"""
        del user_message, file_paths, chat_mode, on_event, images
        skillpacks = self._loader.get_skillpacks()
        if not skillpacks:
            skillpacks = self._loader.load_all()

        if not skillpacks:
            return self._build_result(
                selected=[],
                route_mode="no_skillpack",
            )

        blocked = set(blocked_skillpacks or [])
        available_skillpacks = (
            {
                name: skill
                for name, skill in skillpacks.items()
                if name not in blocked
            }
            if blocked
            else skillpacks
        )

        if slash_command and slash_command.strip():
            direct_skill = self._find_skill_by_name(
                skillpacks=available_skillpacks,
                name=slash_command.strip(),
            )
            if direct_skill is None:
                return self._build_result(
                    selected=[],
                    route_mode="slash_not_found",
                )
            if not direct_skill.user_invocable:
                return self._build_result(
                    selected=[],
                    route_mode="slash_not_user_invocable",
                )
            return self._build_parameterized_result(
                skill=direct_skill,
                raw_args=raw_args or "",
            )

        return self._build_result(
            selected=[],
            route_mode="all_tools",
        )

    def build_skill_catalog(
        self,
        blocked_skillpacks: set[str] | None = None,
    ) -> tuple[str, list[str]]:
        """生成技能目录摘要和技能名称列表。"""
        skillpacks = self._loader.get_skillpacks()
        if not skillpacks:
            skillpacks = self._loader.load_all()

        if not skillpacks:
            return ("", [])

        blocked = set(blocked_skillpacks or [])
        visible_pairs = sorted(
            (
                (name, skill)
                for name, skill in skillpacks.items()
                if not skill.disable_model_invocation
            ),
            key=lambda item: item[0].lower(),
        )
        skill_names = [name for name, _ in visible_pairs]
        lines = ["可用技能：\n"]
        for name, skill in visible_pairs:
            if name in blocked:
                lines.append(
                    f"- {name}：{skill.description} "
                    f"[⚠️ 需要 fullAccess 权限，使用 /fullAccess on 开启]"
                )
            else:
                lines.append(f"- {name}：{skill.description}")
        catalog_text = "\n".join(lines)
        return (catalog_text, skill_names)

    def _build_result(
        self,
        selected: list[Skillpack],
        route_mode: str,
        *,
        parameterized: bool = False,
    ) -> SkillMatchResult:
        skills_used = [skill.name for skill in selected]
        contexts = build_contexts_with_budget(
            selected, self._config.skills_context_char_budget
        )
        return SkillMatchResult(
            skills_used=skills_used,
            route_mode=route_mode,
            system_contexts=contexts,
            parameterized=parameterized,
        )

    @staticmethod
    def _normalize_skill_name(name: str) -> str:
        return name.strip().lower().replace("-", "").replace("_", "")

    def _find_skill_by_name(
        self,
        *,
        skillpacks: dict[str, Skillpack],
        name: str,
    ) -> Skillpack | None:
        direct = skillpacks.get(name)
        if direct is not None:
            return direct

        by_lower = {skill_name.lower(): skill for skill_name, skill in skillpacks.items()}
        direct_lower = by_lower.get(name.lower())
        if direct_lower is not None:
            return direct_lower

        normalized = self._normalize_skill_name(name)
        normalized_map = {
            self._normalize_skill_name(skill_name): skill
            for skill_name, skill in skillpacks.items()
        }
        return normalized_map.get(normalized)

    def _build_parameterized_result(
        self,
        *,
        skill: Skillpack,
        raw_args: str,
    ) -> SkillMatchResult:
        args = parse_arguments(raw_args)
        rendered_instructions = substitute(skill.instructions, args)
        parameterized_skill = replace(skill, instructions=rendered_instructions)
        return self._build_result(
            selected=[parameterized_skill],
            route_mode="slash_direct",
            parameterized=True,
        )
