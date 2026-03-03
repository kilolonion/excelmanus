"""ContextBuilder — 从 AgentEngine 解耦的系统提示词组装组件。

负责管理：
- 系统提示词组装（_prepare_system_prompts_for_request）
- 各类 notice 构建（access/backup/mcp/window/tool_index）
- 工具名列表、窗口感知提示设置
"""

from __future__ import annotations

import hashlib as _hashlib
import json as _json
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from excelmanus.logger import get_logger
from excelmanus.mcp.manager import parse_tool_prefix
from excelmanus.memory import TokenCounter
from excelmanus.task_list import TaskStatus

if TYPE_CHECKING:
    from excelmanus.engine import AgentEngine
    from excelmanus.events import EventCallback
    from excelmanus.skillpacks import SkillMatchResult

_MAX_PLAN_AUTO_CONTINUE = 3  # 计划审批后自动续跑最大次数
_PLAN_CONTEXT_MAX_CHARS = 6000
_MIN_SYSTEM_CONTEXT_CHARS = 256
_SYSTEM_CONTEXT_SHRINK_MARKER = "[上下文已压缩以适配上下文窗口]"

logger = get_logger("context_builder")


class ContextBuilder:
    """系统提示词组装器，从 AgentEngine 搬迁所有 _build_*_notice 和 _prepare_system_prompts。"""

    _TOKEN_COUNT_CACHE_MAX = 16  # fingerprint → token_count LRU 上限

    def __init__(self, engine: "AgentEngine") -> None:
        self._engine = engine
        # O3+O4: 基于内容指纹的 token 计数缓存，避免重复 tiktoken 编码
        self._token_count_cache: dict[str, int] = {}
        # C2: 轮次级静态 notice 缓存，同一 session_turn 内不重复构建
        self._turn_notice_cache: dict[str, str] = {}
        self._turn_notice_cache_key: int = -1
        # W1: 窗口感知 notice 脏标记缓存
        self._window_notice_cache: str | None = None
        self._window_notice_dirty: bool = True
        # P5: 文件全景 panorama 脏标记缓存（CoW 写入时标脏）
        self._panorama_cache: str | None = None
        self._panorama_dirty: bool = True
        self._panorama_cache_turn: int = -1

    def _all_tool_names(self) -> list[str]:
        e = self._engine
        get_tool_names = getattr(e.registry, "get_tool_names", None)
        if callable(get_tool_names):
            return list(get_tool_names())

        get_all_tools = getattr(e.registry, "get_all_tools", None)
        if callable(get_all_tools):
            return [tool.name for tool in get_all_tools()]

        return []

    def _focus_window_refill_reader(
        self,
        *,
        file_path: str,
        sheet_name: str,
        range_ref: str,
    ) -> dict[str, Any]:
        """focus_window 自动补读回调。"""
        e = self._engine
        if not file_path or not sheet_name or not range_ref:
            return {"success": False, "error": "缺少 file_path/sheet_name/range 参数"}

        all_tools = self._all_tool_names()
        read_sheet_tools: list[str] = []
        for tool_name in all_tools:
            if not tool_name.startswith("mcp_"):
                continue
            try:
                _, origin_name = parse_tool_prefix(tool_name)
            except ValueError:
                continue
            if origin_name == "read_sheet":
                read_sheet_tools.append(tool_name)

        for tool_name in read_sheet_tools:
            try:
                arguments = {
                    "file_path": file_path,
                    "sheet_name": sheet_name,
                    "range": range_ref,
                }
                result_text = str(
                    e.registry.call_tool(
                        tool_name,
                        arguments,
                    )
                )
                return {
                    "success": True,
                    "tool_name": tool_name,
                    "arguments": arguments,
                    "result_text": result_text,
                }
            except Exception:
                continue

        if "read_excel" in all_tools:
            arguments: dict[str, Any] = {"file_path": file_path, "sheet_name": sheet_name}
            try:
                from openpyxl.utils.cell import range_boundaries

                _, min_row, _, max_row = range_boundaries(range_ref)
                arguments["max_rows"] = max(1, int(max_row) - int(min_row) + 1)
            except Exception:
                pass
            try:
                result_text = str(
                    e.registry.call_tool(
                        "read_excel",
                        arguments,
                    )
                )
                return {
                    "success": True,
                    "tool_name": "read_excel",
                    "arguments": arguments,
                    "result_text": result_text,
                }
            except Exception as exc:
                return {"success": False, "error": f"补读失败: {exc}"}

        return {"success": False, "error": "未找到可用读取工具（read_sheet/read_excel）"}


    @staticmethod
    def _system_prompts_token_count(system_prompts: Sequence[str]) -> int:
        total = 0
        for prompt in system_prompts:
            total += TokenCounter.count_message({"role": "system", "content": prompt})
        return total

    @staticmethod
    def _shrink_context_text(text: str) -> str:
        normalized = (text or "").strip()
        if not normalized:
            return ""
        if len(normalized) <= _MIN_SYSTEM_CONTEXT_CHARS:
            return ""
        keep_chars = max(_MIN_SYSTEM_CONTEXT_CHARS, len(normalized) // 2)
        shrinked = normalized[:keep_chars].rstrip()
        if _SYSTEM_CONTEXT_SHRINK_MARKER in shrinked:
            return shrinked
        return f"{shrinked}\n{_SYSTEM_CONTEXT_SHRINK_MARKER}"

    @staticmethod
    def _minimize_skill_context(text: str) -> str:
        lines = [line for line in str(text or "").splitlines() if line.strip()]
        if not lines:
            return ""
        head = lines[0]
        second = lines[1] if len(lines) > 1 else ""
        minimal_parts = [head]
        if second:
            minimal_parts.append(second)
        minimal_parts.append("[Skillpack 正文已省略以适配上下文窗口]")
        return "\n".join(minimal_parts)

    def _build_rules_notice(self) -> str:
        """组装用户自定义规则文本，注入 system prompt。"""
        e = self._engine
        rm = getattr(e, "_rules_manager", None)
        if rm is None:
            return ""
        session_id = getattr(e, "_session_id", None)
        try:
            return rm.compose_rules_prompt(session_id)
        except Exception:
            logger.debug("规则注入失败", exc_info=True)
            return ""

    def _build_channel_notice(self) -> str:
        """构建渠道适配提示词。Bot 渠道注入格式/交互指南，Web 返回空。"""
        e = self._engine
        channel = getattr(e, "_channel_context", None)
        if not channel or channel == "web":
            return ""
        try:
            from excelmanus.channels.channel_profile import build_channel_notice
            return build_channel_notice(channel)
        except Exception:
            logger.debug("渠道提示词注入失败", exc_info=True)
            return ""

    @property
    def _channel_cache_key(self) -> str:
        """返回含渠道标识的缓存 key，防止不同渠道间缓存污染。"""
        channel = getattr(self._engine, "_channel_context", None) or "web"
        return f"channel:{channel}"

    def _build_meta_cognition_notice(self) -> str:
        """条件性注入进展反思提示，帮助 agent 在困境中调整策略。

        灵感来源：Metacognition is All You Need 论文。
        仅在特定退化条件下触发（接近迭代上限 / 连续失败 / 执行守卫已触发），
        否则返回空字符串（零 token 开销）。
        """
        e = self._engine
        state = e.state
        max_iter = e.config.max_iterations
        iteration = state.last_iteration_count
        failures = state.last_failure_count
        successes = state.last_success_count

        parts: list[str] = []
        _MAX_WARNINGS = 2

        # 条件 1（优先级最高）：接近迭代上限（已用 >= 60%）
        if max_iter > 0 and iteration >= max_iter * 0.6:
            parts.append(
                f"⚠️ 接近迭代上限（{iteration}/{max_iter}），"
                "请尽快完成任务或调用 ask_user。"
            )

        # 条件 2：连续失败 >= 3
        if len(parts) < _MAX_WARNINGS and failures >= 3 and successes == 0:
            parts.append(
                f"⚠️ 已连续失败 {failures} 次且无成功调用。建议："
                "1) 检查文件路径和 sheet 名是否正确 "
                "2) 简化操作步骤 "
                "3) 调用 ask_user 确认。"
            )

        # 条件 3：执行守卫曾触发（agent 曾给出建议而不执行）
        if len(parts) < _MAX_WARNINGS and state.execution_guard_fired and not state.has_write_tool_call:
            parts.append(
                "⚠️ 此前已触发执行守卫。请通过工具执行操作，不要仅给出文本建议。"
            )

        # 条件 4：推理质量不足（含完全沉默和级别偏低两种情况）
        silent = state.silent_call_count
        reasoned = state.reasoned_call_count
        if len(parts) < _MAX_WARNINGS and silent > 0 and silent >= reasoned:
            # 4a：完全沉默——工具调用未附带任何推理文本
            parts.append(
                f"⚠️ 本轮已有 {silent} 次工具调用未附带推理文本。"
                "请遵循 Think-Act 协议：工具调用前至少用 1 句话说明意图。"
                "（thinking 模型：推理可在 thinking 块中完成。）"
            )
            state.reasoning_upgrade_nudge_count += 1
        elif len(parts) < _MAX_WARNINGS and state.reasoning_level_mismatch_count >= 2:
            # 4b：有推理但深度不足——推荐 standard/complete 但实际偏轻量
            _rec = state.recommended_reasoning_level
            _hint_map = {
                "standard": "多步操作建议在工具调用前后各附 1-2 句观察与决策",
                "complete": "关键决策点建议说明观察到什么、分析了什么、为什么选择这个行动",
            }
            _hint = _hint_map.get(_rec, "")
            if _hint:
                parts.append(
                    f"⚠️ 当前任务推荐 {_rec} 级推理深度，但近期推理偏简略。"
                    f"{_hint}。"
                )
                state.reasoning_upgrade_nudge_count += 1

        if not parts:
            return ""

        return "## 进展反思\n" + "\n".join(parts)

    def _build_memory_notice(self) -> str:
        """构建语义记忆注入文本。

        当 engine 使用 semantic 模式时，从缓存的检索结果中读取；
        static 模式下记忆已在 system_prompt 中，返回空。
        """
        e = self._engine
        mode = getattr(e, "_memory_injection_mode", "static")
        if mode != "semantic":
            return ""
        text = getattr(e, "_relevant_memory_text", "")
        if not text or not text.strip():
            return ""
        return f"## 持久记忆（语义相关）\n{text.strip()}"

    def _build_session_history_notice(self) -> str:
        """构建历史会话摘要注入文本。

        仅在 session_turn <= 1 时注入（首轮/第二轮），后续轮次零开销。
        从 engine 缓存的语义检索结果中读取（由 _search_session_history 在
        chat() 中并行预取并存入 _relevant_session_history）。
        """
        e = self._engine
        if e._session_turn > 1:
            return ""
        text = getattr(e, "_relevant_session_history", "")
        if not text or not text.strip():
            return ""
        return text.strip()

    def _build_playbook_notice(self) -> str:
        """构建 Playbook 历史经验注入文本。

        读取 engine 中缓存的语义检索结果（由 _search_playbook 在 chat() 中
        并行预取并存入 _relevant_playbook_text）。无 playbook 或未启用时零开销。
        """
        e = self._engine
        text = getattr(e, "_relevant_playbook_text", "")
        if not text or not text.strip():
            return ""
        return text.strip()

    def _build_skill_hints_notice(self) -> str:
        """构建语义技能匹配提示文本。

        当 engine 使用 SemanticSkillRouter 时，从缓存的匹配结果中读取；
        无匹配时返回空。
        """
        e = self._engine
        text = getattr(e, "_relevant_skill_hints", "")
        if not text or not text.strip():
            return ""
        return text.strip()

    def _build_verification_fix_notice(self) -> str:
        """读取验证门控的待注入修复提示。正常情况零开销。"""
        e = self._engine
        gate = getattr(e, "_verification_gate", None)
        if gate is None:
            return ""
        return gate.pending_fix_notice

    def _build_explorer_report_notice(self) -> str:
        """将已缓存的 explorer 结构化报告格式化为 system prompt 注入文本。

        仅在 session_state 中存在 explorer_reports 时注入。
        输出紧凑摘要（文件/sheet 概览 + 关键发现），控制 token 开销。
        """
        e = self._engine
        state = getattr(e, "_state", None)
        if state is None:
            return ""
        reports: list[dict[str, Any]] = state.explorer_reports
        if not reports:
            return ""

        # 合并最近 3 份报告（去重文件路径），覆盖多文件上下文
        recent_reports = reports[-3:]
        parts: list[str] = ["## 数据探索概况"]

        # 收集所有摘要（去重）
        seen_summaries: set[str] = set()
        for report in recent_reports:
            summary = report.get("summary", "")
            if summary and summary not in seen_summaries:
                parts.append(summary)
                seen_summaries.add(summary)

        # 合并文件/Sheet 概览（按路径去重）
        seen_paths: set[str] = set()
        file_lines: list[str] = []
        for report in recent_reports:
            if len(file_lines) >= 8:
                break
            files = report.get("files", [])
            for f in files:
                path = f.get("path", "?")
                if path in seen_paths:
                    continue
                seen_paths.add(path)
                sheets = f.get("sheets", [])
                if sheets:
                    sheet_descs = [
                        f"{s.get('name', '?')}({s.get('rows', '?')}×{s.get('cols', '?')})"
                        for s in sheets[:8]
                    ]
                    file_lines.append(f"- `{path}`: {', '.join(sheet_descs)}")
                else:
                    file_lines.append(f"- `{path}`")
                if len(file_lines) >= 8:
                    break
        if file_lines:
            parts.append("文件概览：\n" + "\n".join(file_lines))

        # 合并关键发现（仅 high/medium severity，跨报告去重）
        seen_details: set[str] = set()
        finding_lines: list[str] = []
        for report in recent_reports:
            if len(finding_lines) >= 8:
                break
            findings = report.get("findings", [])
            for f in findings:
                if f.get("severity") not in ("high", "medium"):
                    continue
                detail = f.get("detail", "")
                if detail in seen_details:
                    continue
                seen_details.add(detail)
                finding_lines.append(f"- [{f.get('type', '?')}] {detail}")
                if len(finding_lines) >= 8:
                    break
        if finding_lines:
            parts.append("关键发现：\n" + "\n".join(finding_lines))

        # 建议（取最新报告的建议）
        rec = recent_reports[-1].get("recommendation", "")
        if rec:
            parts.append(f"建议：{rec}")

        return "\n\n".join(parts)

    def _build_post_write_verification_hint(self) -> str:
        """关键写入操作后注入即时验证提示，让主代理在下一轮迭代中自检。

        借鉴 Windsurf 的 post_write_code hook：不等到 finish_task 才验证，
        而是在写入后立即提醒主代理确认结果。

        仅在满足以下条件时触发（避免过度提示）：
        - 本轮有写入操作（write_operations_log 非空）
        - 写入涉及关键操作（跨 sheet、公式、大批量等）
        - 未处于 finish_task 阶段（避免和 verifier 重复）
        """
        e = self._engine
        state = getattr(e, "_state", None)
        if state is None:
            return ""
        ops = state.write_operations_log
        if not ops:
            return ""
        # finish_task 阶段不注入（verifier 会接管）
        if getattr(state, "finish_task_warned", False):
            return ""

        # 判断是否涉及关键操作（简单单次写入不提示，避免噪音）
        tool_names = {entry.get("tool_name", "") for entry in ops}
        sheets = {entry.get("sheet", "") for entry in ops if entry.get("sheet")}

        # 检测是否涉及新文件创建（summary 含创建/新建/生成等关键词）
        _NEW_FILE_KEYWORDS = ("创建", "新建", "生成", "新文件", "new file", "created")
        _has_new_file = any(
            any(kw in (entry.get("summary", "") or "").lower() for kw in _NEW_FILE_KEYWORDS)
            for entry in ops
        )

        is_critical = (
            _has_new_file                          # 新文件创建（必须回读验证）
            or len(ops) >= 3                       # 多步写入
            or len(sheets) > 1                     # 跨 sheet
            or "run_code" in tool_names            # 代码写入（风险高）
            or any("公式" in (entry.get("summary", "") or "") or "VLOOKUP" in (entry.get("summary", "") or "").upper() for entry in ops)
        )
        if not is_critical:
            return ""

        # 构建最近写入的简要摘要
        recent = ops[-3:]  # 最近 3 条
        summaries = []
        for entry in recent:
            tool = entry.get("tool_name", "?")
            fp = entry.get("file_path", "")
            sheet = entry.get("sheet", "")
            desc = entry.get("summary", "")[:60]
            parts = [tool]
            if fp:
                parts.append(fp.split("/")[-1])
            if sheet:
                parts.append(sheet)
            if desc:
                parts.append(desc)
            summaries.append(" · ".join(parts))

        # 新文件创建场景使用更强硬的提示
        if _has_new_file:
            return (
                "## ⚠ 新文件创建——必须回读验证\n"
                "你刚创建了新的 Excel 文件，stdout 输出**不能**替代回读验证：\n"
                + "\n".join(f"- {s}" for s in summaries)
                + "\n\n**必须**立即用 `read_excel` 或 `scan_excel_snapshot` 回读新文件，"
                "确认：① 文件已创建 ② sheet 结构正确 ③ 数据行数/列数与预期一致。"
                "\n验证通过后再继续下一步或汇报结果。"
            )

        return (
            "## ⚡ 写入后自检提示\n"
            "你刚执行了关键写入操作，建议在继续下一步前快速确认：\n"
            + "\n".join(f"- {s}" for s in summaries)
            + "\n\n用 `read_excel` 或 `scan_excel_snapshot` 抽检目标区域，"
            "确认数据正确后再继续。发现问题立即修正，不要等到 finish_task。"
        )

    def _build_scan_tool_hint(self) -> str:
        """当工作区有 Excel 文件但无 explorer 缓存时，自动预扫描并注入缓存。

        - 首次 chat 时检测到 Excel 文件且无缓存 → 自动调用 scan_excel_snapshot
        - 将扫描结果转换为 explorer_report 格式注入 session_state.explorer_reports
        - 同一轮的 _build_explorer_report_notice 即可接管展示
        - 如果自动扫描失败，降级为文本提示

        仅在第一轮（无缓存）时触发，后续轮次零开销。
        """
        e = self._engine
        state = getattr(e, "_state", None)
        if state is None:
            return ""
        if state.explorer_reports:
            return ""
        # 标记探索已启动，防止与 _auto_explore_after_scan 竞态重复注入
        if getattr(state, "_explore_in_progress", False):
            return ""
        # 直接扫描磁盘目录发现 Excel 文件（不依赖 FileRegistry._path_cache，
        # 因为后台 scan_workspace 可能尚未完成）
        workspace_root = getattr(e.config, "workspace_root", None) or getattr(e, "_workspace_root", None)
        if not workspace_root:
            return ""
        excel_paths = self._discover_excel_files_on_disk(str(workspace_root))
        if not excel_paths:
            return ""
        state._explore_in_progress = True  # type: ignore[attr-defined]

        # 尝试自动预扫描（最多扫描前 5 个文件，每个最多 500ms）
        auto_scanned = self._try_auto_prescan(excel_paths[:5], state)
        if auto_scanned:
            # 扫描成功 → 结果已注入 explorer_reports，_build_explorer_report_notice 会接管
            # 返回空字符串，不需要额外提示
            return ""

        # 自动扫描失败 → 降级为文本提示
        return (
            "## 💡 数据快速扫描提示\n"
            "工作区有 Excel 文件但尚无数据概况缓存。"
            "处理数据任务前，建议先调用 `scan_excel_snapshot` 一次性获取文件全貌"
            "（schema、列统计、质量信号、跨 Sheet 关联），"
            "或使用 `search_excel_values` 跨 Sheet 搜索特定值。"
        )

    @staticmethod
    def _try_auto_prescan(excel_paths: list[str], state: Any) -> bool:
        """尝试自动调用 scan_excel_snapshot 并将结果注入 explorer_reports。

        当成功扫描 ≥2 个文件时，额外调用 discover_file_relationships
        检测跨文件列关联，将结果作为 relationship 类型的 finding 注入。

        Returns:
            True 如果至少一个文件扫描成功并注入了缓存。
        """
        import json as _json
        try:
            from excelmanus.tools.data_tools import scan_excel_snapshot
        except ImportError:
            return False

        any_success = False
        scanned_paths: list[str] = []
        for path in excel_paths:
            try:
                raw = scan_excel_snapshot(file_path=path, max_sample_rows=200)
                scan = _json.loads(raw)
                if "error" in scan:
                    continue
                # 转换为 explorer_report 格式
                report = _convert_scan_to_explorer_report(scan, path)
                if report:
                    state.explorer_reports.append(report)
                    any_success = True
                    scanned_paths.append(path)
            except Exception:
                continue

        # 跨文件关系自动发现：≥2 个文件成功扫描时触发
        if len(scanned_paths) >= 2:
            try:
                from excelmanus.tools.data_tools import discover_file_relationships
                rel_raw = discover_file_relationships(
                    file_paths=scanned_paths, sample_rows=200,
                )
                rel_data = _json.loads(rel_raw)
                file_pairs = rel_data.get("file_pairs", [])
                if file_pairs:
                    # 将跨文件关系转换为 explorer_report findings
                    rel_findings: list[dict] = []
                    for pair in file_pairs:
                        fa = pair.get("file_a", "?")
                        fb = pair.get("file_b", "?")
                        for col_info in pair.get("shared_columns", []):
                            ca = col_info.get("col_a", "?")
                            cb = col_info.get("col_b", "?")
                            mt = col_info.get("match_type", "exact")
                            ov = col_info.get("overlap_ratio", 0)
                            rel = col_info.get("relationship", "")
                            sj = col_info.get("suggested_join", "")
                            # 构建富信息 detail
                            if ca == cb:
                                detail = (
                                    f"跨文件关联：{fa} 和 {fb} 共享列 '{ca}'"
                                    f"（{mt}匹配，值重叠率 {ov:.0%}）"
                                )
                            else:
                                detail = (
                                    f"跨文件关联：{fa}.{ca} ↔ {fb}.{cb}"
                                    f"（{mt}匹配，值重叠率 {ov:.0%}）"
                                )
                            if rel:
                                detail += f"，关系: {rel}"
                            if sj:
                                detail += f"，建议: {sj} join"
                            # 类型告警
                            tw = col_info.get("type_warning")
                            if tw:
                                detail += f"⚠️ {tw}"
                            rel_findings.append({
                                "type": "cross_file_relationship",
                                "severity": "high" if ov >= 0.7 else "medium",
                                "detail": detail,
                            })
                    if rel_findings:
                        # 收集合并提示摘要
                        merge_hints = rel_data.get("merge_hints", [])
                        rec_parts = [
                            "检测到跨文件列关联，可使用这些列作为合并/匹配的键列。",
                        ]
                        if merge_hints:
                            hint = merge_hints[0]
                            rec_parts.append(
                                f"推荐: {hint.get('pandas_hint', '')}"
                            )
                        rec_parts.append(
                            "如需更详细的关系分析，可调用 `discover_file_relationships` 工具。"
                            "跨文件数据操作建议用 run_code + pandas merge。"
                        )
                        rel_report: dict = {
                            "summary": rel_data.get("summary", ""),
                            "files": [],
                            "schema": {},
                            "findings": rel_findings[:10],
                            "recommendation": " ".join(rec_parts),
                        }
                        state.explorer_reports.append(rel_report)
            except Exception:
                logger.debug("跨文件关系自动发现失败，不影响正常使用", exc_info=True)

        # 注入已有文件组上下文：让 agent 知道用户已建立的逻辑分组
        if any_success:
            try:
                _reg = getattr(state, "_file_registry", None)
                if _reg is not None and hasattr(_reg, "list_groups"):
                    groups = _reg.list_groups()
                    if groups:
                        group_findings: list[dict] = []
                        for g in groups:
                            members = _reg.get_group_files(g.id)
                            if members:
                                names = [m["original_name"] for m in members]
                                group_findings.append({
                                    "type": "file_group",
                                    "severity": "info",
                                    "detail": (
                                        f"用户文件组「{g.name}」: {', '.join(names)}"
                                        + (f" ({g.description})" if g.description else "")
                                    ),
                                })
                        if group_findings:
                            group_report: dict = {
                                "summary": f"用户已创建 {len(groups)} 个文件组",
                                "files": [],
                                "schema": {},
                                "findings": group_findings,
                                "recommendation": (
                                    "同一文件组内的文件通常有逻辑关联，"
                                    "跨文件操作时优先在同组内匹配。"
                                ),
                            }
                            state.explorer_reports.append(group_report)
            except Exception:
                pass  # 组表可能尚未创建

        return any_success

    @staticmethod
    def _discover_excel_files_on_disk(workspace_root: str, *, max_files: int = 10) -> list[str]:
        """直接扫描磁盘发现 Excel 文件，不依赖 FileRegistry 缓存。

        轻量级目录遍历，仅收集文件路径（不打开文件），
        用于在 FileRegistry scan 尚未完成时作为 fallback。
        """
        import os as _os
        from pathlib import Path as _Path

        _SKIP = frozenset({
            ".git", ".venv", "node_modules", "__pycache__",
            ".worktrees", "dist", "build",
        })
        _EXCEL_EXTS = frozenset({".xlsx", ".xlsm", ".xls", ".xlsb"})
        root = _Path(workspace_root)
        results: list[str] = []
        try:
            for walk_root, dirs, files in _os.walk(root):
                dirs[:] = [d for d in dirs if d not in _SKIP]
                for name in files:
                    if name.startswith((".", "~$")):
                        continue
                    ext = _os.path.splitext(name)[1].lower()
                    if ext in _EXCEL_EXTS:
                        results.append(str(_Path(walk_root, name)))
                        if len(results) >= max_files:
                            return results
        except Exception:
            pass
        return results

    # 推理级别数值映射，用于比较和升降级
    _REASONING_LEVEL_ORDER: dict[str, int] = {
        "lightweight": 0,
        "standard": 1,
        "complete": 2,
    }

    # 推理字符数阈值（每个工具调用的平均字符数）：低于此值视为偏简略
    _REASONING_CHARS_THRESHOLDS: dict[str, int] = {
        "lightweight": 5,
        "standard": 30,
        "complete": 60,
    }

    @staticmethod
    def _compute_reasoning_level_static(route_result: Any) -> str:
        """根据任务上下文计算推荐推理级别（静态版本，兼容外部调用）。"""
        if route_result is None:
            return "standard"
        wh = getattr(route_result, "write_hint", "unknown") or "unknown"
        tags = set(getattr(route_result, "task_tags", []) or [])
        if wh == "read_only":
            return "lightweight"
        if tags & {"cross_sheet", "large_data", "multi_file"}:
            return "complete"
        if wh == "may_write":
            return "standard"
        return "lightweight"

    def _compute_reasoning_level(self, route_result: Any) -> str:
        """根据任务上下文 + 运行时状态动态计算推荐推理级别。

        在静态路由信号基础上，叠加运行时上下文进行升级：
        - 失败后需要更深入分析 → 升级
        - 任务接近尾声需要决策 → 升级
        - 多技能激活暗示复杂度 → 升级
        结果写入 state.recommended_reasoning_level 供闭环检测使用。
        """
        base = self._compute_reasoning_level_static(route_result)
        level = self._REASONING_LEVEL_ORDER.get(base, 1)

        e = self._engine
        state = e.state

        # 升级信号 1：上一迭代有失败 → 至少 standard（需要分析原因）
        if state.last_failure_count > 0 and level < 1:
            level = 1

        # 升级信号 2：连续失败 ≥ 2 → complete（需要深度分析和策略调整）
        if state.last_failure_count >= 2 and state.last_success_count == 0:
            level = 2

        # 升级信号 3：多技能激活暗示复杂任务 → 至少 standard
        if len(e._active_skills) >= 2 and level < 1:
            level = 1

        # 升级信号 4：接近迭代上限（≥50%）→ 至少 standard（需要收敛决策）
        max_iter = e.config.max_iterations
        iteration = state.last_iteration_count
        if max_iter > 0 and iteration >= max_iter * 0.5 and level < 1:
            level = 1

        # 降级信号：不做降级——静态基线是下限，运行时只升不降

        level_names = ["lightweight", "standard", "complete"]
        result = level_names[min(level, 2)]

        # 写入 state 供闭环检测使用
        state.recommended_reasoning_level = result
        return result

    def _build_runtime_metadata_line(self) -> str:
        """生成紧凑的运行时元数据行，让 agent 感知自身状态。

        一行即可让 agent 知道自己是什么模型、当前轮次、权限状态等。
        """
        e = self._engine
        parts: list[str] = [
            f"model={e.active_model}",
            f"turn={e._session_turn}/{e.config.max_iterations}",
            f"write_hint={e.state.current_write_hint}",
            f"fullaccess={'on' if e.full_access_enabled else 'off'}",
            f"backup={'on' if e.workspace.transaction_enabled else 'off'}",
            f"mcp={e.mcp_connected_count}",
            f"subagent={'on' if e._subagent_enabled else 'off'}",
            f"vision={'on' if e._is_vision_capable else 'off'}",
            f"chat_mode={getattr(e, '_current_chat_mode', 'write')}",
            f"channel={getattr(e, '_channel_context', None) or 'web'}",
            f"skills={len(e._active_skills)}",
        ]
        _reg = e.file_registry
        if _reg is not None:
            try:
                parts.append(f"files={len(_reg.list_all())}")
            except Exception:
                pass
        _route = getattr(e, '_last_route_result', None)
        _reasoning_level = self._compute_reasoning_level(_route)
        parts.append(f"reasoning={_reasoning_level}")
        return "Runtime: " + " | ".join(parts)

    def _build_stable_system_prompt(self) -> str:
        """构建仅含稳定前缀的 system prompt（用于 cache 预热等场景）。

        包含: identity + rules + channel + access + backup + mcp_context。
        这些内容在 session 生命周期内不变，适合作为 Anthropic prompt cache 前缀。
        """
        e = self._engine
        prompt = e.memory.system_prompt

        _turn = e._session_turn
        if _turn != self._turn_notice_cache_key:
            self._turn_notice_cache.clear()
            self._turn_notice_cache_key = _turn
        _nc = self._turn_notice_cache

        def _cached_notice(key: str, builder: Any) -> str:
            val = _nc.get(key)
            if val is not None:
                return val
            val = builder()
            _nc[key] = val
            return val

        rules_notice = _cached_notice("rules", self._build_rules_notice)
        if rules_notice:
            prompt = prompt + "\n\n" + rules_notice

        channel_notice = _cached_notice(self._channel_cache_key, self._build_channel_notice)
        if channel_notice:
            prompt = prompt + "\n\n" + channel_notice

        access_notice = _cached_notice("access", self._build_access_notice)
        if access_notice:
            prompt = prompt + "\n\n" + access_notice

        backup_notice = _cached_notice("backup", self._build_backup_notice)
        if backup_notice:
            prompt = prompt + "\n\n" + backup_notice

        mcp_context = _cached_notice("mcp", self._build_mcp_context_notice)
        if mcp_context:
            prompt = prompt + "\n\n" + mcp_context

        return prompt

    def _prepare_system_prompts_for_request(
        self,
        skill_contexts: list[str],
        *,
        route_result: SkillMatchResult | None = None,
    ) -> tuple[list[str], str | None]:
        """构建用于本轮请求的 system prompts，并在必要时压缩上下文。

        Prompt Cache 分层优化：
        - 稳定前缀（identity + rules + channel + access + backup + mcp）作为
          独立 system 消息，由 Provider 设置 cache_control breakpoint，session
          内保持不变以最大化 Anthropic prompt cache 命中率。
        - 动态内容（strategies + runtime + task_plan 等）作为第二个 system 消息，
          每请求/迭代可自由变化而不影响前缀 cache。
        """
        e = self._engine

        # ── C2: 轮次级静态 notice 缓存失效检测 ──
        _turn = e._session_turn
        if _turn != self._turn_notice_cache_key:
            self._turn_notice_cache.clear()
            self._turn_notice_cache_key = _turn
        _nc = self._turn_notice_cache

        def _cached_notice(key: str, builder: Any) -> str:
            val = _nc.get(key)
            if val is not None:
                return val
            val = builder()
            _nc[key] = val
            return val

        # ── 稳定前缀（session 生命周期内不变，Anthropic cache breakpoint 区域） ──
        stable_prompt = self._build_stable_system_prompt()

        # 从缓存中提取各 notice 用于快照采集（已在 _build_stable_system_prompt 中填充）
        rules_notice = _nc.get("rules", "")
        channel_notice = _nc.get(self._channel_cache_key, "")
        access_notice = _nc.get("access", "")
        backup_notice = _nc.get("backup", "")
        mcp_context = _nc.get("mcp", "")

        # ── 分层路由：chitchat 快速通道 ──
        # 仅保留 identity + rules + channel，跳过 access/backup/mcp 等不相关段，
        # 节省 ~5-10K tokens 的系统提示开销。
        _route_mode = getattr(route_result, "route_mode", "") if route_result else ""
        if _route_mode == "chitchat":
            # 构建真正精简的 chitchat prompt（identity + rules + channel）
            # 不使用 stable_prompt 因为它还包含 access/backup/mcp
            _chitchat_prompt = e.memory.system_prompt
            if rules_notice:
                _chitchat_prompt = _chitchat_prompt + "\n\n" + rules_notice
            if channel_notice:
                _chitchat_prompt = _chitchat_prompt + "\n\n" + channel_notice
            logger.debug(
                "chitchat 快速通道: 仅注入 identity+rules+channel (%.0f chars, 省 %.0f chars)",
                len(_chitchat_prompt), len(stable_prompt) - len(_chitchat_prompt),
            )
            return [_chitchat_prompt], None

        # ── 动态内容（每请求/每迭代可能变化，作为独立 system 消息） ──
        dynamic_prompt = ""

        # 统一文件全景 + CoW 路径映射（turn 内缓存，写入时标脏重建）
        file_registry_notice = self._build_file_registry_notice()
        if file_registry_notice:
            dynamic_prompt = dynamic_prompt + file_registry_notice

        # 注入任务策略（PromptComposer strategies，同一轮次内不变）
        _strategy_text_captured = ""
        if e._prompt_composer is not None and route_result is not None:
            try:
                from excelmanus.prompt_composer import PromptContext as _PCtx
                _p_ctx = _PCtx(
                    chat_mode=getattr(e, "_current_chat_mode", "write"),
                    write_hint=route_result.write_hint or "unknown",
                    sheet_count=route_result.sheet_count,
                    total_rows=route_result.max_total_rows,
                    task_tags=list(route_result.task_tags),
                    full_access=e.full_access_enabled,
                )
                _strategy_text = e._prompt_composer.compose_strategies_text(
                    _p_ctx, variables=getattr(e, "_runtime_vars", None),
                )
                if _strategy_text:
                    dynamic_prompt = dynamic_prompt + "\n\n" + _strategy_text if dynamic_prompt else _strategy_text
                    _strategy_text_captured = _strategy_text
            except Exception:
                logger.debug("策略注入失败，跳过", exc_info=True)

        # D2: 语义记忆动态注入（替代 system_prompt 中的全量静态注入）
        memory_notice = self._build_memory_notice()
        if memory_notice:
            dynamic_prompt = dynamic_prompt + "\n\n" + memory_notice if dynamic_prompt else memory_notice

        # 历史会话摘要注入（仅首轮/第二轮，后续零开销）
        session_history_notice = self._build_session_history_notice()
        if session_history_notice:
            dynamic_prompt = dynamic_prompt + "\n\n" + session_history_notice if dynamic_prompt else session_history_notice

        # Playbook 历史经验注入（半静态，session 内稳定，每轮检查一次）
        playbook_notice = self._build_playbook_notice()
        if playbook_notice:
            dynamic_prompt = dynamic_prompt + "\n\n" + playbook_notice if dynamic_prompt else playbook_notice

        # D3: 语义技能匹配提示（embedding 检索相关 skillpack，帮助 agent 推荐技能）
        skill_hints = self._build_skill_hints_notice()
        if skill_hints:
            dynamic_prompt = dynamic_prompt + "\n\n" + skill_hints if dynamic_prompt else skill_hints

        _hook_context_captured = ""
        if e._transient_hook_contexts:
            hook_context = "\n".join(e._transient_hook_contexts).strip()
            e._transient_hook_contexts.clear()
            if hook_context:
                _hc = "## Hook 上下文\n" + hook_context
                dynamic_prompt = dynamic_prompt + "\n\n" + _hc if dynamic_prompt else _hc
                _hook_context_captured = hook_context

        # 注入运行时元数据（每轮/每迭代变化）
        runtime_line = self._build_runtime_metadata_line()
        dynamic_prompt = dynamic_prompt + "\n\n" + runtime_line if dynamic_prompt else runtime_line

        # 注入任务清单状态 + 计划文档引用（每迭代重建，不缓存）
        task_plan_notice = self._build_task_plan_notice()
        if task_plan_notice:
            dynamic_prompt = dynamic_prompt + "\n\n" + task_plan_notice

        # 条件性注入进展反思（仅在退化条件下触发，正常情况零开销）
        meta_cognition = self._build_meta_cognition_notice()
        if meta_cognition:
            dynamic_prompt = dynamic_prompt + "\n\n" + meta_cognition

        # 验证门控修复提示（验证失败时注入，正常情况零开销）
        verification_fix_notice = self._build_verification_fix_notice()
        if verification_fix_notice:
            dynamic_prompt = dynamic_prompt + "\n\n" + verification_fix_notice

        # R8: 写入后即时验证提示（借鉴 Windsurf post_write hooks）
        post_write_hint = self._build_post_write_verification_hint()
        if post_write_hint:
            dynamic_prompt = dynamic_prompt + "\n\n" + post_write_hint

        # R7: 自动预扫描（必须在 R6 之前，以便注入缓存后被 R6 读取）
        scan_hint = self._build_scan_tool_hint()

        # R6: 注入已缓存的 explorer 结构化报告摘要
        explorer_notice = self._build_explorer_report_notice()
        if explorer_notice:
            dynamic_prompt = dynamic_prompt + "\n\n" + explorer_notice

        # R7 降级提示：自动扫描失败时的文本提示
        if scan_hint:
            dynamic_prompt = dynamic_prompt + "\n\n" + scan_hint

        window_perception_context = self._build_window_perception_notice()
        window_at_tail = e._effective_window_return_mode() != "enriched"
        current_skill_contexts = [
            ctx for ctx in skill_contexts if isinstance(ctx, str) and ctx.strip()
        ]

        # ── 采集提示词注入快照 ──
        _snapshot_components: dict[str, str] = {}
        if rules_notice:
            _snapshot_components["user_rules"] = rules_notice
        if channel_notice:
            _snapshot_components["channel_notice"] = channel_notice
        if access_notice:
            _snapshot_components["access_notice"] = access_notice
        if backup_notice:
            _snapshot_components["backup_notice"] = backup_notice
        if file_registry_notice:
            _snapshot_components["file_registry_notice"] = file_registry_notice
        if mcp_context:
            _snapshot_components["mcp_context"] = mcp_context
        if runtime_line:
            _snapshot_components["runtime_metadata"] = runtime_line
        if _strategy_text_captured:
            _snapshot_components["prompt_strategies"] = _strategy_text_captured
        if _hook_context_captured:
            _snapshot_components["hook_context"] = _hook_context_captured
        if task_plan_notice:
            _snapshot_components["task_plan_notice"] = task_plan_notice
        if memory_notice:
            _snapshot_components["memory_notice"] = memory_notice
        if session_history_notice:
            _snapshot_components["session_history_notice"] = session_history_notice
        if playbook_notice:
            _snapshot_components["playbook_notice"] = playbook_notice
        if verification_fix_notice:
            _snapshot_components["verification_fix_notice"] = verification_fix_notice
        if explorer_notice:
            _snapshot_components["explorer_report_notice"] = explorer_notice
        if window_perception_context:
            _snapshot_components["window_perception_context"] = window_perception_context
        for idx, ctx in enumerate(current_skill_contexts):
            _snapshot_components[f"skill_context_{idx}"] = ctx

        _injection_summary: list[dict[str, Any]] = [
            {"name": name, "chars": len(text)}
            for name, text in _snapshot_components.items()
        ]
        _content_fingerprint = _hashlib.md5(
            _json.dumps(
                _snapshot_components, sort_keys=True, ensure_ascii=False,
            ).encode()
        ).hexdigest()[:12]

        _snapshots = e.state.prompt_injection_snapshots
        _last_fp = _snapshots[-1].get("_fingerprint") if _snapshots else None

        if _last_fp != _content_fingerprint:
            _snapshots.append({
                "session_turn": e._session_turn,
                "summary": _injection_summary,
                "total_chars": sum(len(t) for t in _snapshot_components.values()),
                "components": _snapshot_components,
                "_fingerprint": _content_fingerprint,
            })
        else:
            _snapshots.append({
                "session_turn": e._session_turn,
                "_ref": _content_fingerprint,
            })

        def _compose_prompts() -> list[str]:
            mode = e._effective_system_mode()
            if mode == "merge":
                # merge 模式下仍然保持 stable 和 dynamic 分离以支持 cache
                merged_dynamic_parts = [dynamic_prompt] if dynamic_prompt else []
                merged_dynamic_parts.extend(current_skill_contexts)
                if window_perception_context:
                    if window_at_tail:
                        merged_dynamic_parts.append(window_perception_context)
                    else:
                        merged_dynamic_parts.insert(0, window_perception_context)
                result = [stable_prompt]
                if merged_dynamic_parts:
                    result.append("\n\n".join(merged_dynamic_parts))
                return result

            # multi 模式：stable 前缀 + dynamic + skill_contexts + window
            prompts = [stable_prompt]
            if dynamic_prompt:
                prompts.append(dynamic_prompt)
            if window_at_tail:
                prompts.extend(current_skill_contexts)
                if window_perception_context:
                    prompts.append(window_perception_context)
            else:
                if window_perception_context:
                    prompts.append(window_perception_context)
                prompts.extend(current_skill_contexts)
            return prompts

        threshold = max(1, int(e.max_context_tokens * 0.9))
        prompts = _compose_prompts()

        # O3+O4: 基于内容指纹的 token 计数缓存
        _cached_count = self._token_count_cache.get(_content_fingerprint)
        if _cached_count is not None:
            total_tokens = _cached_count
        else:
            total_tokens = self._system_prompts_token_count(prompts)
            # LRU 淘汰（最近最少使用）
            if len(self._token_count_cache) >= self._TOKEN_COUNT_CACHE_MAX:
                self._token_count_cache.pop(next(iter(self._token_count_cache)))
            self._token_count_cache[_content_fingerprint] = total_tokens

        if total_tokens <= threshold:
            return prompts, None

        if window_perception_context:
            window_perception_context = self._shrink_context_text(window_perception_context)
            prompts = _compose_prompts()
            total_tokens = self._system_prompts_token_count(prompts)
            if total_tokens <= threshold:
                return prompts, None
            window_perception_context = ""

        for idx in range(len(current_skill_contexts) - 1, -1, -1):
            minimized = self._minimize_skill_context(current_skill_contexts[idx])
            if minimized and minimized != current_skill_contexts[idx]:
                current_skill_contexts[idx] = minimized
                prompts = _compose_prompts()
                total_tokens = self._system_prompts_token_count(prompts)
                if total_tokens <= threshold:
                    return prompts, None

        while current_skill_contexts:
            current_skill_contexts.pop()
            prompts = _compose_prompts()
            total_tokens = self._system_prompts_token_count(prompts)
            if total_tokens <= threshold:
                return prompts, None

        if self._system_prompts_token_count(prompts) > threshold:
            return [], (
                "系统上下文过长，已无法在当前上下文窗口内继续执行。"
                "请减少附加上下文或拆分任务后重试。"
            )
        return prompts, None


    def _build_task_plan_notice(self) -> str:
        """构建计划文档引用 + 任务清单状态，注入主 system prompt 动态区域。

        仅当存在活跃 TaskList 时生成（零开销原则）。
        每迭代重建，不缓存（task_update 会改变状态）。
        """
        e = self._engine
        task_list = e._task_store.current
        if task_list is None:
            return ""

        parts: list[str] = ["## 当前计划与任务清单"]

        # 计划文档路径引用
        plan_path = e._task_store.plan_file_path
        if plan_path:
            parts.append(f"📄 计划文档: `{plan_path}`")

        # 任务清单状态（复用 _build_task_list_status_notice 的逻辑）
        parts.append(self._build_task_list_status_notice())

        return "\n".join(parts)

    def _build_task_list_status_notice(self) -> str:
        """构建当前任务清单状态摘要，用于注入 system prompt。"""
        e = self._engine
        task_list = e._task_store.current
        if task_list is None:
            return ""
        lines = [f"### 任务清单状态「{task_list.title}」"]
        for idx, item in enumerate(task_list.items):
            status_icon = {
                TaskStatus.PENDING: "🔵",
                TaskStatus.IN_PROGRESS: "🟡",
                TaskStatus.COMPLETED: "✅",
                TaskStatus.FAILED: "❌",
            }.get(item.status, "⬜")
            lines.append(f"- {status_icon} #{idx} {item.title} ({item.status.value})")
        return "\n".join(lines)

    def _has_incomplete_tasks(self) -> bool:
        """检查任务清单是否存在未完成的子任务。"""
        e = self._engine
        task_list = e._task_store.current
        if task_list is None:
            return False
        return any(
            item.status in (TaskStatus.PENDING, TaskStatus.IN_PROGRESS)
            for item in task_list.items
        )

    def _has_verification_failed_blocking_task(self) -> bool:
        """检查任务序列中是否有带验证条件的失败任务阻断后续步骤。

        仅当失败任务具有 verification_criteria 时视为验证失败阻断；
        无验证条件的操作失败不阻断（保持现有容错行为）。
        """
        e = self._engine
        task_list = e._task_store.current
        if task_list is None:
            return False
        for item in task_list.items:
            if item.status == TaskStatus.FAILED and item.verification_criteria:
                return True
            if item.status in (TaskStatus.PENDING, TaskStatus.IN_PROGRESS):
                break
        return False

    async def _auto_continue_task_loop(
        self,
        route_result: "SkillMatchResult",
        on_event: EventCallback | None,
        initial_result: ChatResult,
    ) -> ChatResult:
        """计划审批后自动续跑：若任务清单仍有未完成子任务，自动注入续跑消息。

        集成 Fix-Verify 循环：验证失败时尝试修复→重跑→重验，
        而非直接阻断。修复次数用尽后才真正阻断。
        """
        from excelmanus.engine import ChatResult
        e = self._engine
        result = initial_result
        for attempt in range(_MAX_PLAN_AUTO_CONTINUE):
            if not self._has_incomplete_tasks():
                break

            # ── Fix-Verify 循环：验证失败时尝试修复 ──
            if self._has_verification_failed_blocking_task():
                fix_result = await self._attempt_fix_verify(
                    route_result, on_event, result,
                )
                if fix_result is not None:
                    result = fix_result
                    # 修复后重新检查：可能还有未完成任务需要继续
                    continue
                # 修复失败或次数用尽，阻断
                logger.info("自动续跑停止：验证失败且修复次数已耗尽")
                break

            # 遇到待确认/待回答/待审批时不续跑，交还用户控制
            if e.approval.has_pending():
                break
            if e._question_flow.has_pending():
                break
            if e._pending_plan is not None:
                break

            logger.info(
                "自动续跑 %d/%d：任务清单仍有未完成子任务",
                attempt + 1,
                _MAX_PLAN_AUTO_CONTINUE,
            )
            e.memory.add_user_message(
                "请继续执行剩余的未完成子任务，直到全部完成。"
            )
            e._set_window_perception_turn_hints(
                user_message="继续执行剩余子任务",
                is_new_task=False,
            )
            resumed = await e._tool_calling_loop(route_result, on_event)
            result = ChatResult(
                reply=f"{result.reply}\n\n{resumed.reply}",
                tool_calls=list(result.tool_calls) + list(resumed.tool_calls),
                iterations=result.iterations + resumed.iterations,
                truncated=resumed.truncated,
                prompt_tokens=result.prompt_tokens + resumed.prompt_tokens,
                completion_tokens=result.completion_tokens + resumed.completion_tokens,
                total_tokens=result.total_tokens + resumed.total_tokens,
            )
        return result

    async def _attempt_fix_verify(
        self,
        route_result: "SkillMatchResult",
        on_event: EventCallback | None,
        current_result: ChatResult,
    ) -> ChatResult | None:
        """尝试一次 Fix-Verify 修复循环。

        Returns:
            ChatResult — 修复成功后的累积结果
            None — 无法修复（次数用尽或无修复目标）
        """
        from excelmanus.engine import ChatResult
        e = self._engine
        gate = getattr(e, "_verification_gate", None)
        if gate is None:
            return None

        task_index, criteria = gate.get_failed_verification_task()
        if task_index < 0 or criteria is None:
            return None

        if not gate.can_fix_verify(task_index):
            logger.info("任务 #%d 修复次数已耗尽", task_index)
            return None

        # 构建修复消息并注入
        fix_msg = gate.prepare_fix_message(task_index, criteria)
        gate.record_fix_attempt(task_index)

        logger.info(
            "Fix-Verify: 任务 #%d 开始修复尝试 (%s)",
            task_index, criteria.check_type,
        )

        # 将失败任务重置为 IN_PROGRESS（允许 agent 重试）
        task_list = e._task_store.current
        if task_list is not None and task_index < len(task_list.items):
            task_list.items[task_index].force_retry()

        e.memory.add_user_message(fix_msg)
        e._set_window_perception_turn_hints(
            user_message=fix_msg,
            is_new_task=False,
        )

        resumed = await e._tool_calling_loop(route_result, on_event)
        merged = ChatResult(
            reply=f"{current_result.reply}\n\n{resumed.reply}",
            tool_calls=list(current_result.tool_calls) + list(resumed.tool_calls),
            iterations=current_result.iterations + resumed.iterations,
            truncated=resumed.truncated,
            prompt_tokens=current_result.prompt_tokens + resumed.prompt_tokens,
            completion_tokens=current_result.completion_tokens + resumed.completion_tokens,
            total_tokens=current_result.total_tokens + resumed.total_tokens,
        )

        # 检查修复后任务是否仍为 FAILED
        if task_list is not None and task_index < len(task_list.items):
            if task_list.items[task_index].status == TaskStatus.FAILED:
                logger.info("Fix-Verify: 任务 #%d 修复后仍失败", task_index)
                if not gate.can_fix_verify(task_index):
                    return None  # 次数用尽
                # 还有修复机会，返回累积结果让外层循环再试
                return merged

        return merged

    # 对原始文件本身执行破坏性操作的工具。
    # 这些工具绕过备份重定向 — 审批门禁已提供安全保障，
    # 重定向会静默创建一个用户从未打算使用的一次性备份副本。
    _DESTRUCTIVE_NO_REDIRECT_TOOLS = frozenset({"delete_file"})

    def _redirect_backup_paths(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        """备份模式下重定向工具参数中的文件路径到备份副本。"""
        e = self._engine
        tx = e.transaction
        if not e.workspace.transaction_enabled or tx is None:
            return arguments

        if tool_name in self._DESTRUCTIVE_NO_REDIRECT_TOOLS:
            return arguments

        from excelmanus.tools.policy import (
            AUDIT_TARGET_ARG_RULES_ALL,
            AUDIT_TARGET_ARG_RULES_FIRST,
            READ_ONLY_SAFE_TOOLS,
        )

        path_fields: list[str] = []
        all_fields = AUDIT_TARGET_ARG_RULES_ALL.get(tool_name)
        if all_fields is not None:
            path_fields.extend(all_fields)
        else:
            first_fields = AUDIT_TARGET_ARG_RULES_FIRST.get(tool_name)
            if first_fields is not None:
                path_fields.extend(first_fields)

        if tool_name in READ_ONLY_SAFE_TOOLS:
            for key in ("file_path", "path", "directory"):
                if key in arguments and key not in path_fields:
                    path_fields.append(key)

        if not path_fields:
            return arguments

        redirected = dict(arguments)
        for field_name in path_fields:
            raw = arguments.get(field_name)
            if raw is None:
                continue
            raw_str = str(raw).strip()
            if not raw_str:
                continue
            try:
                if tool_name in READ_ONLY_SAFE_TOOLS:
                    redirected[field_name] = tx.resolve_read(raw_str)
                else:
                    redirected[field_name] = tx.stage_for_write(raw_str)
            except ValueError:
                pass
        return redirected

    def _build_access_notice(self) -> str:
        """当 fullaccess 关闭时，生成权限限制说明注入 system prompt。"""
        e = self._engine
        if e.full_access_enabled:
            return ""
        restricted = e._restricted_code_skillpacks
        if not restricted:
            return ""
        skill_list = "、".join(sorted(restricted))
        return (
            f"【权限提示】当前 fullaccess 权限处于关闭状态。"
            f"以下技能需要 fullaccess 权限才能激活：{skill_list}。"
            f"注意：run_code 工具已配备代码策略引擎（自动风险分级 + 运行时沙盒），"
            f"安全代码（GREEN/YELLOW 等级）可直接使用，无需 fullaccess 权限。"
            f"仅涉及高风险操作（如 subprocess、exec）的代码需要用户确认。"
        )

    def _build_backup_notice(self) -> str:
        """备份模式（workspace transaction）启用时，生成提示词注入。

        注意：此文本必须在整个 turn 内保持稳定（不含动态计数等），
        以确保系统提示前缀一致性，最大化 provider prompt cache 命中率。
        """
        e = self._engine
        if not e.workspace.transaction_enabled or e.transaction is None:
            return ""
        lines = [
            "## ⚠️ 工作区事务模式已启用",
            "所有文件写入操作已自动重定向到 `outputs/backups/` 下的工作副本，原始文件不会被修改。",
            "",
            "**存储结构**：",
            "- `outputs/backups/` — 当前会话的工作副本（staged files），读写操作透明重定向",
            "- `outputs/.versions/` — 文件版本快照（自动管理，支持精确回滚）",
            "",
            "**用户可用命令**：",
            "- `/backup apply` — 将工作副本应用到原文件",
            "- `/backup rollback` — 丢弃所有修改，恢复原始文件",
            "- `/backup list` — 查看当前暂存的文件列表",
        ]
        # 优先从 FileRegistry 获取版本追踪信息
        _reg = getattr(e, "_file_registry", None)
        if _reg is not None and getattr(_reg, "has_versions", False):
            tracked = _reg.list_all_tracked()
            if tracked:
                lines.append(f"\n当前有 {len(tracked)} 个文件受版本追踪保护。")
        else:
            fvm = getattr(e, "_fvm", None)
            if fvm is not None:
                tracked = fvm.list_all_tracked()
                if tracked:
                    lines.append(f"\n当前有 {len(tracked)} 个文件受版本追踪保护。")
        return "\n".join(lines)

    def _build_mcp_context_notice(self) -> str:
        """生成已连接 MCP Server 的概要信息，注入 system prompt。"""
        e = self._engine
        servers = e._mcp_manager.get_server_info()
        if not servers:
            return ""
        lines = ["## MCP 扩展能力"]
        for srv in servers:
            name = srv["name"]
            tool_count = srv.get("tool_count", 0)
            tool_names = srv.get("tools", [])
            tools_str = "、".join(tool_names) if tool_names else "无"
            lines.append(f"- **{name}**（{tool_count} 个工具）：{tools_str}")
        lines.append(
            "以上 MCP 工具已注册，工具名带 `mcp_{server}_` 前缀，可直接调用。"
            "当用户询问你有哪些 MCP 或外部能力时，据此如实回答。\n"
            "**工具优先级**：当内置工具（不带 `mcp_` 前缀）能完成任务时，"
            "优先使用内置工具。MCP 工具仅在内置工具无法覆盖的场景下使用。"
        )
        return "\n".join(lines)

    def _build_file_registry_notice(self) -> str:
        """统一文件全景 + 语义相关文件摘要 + CoW 路径映射注入。

        使用 FileRegistry.build_panorama() 作为唯一数据源。
        panorama 部分使用脏标记缓存（写入时标脏），CoW 映射始终实时追加。
        当 SemanticRegistry 可用时，额外注入与用户查询语义相关的文件摘要。
        """
        e = self._engine
        parts: list[str] = []

        # ── 语义相关文件摘要（embedding 检索结果，优先注入）──
        _relevant_summary = getattr(e, "_relevant_file_summary", "")
        if _relevant_summary and _relevant_summary.strip():
            parts.append(_relevant_summary.strip())

        # ── 文件全景：FileRegistry（turn 内缓存，写入时标脏重建）──
        _turn = e._session_turn
        if _turn != self._panorama_cache_turn:
            self._panorama_dirty = True
            self._panorama_cache_turn = _turn

        if self._panorama_dirty:
            _reg = e.file_registry
            if _reg is not None:
                panorama = _reg.build_panorama()
                self._panorama_cache = panorama if panorama else None
            else:
                self._panorama_cache = None
            self._panorama_dirty = False

        if self._panorama_cache:
            parts.append(self._panorama_cache)

        # ── CoW 路径映射（始终实时追加，turn 内可增长） ──
        cow_registry: dict[str, str] = {}
        try:
            if hasattr(e.state, "get_cow_mappings"):
                mappings = e.state.get_cow_mappings()
                if isinstance(mappings, dict):
                    cow_registry = mappings
        except Exception:
            cow_registry = {}
        if cow_registry:
            cow_lines = [
                "## ⚠️ 文件保护路径映射（CoW）",
                "以下原始文件受保护，已自动复制到 outputs/ 目录。",
                "**你必须使用副本路径进行所有后续读取和写入操作，严禁访问原始路径。**",
                "",
                "| 原始路径（禁止访问） | 副本路径（请使用） |",
                "|---|---|",
            ]
            for src, dst in cow_registry.items():
                cow_lines.append(f"| `{src}` | `{dst}` |")
            cow_lines.append("")
            cow_lines.append(
                "如果你在工具参数中使用了原始路径，系统会自动重定向到副本，"
                "但请主动记住并使用副本路径以避免混淆。"
            )
            parts.append("\n".join(cow_lines))

        return "\n\n".join(parts)

    def mark_panorama_dirty(self) -> None:
        """标记文件全景缓存为脏，下次构建时重建 panorama。

        应在工具写入操作成功后调用（_record_workspace_write_action 等）。
        """
        self._panorama_dirty = True

    def mark_window_notice_dirty(self) -> None:
        """标记窗口感知 notice 缓存为脏，下次构建时重新渲染。

        应在工具执行修改窗口状态后调用（observe_write_tool_call、
        observe_code_execution 等），以及每个新 turn 开始时隐式失效。
        """
        self._window_notice_dirty = True

    def _build_window_perception_notice(self) -> str:
        """渲染窗口感知系统注入文本。

        注意：build_system_notice 内部会推进窗口生命周期（idle 计数器、
        BG/IDLE 转换），属于有副作用的方法，不能缓存。
        mark_window_notice_dirty 基础设施保留，待未来 lifecycle 与 render 解耦后启用。
        """
        e = self._engine
        requested_mode = e._requested_window_return_mode()
        return e._window_perception.build_system_notice(
            mode=requested_mode,
            model_id=e.active_model,
        )
    def _build_tool_index_notice(
        self,
        *,
        compact: bool = False,
        max_tools_per_category: int = 8,
    ) -> str:
        """生成工具分类索引，注入 system prompt。

        所有工具始终暴露完整 schema，统一按类别展示。
        """
        from excelmanus.tools.policy import TOOL_CATEGORIES, TOOL_SHORT_DESCRIPTIONS

        _CATEGORY_LABELS: dict[str, str] = {
            "data_read": "数据读取",
            "sheet": "工作表操作",
            "file": "文件操作",
            "code": "代码执行",
            "macro": "声明式复合操作",
            "vision": "图片视觉",
        }

        limit = max(1, int(max_tools_per_category))
        registered = set(self._all_tool_names())
        category_lines: list[str] = []

        def _format_tool_list(tools: Sequence[str], *, with_desc: bool = False) -> str:
            visible = list(tools[:limit])
            hidden = max(0, len(tools) - len(visible))
            if not visible:
                return ""
            if with_desc:
                parts_list = []
                for t in visible:
                    desc = TOOL_SHORT_DESCRIPTIONS.get(t)
                    parts_list.append(f"{t}({desc})" if desc else t)
                text = ", ".join(parts_list)
            else:
                text = ", ".join(visible)
            if hidden > 0:
                text += f" (+{hidden})"
            return text

        for cat, tools in TOOL_CATEGORIES.items():
            label = _CATEGORY_LABELS.get(cat, cat)
            available = [t for t in tools if t in registered]
            if not available:
                continue
            code_suffix = " [需 fullaccess]" if cat == "code" else ""
            line = _format_tool_list(available, with_desc=True)
            if line:
                category_lines.append(f"- {label}：{line}{code_suffix}")

        if not category_lines:
            return ""

        parts: list[str] = ["## 工具索引"]
        parts.append("可用工具（所有工具参数已完整可见，直接调用）：")
        parts.extend(category_lines)
        parts.append(
            "\n⚠️ 写入类任务（公式、数据、格式）必须调用工具执行，"
            "不得以文本建议替代实际写入操作。"
        )
        return "\n".join(parts)



    def _set_window_perception_turn_hints(
        self,
        *,
        user_message: str,
        is_new_task: bool,
        task_tags: tuple[str, ...] | None = None,
    ) -> None:
        """设置窗口感知层的当前轮提示。"""
        e = self._engine
        clipped_hint = self._clip_window_hint(user_message)
        e._window_perception.set_turn_hints(
            is_new_task=is_new_task,
            user_intent_summary=clipped_hint,
            agent_recent_output=self._clip_window_hint(self._latest_assistant_text()),
            turn_intent_hint=clipped_hint,
            task_tags=task_tags,
        )

    def _latest_assistant_text(self) -> str:
        """提取最近一条 assistant 文本。"""
        e = self._engine
        for item in reversed(e.memory.get_messages()):
            if str(item.get("role", "")).strip() != "assistant":
                continue
            from excelmanus.engine import _message_content_to_text
            text = _message_content_to_text(item.get("content"))
            if text.strip():
                return text.strip()
        return ""

    @staticmethod
    def _clip_window_hint(text: str, *, max_chars: int = 200) -> str:
        normalized = " ".join(str(text or "").split())
        if len(normalized) <= max_chars:
            return normalized
        return normalized[:max_chars]


def _convert_scan_to_explorer_report(scan: dict, file_path: str) -> dict | None:
    """将 scan_excel_snapshot 的输出转换为 explorer_report 格式。

    explorer_report 格式 (兼容 _build_explorer_report_notice):
    {
        "summary": str,
        "files": [{"path": str, "sheets": [{"name", "rows", "cols", "has_header"}]}],
        "schema": {sheet_name: [{"column", "dtype", "nulls", "unique", "sample"}]},
        "findings": [{"type", "severity", "detail"}],
        "recommendation": str,
    }
    """
    sheets = scan.get("sheets", [])
    if not sheets:
        return None

    # files
    files_entry = {
        "path": file_path,
        "sheets": [
            {
                "name": s.get("name", "?"),
                "rows": s.get("rows", 0),
                "cols": s.get("cols", 0),
                "has_header": True,
            }
            for s in sheets
        ],
    }

    # schema
    schema: dict[str, list[dict]] = {}
    for s in sheets:
        sheet_name = s.get("name", "?")
        cols = []
        for c in s.get("columns", []):
            entry: dict = {
                "column": c.get("name", "?"),
                "dtype": c.get("inferred_type", c.get("dtype", "?")),
                "nulls": c.get("null_count", 0),
                "unique": c.get("unique_count", 0),
            }
            sample = c.get("sample_values")
            if sample:
                entry["sample"] = sample[:3]
            if "min" in c:
                entry["min"] = c["min"]
            if "max" in c:
                entry["max"] = c["max"]
            cols.append(entry)
        if cols:
            schema[sheet_name] = cols

    # findings (from quality_signals + relationships)
    findings: list[dict] = []
    for sig in scan.get("quality_signals", []):
        sig_type = sig.get("type", "quality")
        mapped_type = "anomaly" if sig_type in ("missing_data", "empty_column", "outliers") else "quality"
        if sig_type in ("candidate_foreign_key", "shared_column_name"):
            mapped_type = "relationship"
        findings.append({
            "type": mapped_type,
            "severity": sig.get("severity", "info"),
            "detail": sig.get("detail", ""),
        })
    for rel in scan.get("relationships", []):
        detail = ""
        if rel.get("type") == "shared_column_name":
            detail = f"共享列 {rel.get('columns', [])} 出现在 {rel.get('sheets', [])}"
        elif rel.get("type") == "candidate_foreign_key":
            src = rel.get("source", {})
            tgt = rel.get("target", {})
            detail = (
                f"{src.get('sheet', '?')}.{src.get('column', '?')} → "
                f"{tgt.get('sheet', '?')}.{tgt.get('column', '?')} "
                f"(重叠率 {rel.get('overlap_rate', 0):.0%})"
            )
        if detail:
            findings.append({
                "type": "relationship",
                "severity": "info",
                "detail": detail,
            })

    # summary
    total_sheets = len(sheets)
    total_rows = sum(s.get("rows", 0) for s in sheets)
    signal_count = len(scan.get("quality_signals", []))
    summary = f"{file_path}: {total_sheets} 个 Sheet, 共 {total_rows} 行"
    if signal_count > 0:
        summary += f", {signal_count} 个质量信号"
    summary += " (自动预扫描)"

    # recommendation
    high_signals = [s for s in scan.get("quality_signals", []) if s.get("severity") == "high"]
    recommendation = ""
    if high_signals:
        types = list({s.get("type", "") for s in high_signals})
        recommendation = f"检测到 {len(high_signals)} 个高优先级问题（{', '.join(types)}），建议优先处理"

    return {
        "summary": summary,
        "files": [files_entry],
        "schema": schema,
        "findings": findings[:10],
        "recommendation": recommendation,
    }

