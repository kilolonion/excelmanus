"""窗口感知层管理器。"""

from __future__ import annotations

import asyncio
from copy import deepcopy
from typing import Any, Awaitable, Callable, Literal

from excelmanus.memory import TokenCounter

from .advisor import HybridAdvisor, LifecyclePlan, RuleBasedAdvisor, WindowLifecycleAdvisor
from .advisor_context import AdvisorContext
from .budget import WindowBudgetAllocator
from .extractor import (
    compute_scroll_position,
    extract_column_widths,
    extract_conditional_effects,
    extract_directory,
    extract_explorer_entries,
    extract_file_path,
    extract_freeze_panes,
    extract_merged_range_delta,
    extract_merged_ranges,
    extract_preview_rows,
    extract_range_ref,
    extract_row_heights,
    extract_shape,
    extract_sheet_name,
    extract_sheet_tabs,
    extract_status_bar,
    extract_style_summary,
    extract_viewport_geometry,
    is_excel_path,
    normalize_path,
    parse_json_payload,
)
from .ingest import (
    extract_columns,
    extract_data_rows,
    ingest_filter_result,
    ingest_read_result,
    ingest_write_result,
    make_change_record,
)
from .models import (
    DetailLevel,
    OpEntry,
    PerceptionBudget,
    Viewport,
    WindowRenderAction,
    WindowState,
    WindowType,
)
from .renderer import (
    build_tool_perception_payload,
    render_system_notice,
    render_tool_perception_block,
    render_window_background,
    render_window_keep,
    render_window_minimized,
)
from .rules import classify_tool

AsyncAdvisorRunner = Callable[
    [list[WindowState], str | None, PerceptionBudget, AdvisorContext],
    Awaitable[LifecyclePlan | None],
]


