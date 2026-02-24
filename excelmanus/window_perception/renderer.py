"""窗口感知层渲染器。"""

from __future__ import annotations

from typing import Any

from .domain import ExplorerWindow, SheetWindow, Window
from .models import DetailLevel, IntentTag, WindowSnapshot, WindowType
from .projection_models import NoticeProjection, ToolPayloadProjection
from .projection_service import project_notice, project_tool_payload


def render_system_notice(snapshots: list[WindowSnapshot], *, mode: str = "enriched") -> str:
    """渲染系统上下文注入文本。"""
    if not snapshots:
        return ""
    body = "\n\n".join(item.rendered_text for item in snapshots if item.rendered_text.strip())
    if not body:
        return ""
    if mode in {"anchored", "unified"}:
        return (
            "## 数据窗口\n"
            "以下窗口包含你通过工具操作获取的所有数据。\n"
            "窗口内容是最近一次工具结果快照；若窗口未覆盖所需范围，再补充读取。\n"
            "写入类任务必须调用写入工具并依据其返回确认完成。\n\n"
            + body
        )
    return (
        "## 窗口感知上下文\n"
        "以下是你当前已打开的窗口实时状态。\n"
        "若窗口信息不足或未覆盖目标范围，再补充读取；写入类任务必须以写入工具返回为准。\n\n"
        + body
    )


def render_window_keep(
    window: Window | NoticeProjection,
    *,
    payload: ToolPayloadProjection | None = None,
    detail_level: DetailLevel | None = None,
    mode: str = "enriched",
    max_rows: int = 25,
    current_iteration: int = 0,
    intent_profile: dict[str, Any] | None = None,
) -> str:
    """渲染 ACTIVE 窗口。"""
    if isinstance(window, NoticeProjection):
        projection = window
        effective_level = detail_level or DetailLevel.FULL
        effective_payload = payload
    else:
        if isinstance(window, ExplorerWindow):
            return _render_explorer(window)
        if mode in {"anchored", "unified"} and window.detail_level == DetailLevel.FULL and window.data_buffer:
            return render_window_wurm_full(
                window,
                max_rows=max_rows,
                current_iteration=current_iteration,
                intent_profile=intent_profile,
            )
        projection = project_notice(window)
        effective_level = detail_level or window.detail_level
        effective_payload = payload or project_tool_payload(window)

    if mode in {"anchored", "unified"}:
        profile = _normalize_projection_intent_profile(projection, intent_profile=intent_profile)
        if effective_level == DetailLevel.ICON:
            return _render_minimized_projection(projection, payload=effective_payload, intent_profile=profile)
        if effective_level == DetailLevel.SUMMARY:
            return _render_background_projection(projection, payload=effective_payload, intent_profile=profile)
        if effective_level == DetailLevel.NONE:
            return ""
    return _render_notice_projection(projection, payload=effective_payload, detail_level=effective_level)


def render_window_background(
    window: Window | NoticeProjection,
    *,
    payload: ToolPayloadProjection | None = None,
    intent_profile: dict[str, Any] | None = None,
) -> str:
    """渲染 BACKGROUND 窗口（结构缩略图）。"""
    if isinstance(window, NoticeProjection):
        projection = window
        effective_payload = payload
    else:
        projection = project_notice(window)
        effective_payload = payload or project_tool_payload(window)
    profile = _normalize_projection_intent_profile(projection, intent_profile=intent_profile)
    return _render_background_projection(projection, payload=effective_payload, intent_profile=profile)


def render_window_minimized(
    window: Window | NoticeProjection,
    *,
    payload: ToolPayloadProjection | None = None,
    intent_profile: dict[str, Any] | None = None,
) -> str:
    """渲染 SUSPENDED 窗口（一行摘要）。"""
    if isinstance(window, NoticeProjection):
        projection = window
        effective_payload = payload
    else:
        projection = project_notice(window)
        effective_payload = payload or project_tool_payload(window)
    profile = _normalize_projection_intent_profile(projection, intent_profile=intent_profile)
    return _render_minimized_projection(projection, payload=effective_payload, intent_profile=profile)


