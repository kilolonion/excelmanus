"""窗口感知层渲染器。"""

from __future__ import annotations

from typing import Any

from .models import DetailLevel, IntentTag, WindowSnapshot, WindowState, WindowType


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
            "窗口内容与工具执行结果完全等价，若已包含所需信息请直接引用，无需重复调用工具。\n\n"
            + body
        )
    return (
        "## 窗口感知上下文\n"
        "以下是你当前已打开的窗口实时状态，数据与工具返回完全一致。\n"
        "如果所需信息已在下方窗口中，直接引用回答或基于窗口数据调用工具执行，无需重复读取。\n\n"
        + body
    )


def render_window_keep(
    window: WindowState,
    *,
    mode: str = "enriched",
    max_rows: int = 25,
    current_iteration: int = 0,
    intent_profile: dict[str, Any] | None = None,
) -> str:
    """渲染 ACTIVE 窗口。"""
    if window.type == WindowType.EXPLORER:
        return _render_explorer(window)
    if mode in {"anchored", "unified"}:
        if window.detail_level == DetailLevel.ICON:
            return render_window_minimized(window, intent_profile=intent_profile)
        if window.detail_level == DetailLevel.SUMMARY:
            return render_window_background(window, intent_profile=intent_profile)
        if window.detail_level == DetailLevel.NONE:
            return ""
        if window.data_buffer:
            return render_window_wurm_full(
                window,
                max_rows=max_rows,
                current_iteration=current_iteration,
                intent_profile=intent_profile,
            )
    return _render_sheet(window)


def render_window_background(
    window: WindowState,
    *,
    intent_profile: dict[str, Any] | None = None,
) -> str:
    """渲染 BACKGROUND 窗口（结构缩略图）。"""
    profile = _normalize_intent_profile(window, intent_profile=intent_profile)
    if window.type == WindowType.EXPLORER:
        title = window.title or "资源管理器"
        summary = window.summary or "目录视图"
        return f"【后台 · {title}】{summary}"

    file_name = window.file_path or "未知文件"
    sheet_name = window.sheet_name or "未知Sheet"
    lines = [f"【后台 · {file_name} / {sheet_name}】"]

    viewport = window.viewport
    if viewport is not None:
        lines.append(f"{viewport.total_rows}行 × {viewport.total_cols}列")

    columns = _extract_columns_from_preview(window.preview_rows)
    if columns:
        lines.append("列: " + ", ".join(columns))
    lines.append(f"意图: {profile['intent']}（{profile['focus_text']}）")

    if profile.get("show_style"):
        lines.extend(_render_style_summary_lines(window))
    if profile.get("show_quality"):
        lines.append("质量: " + _build_quality_summary(window.data_buffer))
    if profile.get("show_formula"):
        lines.append("公式线索: " + _build_formula_summary(window))
    if profile.get("show_change") and window.change_log:
        latest = window.change_log[-1]
        lines.append(f"最近变更: {latest.tool_summary}")

    parts: list[str] = []
    if viewport is not None:
        parts.append(f"视口: {viewport.range_ref}")
    if window.sheet_tabs:
        tabs = [f"[{name}]" for name in window.sheet_tabs[:8]]
        if len(window.sheet_tabs) > 8:
            tabs.append("...")
        parts.append("Tabs: " + " ".join(tabs))
    if parts:
        lines.append(" | ".join(parts))

    return "\n".join(lines)


def render_window_minimized(
    window: WindowState,
    *,
    intent_profile: dict[str, Any] | None = None,
) -> str:
    """渲染 SUSPENDED 窗口（一行摘要）。"""
    profile = _normalize_intent_profile(window, intent_profile=intent_profile)
    if window.type == WindowType.EXPLORER:
        title = window.title or "资源管理器"
        summary = window.summary or "目录视图"
        return f"【挂起 · {title}】{summary}"

    file_name = window.file_path or "未知文件"
    sheet_name = window.sheet_name or "未知Sheet"
    viewport = window.viewport
    if viewport is not None and viewport.total_rows > 0 and viewport.total_cols > 0:
        return (
            f"【挂起 · {file_name} / {sheet_name} | {viewport.total_rows}×{viewport.total_cols}】"
            f" 意图={profile['intent']}"
        )

    summary = window.summary or "上次视图已压缩"
    return f"【挂起 · {file_name} / {sheet_name}】{summary} | 意图={profile['intent']}"