class WindowPerceptionManager:
    """维护窗口状态并生成上下文注入。"""

    _MAX_DORMANT_WINDOWS = 10

    def __init__(
        self,
        *,
        enabled: bool,
        budget: PerceptionBudget,
        advisor_mode: Literal["rules", "hybrid"] = "hybrid",
        advisor_trigger_window_count: int = 3,
        advisor_trigger_turn: int = 4,
        advisor_plan_ttl_turns: int = 2,
    ) -> None:
        self._enabled = enabled
        self._budget = budget
        self._advisor_mode: Literal["rules", "hybrid"] = (
            advisor_mode if advisor_mode in {"rules", "hybrid"} else "hybrid"
        )
        self._advisor: WindowLifecycleAdvisor = (
            RuleBasedAdvisor() if self._advisor_mode == "rules" else HybridAdvisor()
        )
        self._windows: dict[str, WindowState] = {}
        self._explorer_index: dict[str, str] = {}
        self._sheet_index: dict[tuple[str, str], str] = {}
        self._active_window_id: str | None = None
        self._seq: int = 0
        self._operation_seq: int = 0
        self._notice_turn: int = 0
        self._last_notice_operation_seq: int = 0
        self._last_window_count: int = 0
        self._turn_hint_is_new_task: bool = False
        self._turn_hint_user_intent_summary: str = ""
        self._turn_hint_agent_recent_output: str = ""
        self._advisor_runner: AsyncAdvisorRunner | None = None
        self._advisor_task: asyncio.Task[None] | None = None
        self._cached_small_model_plan: LifecyclePlan | None = None
        self._advisor_trigger_window_count = max(1, int(advisor_trigger_window_count))
        self._advisor_trigger_turn = max(1, int(advisor_trigger_turn))
        self._advisor_plan_ttl_turns = max(0, int(advisor_plan_ttl_turns))

    @property
    def enabled(self) -> bool:
        """是否启用窗口感知层。"""
        return self._enabled

    def bind_async_advisor_runner(self, runner: AsyncAdvisorRunner | None) -> None:
        """绑定异步小模型顾问回调。"""
        self._advisor_runner = runner

    def set_turn_hints(
        self,
        *,
        is_new_task: bool,
        user_intent_summary: str = "",
        agent_recent_output: str = "",
    ) -> None:
        """设置当前轮次提示信息。"""
        self._turn_hint_is_new_task = bool(is_new_task)
        self._turn_hint_user_intent_summary = self._normalize_hint(user_intent_summary, max_chars=200)
        self._turn_hint_agent_recent_output = self._normalize_hint(agent_recent_output, max_chars=200)

    def reset(self) -> None:
        """重置状态。"""
        self._cancel_advisor_task()
        self._windows.clear()
        self._explorer_index.clear()
        self._sheet_index.clear()
        self._active_window_id = None
        self._seq = 0
        self._operation_seq = 0
        self._notice_turn = 0
        self._last_notice_operation_seq = 0
        self._last_window_count = 0
        self._turn_hint_is_new_task = False
        self._turn_hint_user_intent_summary = ""
        self._turn_hint_agent_recent_output = ""
        self._cached_small_model_plan = None

    def observe_subagent_context(
        self,
        *,
        candidate_paths: list[str],
        subagent_name: str,
        task: str,
    ) -> None:
        """记录子代理观察到的文件。"""
        if not self._enabled:
            return
        self._operation_seq += 1
        clean_task = " ".join(task.strip().split())
        summary = f"由{subagent_name}观察: {clean_task}" if clean_task else f"由{subagent_name}观察"
        for raw in candidate_paths:
            normalized = normalize_path(raw)
            if not normalized or not is_excel_path(normalized):
                continue
            key = (normalized, "")
            window_id = self._sheet_index.get(key)
            if window_id is None:
                window = WindowState(
                    id=self._new_id("sheet"),
                    type=WindowType.SHEET,
                    title=normalized,
                    file_path=normalized,
                    sheet_name="",
                    summary=summary,
                )
                self._windows[window.id] = window
                self._sheet_index[key] = window.id
                window_id = window.id
            window = self._windows[window_id]
            self._wake_window(window)
            window.summary = summary
            self._touch(window)

    def build_system_notice(self, *, mode: str = "enriched") -> str:
        """构建系统注入窗口快照。"""
        if not self._enabled:
            return ""
        self._notice_turn += 1
        self._age_windows()
        self._refresh_active_window()
        self._recycle_idle_windows()

        active_windows = [item for item in self._windows.values() if not item.dormant]
        if not active_windows:
            self._last_notice_operation_seq = self._operation_seq
            self._last_window_count = 0
            return ""

        is_new_task = self._turn_hint_is_new_task
        self._turn_hint_is_new_task = False
        context = AdvisorContext(
            turn_number=self._notice_turn,
            is_new_task=is_new_task,
            window_count_changed=len(active_windows) != self._last_window_count,
            user_intent_summary=self._turn_hint_user_intent_summary,
            agent_recent_output=self._turn_hint_agent_recent_output,
        )
        cached_small_model_plan = self._get_fresh_small_model_plan(current_turn=context.turn_number)
        lifecycle_plan = self._advisor.advise(
            windows=active_windows,
            active_window_id=self._active_window_id,
            budget=self._budget,
            context=context,
            small_model_plan=cached_small_model_plan,
            plan_ttl_turns=self._advisor_plan_ttl_turns,
        )

        allocator = WindowBudgetAllocator(self._budget)
        full_rows = allocator.compute_window_full_max_rows(len(active_windows))
        snapshots = allocator.allocate(
            windows=active_windows,
            active_window_id=self._active_window_id,
            render_keep=lambda window: render_window_keep(
                window,
                mode=mode,
                max_rows=full_rows,
                current_iteration=window.current_iteration,
            ),
            render_background=render_window_background,
            render_minimized=render_window_minimized,
            lifecycle_plan=lifecycle_plan,
        )
        visible = [item for item in snapshots if item.action != WindowRenderAction.CLOSE]

        for item in snapshots:
            if item.action != WindowRenderAction.CLOSE:
                continue
            self._mark_window_dormant(item.window_id)
        self._evict_dormant_windows()

        self._last_notice_operation_seq = self._operation_seq
        self._last_window_count = len([item for item in self._windows.values() if not item.dormant])
        self._schedule_async_advisor(
            active_windows=[item for item in self._windows.values() if not item.dormant],
            context=context,
        )

        return render_system_notice(visible, mode=mode)

    def enrich_tool_result(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        result_text: str,
        success: bool,
        mode: str = "enriched",
    ) -> str:
        """增强工具返回。"""
        if not self._enabled or not success:
            return result_text

        if mode == "anchored":
            return self.ingest_and_confirm(
                tool_name=tool_name,
                arguments=arguments,
                result_text=result_text,
                success=success,
            )

        payload = self.update_from_tool_call(
            tool_name=tool_name,
            arguments=arguments,
            result_text=result_text,
        )
        if payload is None:
            return result_text

        block = render_tool_perception_block(payload)
        if not block:
            return result_text
        block = self._truncate_tool_append(block)
        if not block.strip():
            return result_text
        return f"{result_text}\n\n{block}" if result_text.strip() else block

    def ingest_and_confirm(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        result_text: str,
        success: bool,
    ) -> str:
        """WURM 路径：ingest + anchored 确认，异常时原子回退 enriched。"""
        if not self._enabled or not success:
            return result_text

        classification = classify_tool(tool_name)
        if classification.window_type is None:
            return result_text

        parsed = parse_json_payload(result_text)
        result_json = parsed if isinstance(parsed, dict) else None
        try:
            payload = self.update_from_tool_call(
                tool_name=tool_name,
                arguments=arguments,
                result_text=result_text,
            )
            if payload is None:
                return result_text

            window = self._resolve_target_window(classification.window_type, arguments, result_json)
            if window is None:
                return self._enriched_fallback(
                    tool_name=tool_name,
                    arguments=arguments,
                    result_text=result_text,
                    success=success,
                )

            self._apply_ingest(
                window=window,
                canonical_tool_name=classification.canonical_name,
                arguments=arguments,
                result_json=result_json,
            )
            return self.generate_confirmation(
                window=window,
                tool_name=classification.canonical_name or tool_name,
            )
        except Exception:
            return self._enriched_fallback(
                tool_name=tool_name,
                arguments=arguments,
                result_text=result_text,
                success=success,
            )

    def update_from_tool_call(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        result_text: str,
    ) -> dict[str, Any] | None:
        """根据工具调用更新窗口状态并返回感知 payload。"""
        if not self._enabled:
            return None

        classification = classify_tool(tool_name)
        if classification.window_type is None:
            return None

        parsed = parse_json_payload(result_text)
        result_json = parsed if isinstance(parsed, dict) else None

        self._operation_seq += 1

        if classification.window_type == WindowType.EXPLORER:
            window = self._update_explorer_window(
                arguments=arguments,
                result_json=result_json,
            )
            return build_tool_perception_payload(window)

        window = self._update_sheet_window(
            canonical_tool_name=classification.canonical_name,
            arguments=arguments,
            result_json=result_json,
        )
        return build_tool_perception_payload(window)

    def generate_confirmation(
        self,
        *,
        window: WindowState,
        tool_name: str,
    ) -> str:
        """生成 anchored 模式工具确认文本。"""
        rows = window.total_rows or (window.viewport.total_rows if window.viewport else 0) or len(window.data_buffer)
        cols = window.total_cols or (window.viewport.total_cols if window.viewport else 0) or len(window.columns)
        preview_text = "无数据行（已更新窗口元信息）"
        if window.data_buffer:
            first_row = window.data_buffer[0]
            values = [str(first_row.get(col.name, "")) for col in window.columns[:5]]
            if not any(values):
                values = [str(v) for v in list(first_row.values())[:5]]
            preview_text = " | ".join(values) if values else "无可预览字段"

        return (
            f"✅ {tool_name} 执行成功 → {rows}行×{cols}列\n"
            f"  数据已写入窗口[{window.id}]，请在系统上下文「数据窗口」区域查看完整内容。\n"
            f"  首行预览: {preview_text}"
        )

    def _enriched_fallback(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        result_text: str,
        success: bool,
    ) -> str:
        """ingest 失败时回退到 enriched 逻辑。"""
        payload = self.update_from_tool_call(
            tool_name=tool_name,
            arguments=arguments,
            result_text=result_text,
        )
        if payload is None:
            return result_text
        block = render_tool_perception_block(payload)
        if not block:
            return result_text
        block = self._truncate_tool_append(block)
        if not block.strip():
            return result_text
        return f"{result_text}\n\n{block}" if result_text.strip() else block

    def _resolve_target_window(
        self,
        window_type: WindowType,
        arguments: dict[str, Any],
        result_json: dict[str, Any] | None,
    ) -> WindowState | None:
        if window_type == WindowType.EXPLORER:
            if self._active_window_id:
                return self._windows.get(self._active_window_id)
            return None

        file_path = normalize_path(extract_file_path(arguments, result_json))
        sheet_name = extract_sheet_name(arguments, result_json)
        if not file_path and self._active_window_id:
            active = self._windows.get(self._active_window_id)
            if active is not None and active.type == WindowType.SHEET:
                file_path = active.file_path or ""
                if not sheet_name:
                    sheet_name = active.sheet_name or ""
        key = (file_path, sheet_name)
        window_id = self._sheet_index.get(key)
        if window_id is None and self._active_window_id:
            active = self._windows.get(self._active_window_id)
            if active is not None and active.type == WindowType.SHEET:
                return active
        return self._windows.get(window_id) if window_id else None

    def _apply_ingest(
        self,
        *,
        window: WindowState,
        canonical_tool_name: str,
        arguments: dict[str, Any],
        result_json: dict[str, Any] | None,
    ) -> None:
        """根据工具类别将结果写入 WURM 数据容器。"""
        iteration = self._operation_seq
        window.current_iteration = iteration
        rows = extract_data_rows(result_json, canonical_tool_name)
        columns = extract_columns(result_json, rows)
        if columns:
            window.columns = columns

        if window.type == WindowType.EXPLORER:
            self._append_operation(window, canonical_tool_name, arguments, True)
            self._append_change(
                window,
                make_change_record(
                    operation="enrich",
                    tool_summary=f"{canonical_tool_name}",
                    affected_range="-",
                    change_type="enriched",
                    iteration=iteration,
                    affected_row_indices=[],
                ),
            )
            return

        range_ref = extract_range_ref(
            arguments,
            default_rows=self._budget.default_rows,
            default_cols=self._budget.default_cols,
        )
        window.viewport_range = range_ref
        window.max_cached_rows = max(1, int(self._budget.window_data_buffer_max_rows))

        if canonical_tool_name in {"filter_data"}:
            filter_condition = {
                "column": arguments.get("column"),
                "operator": arguments.get("operator"),
                "value": arguments.get("value"),
            }
            affected = ingest_filter_result(
                window,
                filter_condition=filter_condition,
                filtered_rows=rows,
                iteration=iteration,
            )
            change = make_change_record(
                operation="filter",
                tool_summary=f"{canonical_tool_name}({filter_condition})",
                affected_range=range_ref,
                change_type="filtered",
                iteration=iteration,
                affected_row_indices=affected,
            )
        elif canonical_tool_name in {
            "write_excel",
            "write_to_sheet",
            "write_cells",
            "format_cells",
            "format_range",
            "adjust_column_width",
            "adjust_row_height",
            "merge_cells",
            "unmerge_cells",
            "add_color_scale",
            "add_data_bar",
            "add_conditional_rule",
        }:
            target_range = str(
                arguments.get("range")
                or arguments.get("cell_range")
                or arguments.get("cell")
                or range_ref
            ).strip()
            affected = ingest_write_result(
                window,
                target_range=target_range,
                result_json=result_json,
                iteration=iteration,
            )
            change = make_change_record(
                operation="write",
                tool_summary=f"{canonical_tool_name}({target_range})",
                affected_range=target_range,
                change_type="modified",
                iteration=iteration,
                affected_row_indices=affected,
            )
        else:
            affected = ingest_read_result(
                window,
                new_range=range_ref,
                new_rows=rows,
                iteration=iteration,
            )
            change = make_change_record(
                operation="read",
                tool_summary=f"{canonical_tool_name}({range_ref})",
                affected_range=range_ref,
                change_type="added" if rows else "enriched",
                iteration=iteration,
                affected_row_indices=affected,
            )

        if window.viewport is not None:
            window.total_rows = window.viewport.total_rows
            window.total_cols = window.viewport.total_cols
        if window.total_rows <= 0:
            window.total_rows = len(window.data_buffer)
        if window.total_cols <= 0:
            window.total_cols = len(window.columns)
        window.detail_level = DetailLevel.FULL
        self._append_operation(window, canonical_tool_name, arguments, True)
        self._append_change(window, change)

    @staticmethod
    def _append_operation(
        window: WindowState,
        tool_name: str,
        arguments: dict[str, Any],
        success: bool,
    ) -> None:
        window.operation_history.append(
            OpEntry(
                tool_name=tool_name,
                arguments=dict(arguments),
                iteration=window.current_iteration,
                success=success,
            )
        )
        max_entries = max(1, int(window.max_history_entries))
        if len(window.operation_history) > max_entries:
            window.operation_history = window.operation_history[-max_entries:]

    @staticmethod
    def _append_change(window: WindowState, record) -> None:
        window.change_log.append(record)
        max_entries = max(1, int(window.max_change_records))
        if len(window.change_log) > max_entries:
            window.change_log = window.change_log[-max_entries:]

    def _update_explorer_window(
        self,
        *,
        arguments: dict[str, Any],
        result_json: dict[str, Any] | None,
    ) -> WindowState:
        directory = normalize_path(extract_directory(arguments, result_json)) or "."
        entries = extract_explorer_entries(result_json)

        window_id = self._explorer_index.get(directory)
        if window_id is None:
            window = WindowState(
                id=self._new_id("explorer"),
                type=WindowType.EXPLORER,
                title="资源管理器",
                directory=directory,
            )
            self._windows[window.id] = window
            self._explorer_index[directory] = window.id
        else:
            window = self._windows[window_id]

        self._wake_window(window)
        window.directory = directory
        window.metadata["entries"] = entries
        window.summary = f"{len(entries)} 个可见项" if entries else "目录视图"
        self._touch(window)
        self._active_window_id = window.id
        return window

    def _update_sheet_window(
        self,
        *,
        canonical_tool_name: str,
        arguments: dict[str, Any],
        result_json: dict[str, Any] | None,
    ) -> WindowState:
        file_path = normalize_path(extract_file_path(arguments, result_json))
        sheet_name = extract_sheet_name(arguments, result_json)

        if not file_path and self._active_window_id:
            active = self._windows.get(self._active_window_id)
            if active is not None and active.type == WindowType.SHEET:
                file_path = active.file_path or ""
                if not sheet_name:
                    sheet_name = active.sheet_name or ""

        key = (file_path, sheet_name)
        window_id = self._sheet_index.get(key)
        if window_id is None:
            window = WindowState(
                id=self._new_id("sheet"),
                type=WindowType.SHEET,
                title=f"{file_path}/{sheet_name}" if file_path or sheet_name else "表格窗口",
                file_path=file_path or None,
                sheet_name=sheet_name or None,
            )
            self._windows[window.id] = window
            self._sheet_index[key] = window.id
        else:
            window = self._windows[window_id]

        self._wake_window(window)
        tabs = extract_sheet_tabs(result_json)
        if tabs:
            window.sheet_tabs = tabs
        if sheet_name:
            window.sheet_name = sheet_name
        if file_path:
            window.file_path = file_path

        total_rows, total_cols = extract_shape(result_json)
        range_ref = extract_range_ref(
            arguments,
            default_rows=self._budget.default_rows,
            default_cols=self._budget.default_cols,
        )
        geometry = extract_viewport_geometry(
            range_ref,
            default_rows=self._budget.default_rows,
            default_cols=self._budget.default_cols,
        )
        viewport = window.viewport or Viewport()
        viewport.range_ref = range_ref
        viewport.visible_rows = geometry["visible_rows"]
        viewport.visible_cols = geometry["visible_cols"]
        if total_rows > 0:
            viewport.total_rows = total_rows
        if total_cols > 0:
            viewport.total_cols = total_cols
        window.viewport = viewport
        window.viewport_range = range_ref
        window.total_rows = viewport.total_rows
        window.total_cols = viewport.total_cols
        window.max_cached_rows = max(1, int(self._budget.window_data_buffer_max_rows))

        scroll_position = compute_scroll_position(
            geometry,
            total_rows=viewport.total_rows,
            total_cols=viewport.total_cols,
        )
        window.metadata["scroll_position"] = scroll_position

        preview = extract_preview_rows(result_json)
        if preview:
            window.preview_rows = preview
            if not window.data_buffer:
                normalized_preview = extract_data_rows({"preview": preview}, "read_excel")
                if normalized_preview:
                    window.data_buffer = normalized_preview
            if not window.columns:
                inferred_columns = extract_columns({"preview": preview}, window.data_buffer)
                if inferred_columns:
                    window.columns = inferred_columns

        status_bar = extract_status_bar(window.preview_rows)
        if status_bar:
            window.metadata["status_bar"] = status_bar

        freeze = extract_freeze_panes(result_json)
        if freeze:
            window.freeze_panes = freeze

        column_widths = extract_column_widths(result_json, sheet_name=window.sheet_name or "")
        if column_widths:
            window.metadata["column_widths"] = column_widths

        row_heights = extract_row_heights(result_json)
        if row_heights:
            window.metadata["row_heights"] = row_heights

        merged_ranges = extract_merged_ranges(result_json)
        if merged_ranges:
            window.metadata["merged_ranges"] = merged_ranges
        add_ranges, remove_ranges = extract_merged_range_delta(result_json)
        if add_ranges or remove_ranges:
            existing = {
                str(item).strip().upper()
                for item in window.metadata.get("merged_ranges", [])
                if str(item).strip()
            }
            existing.update(add_ranges)
            for removed in remove_ranges:
                existing.discard(removed)
            window.metadata["merged_ranges"] = sorted(existing)

        conditional_effects = extract_conditional_effects(result_json)
        if conditional_effects:
            window.metadata["conditional_effects"] = conditional_effects

        style_summary = extract_style_summary(result_json)
        if style_summary:
            window.style_summary = style_summary

        if canonical_tool_name in {"write_excel", "write_to_sheet", "write_cells", "format_cells", "format_range"}:
            target_range = str(arguments.get("range") or arguments.get("cell_range") or arguments.get("cell") or "").strip()
            if target_range:
                window.summary = f"最近修改区域: {target_range}"
        elif canonical_tool_name in {"adjust_column_width"}:
            if column_widths:
                window.summary = f"最近调整列宽: {len(column_widths)}列"
        elif canonical_tool_name in {"adjust_row_height"}:
            if row_heights:
                window.summary = f"最近调整行高: {len(row_heights)}行"
        elif canonical_tool_name in {"merge_cells", "unmerge_cells"}:
            merged_total = len(window.metadata.get("merged_ranges", []))
            window.summary = f"当前合并区域: {merged_total}处"
        elif canonical_tool_name in {"add_color_scale", "add_data_bar", "add_conditional_rule"}:
            effect_total = len(window.metadata.get("conditional_effects", []))
            window.summary = f"条件格式视觉效果: {effect_total}条"
        elif canonical_tool_name in {"copy_sheet", "rename_sheet", "delete_sheet", "create_sheet", "list_sheets", "describe_sheets"}:
            window.summary = "工作表元信息已更新"

        self._touch(window)
        self._active_window_id = window.id
        return window

    def _age_windows(self) -> None:
        """窗口老化：未在本轮前后被新访问的窗口递增 idle。"""
        for window in self._windows.values():
            if window.dormant:
                continue
            if window.last_access_seq > self._last_notice_operation_seq:
                continue
            window.idle_turns += 1

    def _recycle_idle_windows(self) -> None:
        """按 idle 阈值自动回收窗口（标记 dormant）。"""
        terminate_after = max(1, int(self._budget.terminate_after_idle))
        for window in list(self._windows.values()):
            if window.dormant:
                continue
            if window.idle_turns < terminate_after:
                continue
            self._mark_window_dormant(window.id)

    def _refresh_active_window(self) -> None:
        """当前活动窗口只在“本轮有新访问”时保持激活。"""
        if self._active_window_id is None:
            return
        active = self._windows.get(self._active_window_id)
        if active is None or active.dormant:
            self._active_window_id = None
            return
        if active.last_access_seq <= self._last_notice_operation_seq:
            self._active_window_id = None

    def _touch(self, window: WindowState) -> None:
        window.last_access_seq = self._operation_seq
        window.idle_turns = 0

    def _new_id(self, prefix: str) -> str:
        self._seq += 1
        return f"{prefix}_{self._seq}"

    def _drop_window(self, window_id: str) -> None:
        window = self._windows.pop(window_id, None)
        if window is None:
            return

        if window.type == WindowType.EXPLORER:
            directories = [
                key
                for key, val in self._explorer_index.items()
                if val == window_id
            ]
            for key in directories:
                self._explorer_index.pop(key, None)

        if window.type == WindowType.SHEET:
            keys = [
                key
                for key, val in self._sheet_index.items()
                if val == window_id
            ]
            for key in keys:
                self._sheet_index.pop(key, None)

        if self._active_window_id == window_id:
            self._active_window_id = None

    def _mark_window_dormant(self, window_id: str) -> None:
        window = self._windows.get(window_id)
        if window is None:
            return
        window.dormant = True
        window.detail_level = DetailLevel.NONE
        if self._active_window_id == window_id:
            self._active_window_id = None

    @staticmethod
    def _wake_window(window: WindowState) -> None:
        window.dormant = False
        if window.detail_level == DetailLevel.NONE:
            window.detail_level = DetailLevel.FULL

    def _evict_dormant_windows(self) -> None:
        dormant = [item for item in self._windows.values() if item.dormant]
        overflow = len(dormant) - self._MAX_DORMANT_WINDOWS
        if overflow <= 0:
            return
        to_drop = sorted(dormant, key=lambda item: item.last_access_seq)[:overflow]
        for item in to_drop:
            self._drop_window(item.id)

    def _schedule_async_advisor(
        self,
        *,
        active_windows: list[WindowState],
        context: AdvisorContext,
    ) -> None:
        if self._advisor_mode != "hybrid":
            return
        if self._advisor_runner is None:
            return
        if not active_windows:
            return
        if not self._should_invoke_small_model(context=context, active_windows=active_windows):
            return
        if self._advisor_task is not None and not self._advisor_task.done():
            return

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        windows_snapshot = [deepcopy(item) for item in active_windows]
        context_snapshot = AdvisorContext(
            turn_number=context.turn_number,
            is_new_task=context.is_new_task,
            window_count_changed=context.window_count_changed,
            user_intent_summary=context.user_intent_summary,
            agent_recent_output=context.agent_recent_output,
            task_type=context.task_type,
        )
        task = loop.create_task(
            self._run_async_advisor(
                windows=windows_snapshot,
                active_window_id=self._active_window_id,
                context=context_snapshot,
            )
        )
        self._advisor_task = task
        task.add_done_callback(self._on_advisor_task_done)

    async def _run_async_advisor(
        self,
        *,
        windows: list[WindowState],
        active_window_id: str | None,
        context: AdvisorContext,
    ) -> None:
        runner = self._advisor_runner
        if runner is None:
            return

        try:
            plan = await runner(windows, active_window_id, self._budget, context)
        except asyncio.CancelledError:
            return
        except Exception:
            return

        if plan is None:
            return
        if plan.generated_turn <= 0:
            plan.generated_turn = context.turn_number
        self._cached_small_model_plan = plan

    def _on_advisor_task_done(self, task: asyncio.Task[None]) -> None:
        if self._advisor_task is task:
            self._advisor_task = None
        if task.cancelled():
            return
        try:
            _ = task.exception()
        except Exception:
            return

    def _cancel_advisor_task(self) -> None:
        task = self._advisor_task
        self._advisor_task = None
        if task is not None and not task.done():
            task.cancel()

    def _get_fresh_small_model_plan(self, *, current_turn: int) -> LifecyclePlan | None:
        plan = self._cached_small_model_plan
        if plan is None:
            return None
        if int(plan.generated_turn) <= 0:
            return None
        if current_turn - int(plan.generated_turn) > self._advisor_plan_ttl_turns:
            return None
        return plan

    def _should_invoke_small_model(
        self,
        *,
        context: AdvisorContext,
        active_windows: list[WindowState],
    ) -> bool:
        if context.is_new_task:
            return True
        if context.window_count_changed:
            return True
        if len(active_windows) >= self._advisor_trigger_window_count:
            return True
        return context.turn_number >= self._advisor_trigger_turn

    @staticmethod
    def _normalize_hint(text: str, *, max_chars: int) -> str:
        normalized = " ".join(str(text or "").split())
        if len(normalized) <= max_chars:
            return normalized
        return normalized[:max_chars]

    def _truncate_tool_append(self, text: str) -> str:
        max_tokens = max(0, int(self._budget.tool_append_tokens))
        if max_tokens <= 0:
            return ""
        if self._estimate_tokens(text) <= max_tokens:
            return text

        lines = text.splitlines()
        while lines and self._estimate_tokens("\n".join(lines)) > max_tokens:
            lines.pop()
        return "\n".join(lines)

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        return TokenCounter.count_message({"role": "system", "content": text})
