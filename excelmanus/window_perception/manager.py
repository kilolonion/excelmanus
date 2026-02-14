"""窗口感知层管理器。"""

from __future__ import annotations

from typing import Any

from excelmanus.memory import TokenCounter

from .advisor import RuleBasedAdvisor, WindowLifecycleAdvisor
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
from .models import PerceptionBudget, Viewport, WindowRenderAction, WindowState, WindowType
from .renderer import (
    build_tool_perception_payload,
    render_system_notice,
    render_tool_perception_block,
    render_window_background,
    render_window_keep,
    render_window_minimized,
)
from .rules import classify_tool


class WindowPerceptionManager:
    """维护窗口状态并生成上下文注入。"""

    _MAX_DORMANT_WINDOWS = 10

    def __init__(
        self,
        *,
        enabled: bool,
        budget: PerceptionBudget,
    ) -> None:
        self._enabled = enabled
        self._budget = budget
        self._advisor: WindowLifecycleAdvisor = RuleBasedAdvisor()
        self._windows: dict[str, WindowState] = {}
        self._explorer_index: dict[str, str] = {}
        self._sheet_index: dict[tuple[str, str], str] = {}
        self._active_window_id: str | None = None
        self._seq: int = 0
        self._operation_seq: int = 0
        self._notice_turn: int = 0
        self._last_notice_operation_seq: int = 0
        self._last_window_count: int = 0

    @property
    def enabled(self) -> bool:
        """是否启用窗口感知层。"""
        return self._enabled

    def reset(self) -> None:
        """重置状态。"""
        self._windows.clear()
        self._explorer_index.clear()
        self._sheet_index.clear()
        self._active_window_id = None
        self._seq = 0
        self._operation_seq = 0
        self._notice_turn = 0
        self._last_notice_operation_seq = 0
        self._last_window_count = 0

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

    def build_system_notice(self) -> str:
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

        context = AdvisorContext(
            turn_number=self._notice_turn,
            is_new_task=self._notice_turn == 1,
            window_count_changed=len(active_windows) != self._last_window_count,
        )
        lifecycle_plan = self._advisor.advise(
            windows=active_windows,
            active_window_id=self._active_window_id,
            budget=self._budget,
            context=context,
        )

        allocator = WindowBudgetAllocator(self._budget)
        snapshots = allocator.allocate(
            windows=active_windows,
            active_window_id=self._active_window_id,
            render_keep=render_window_keep,
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

        return render_system_notice(visible)

    def enrich_tool_result(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        result_text: str,
        success: bool,
    ) -> str:
        """增强工具返回。"""
        if not self._enabled or not success:
            return result_text

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

        scroll_position = compute_scroll_position(
            geometry,
            total_rows=viewport.total_rows,
            total_cols=viewport.total_cols,
        )
        window.metadata["scroll_position"] = scroll_position

        preview = extract_preview_rows(result_json)
        if preview:
            window.preview_rows = preview

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
        if self._active_window_id == window_id:
            self._active_window_id = None

    @staticmethod
    def _wake_window(window: WindowState) -> None:
        window.dormant = False

    def _evict_dormant_windows(self) -> None:
        dormant = [item for item in self._windows.values() if item.dormant]
        overflow = len(dormant) - self._MAX_DORMANT_WINDOWS
        if overflow <= 0:
            return
        to_drop = sorted(dormant, key=lambda item: item.last_access_seq)[:overflow]
        for item in to_drop:
            self._drop_window(item.id)

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
