"""Skillpack 路由器：斜杠直连与 fallback。"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import re
from typing import Any

from excelmanus.config import ExcelManusConfig
from excelmanus.logger import get_logger
from excelmanus.skillpacks.arguments import parse_arguments, substitute
from excelmanus.skillpacks.context_builder import build_contexts_with_budget
from excelmanus.skillpacks.loader import SkillpackLoader
from excelmanus.skillpacks.models import SkillMatchResult, Skillpack
from excelmanus.tools.policy import TOOL_CATEGORIES

logger = get_logger("skillpacks.router")

_EXCEL_PATH_PATTERN = re.compile(
    r"""(?:"([^"]+\.(?:xlsx|xlsm|xls))"|'([^']+\.(?:xlsx|xlsm|xls))'|([^\s"'，。！？；：]+?\.(?:xlsx|xlsm|xls)))""",
    re.IGNORECASE,
)
_EXCEL_SUFFIXES = {".xlsx", ".xlsm", ".xls"}
_MAY_WRITE_HINT_RE = re.compile(
    r"(创建|修改|写入|删除|替换|填充|插入|更新|设置|格式化|高亮|加粗|标红|美化|条件格式|画图|生成图表|柱状图|饼图|折线图|雷达图|散点图|排序|合并|转置|拆分|write|update|format|chart)",
    re.IGNORECASE,
)
_READ_ONLY_HINT_RE = re.compile(
    r"(查看|列出|读取|扫描|分析|统计|对比|检查|预览|read|scan|analyz|inspect|list)",
    re.IGNORECASE,
)