def build_tool_perception_payload(window: Window | None) -> dict[str, Any] | None:
    """生成工具返回增强 payload。"""
    projection = project_tool_payload(window)
    if projection is None:
        return None

    if projection.window_type == "explorer":
        return {
            "window_type": "explorer",
            "title": projection.title,
            "identity": projection.identity,
            "directory": projection.directory,
            "entries": list(projection.entries)[:12],
        }

    return {
        "window_type": "sheet",
        "identity": projection.identity,
        "file": projection.file,
        "sheet": projection.sheet,
        "intent": projection.intent,
        "sheet_tabs": list(projection.sheet_tabs),
        "viewport": {
            "range": projection.viewport_range,
            "visible_rows": projection.visible_rows,
            "visible_cols": projection.visible_cols,
            "total_rows": projection.total_rows,
            "total_cols": projection.total_cols,
        },
        "freeze_panes": projection.freeze_panes,
        "style_summary": projection.style_summary,
        "scroll_position": dict(projection.scroll_position),
        "status_bar": dict(projection.status_bar),
        "column_widths": dict(projection.column_widths),
        "row_heights": dict(projection.row_heights),
        "merged_ranges": list(projection.merged_ranges),
        "conditional_effects": list(projection.conditional_effects),
        "sheet_dimensions": list(projection.sheet_dimensions) if projection.sheet_dimensions else [],
    }


def render_tool_perception_block(payload: dict[str, Any] | None) -> str:
    """渲染文本增强块。"""
    if not isinstance(payload, dict):
        return ""

    if payload.get("window_type") == "explorer":
        lines = [
            "--- perception ---",
            f"identity: {payload.get('identity') or '.'}",
            f"path: {payload.get('directory') or '.'}",
        ]
        entries = payload.get("entries")
        if isinstance(entries, list) and entries:
            for entry in entries[:8]:
                lines.append(f"  {entry}")
        lines.append("--- end ---")
        return "\n".join(lines)

    viewport = payload.get("viewport") if isinstance(payload.get("viewport"), dict) else {}
    tab_names = [
        str(item).strip()
        for item in (payload.get("sheet_tabs") or [])
        if str(item).strip()
    ]
    current_sheet = str(payload.get("sheet") or "").strip()
    if not current_sheet and tab_names:
        current_sheet = tab_names[0]
    if not current_sheet:
        current_sheet = "未知"
    other_tabs = [f"[{name}]" for name in tab_names if name != current_sheet]

    lines = [
        "--- perception ---",
        f"identity: {payload.get('identity') or 'unknown-file#unknown-sheet'}",
        f"file: {payload.get('file') or '未知'}",
        f"intent: {payload.get('intent') or 'general'}",
        (
            f"sheet: {current_sheet} | others: {' '.join(other_tabs)}"
            if other_tabs
            else f"sheet: {current_sheet}"
        ),
        (
            "range: "
            f"{viewport.get('total_rows', 0)}r x {viewport.get('total_cols', 0)}c"
        ),
        f"viewport: {viewport.get('range') or '未知'}",
    ]

    sheet_dimensions = payload.get("sheet_dimensions")
    if isinstance(sheet_dimensions, (list, tuple)) and sheet_dimensions:
        dims_parts = [f"{name}({r}r×{c}c)" for name, r, c in sheet_dimensions]
        if len(dims_parts) > 20:
            dims_parts = dims_parts[:20]
            dims_parts.append(f"...(+{len(sheet_dimensions) - 20})")
        lines.append(f"sheets: {' | '.join(dims_parts)}")

    # 列截断警告：当实际列数超过视口可见列数时提醒
    _visible_cols = viewport.get("visible_cols", 0)
    _total_cols = viewport.get("total_cols", 0)
    if _total_cols > _visible_cols > 0:
        lines.append(
            f"⚠️ 列截断：工作表共 {_total_cols} 列，视口仅显示 {_visible_cols} 列，"
            f"格式化整行时建议使用行引用（如 1:1）"
        )

    freeze = payload.get("freeze_panes")
    if freeze:
        lines.append(f"freeze: {freeze}")

    scroll = payload.get("scroll_position")
    if isinstance(scroll, dict) and scroll:
        vertical = _format_percent(scroll.get("vertical_pct"))
        horizontal = _format_percent(scroll.get("horizontal_pct"))
        remain_rows = _format_percent(scroll.get("remaining_rows_pct"))
        remain_cols = _format_percent(scroll.get("remaining_cols_pct"))
        lines.append(f"scroll: v={vertical} | h={horizontal}")
        lines.append(f"remain: below={remain_rows} | right={remain_cols}")

    status_bar = payload.get("status_bar")
    if isinstance(status_bar, dict) and status_bar:
        lines.append(
            "stats: "
            f"SUM={_format_number(status_bar.get('sum'))} | "
            f"COUNT={_format_int(status_bar.get('count'))} | "
            f"AVG={_format_number(status_bar.get('average'))}"
        )

    column_widths = payload.get("column_widths")
    if isinstance(column_widths, dict) and column_widths:
        lines.append(f"col-width: {_format_map_preview(column_widths, max_items=8)}")

    row_heights = payload.get("row_heights")
    if isinstance(row_heights, dict) and row_heights:
        lines.append(f"row-height: {_format_map_preview(row_heights, max_items=8)}")

    merged_ranges = payload.get("merged_ranges")
    if isinstance(merged_ranges, list) and merged_ranges:
        merged_preview = ", ".join(str(item) for item in merged_ranges[:6])
        extra = f" ...(+{len(merged_ranges) - 6})" if len(merged_ranges) > 6 else ""
        lines.append(f"merged: {merged_preview}{extra}")

    conditional_effects = payload.get("conditional_effects")
    if isinstance(conditional_effects, list) and conditional_effects:
        effect_preview = " | ".join(str(item) for item in conditional_effects[:4])
        extra = f" ...(+{len(conditional_effects) - 4})" if len(conditional_effects) > 4 else ""
        lines.append(f"cond-fmt: {effect_preview}{extra}")

    style_summary = payload.get("style_summary")
    if style_summary:
        lines.append(f"style: {style_summary}")
    lines.append("--- end ---")
    return "\n".join(lines)