def build_tool_perception_payload(window: WindowState | None) -> dict[str, Any] | None:
    """生成工具返回增强 payload。"""
    if window is None:
        return None

    if window.type == WindowType.EXPLORER:
        return {
            "window_type": "explorer",
            "title": window.title,
            "directory": window.directory,
            "entries": list(window.metadata.get("entries", []))[:12],
        }

    viewport = window.viewport
    scroll = window.metadata.get("scroll_position")
    status_bar = window.metadata.get("status_bar")
    column_widths = window.metadata.get("column_widths")
    row_heights = window.metadata.get("row_heights")
    merged_ranges = window.metadata.get("merged_ranges")
    conditional_effects = window.metadata.get("conditional_effects")
    return {
        "window_type": "sheet",
        "file": window.file_path,
        "sheet": window.sheet_name,
        "intent": window.intent_tag.value,
        "sheet_tabs": window.sheet_tabs,
        "viewport": {
            "range": viewport.range_ref if viewport else "",
            "visible_rows": viewport.visible_rows if viewport else 0,
            "visible_cols": viewport.visible_cols if viewport else 0,
            "total_rows": viewport.total_rows if viewport else 0,
            "total_cols": viewport.total_cols if viewport else 0,
        },
        "freeze_panes": window.freeze_panes,
        "style_summary": window.style_summary,
        "scroll_position": scroll if isinstance(scroll, dict) else {},
        "status_bar": status_bar if isinstance(status_bar, dict) else {},
        "column_widths": column_widths if isinstance(column_widths, dict) else {},
        "row_heights": row_heights if isinstance(row_heights, dict) else {},
        "merged_ranges": merged_ranges if isinstance(merged_ranges, list) else [],
        "conditional_effects": conditional_effects if isinstance(conditional_effects, list) else [],
    }


def render_tool_perception_block(payload: dict[str, Any] | None) -> str:
    """渲染文本增强块。"""
    if not isinstance(payload, dict):
        return ""

    if payload.get("window_type") == "explorer":
        lines = [
            "───────────── 环境感知 ─────────────",
            f"📁 目录: {payload.get('directory') or '.'}",
        ]
        entries = payload.get("entries")
        if isinstance(entries, list) and entries:
            for entry in entries[:8]:
                lines.append(f"  · {entry}")
        lines.append("────────────────────────────────────")
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
        "───────────── 环境感知 ─────────────",
        f"📊 文件: {payload.get('file') or '未知'}",
        f"🧠 意图: {payload.get('intent') or 'general'}",
        (
            f"📑 当前Sheet: {current_sheet} | 其他: {' '.join(other_tabs)}"
            if other_tabs
            else f"📑 当前Sheet: {current_sheet}"
        ),
        (
            "📐 数据范围: "
            f"{viewport.get('total_rows', 0)}行 × {viewport.get('total_cols', 0)}列"
        ),
        f"📍 当前视口: {viewport.get('range') or '未知'}",
    ]
    freeze = payload.get("freeze_panes")
    if freeze:
        lines.append(f"🧊 冻结: {freeze}")

    scroll = payload.get("scroll_position")
    if isinstance(scroll, dict) and scroll:
        vertical = _format_percent(scroll.get("vertical_pct"))
        horizontal = _format_percent(scroll.get("horizontal_pct"))
        remain_rows = _format_percent(scroll.get("remaining_rows_pct"))
        remain_cols = _format_percent(scroll.get("remaining_cols_pct"))
        lines.append(f"🧭 滚动条位置: 纵向 {vertical} | 横向 {horizontal}")
        lines.append(f"↘️  剩余数据: 下方 {remain_rows} | 右侧 {remain_cols}")

    status_bar = payload.get("status_bar")
    if isinstance(status_bar, dict) and status_bar:
        lines.append(
            "📊 状态栏: "
            f"SUM={_format_number(status_bar.get('sum'))} | "
            f"COUNT={_format_int(status_bar.get('count'))} | "
            f"AVERAGE={_format_number(status_bar.get('average'))}"
        )

    column_widths = payload.get("column_widths")
    if isinstance(column_widths, dict) and column_widths:
        lines.append(f"📏 列宽: {_format_map_preview(column_widths, max_items=8)}")

    row_heights = payload.get("row_heights")
    if isinstance(row_heights, dict) and row_heights:
        lines.append(f"📐 行高: {_format_map_preview(row_heights, max_items=8)}")

    merged_ranges = payload.get("merged_ranges")
    if isinstance(merged_ranges, list) and merged_ranges:
        merged_preview = ", ".join(str(item) for item in merged_ranges[:6])
        extra = f" ...(+{len(merged_ranges) - 6})" if len(merged_ranges) > 6 else ""
        lines.append(f"🔗 合并单元格: {merged_preview}{extra}")

    conditional_effects = payload.get("conditional_effects")
    if isinstance(conditional_effects, list) and conditional_effects:
        effect_preview = " | ".join(str(item) for item in conditional_effects[:4])
        extra = f" ...(+{len(conditional_effects) - 4})" if len(conditional_effects) > 4 else ""
        lines.append(f"🎯 条件格式效果: {effect_preview}{extra}")

    style_summary = payload.get("style_summary")
    if style_summary:
        lines.append(f"🎨 样式概要: {style_summary}")
    lines.append("────────────────────────────────────")
    return "\n".join(lines)