class SkillRouter:
    """简化后的技能路由器：仅负责斜杠直连路由和技能目录生成。"""

    def __init__(self, config: ExcelManusConfig, loader: SkillpackLoader) -> None:
        self._config = config
        self._loader = loader
        self._router_client: Any = None
        self._fallback_client: Any = None

    def _get_router_client(self) -> Any:
        """懒加载 router 端点客户端，首次调用时创建并缓存。"""
        if self._router_client is None:
            from excelmanus.providers import create_client
            self._router_client = create_client(
                api_key=self._config.router_api_key or self._config.api_key,
                base_url=self._config.router_base_url or self._config.base_url,
            )
        return self._router_client

    def _get_fallback_client(self) -> Any:
        """懒加载主模型降级客户端，首次调用时创建并缓存。"""
        if self._fallback_client is None:
            from excelmanus.providers import create_client
            self._fallback_client = create_client(
                api_key=self._config.api_key,
                base_url=self._config.base_url,
            )
        return self._fallback_client

    async def route(
        self,
        user_message: str,
        *,
        slash_command: str | None = None,
        raw_args: str | None = None,
        file_paths: list[str] | None = None,
        blocked_skillpacks: set[str] | None = None,
        write_hint: str | None = None,
    ) -> SkillMatchResult:
        """执行路由：斜杠命令按技能直连；非斜杠默认全量工具。"""
        skillpacks = self._loader.get_skillpacks()
        if not skillpacks:
            skillpacks = self._loader.load_all()

        if not skillpacks:
            return self._build_fallback_result(
                selected=[],
                route_mode="no_skillpack",
                all_skillpacks={},
            )

        blocked = set(blocked_skillpacks or [])
        # 斜杠直连路由使用排除被限制技能后的集合
        available_skillpacks = (
            {
                name: skill
                for name, skill in skillpacks.items()
                if name not in blocked
            }
            if blocked
            else skillpacks
        )

        # ── 0. 斜杠命令直连路由（参数化）──
        candidate_file_paths = self._collect_candidate_file_paths(
            user_message=user_message,
            file_paths=file_paths or [],
            raw_args=raw_args or "",
        )
        if slash_command and slash_command.strip():
            # 斜杠路径同步词法分类 write_hint，避免 LLM 调用延迟
            # 分类文本优先用 raw_args（任务描述），其次 user_message
            _hint_text = (raw_args or "").strip() or user_message
            slash_write_hint: str = (
                write_hint
                or self._classify_write_hint_lexical(_hint_text)
                or "unknown"
            )
            direct_skill = self._find_skill_by_name(
                skillpacks=available_skillpacks,
                name=slash_command.strip(),
            )
            if direct_skill is None:
                return self._build_fallback_result(
                    selected=[],
                    route_mode="slash_not_found",
                    all_skillpacks=skillpacks,
                    user_message=user_message,
                    candidate_file_paths=candidate_file_paths,
                    blocked_skillpacks=blocked_skillpacks,
                    write_hint=slash_write_hint,
                )
            if not direct_skill.user_invocable:
                return self._build_fallback_result(
                    selected=[],
                    route_mode="slash_not_user_invocable",
                    all_skillpacks=skillpacks,
                    user_message=user_message,
                    candidate_file_paths=candidate_file_paths,
                    blocked_skillpacks=blocked_skillpacks,
                    write_hint=slash_write_hint,
                )
            return self._build_parameterized_result(
                skill=direct_skill,
                raw_args=raw_args or "",
                user_message=user_message,
                candidate_file_paths=candidate_file_paths,
                write_hint=slash_write_hint,
            )

        # ── 1. 非斜杠消息：默认全量工具（tool_scope 置空，由引擎补全）──
        # 并行调用小模型判断 write_hint
        write_hint = write_hint or await self._classify_write_hint(user_message)
        return self._build_all_tools_result(
            user_message=user_message,
            candidate_file_paths=candidate_file_paths,
            write_hint=write_hint,
        )

    def build_skill_catalog(
        self,
        blocked_skillpacks: set[str] | None = None,
    ) -> tuple[str, list[str]]:
        """生成技能目录摘要和技能名称列表。

        被限制的技能仍会出现在目录中（标注需要 fullAccess），
        以便 LLM 知道其存在并在用户需要时给出权限提示。

        返回:
            (catalog_text, skill_names) 元组
        """
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
        write_hint: str = "unknown",
    ) -> SkillMatchResult:
        """构建路由结果。"""
        skills_used = [skill.name for skill in selected]
        contexts = build_contexts_with_budget(
            selected, self._config.skills_context_char_budget
        )
        return SkillMatchResult(
            skills_used=skills_used,
            route_mode=route_mode,
            system_contexts=contexts,
            parameterized=parameterized,
            write_hint=write_hint,
        )

    def _build_fallback_result(
        self,
        selected: list[Skillpack],
        route_mode: str,
        all_skillpacks: dict[str, Skillpack],
        *,
        user_message: str = "",
        candidate_file_paths: list[str] | None = None,
        blocked_skillpacks: set[str] | None = None,
        write_hint: str = "unknown",
    ) -> SkillMatchResult:
        """构建 fallback 路由结果。

        技能目录通过 activate_skill 元工具的 description 传递。
        """
        result = self._build_result(
            selected=selected,
            route_mode=route_mode,
            write_hint=write_hint,
        )

        system_contexts = list(result.system_contexts)
        large_file_context = self._build_large_file_context(
            user_message=user_message,
            candidate_file_paths=candidate_file_paths,
        )
        if large_file_context:
            system_contexts.append(large_file_context)

        file_structure_context = self._build_file_structure_context(
            candidate_file_paths=candidate_file_paths,
        )
        if file_structure_context:
            system_contexts.append(file_structure_context)

        return SkillMatchResult(
            skills_used=result.skills_used,
            route_mode=result.route_mode,
            system_contexts=system_contexts,
            parameterized=result.parameterized,
            write_hint=result.write_hint,
        )

    def _build_all_tools_result(
        self,
        *,
        user_message: str,
        candidate_file_paths: list[str] | None,
        write_hint: str = "unknown",
    ) -> SkillMatchResult:
        """构建非斜杠默认路由：tool_scope 为空表示由引擎放开全量工具。"""
        system_contexts: list[str] = []
        large_file_context = self._build_large_file_context(
            user_message=user_message,
            candidate_file_paths=candidate_file_paths,
        )
        if large_file_context:
            system_contexts.append(large_file_context)

        file_structure_context = self._build_file_structure_context(
            candidate_file_paths=candidate_file_paths,
        )
        if file_structure_context:
            system_contexts.append(file_structure_context)

        return SkillMatchResult(
            skills_used=[],
            route_mode="all_tools",
            system_contexts=system_contexts,
            parameterized=False,
            write_hint=write_hint,
        )

    async def _classify_write_hint(self, user_message: str) -> str:
        """调用小模型判断用户消息是否涉及写入操作。

        返回 "may_write" | "read_only" | "unknown"。
        超时、解析失败、未配置 router_model 时均返回 "unknown"。

        降级策略：当 router 端点不可用时，自动降级使用主模型。
        """
        import asyncio
        import json as _json

        lexical_hint = self._classify_write_hint_lexical(user_message)

        if not self._config.router_model:
            return lexical_hint or "unknown"

        system_prompt = (
            "你是任务分类器。判断用户请求是否涉及文件写入或修改表格操作。"
            '只输出 JSON: {"write_hint": "may_write"} 或 {"write_hint": "read_only"}\n'
            "判断为 may_write 的信号：\n"
            "- 明确的写入动词：创建、修改、写入、删除、替换、填充、插入、更新、设置\n"
            "- 明确的格式化动词：格式化、高亮、加粗、标红、美化、条件格式\n"
            "- 明确的图表动词：画图、生成图表、柱状图、饼图\n"
            "- 明确的结构操作：排序、合并、转置、拆分、复制到\n"
            "判断为 read_only 的信号：\n"
            "- 查看、列出、读取、扫描、分析、统计、对比、检查、预览\n"
            "当不能完全确定有写入操作时请优先使用 read_only"
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message[:500]},
        ]

        async def _try_classify(client: Any, model: str, timeout: float) -> str | None:
            """尝试用指定端点分类，成功返回 hint，失败返回 None。"""
            try:
                response = await asyncio.wait_for(
                    client.chat.completions.create(
                        model=model,
                        messages=messages,
                        max_tokens=30,
                        temperature=0,
                    ),
                    timeout=timeout,
                )
                text = (response.choices[0].message.content or "").strip()
                candidate = text
                left = candidate.find("{")
                right = candidate.rfind("}")
                if left >= 0 and right > left:
                    candidate = candidate[left:right + 1]
                try:
                    data = _json.loads(candidate)
                    hint = str(data.get("write_hint", "")).strip().lower()
                    if hint in ("may_write", "read_only"):
                        return hint
                except (_json.JSONDecodeError, TypeError, AttributeError):
                    pass
                return lexical_hint
            except Exception:
                # 端点异常时返回 None，让外层继续走主模型降级链。
                return None

        timeout_seconds = 10.0

        # 1) 先尝试 router 端点
        result = await _try_classify(
            client=self._get_router_client(),
            model=self._config.router_model,
            timeout=timeout_seconds,
        )
        if result is not None:
            return result

        # 2) 降级：使用主模型
        result = await _try_classify(
            client=self._get_fallback_client(),
            model=self._config.model,
            timeout=timeout_seconds,
        )
        if result is not None:
            return result

        return "unknown"

    def _classify_write_hint_lexical(self, user_message: str) -> str | None:
        """本地词法兜底：优先识别写入/格式化/图表等明确写意图。"""
        text = str(user_message or "").strip()
        if not text:
            return None
        if _MAY_WRITE_HINT_RE.search(text):
            return "may_write"
        if _READ_ONLY_HINT_RE.search(text):
            return "read_only"
        return None

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
        write_hint: str = "unknown",
    ) -> SkillMatchResult:
        args = parse_arguments(raw_args)
        rendered_instructions = substitute(skill.instructions, args)
        parameterized_skill = replace(skill, instructions=rendered_instructions)
        result = self._build_result(
            selected=[parameterized_skill],
            route_mode="slash_direct",
            parameterized=True,
            write_hint=write_hint,
        )
        system_contexts = list(result.system_contexts)
        large_file_context = self._build_large_file_context(
            user_message=user_message,
            candidate_file_paths=candidate_file_paths,
        )
        if large_file_context:
            system_contexts.append(large_file_context)
        return SkillMatchResult(
            skills_used=result.skills_used,
            route_mode=result.route_mode,
            system_contexts=system_contexts,
            parameterized=result.parameterized,
            write_hint=result.write_hint,
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

    def _build_file_structure_context(
        self,
        *,
        candidate_file_paths: list[str] | None,
        max_files: int = 3,
        max_sheets: int = 5,
        scan_rows: int = 12,
    ) -> str:
        """预读候选 Excel 文件的 sheet 结构，注入路由上下文。

        用 openpyxl 只读模式仅读前几行，帮助 LLM 确定 header_row
        和可用列名，避免盲猜导致的多轮重试。
        """
        if not candidate_file_paths:
            return ""

        file_sections: list[str] = []
        processed = 0
        seen: set[str] = set()

        for raw_path in candidate_file_paths:
            if processed >= max_files:
                break
            normalized = (raw_path or "").strip()
            if not normalized:
                continue
            try:
                path_obj = Path(normalized).expanduser()
                if not path_obj.is_absolute():
                    path_obj = Path.cwd() / path_obj
                resolved = path_obj.resolve(strict=False)
            except OSError:
                continue

            resolved_str = str(resolved)
            if resolved_str in seen:
                continue
            seen.add(resolved_str)

            if resolved.suffix.lower() not in _EXCEL_SUFFIXES:
                continue
            try:
                if not resolved.is_file():
                    continue
            except OSError:
                continue

            try:
                from openpyxl import load_workbook
                wb = load_workbook(resolved, read_only=True, data_only=True)
            except Exception:
                continue

            sheet_lines: list[str] = []
            try:
                for idx, sn in enumerate(wb.sheetnames):
                    ws = wb[sn]
                    if idx >= max_sheets:
                        # 超出详细预览数量的 sheet：仅输出摘要行（名称+行列数）
                        summary_rows = ws.max_row or 0
                        summary_cols = ws.max_column or 0
                        sheet_lines.append(f"  [{sn}] {summary_rows}行×{summary_cols}列")
                        continue

                    rows_data: list[list[Any]] = []
                    for i, row in enumerate(ws.iter_rows(values_only=True)):
                        if i >= scan_rows:
                            break
                        normalized_row = []
                        for c in row:
                            if isinstance(c, str):
                                text = c.strip()
                                normalized_row.append(text if text else None)
                            else:
                                normalized_row.append(c)
                        rows_data.append(normalized_row)

                    total_rows = ws.max_row or 0
                    total_cols = ws.max_column or 0
                    sheet_lines.append(f"  [{sn}] {total_rows}行×{total_cols}列")

                    for ri, row_vals in enumerate(rows_data):
                        # 过滤尾部 None
                        trimmed = row_vals
                        while trimmed and trimmed[-1] is None:
                            trimmed = trimmed[:-1]
                        display = [str(v) if v is not None else None for v in trimmed]
                        sheet_lines.append(f"    第{ri}行: {display}")

                    # 启发式 header_row 建议
                    header_hint = self._guess_header_row(rows_data)
                    if header_hint is not None:
                        sheet_lines.append(f"    → 建议 header_row={header_hint}")
            finally:
                wb.close()

            if sheet_lines:
                file_sections.append(f"文件: {normalized}\n" + "\n".join(sheet_lines))
                processed += 1

        if not file_sections:
            return ""

        header = (
            "[文件结构预览] 以下是用户提及的 Excel 文件结构，请据此确定正确的 header_row 和列名。\n"
            "请基于以上预览直接调用工具执行用户请求，不要重复描述文件结构。"
        )
        return header + "\n" + "\n".join(file_sections)

    @staticmethod
    def _guess_header_row(rows: list[list[Any]], max_scan: int = 12) -> int | None:
        """启发式猜测 header 行号（0-indexed）。

        支持多段结构：通过非空数量、文本占比、关键字命中和下一行数据特征综合评分。
        """
        if not rows:
            return None

        keywords = ("月份", "日期", "城市", "产品", "部门", "姓名", "工号", "金额", "数量", "状态", "营收", "利润")

        def _trim(row: list[Any]) -> list[Any]:
            trimmed = list(row)
            while trimmed and trimmed[-1] is None:
                trimmed.pop()
            return trimmed

        def _score(row: list[Any], row_idx: int, next_row: list[Any] | None) -> float:
            row = _trim(row)
            non_empty = [v for v in row if v is not None]
            if len(non_empty) < 3:
                return float("-inf")

            text_values = [str(v).strip() for v in non_empty if isinstance(v, str) and str(v).strip()]
            string_count = len(text_values)
            numeric_count = sum(1 for v in non_empty if isinstance(v, (int, float)))
            unique_ratio = len(set(map(str, non_empty))) / max(len(non_empty), 1)
            keyword_hits = sum(1 for text in text_values if any(k in text for k in keywords))

            score = 0.0
            score += len(non_empty) * 2.0
            score += string_count * 1.4
            score -= numeric_count * 1.2
            score += unique_ratio * 2.0
            score += keyword_hits * 2.5
            score -= row_idx * 0.03

            first = non_empty[0]
            if isinstance(first, str) and any(token in first for token in ("生成时间", "报表", "分析", "仪表盘", "──")):
                score -= 5.0

            if next_row:
                nn = [v for v in _trim(next_row) if v is not None]
                if nn:
                    next_numeric_ratio = sum(1 for v in nn if isinstance(v, (int, float))) / len(nn)
                    score += next_numeric_ratio * 1.5
            return score

        upper = min(max_scan, len(rows))
        best_idx: int | None = None
        best_score = float("-inf")
        for idx in range(upper):
            nxt = rows[idx + 1] if idx + 1 < upper else None
            score = _score(rows[idx], idx, nxt)
            if score > best_score:
                best_score = score
                best_idx = idx

        if best_idx is None or best_score == float("-inf"):
            return None
        return best_idx

    def _build_large_file_context(
        self,
        *,
        user_message: str,
        candidate_file_paths: list[str] | None,
    ) -> str:
        large_files = self._detect_large_excel_files(candidate_file_paths)
        if not large_files:
            return ""

        threshold = self._config.large_excel_threshold_bytes
        normalized_message = (user_message or "").strip()
        if normalized_message:
            user_summary = normalized_message[:120]
            if len(normalized_message) > 120:
                user_summary += "..."
        else:
            user_summary = "(空)"

        lines = [
            "[路由提示] 检测到大文件 Excel，优先采用代码方式分步处理。"
            "请直接调用推荐的工具开始处理，不要先输出处理计划。",
            f"- 用户请求：{user_summary}",
            f"- 大文件阈值：{self._format_bytes(threshold)}（{threshold} bytes）",
            "- 命中文件：",
        ]
        for file_path, file_size in large_files:
            lines.append(f"  - {file_path}（{self._format_bytes(file_size)}）")
        lines.append("- 建议优先选择 `excel_code_runner`，先抽样探查后再全量处理。")
        return "\n".join(lines)

    def _detect_large_excel_files(
        self,
        candidate_file_paths: list[str] | None,
    ) -> list[tuple[str, int]]:
        if not candidate_file_paths:
            return []

        threshold = self._config.large_excel_threshold_bytes
        if threshold <= 0:
            return []

        large_files: list[tuple[str, int]] = []
        seen_paths: set[str] = set()
        for raw_path in candidate_file_paths:
            normalized = (raw_path or "").strip()
            if not normalized:
                continue
            try:
                path_obj = Path(normalized).expanduser()
                if not path_obj.is_absolute():
                    path_obj = Path.cwd() / path_obj
                resolved = path_obj.resolve(strict=False)
            except OSError:
                continue

            normalized_resolved = str(resolved)
            if normalized_resolved in seen_paths:
                continue
            seen_paths.add(normalized_resolved)

            if resolved.suffix.lower() not in _EXCEL_SUFFIXES:
                continue

            try:
                if not resolved.is_file():
                    continue
                file_size = resolved.stat().st_size
            except OSError:
                continue

            if file_size >= threshold:
                large_files.append((normalized_resolved, file_size))
        return large_files

    @staticmethod
    def _format_bytes(size: int) -> str:
        if size < 1024:
            return f"{size} B"
        value = float(size)
        units = ["KB", "MB", "GB", "TB"]
        for unit in units:
            value /= 1024
            if value < 1024 or unit == units[-1]:
                return f"{value:.2f} {unit}"
        return f"{size} B"

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