def _render_explorer(window: ExplorerWindow) -> str:
    lines = [
        "[ACTIVE -- 资源管理器]",
        f"path: {window.directory or '.'}",
    ]
    entries = window.entries
    if entries:
        for entry in entries[:15]:
            lines.append(str(entry))
        if len(entries) > 15:
            lines.append(f"  ... (+{len(entries) - 15} more)")
    elif window.summary:
        lines.append(window.summary)
    return "\n".join(lines)


def _render_notice_projection(
    projection: NoticeProjection,
    *,
    payload: ToolPayloadProjection | None = None,
    detail_level: DetailLevel = DetailLevel.FULL,
) -> str:
    """Render notice DTO without reading mutable window fields."""
    if projection.kind == "explorer":
        directory = payload.directory if payload is not None else projection.identity
        lines = [
            "[ACTIVE -- 资源管理器]",
            f"path: {directory or '.'}",
        ]
        entries = list(payload.entries) if payload is not None else []
        if entries:
            for entry in entries[:15]:
                lines.append(str(entry))
            if len(entries) > 15:
                lines.append(f"  ... (+{len(entries) - 15} more)")
        elif projection.summary:
            lines.append(projection.summary)
        return "\n".join(lines)

    if payload is not None and payload.window_type == "sheet":
        if detail_level == DetailLevel.ICON:
            return _render_minimized_projection(projection, payload=payload, intent_profile=None)
        if detail_level == DetailLevel.SUMMARY:
            return _render_background_projection(projection, payload=payload, intent_profile=None)
        return _render_sheet_projection(projection, payload=payload)

    lines = [
        f"[ACTIVE -- {projection.window_id}]",
        f"identity: {projection.identity}",
        f"range: {projection.range_ref} ({projection.rows}r x {projection.cols}c)",
        f"intent: {projection.intent}",
    ]
    if projection.summary:
        lines.append(f"summary: {projection.summary}")
    return "\n".join(lines)