def _render_explorer(window: WindowState) -> str:
    lines = [
        "【当前环境 · 资源管理器】",
        f"📁 {window.directory or '.'}",
    ]
    entries = window.metadata.get("entries")
    if isinstance(entries, list) and entries:
        for entry in entries[:15]:
            lines.append(f"{entry}")
    elif window.summary:
        lines.append(window.summary)
    return "\n".join(lines)


def _render_sheet(window: WindowState) -> str:
    file_name = window.file_path or "未知文件"
    sheet_name = window.sheet_name or "未知Sheet"
    lines = [f"【窗口 · {file_name} / {sheet_name}】"]

    if window.sheet_tabs:
        tabs = []
        current = sheet_name
        for item in window.sheet_tabs:
            token = f"▶{item}" if item == current else item
            tabs.append(f"[{token}]")
        lines.append("工作表: " + " ".join(tabs))

    viewport = window.viewport
    if viewport is not None:
        lines.append(
            "可见区域: "
            f"{viewport.range_ref}（共 {viewport.total_rows}行 × {viewport.total_cols}列）"
        )

    if window.freeze_panes:
        lines.append(f"冻结窗格: {window.freeze_panes}")

    preview = window.preview_rows
    if isinstance(preview, list) and preview:
        lines.append("预览数据:")
        lines.extend(_render_preview(preview, max_rows=8))

    if window.style_summary:
        lines.append("样式信息:")
        lines.append(f"  · {window.style_summary}")

    scroll = window.metadata.get("scroll_position")
    if isinstance(scroll, dict) and scroll:
        lines.append(
            "滚动条位置: "
            f"纵向 {_format_percent(scroll.get('vertical_pct'))} | "
            f"横向 {_format_percent(scroll.get('horizontal_pct'))}"
        )

    status_bar = window.metadata.get("status_bar")
    if isinstance(status_bar, dict) and status_bar:
        lines.append(
            "状态栏: "
            f"SUM={_format_number(status_bar.get('sum'))} | "
            f"COUNT={_format_int(status_bar.get('count'))} | "
            f"AVERAGE={_format_number(status_bar.get('average'))}"
        )

    column_widths = window.metadata.get("column_widths")
    if isinstance(column_widths, dict) and column_widths:
        lines.append(f"列宽: {_format_map_preview(column_widths, max_items=8)}")

    row_heights = window.metadata.get("row_heights")
    if isinstance(row_heights, dict) and row_heights:
        lines.append(f"行高: {_format_map_preview(row_heights, max_items=8)}")

    merged_ranges = window.metadata.get("merged_ranges")
    if isinstance(merged_ranges, list) and merged_ranges:
        merged_preview = ", ".join(str(item) for item in merged_ranges[:6])
        extra = f" ...(+{len(merged_ranges) - 6})" if len(merged_ranges) > 6 else ""
        lines.append(f"合并单元格: {merged_preview}{extra}")

    conditional_effects = window.metadata.get("conditional_effects")
    if isinstance(conditional_effects, list) and conditional_effects:
        effect_preview = " | ".join(str(item) for item in conditional_effects[:4])
        extra = f" ...(+{len(conditional_effects) - 4})" if len(conditional_effects) > 4 else ""
        lines.append(f"条件格式效果: {effect_preview}{extra}")

    if window.summary:
        lines.append(f"摘要: {window.summary}")

    return "\n".join(lines)


