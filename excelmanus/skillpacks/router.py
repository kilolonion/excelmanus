"""Skillpack 路由器：斜杠直连与 fallback。"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import re

from excelmanus.config import ExcelManusConfig
from excelmanus.logger import get_logger
from excelmanus.skillpacks.arguments import parse_arguments, substitute
from excelmanus.skillpacks.context_builder import build_contexts_with_budget
from excelmanus.skillpacks.loader import SkillpackLoader
from excelmanus.skillpacks.models import SkillMatchResult, Skillpack

logger = get_logger("skillpacks.router")

_EXCEL_PATH_PATTERN = re.compile(
    r"""(?:"([^"]+\.(?:xlsx|xlsm|xls))"|'([^']+\.(?:xlsx|xlsm|xls))'|([^\s"'，。！？；：]+?\.(?:xlsx|xlsm|xls)))""",
    re.IGNORECASE,
)


class SkillRouter:
    """简化后的技能路由器：仅负责斜杠直连路由和技能目录生成。"""

    _EXCEL_EXTENSIONS = {".xlsx", ".xlsm", ".xls"}
    _MAX_LARGE_FILE_HINTS = 3
    _READ_ONLY_TOOLS = (
        "read_excel",
        "analyze_data",
        "filter_data",
        "list_sheets",
        "get_file_info",
        "search_files",
        "list_directory",
        "read_text_file",
        "read_cell_styles",
    )

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
    ) -> SkillMatchResult:
        """执行路由：仅处理斜杠直连，非斜杠返回 fallback。"""
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

        # ── 0. 斜杠命令直连路由（参数化）──
        candidate_file_paths = self._collect_candidate_file_paths(
            user_message=user_message,
            file_paths=file_paths or [],
            raw_args=raw_args or "",
        )
        if slash_command and slash_command.strip():
            direct_skill = self._find_skill_by_name(
                skillpacks=skillpacks,
                name=slash_command.strip(),
            )
            if direct_skill is None:
                return self._build_fallback_result(
                    selected=[],
                    route_mode="slash_not_found",
                    all_skillpacks=skillpacks,
                    user_message=user_message,
                    candidate_file_paths=candidate_file_paths,
                )
            return self._build_parameterized_result(
                skill=direct_skill,
                raw_args=raw_args or "",
                user_message=user_message,
                candidate_file_paths=candidate_file_paths,
            )

        # ── 1. 非斜杠消息：返回 fallback 结果（全量工具 + 技能目录）──
        return self._build_fallback_result(
            selected=[],
            route_mode="fallback",
            all_skillpacks=skillpacks,
            user_message=user_message,
            candidate_file_paths=candidate_file_paths,
        )

    def build_skill_catalog(
        self,
        blocked_skillpacks: set[str] | None = None,
    ) -> tuple[str, list[str]]:
        """生成技能目录摘要和技能名称列表。

        返回:
            (catalog_text, skill_names) 元组
        """
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
            return ("", [])

        skill_names = sorted(skillpacks.keys())
        lines = ["可用技能：\n"]
        for name in skill_names:
            skill = skillpacks[name]
            lines.append(f"- {name}：{skill.description}")
        catalog_text = "\n".join(lines)
        return (catalog_text, skill_names)

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

    def _build_result(
        self,
        selected: list[Skillpack],
        route_mode: str,
        *,
        parameterized: bool = False,
    ) -> SkillMatchResult:
        """构建路由结果。"""
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
            parameterized=parameterized,
        )

    def _build_fallback_result(
        self,
        selected: list[Skillpack],
        route_mode: str,
        all_skillpacks: dict[str, Skillpack],
        *,
        user_message: str = "",
        candidate_file_paths: list[str] | None = None,
    ) -> SkillMatchResult:
        """构建 fallback 路由结果：注入技能目录摘要 + list_skills 工具。"""
        result = self._build_result(
            selected=selected,
            route_mode=route_mode,
        )

        # 将 list_skills 加入 tool_scope
        tool_scope = list(result.tool_scope)
        if "list_skills" not in tool_scope:
            tool_scope.append("list_skills")

        # 生成技能目录摘要并注入 system_contexts
        catalog_text, _ = self.build_skill_catalog()
        contexts = list(result.system_contexts)
        if catalog_text:
            contexts.append(catalog_text)

        return SkillMatchResult(
            skills_used=result.skills_used,
            tool_scope=tool_scope,
            route_mode=result.route_mode,
            system_contexts=contexts,
            parameterized=result.parameterized,
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
        user_message: str = "",
        candidate_file_paths: list[str] | None = None,
    ) -> SkillMatchResult:
        args = parse_arguments(raw_args)
        rendered_instructions = substitute(skill.instructions, args)
        parameterized_skill = replace(skill, instructions=rendered_instructions)
        return self._build_result(
            selected=[parameterized_skill],
            route_mode="slash_direct",
            parameterized=True,
        )

    def _collect_candidate_file_paths(
        self,
        *,
        user_message: str,
        file_paths: list[str],
        raw_args: str,
    ) -> list[str]:
        merged: list[str] = []
        merged.extend(file_paths)
        merged.extend(self._extract_excel_paths(user_message))
        merged.extend(self._extract_excel_paths(raw_args))
        deduped: list[str] = []
        seen: set[str] = set()
        for path in merged:
            normalized = path.strip()
            if not normalized or normalized in seen:
                continue
            deduped.append(normalized)
            seen.add(normalized)
        return deduped

    @staticmethod
    def _extract_excel_paths(text: str) -> list[str]:
        if not text:
            return []
        paths: list[str] = []
        for match in _EXCEL_PATH_PATTERN.finditer(text):
            value = next((group for group in match.groups() if group), "")
            candidate = value.strip().strip("，。！？；：,.;:()[]{}")
            if candidate:
                paths.append(candidate)
        return paths

    def _detect_large_excel_files(
        self,
        candidate_file_paths: list[str],
    ) -> list[tuple[str, int]]:
        threshold = self._config.large_excel_threshold_bytes
        if threshold <= 0:
            return []
        workspace_root = Path(self._config.workspace_root).expanduser().resolve()
        large_files: list[tuple[str, int]] = []
        for raw_path in candidate_file_paths:
            resolved = self._resolve_path_in_workspace(raw_path, workspace_root)
            if resolved is None:
                continue
            if resolved.suffix.lower() not in self._EXCEL_EXTENSIONS:
                continue
            if not resolved.exists() or not resolved.is_file():
                continue
            try:
                size_bytes = resolved.stat().st_size
            except OSError:
                continue
            if size_bytes < threshold:
                continue
            try:
                display_path = str(resolved.relative_to(workspace_root))
            except ValueError:
                display_path = str(resolved)
            large_files.append((display_path, size_bytes))
            if len(large_files) >= self._MAX_LARGE_FILE_HINTS:
                break
        return large_files

    @staticmethod
    def _resolve_path_in_workspace(
        raw_path: str,
        workspace_root: Path,
    ) -> Path | None:
        try:
            candidate = Path(raw_path).expanduser()
            if not candidate.is_absolute():
                candidate = (workspace_root / candidate).resolve()
            else:
                candidate = candidate.resolve()
            candidate.relative_to(workspace_root)
            return candidate
        except Exception:
            return None

    @staticmethod
    def _format_size(size_bytes: int) -> str:
        value = float(size_bytes)
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if value < 1024 or unit == "TB":
                if unit == "B":
                    return f"{int(value)}{unit}"
                return f"{value:.1f}{unit}"
            value /= 1024
        return f"{int(size_bytes)}B"