def _render_sheet_projection(projection: NoticeProjection, *, payload: ToolPayloadProjection) -> str:
    file_name = payload.file or "未知文件"
    sheet_name = payload.sheet or "未知Sheet"
    lines = [f"[ACTIVE -- {file_name} / {sheet_name}]"]

    if payload.sheet_tabs:
        tabs = []
        current = sheet_name
        for item in payload.sheet_tabs:
            token = f">{item}" if item == current else item
            tabs.append(f"[{token}]")
        lines.append("tabs: " + " ".join(tabs))

    lines.append(
        "range: "
        f"{payload.viewport_range or projection.range_ref} ({projection.rows}r x {projection.cols}c)"
    )
    if payload.freeze_panes:
        lines.append(f"freeze: {payload.freeze_panes}")
    if payload.style_summary:
        lines.append("style:")
        lines.append(f"  {payload.style_summary}")
    if projection.summary:
        lines.append(f"summary: {projection.summary}")
    return "\n".join(lines)

def _range_overlaps(range_a: str, range_b: str) -> bool:
    """判断两个 Excel 范围是否有交集。"""
    try:
        from openpyxl.utils.cell import range_boundaries
        a_min_col, a_min_row, a_max_col, a_max_row = range_boundaries(range_a)
        b_min_col, b_min_row, b_max_col, b_max_row = range_boundaries(range_b)
    except (ValueError, TypeError):
        return False
    return not (
        a_max_row < b_min_row or b_max_row < a_min_row
        or a_max_col < b_min_col or b_max_col < a_min_col
    )



def _render_background_projection(
    projection: NoticeProjection,
    *,
    payload: ToolPayloadProjection | None,
    intent_profile: dict[str, Any] | None,
) -> str:
    intent = str((intent_profile or {}).get("intent") or projection.intent or "general").strip().lower()
    focus = str((intent_profile or {}).get("focus_text") or "通用浏览")
    if projection.kind == "explorer":
        title = projection.title or "资源管理器"
        summary = projection.summary or "目录视图"
        return f"[BG -- {title}] {summary}"

    file_name = payload.file if payload is not None else "未知文件"
    sheet_name = payload.sheet if payload is not None else "未知Sheet"
    lines = [f"[BG -- {file_name} / {sheet_name}]"]
    lines.append(f"{projection.rows}r x {projection.cols}c")

    if projection.preview_rows:
        cols = [str(k) for k in projection.preview_rows[0].keys()][:10]
        if cols:
            lines.append("列: " + ", ".join(cols))
    lines.append(f"intent: {intent}（{focus}）")

    parts: list[str] = []
    if projection.range_ref:
        parts.append(f"viewport: {projection.range_ref}")
    if payload is not None and payload.sheet_tabs:
        tabs = [f"[{name}]" for name in payload.sheet_tabs[:8]]
        if len(payload.sheet_tabs) > 8:
            tabs.append("...")
        parts.append("tabs: " + " ".join(tabs))
    if parts:
        lines.append(" | ".join(parts))
    return "\n".join(lines)


def _render_minimized_projection(
    projection: NoticeProjection,
    *,
    payload: ToolPayloadProjection | None,
    intent_profile: dict[str, Any] | None,
) -> str:
    intent = str((intent_profile or {}).get("intent") or projection.intent or "general").strip().lower()
    if projection.kind == "explorer":
        title = projection.title or "资源管理器"
        summary = projection.summary or "目录视图"
        return f"[IDLE -- {title}] {summary}"

    file_name = payload.file if payload is not None else "未知文件"
    sheet_name = payload.sheet if payload is not None else "未知Sheet"
    if projection.rows > 0 and projection.cols > 0:
        return f"[IDLE -- {file_name} / {sheet_name} | {projection.rows}x{projection.cols}] intent={intent}"
    summary = projection.summary or "上次视图已压缩"
    return f"[IDLE -- {file_name} / {sheet_name}] {summary} | intent={intent}"


