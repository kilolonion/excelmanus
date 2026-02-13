"""Skillpack 路由器：预筛、快速路径与 LLM 确认。"""

from __future__ import annotations

from dataclasses import replace
import fnmatch
from pathlib import Path
import re
from typing import Awaitable, Callable

from excelmanus.config import ExcelManusConfig
from excelmanus.logger import get_logger
from excelmanus.skillpacks.arguments import parse_arguments, substitute
from excelmanus.skillpacks.context_builder import build_contexts_with_budget
from excelmanus.skillpacks.loader import SkillpackLoader
from excelmanus.skillpacks.models import ForkPlan, SkillMatchResult, Skillpack

logger = get_logger("skillpacks.router")

ConfirmWithLLM = Callable[[str, list[Skillpack]], Awaitable[list[str]]]
_EXCEL_PATH_PATTERN = re.compile(
    r"""(?:"([^"]+\.(?:xlsx|xlsm|xls))"|'([^']+\.(?:xlsx|xlsm|xls))'|([^\s"'，。！？；：]+?\.(?:xlsx|xlsm|xls)))""",
    re.IGNORECASE,
)


class SkillRouter:
    """Skillpack 路由器。"""

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
        skill_hints: list[str] | None = None,
        slash_command: str | None = None,
        raw_args: str | None = None,
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

        # ── 1. 用户显式 hint 直连（手动调用）──
        hints = [hint.strip() for hint in (skill_hints or []) if hint and hint.strip()]
        if hints:
            hinted = self._pick_by_hints(hints=hints, skillpacks=skillpacks)
            if hinted:
                selected = self._apply_selection_limit(hinted)
                return self._build_result(
                    selected=selected,
                    route_mode="hint_direct",
                    user_message=user_message,
                    candidate_file_paths=candidate_file_paths,
                )
            return self._build_fallback_result(
                selected=[],
                route_mode="hint_not_found",
                all_skillpacks=skillpacks,
                user_message=user_message,
                candidate_file_paths=candidate_file_paths,
            )

        # ── 2. 模型自动路由前过滤（禁用自动调用 / 禁止用户手动调用）──
        auto_skillpacks = self._filter_auto_routable_skillpacks(skillpacks)
        if not auto_skillpacks:
            return self._build_fallback_result(
                selected=[],
                route_mode="no_skillpack",
                all_skillpacks=skillpacks,
                user_message=user_message,
                candidate_file_paths=candidate_file_paths,
            )

        # ── 3. 预筛选 ──
        candidates = self._prefilter_candidates(
            user_message=user_message,
            skillpacks=auto_skillpacks,
            file_paths=candidate_file_paths,
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
                    return self._build_result(
                        selected=selected,
                        route_mode="llm_confirm",
                        user_message=user_message,
                        candidate_file_paths=candidate_file_paths,
                    )
            fallback = self._fallback_skillpack(skillpacks=auto_skillpacks)
            return self._build_fallback_result(
                selected=[fallback] if fallback else [],
                route_mode="fallback",
                all_skillpacks=auto_skillpacks,
                user_message=user_message,
                candidate_file_paths=candidate_file_paths,
            )

        # ── 5. 高分领先：confident_direct ──
        top1, top1_score = candidates[0]
        top2_score = candidates[1][1] if len(candidates) > 1 else 0
        if (
            top1_score >= self._config.skills_fastpath_min_score
            and (top1_score - top2_score) >= self._config.skills_fastpath_min_gap
        ):
            selected = self._apply_selection_limit([top1])
            return self._build_result(
                selected=selected,
                route_mode="confident_direct",
                user_message=user_message,
                candidate_file_paths=candidate_file_paths,
            )

        # ── 6. 分数不够突出 + skip_llm → 直接用 topK ──
        topk_skillpacks = [skill for skill, _ in candidates[: self._config.skills_prefilter_topk]]
        if self._config.skills_skip_llm_confirm or confirm_with_llm is None:
            selected = self._apply_selection_limit(topk_skillpacks)
            if not selected:
                fallback = self._fallback_skillpack(skillpacks=auto_skillpacks)
                selected = [fallback] if fallback else []
            return self._build_result(
                selected=selected,
                route_mode="topk_direct",
                user_message=user_message,
                candidate_file_paths=candidate_file_paths,
            )

        # ── 7. 调 LLM 从候选中确认，失败则降级 topK ──
        selected = await self._llm_select(
            user_message=user_message,
            confirm_with_llm=confirm_with_llm,
            candidate_skillpacks=topk_skillpacks,
            fallback_skillpacks=topk_skillpacks,
            skillpacks=auto_skillpacks,
        )
        return self._build_result(
            selected=selected,
            route_mode="llm_confirm",
            user_message=user_message,
            candidate_file_paths=candidate_file_paths,
        )

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

    def _build_result(
        self,
        selected: list[Skillpack],
        route_mode: str,
        *,
        parameterized: bool = False,
        user_message: str = "",
        candidate_file_paths: list[str] | None = None,
    ) -> SkillMatchResult:
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
        base_result = SkillMatchResult(
            skills_used=skills_used,
            tool_scope=tool_scope,
            route_mode=route_mode,
            system_contexts=contexts,
            parameterized=parameterized,
        )
        return self._decorate_result(
            result=base_result,
            selected=selected,
            user_message=user_message,
            candidate_file_paths=candidate_file_paths,
        )

    def _decorate_result(
        self,
        *,
        result: SkillMatchResult,
        selected: list[Skillpack],
        user_message: str,
        candidate_file_paths: list[str] | None,
    ) -> SkillMatchResult:
        route_mode = result.route_mode
        contexts = list(result.system_contexts)

        large_files = self._detect_large_excel_files(candidate_file_paths or [])
        fork_plan = self._build_fork_plan(
            selected=selected,
            user_message=user_message,
            large_files=large_files,
        )
        if large_files:
            contexts.append(
                self._build_large_file_fork_hint(
                    user_message=user_message,
                    large_files=large_files,
                )
            )
            route_mode = self._append_route_tag(route_mode, "large_excel")

        fork_skills = [skill.name for skill in selected if skill.context == "fork"]
        if fork_skills:
            contexts.append(self._build_fork_context_hint(fork_skills))
            route_mode = self._append_route_tag(route_mode, "fork")
        if fork_plan is not None:
            route_mode = self._append_route_tag(route_mode, "fork_plan")

        return SkillMatchResult(
            skills_used=result.skills_used,
            tool_scope=result.tool_scope,
            route_mode=route_mode,
            system_contexts=contexts,
            parameterized=result.parameterized,
            fork_plan=fork_plan,
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
            user_message=user_message,
            candidate_file_paths=candidate_file_paths,
        )

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
            parameterized=result.parameterized,
            fork_plan=result.fork_plan,
        )

    @staticmethod
    def _append_route_tag(route_mode: str, tag: str) -> str:
        marker = f"+{tag}"
        if marker in route_mode:
            return route_mode
        return f"{route_mode}{marker}"

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
            user_message=user_message,
            candidate_file_paths=candidate_file_paths,
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

    def _build_large_file_fork_hint(
        self,
        *,
        user_message: str,
        large_files: list[tuple[str, int]],
    ) -> str:
        threshold = self._format_size(self._config.large_excel_threshold_bytes)
        lines = [
            "[ForkContextHint] 检测到大体量 Excel 文件，建议先在子上下文执行只读探索，再回主上下文执行写入操作。",
            f"大文件阈值：{threshold}",
            "命中文件：",
        ]
        for path, size_bytes in large_files:
            lines.append(f"- {path} ({self._format_size(size_bytes)})")
        lines.extend(
            [
                "推荐流程：",
                "1. 子上下文仅使用只读工具（read_excel/analyze_data/filter_data/get_file_info）做结构探查与异常扫描。",
                "2. 子上下文只返回摘要（列结构、行数级别、异常概览、建议操作顺序）。",
                "3. 主上下文基于摘要再执行 transform/write，避免把中间明细塞满主上下文。",
                f"当前任务：{user_message.strip()}",
            ]
        )
        return "\n".join(lines)

    @staticmethod
    def _build_fork_context_hint(fork_skills: list[str]) -> str:
        joined = ", ".join(fork_skills)
        return (
            "[ForkContextSkill] 本轮命中 fork 上下文技能："
            f"{joined}\n"
            "执行约束：先做只读探查并返回摘要，再进入写入阶段。"
        )

    def _build_fork_plan(
        self,
        *,
        selected: list[Skillpack],
        user_message: str,
        large_files: list[tuple[str, int]],
    ) -> ForkPlan | None:
        fork_skills = [skill for skill in selected if skill.context == "fork"]
        if not fork_skills and not large_files:
            return None

        chosen_tools: list[str] = []
        seen: set[str] = set()
        for skill in selected:
            for tool in skill.allowed_tools:
                if tool not in self._READ_ONLY_TOOLS:
                    continue
                if tool in seen:
                    continue
                seen.add(tool)
                chosen_tools.append(tool)

        for tool in self._READ_ONLY_TOOLS:
            if tool in seen:
                continue
            seen.add(tool)
            chosen_tools.append(tool)

        source_skills = [skill.name for skill in fork_skills]
        file_names = [path for path, _ in large_files]

        if source_skills and file_names:
            reason = "命中 fork 技能，且检测到大体量 Excel 文件"
        elif source_skills:
            reason = "命中 context=fork 技能"
        else:
            reason = "检测到大体量 Excel 文件"

        prompt_lines = [
            "你是子代理（fork context），负责只读探索并输出高密度摘要。",
            "硬约束：只允许调用只读工具；禁止任何写入/删除/重命名/覆盖操作。",
            "输出要求：",
            "1. 数据结构：sheet 列表、目标 sheet、关键列、行列规模",
            "2. 数据质量：缺失值、异常值、类型冲突、可疑边界值",
            "3. 执行建议：主代理下一步应使用的工具顺序与参数建议",
            "4. 仅输出摘要，不输出全量明细",
            f"用户任务：{user_message.strip()}",
        ]
        if source_skills:
            prompt_lines.append("触发技能：" + ", ".join(source_skills))
        if file_names:
            prompt_lines.append("目标文件：" + ", ".join(file_names))

        return ForkPlan(
            reason=reason,
            tool_scope=chosen_tools,
            prompt="\n".join(prompt_lines),
            source_skills=source_skills,
            detected_files=file_names,
        )

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