def render_window_wurm_full(
    window: WindowState,
    *,
    max_rows: int,
    current_iteration: int,
    intent_profile: dict[str, Any] | None = None,
) -> str:
    """渲染 WURM FULL 模式窗口。"""
    profile = _normalize_intent_profile(window, intent_profile=intent_profile)
    file_name = window.file_path or "未知文件"
    sheet_name = window.sheet_name or "未知Sheet"
    lines = [f"[{window.id} · {file_name} / {sheet_name}]"]
    lines.append(f"🎯 意图: {profile['intent']}（{profile['focus_text']}）")

    if window.stale_hint:
        lines.append(f"⚠ stale: {window.stale_hint}")

    if window.change_log:
        latest = window.change_log[-1]
        lines.append(f"📝 最近: {latest.tool_summary}")

    if window.sheet_tabs:
        tabs = []
        for item in window.sheet_tabs:
            token = f"▶{item}" if item == sheet_name else item
            tabs.append(f"[{token}]")
        lines.append("Tabs: " + " ".join(tabs))

    total_rows = window.total_rows or len(window.data_buffer)
    columns = window.columns or window.schema
    total_cols = window.total_cols or len(columns)
    lines.append(f"范围: {total_rows}行×{total_cols}列 | 视口: {window.viewport_range or '-'}")

    column_names = [col.name for col in columns]
    if not column_names and window.data_buffer:
        column_names = [str(key) for key in window.data_buffer[0].keys()]
    if column_names:
        lines.append("列: [" + ", ".join(column_names) + "]")

    if profile.get("show_style"):
        lines.extend(_render_style_summary_lines(window))
    if profile.get("show_quality"):
        lines.append("质量: " + _build_quality_summary(window.data_buffer))
    if profile.get("show_formula"):
        lines.append("公式线索: " + _build_formula_summary(window))
    if profile.get("show_change") and window.change_log:
        latest = window.change_log[-1]
        lines.append(f"变更聚焦: {latest.tool_summary} @ {latest.affected_range}")

    # 多范围优先，按当前视口块置后展示以提升注意力。
    render_max_rows = max(1, min(max_rows, int(profile.get("max_rows", max_rows))))
    ranges = list(window.cached_ranges)
    if ranges:
        ranges.sort(key=lambda item: (0 if not item.is_current_viewport else 1, item.added_at_iteration))
        for cached in ranges:
            marker = " [当前视口]" if cached.is_current_viewport else ""
            rows_to_render = cached.rows
            if profile.get("show_quality"):
                rows_to_render = _pick_anomaly_rows(cached.rows, limit=render_max_rows) or cached.rows
            lines.append(f"── 缓存范围 {cached.range_ref} ({len(cached.rows)}行){marker} ──")
            lines.extend(_render_pipe_rows(
                rows=rows_to_render,
                columns=column_names,
                max_rows=render_max_rows,
                current_iteration=current_iteration,
                changed_indices=set(window.change_log[-1].affected_row_indices) if window.change_log else set(),
            ))
    else:
        rows_to_render = window.data_buffer
        if profile.get("show_quality"):
            rows_to_render = _pick_anomaly_rows(window.data_buffer, limit=render_max_rows) or window.data_buffer
        lines.append("数据:")
        lines.extend(_render_pipe_rows(
            rows=rows_to_render,
            columns=column_names,
            max_rows=render_max_rows,
            current_iteration=current_iteration,
            changed_indices=set(window.change_log[-1].affected_row_indices) if window.change_log else set(),
        ))

    status_bar = window.metadata.get("status_bar")
    if isinstance(status_bar, dict) and status_bar:
        lines.append(
            "统计: "
            f"SUM={_format_number(status_bar.get('sum'))} | "
            f"COUNT={_format_int(status_bar.get('count'))} | "
            f"AVG={_format_number(status_bar.get('average'))}"
        )
    return "\n".join(lines)


def _normalize_intent_profile(
    window: WindowState,
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


def _render_style_summary_lines(window: WindowState) -> list[str]:
    lines: list[str] = []
    if window.style_summary:
        lines.append(f"样式摘要: {window.style_summary}")
    column_widths = window.metadata.get("column_widths")
    if isinstance(column_widths, dict) and column_widths:
        lines.append(f"列宽: {_format_map_preview(column_widths, max_items=6)}")
    row_heights = window.metadata.get("row_heights")
    if isinstance(row_heights, dict) and row_heights:
        lines.append(f"行高: {_format_map_preview(row_heights, max_items=6)}")
    merged_ranges = window.metadata.get("merged_ranges")
    if isinstance(merged_ranges, list) and merged_ranges:
        merged_preview = ", ".join(str(item) for item in merged_ranges[:4])
        lines.append(f"合并: {merged_preview}")
    conditional_effects = window.metadata.get("conditional_effects")
    if isinstance(conditional_effects, list) and conditional_effects:
        lines.append(f"条件格式: {len(conditional_effects)}条")
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
    return f"空值单元格={empty_cells}，疑似重复行={duplicate_rows}"


def _build_formula_summary(window: WindowState) -> str:
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
        return f"最近公式相关变更区域: {latest.affected_range}"
    return "未检测到明显公式线索"


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
        return ["  (无数据)"]
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
        output.append(f"  ... (共{len(rows)}行，省略{omitted}行)")
    return output