def _render_sheet(window: SheetWindow) -> str:
    file_name = window.file_path or "未知文件"
    sheet_name = window.sheet_name or "未知Sheet"
    lines = [f"[ACTIVE -- {file_name} / {sheet_name}]"]

    if window.sheet_tabs:
        tabs = []
        current = sheet_name
        for item in window.sheet_tabs:
            token = f">{item}" if item == current else item
            tabs.append(f"[{token}]")
        lines.append("tabs: " + " ".join(tabs))

    viewport = window.viewport
    if viewport is not None:
        lines.append(
            "range: "
            f"{viewport.range_ref} ({viewport.total_rows}r x {viewport.total_cols}c)"
        )

    if window.freeze_panes:
        lines.append(f"freeze: {window.freeze_panes}")

    preview = window.preview_rows
    if isinstance(preview, list) and preview:
        lines.append("preview:")
        lines.extend(_render_preview(preview, max_rows=8))

    # 列截断提示：当实际列数超过视口可见列数时，提醒 LLM 还有未显示的列
    if viewport is not None and viewport.total_cols > viewport.visible_cols > 0:
        lines.append(
            f"⚠️ 还有 {viewport.total_cols - viewport.visible_cols} 列未在视口中显示"
            f"（总列数: {viewport.total_cols}）"
        )

    # 数据充分性警告：聚合意图下样本不足时提醒
    preview_len = len(preview) if isinstance(preview, list) else 0
    sheet_total_rows = viewport.total_rows if viewport is not None else 0
    if (
        window.intent_tag == IntentTag.AGGREGATE
        and sheet_total_rows > preview_len * 2
        and preview_len > 0
    ):
        lines.append(
            f"⚠️ 当前仅显示 {preview_len} 行样本（共 {sheet_total_rows} 行），"
            f"分组统计需通过 run_code 执行 pandas 聚合"
        )

    if window.style_summary:
        lines.append("style:")
        lines.append(f"  {window.style_summary}")

    scroll = window.scroll_position
    if scroll:
        lines.append(
            "scroll: "
            f"v={_format_percent(scroll.get('vertical_pct'))} | "
            f"h={_format_percent(scroll.get('horizontal_pct'))}"
        )

    status_bar = window.status_bar
    if status_bar:
        lines.append(
            "stats: "
            f"SUM={_format_number(status_bar.get('sum'))} | "
            f"COUNT={_format_int(status_bar.get('count'))} | "
            f"AVG={_format_number(status_bar.get('average'))}"
        )

    column_widths = window.column_widths
    if column_widths:
        lines.append(f"col-width: {_format_map_preview(column_widths, max_items=8)}")

    row_heights = window.row_heights
    if row_heights:
        lines.append(f"row-height: {_format_map_preview(row_heights, max_items=8)}")

    merged_ranges = window.merged_ranges
    if merged_ranges:
        merged_preview = ", ".join(str(item) for item in merged_ranges[:6])
        extra = f" ...(+{len(merged_ranges) - 6})" if len(merged_ranges) > 6 else ""
        lines.append(f"merged: {merged_preview}{extra}")

    conditional_effects = window.conditional_effects
    if conditional_effects:
        effect_preview = " | ".join(str(item) for item in conditional_effects[:4])
        extra = f" ...(+{len(conditional_effects) - 4})" if len(conditional_effects) > 4 else ""
        lines.append(f"cond-fmt: {effect_preview}{extra}")

    if window.summary:
        lines.append(f"summary: {window.summary}")

    return "\n".join(lines)


def render_window_wurm_full(
    window: SheetWindow,
    *,
    max_rows: int,
    current_iteration: int,
    intent_profile: dict[str, Any] | None = None,
) -> str:
    """渲染 WURM FULL 模式窗口。"""
    profile = _normalize_intent_profile(window, intent_profile=intent_profile)
    last_op_kind = getattr(window, "last_op_kind", None)
    focus_range = getattr(window, "last_write_range", None)
    is_write_focus = last_op_kind == "write" and bool(focus_range)
    file_name = window.file_path or "未知文件"
    sheet_name = window.sheet_name or "未知Sheet"
    lines = [f"[{window.id} · {file_name} / {sheet_name}]"]
    lines.append(f"intent: {profile['intent']}（{profile['focus_text']}）")

    if window.stale_hint:
        lines.append(f"[STALE] {window.stale_hint}")

    if window.change_log:
        latest = window.change_log[-1]
        lines.append(f"recent: {latest.tool_summary}")

    if window.sheet_tabs:
        tabs = []
        for item in window.sheet_tabs:
            token = f"▶{item}" if item == sheet_name else item
            tabs.append(f"[{token}]")
        lines.append("tabs: " + " ".join(tabs))

    total_rows = window.total_rows or len(window.data_buffer)
    columns = window.columns or window.schema
    total_cols = window.total_cols or len(columns)
    lines.append(f"range: {total_rows}r x {total_cols}c | viewport: {window.viewport_range or '-'}")

    column_names = [col.name for col in columns]
    if not column_names and window.data_buffer:
        column_names = [str(key) for key in window.data_buffer[0].keys()]
    if column_names:
        lines.append("cols: [" + ", ".join(column_names) + "]")

    if profile.get("show_style"):
        lines.extend(_render_style_summary_lines(window))
    if profile.get("show_quality"):
        lines.append("quality: " + _build_quality_summary(window.data_buffer))
    if profile.get("show_formula"):
        lines.append("formula: " + _build_formula_summary(window))
    if profile.get("show_change") and window.change_log:
        latest = window.change_log[-1]
        lines.append(f"change-focus: {latest.tool_summary} @ {latest.affected_range}")

    # 多范围优先，按当前视口块置后展示以提升注意力。
    render_max_rows = max(1, min(max_rows, int(profile.get("max_rows", max_rows))))
    ranges = list(window.cached_ranges)
    if ranges:
        ranges.sort(key=lambda item: (0 if not item.is_current_viewport else 1, item.added_at_iteration))
        any_focus_hit = False
        for cached in ranges:
            marker = " [current-viewport]" if cached.is_current_viewport else ""
            if is_write_focus:
                if _range_overlaps(cached.range_ref, focus_range):
                    any_focus_hit = True
                    focus_marker = " [FOCUS·STALE]" if window.stale_hint else " [FOCUS]"
                    lines.append(f"-- cached {cached.range_ref} ({len(cached.rows)}r){marker}{focus_marker} --")
                    rows_to_render = cached.rows
                    if profile.get("show_quality"):
                        rows_to_render = _pick_anomaly_rows(cached.rows, limit=render_max_rows) or cached.rows
                    lines.extend(_render_pipe_rows(
                        rows=rows_to_render,
                        columns=column_names,
                        max_rows=render_max_rows,
                        current_iteration=current_iteration,
                        changed_indices=set(window.change_log[-1].affected_row_indices) if window.change_log else set(),
                    ))
                else:
                    lines.append(f"-- cached {cached.range_ref} ({len(cached.rows)}r){marker} [collapsed] --")
            else:
                rows_to_render = cached.rows
                if profile.get("show_quality"):
                    rows_to_render = _pick_anomaly_rows(cached.rows, limit=render_max_rows) or cached.rows
                lines.append(f"-- cached {cached.range_ref} ({len(cached.rows)}r){marker} --")
                lines.extend(_render_pipe_rows(
                    rows=rows_to_render,
                    columns=column_names,
                    max_rows=render_max_rows,
                    current_iteration=current_iteration,
                    changed_indices=set(window.change_log[-1].affected_row_indices) if window.change_log else set(),
                ))
        if is_write_focus and not any_focus_hit:
            lines.append(f"⚠️ 写入范围 {focus_range} 不在缓存视口中，数据可能需要重新读取")
    else:
        rows_to_render = window.data_buffer
        if profile.get("show_quality"):
            rows_to_render = _pick_anomaly_rows(window.data_buffer, limit=render_max_rows) or window.data_buffer
        if is_write_focus:
            lines.append(f"data: (write-focus @ {focus_range})")
        else:
            lines.append("data:")
        lines.extend(_render_pipe_rows(
            rows=rows_to_render,
            columns=column_names,
            max_rows=render_max_rows,
            current_iteration=current_iteration,
            changed_indices=set(window.change_log[-1].affected_row_indices) if window.change_log else set(),
        ))

    # 列截断提示：当实际列数超过可见列数时，提醒 LLM 还有未显示的列
    visible_cols = len(column_names)
    if total_cols > visible_cols > 0:
        lines.append(f"⚠️ 还有 {total_cols - visible_cols} 列未在视口中显示（总列数: {total_cols}）")

    # 数据充分性警告：当 total_rows 远大于预览行数且意图为聚合时，提醒 LLM 样本不足
    if profile["intent"] == IntentTag.AGGREGATE.value and total_rows > render_max_rows * 2:
        lines.append(
            f"⚠️ 当前仅显示 {render_max_rows} 行样本（共 {total_rows} 行），"
            f"分组统计需通过 run_code 执行 pandas 聚合"
        )

    status_bar = window.status_bar
    if status_bar:
        lines.append(
            "统计: "
            f"SUM={_format_number(status_bar.get('sum'))} | "
            f"COUNT={_format_int(status_bar.get('count'))} | "
            f"AVG={_format_number(status_bar.get('average'))}"
        )
        # intent=aggregate 时输出分类列 top-N 频次分布
        if profile["intent"] == IntentTag.AGGREGATE.value:
            cat = status_bar.get("categorical")
            if isinstance(cat, dict) and cat:
                for col_name, ranked in cat.items():
                    if not ranked:
                        continue
                    pairs = ", ".join(f"{v}({c})" for v, c in ranked)
                    lines.append(f"  分布·{col_name}: {pairs}")
    return "\n".join(lines)


def _normalize_intent_profile(
    window: Window,
    *,
    intent_profile: dict[str, Any] | None,
) -> dict[str, Any]:
    profile = dict(intent_profile or {})
    intent_value = str(profile.get("intent") or window.intent_tag.value).strip().lower()
    try:
        intent = IntentTag(intent_value)
    except ValueError:
        intent = IntentTag.GENERAL

    focus_map = {
        IntentTag.AGGREGATE: "统计优先",
        IntentTag.FORMAT: "样式优先",
        IntentTag.VALIDATE: "质量校验优先",
        IntentTag.FORMULA: "公式排查优先",
        IntentTag.ENTRY: "写入变更优先",
        IntentTag.GENERAL: "通用浏览",
    }
    profile["intent"] = intent.value
    profile.setdefault("focus_text", focus_map[intent])
    profile.setdefault("show_style", intent == IntentTag.FORMAT)
    profile.setdefault("show_quality", intent == IntentTag.VALIDATE)
    profile.setdefault("show_formula", intent == IntentTag.FORMULA)
    profile.setdefault("show_change", intent in {IntentTag.ENTRY, IntentTag.FORMAT, IntentTag.FORMULA})
    default_rows = 25 if intent in {IntentTag.AGGREGATE, IntentTag.GENERAL} else 5
    if intent == IntentTag.FORMAT:
        default_rows = 3
    profile.setdefault("max_rows", default_rows)
    return profile


def _normalize_projection_intent_profile(
    projection: NoticeProjection,
    *,
    intent_profile: dict[str, Any] | None,
) -> dict[str, Any]:
    profile = dict(intent_profile or {})
    intent_value = str(profile.get("intent") or projection.intent or IntentTag.GENERAL.value).strip().lower()
    try:
        intent = IntentTag(intent_value)
    except ValueError:
        intent = IntentTag.GENERAL

    focus_map = {
        IntentTag.AGGREGATE: "统计优先",
        IntentTag.FORMAT: "样式优先",
        IntentTag.VALIDATE: "质量校验优先",
        IntentTag.FORMULA: "公式排查优先",
        IntentTag.ENTRY: "写入变更优先",
        IntentTag.GENERAL: "通用浏览",
    }
    profile["intent"] = intent.value
    profile.setdefault("focus_text", focus_map[intent])
    return profile


def _render_style_summary_lines(window: SheetWindow) -> list[str]:
    lines: list[str] = []
    if window.style_summary:
        lines.append(f"style: {window.style_summary}")
    column_widths = window.column_widths
    if column_widths:
        lines.append(f"col-width: {_format_map_preview(column_widths, max_items=6)}")
    row_heights = window.row_heights
    if row_heights:
        lines.append(f"row-height: {_format_map_preview(row_heights, max_items=6)}")
    merged_ranges = window.merged_ranges
    if merged_ranges:
        merged_preview = ", ".join(str(item) for item in merged_ranges[:4])
        lines.append(f"merged: {merged_preview}")
    conditional_effects = window.conditional_effects
    if conditional_effects:
        lines.append(f"cond-fmt: {len(conditional_effects)} rules")
    return lines


def _build_quality_summary(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "无数据可校验"
    empty_cells = 0
    signature_counter: dict[str, int] = {}
    duplicate_rows = 0
    for row in rows:
        empty_cells += sum(1 for value in row.values() if value is None or value == "")
        signature = "|".join(str(value) for value in row.values())
        signature_counter[signature] = signature_counter.get(signature, 0) + 1
        if signature_counter[signature] == 2:
            duplicate_rows += 1
    return f"empty_cells={empty_cells}, dup_rows={duplicate_rows}"


def _build_formula_summary(window: Window) -> str:
    hints: list[str] = []
    for row in window.data_buffer[:30]:
        for key, value in row.items():
            text = str(value or "")
            if "=" in text or _looks_like_formula_call(text):
                hints.append(f"{key}:{text[:24]}")
                if len(hints) >= 3:
                    break
        if len(hints) >= 3:
            break
    if hints:
        return " | ".join(hints)
    if window.change_log:
        latest = window.change_log[-1]
        return f"recent formula area: {latest.affected_range}"
    return "no formula hints detected"


def _looks_like_formula_call(text: str) -> bool:
    upper = text.upper()
    return any(token in upper for token in ("SUMIFS(", "VLOOKUP(", "XLOOKUP(", "INDEX(", "MATCH(", "IF("))


def _pick_anomaly_rows(rows: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    if not rows or limit <= 0:
        return []
    anomalies: list[dict[str, Any]] = []
    seen: set[str] = set()
    duplicates: set[str] = set()
    for row in rows:
        signature = "|".join(str(value) for value in row.values())
        if signature in seen:
            duplicates.add(signature)
        else:
            seen.add(signature)

    for row in rows:
        has_empty = any(value is None or value == "" for value in row.values())
        signature = "|".join(str(value) for value in row.values())
        if has_empty or signature in duplicates:
            anomalies.append(row)
        if len(anomalies) >= limit:
            break
    return anomalies


def _render_preview(rows: list[Any], *, max_rows: int) -> list[str]:
    rendered: list[str] = []
    for idx, row in enumerate(rows[:max_rows], start=1):
        if isinstance(row, dict):
            parts = []
            for key, value in list(row.items())[:6]:
                parts.append(f"{key}={value}")
            rendered.append(f"  {idx}. " + ", ".join(parts))
        elif isinstance(row, list):
            values = ", ".join(str(item) for item in row[:8])
            rendered.append(f"  {idx}. {values}")
        else:
            rendered.append(f"  {idx}. {row}")
    if len(rows) > max_rows:
        rendered.append("  ...")
    return rendered


def _format_percent(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = 0.0
    return f"{number:.1f}%"


def _format_number(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = 0.0
    rounded = round(number, 2)
    if abs(rounded - int(rounded)) < 1e-9:
        return f"{int(rounded):,}"
    return f"{rounded:,.2f}"


def _format_int(value: Any) -> str:
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        number = 0
    return f"{number:,}"


def _format_map_preview(values: dict[str, Any], *, max_items: int) -> str:
    ordered = sorted(values.items(), key=lambda item: str(item[0]))
    chunks = [f"{key}={_format_number(val)}" for key, val in ordered[:max_items]]
    if len(ordered) > max_items:
        chunks.append(f"...(+{len(ordered) - max_items})")
    return ", ".join(chunks)


def _extract_columns_from_preview(rows: list[Any]) -> list[str]:
    columns: list[str] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        for key in row.keys():
            name = str(key).strip()
            if not name or name in seen:
                continue
            seen.add(name)
            columns.append(name)
    return columns


def _render_pipe_rows(
    *,
    rows: list[dict[str, Any]],
    columns: list[str],
    max_rows: int,
    current_iteration: int,
    changed_indices: set[int],
) -> list[str]:
    _ = current_iteration
    if not rows:
        return ["  (no data)"]
    if max_rows <= 0:
        max_rows = 1

    effective_rows = rows[:max_rows]
    output: list[str] = []
    for idx, row in enumerate(effective_rows):
        values = [str(row.get(name, "")) for name in columns] if columns else [str(v) for v in row.values()]
        prefix = "* " if idx in changed_indices else "  "
        output.append(prefix + " | ".join(values[:8]))

    omitted = len(rows) - len(effective_rows)
    if omitted > 0:
        output.append(f"  ... ({len(rows)} total, {omitted} omitted)")
    return output
