"""窗口类型策略：按窗口类型分发 ingest / confirm / render 行为。"""

from __future__ import annotations

from typing import Any, Protocol

from .domain import ExplorerWindow, SheetWindow, Window
from .extractor import (
    extract_directory,
    extract_explorer_entries,
    is_excel_path,
    normalize_path,
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
    ChangeRecord,
    DetailLevel,
    IntentTag,
    WindowType,
)


# ── ASCII 标记常量 ────────────────────────────────────────

TAG_OK = "[OK]"
TAG_FAIL = "[FAIL]"
TAG_DIR = "[DIR]"
TAG_XLS = "[XLS]"
TAG_FILE = "[FILE]"
TAG_STALE = "[STALE]"

SECTION_BEGIN = "--- perception ---"
SECTION_END = "--- end ---"

# 写入类工具集合（与 manager._apply_ingest 保持一致）
_WRITE_TOOLS = {
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
}


# ── 策略协议 ──────────────────────────────────────────────


class WindowTypeStrategy(Protocol):
    """窗口类型行为策略。"""

    def should_replace_result(self) -> bool:
        """unified 模式下是否用确认文本替代原始结果。

        返回 False 时走 enriched fallback（保留原始结果 + 追加感知块）。
        """
        ...

    def build_inline_confirmation(
        self,
        window: Window,
        tool_name: str,
        result_json: dict[str, Any] | None,
    ) -> str:
        """构建类型特定的 inline 确认文本。"""
        ...

    def apply_ingest(
        self,
        window: Window,
        tool_name: str,
        arguments: dict[str, Any],
        result_json: dict[str, Any] | None,
        iteration: int,
    ) -> None:
        """将工具结果摄入窗口数据容器。"""
        ...

    def render_full(
        self,
        window: Window,
        *,
        max_rows: int,
        current_iteration: int,
        intent_profile: dict[str, Any] | None,
    ) -> str:
        """渲染完整窗口内容（system_notice 中的 ACTIVE 级别）。"""
        ...

    def render_background(
        self,
        window: Window,
        *,
        intent_profile: dict[str, Any] | None,
    ) -> str:
        """渲染背景摘要。"""
        ...

    def render_minimized(
        self,
        window: Window,
        *,
        intent_profile: dict[str, Any] | None,
    ) -> str:
        """渲染最小化摘要。"""
        ...


# ── 辅助函数 ──────────────────────────────────────────────


def _entry_tag(name: str, item_type: str) -> str:
    """根据文件名和类型返回 ASCII 标记。"""
    if item_type == "directory":
        return TAG_DIR
    if is_excel_path(name):
        return TAG_XLS
    return TAG_FILE


def _format_explorer_entries_ascii(result_json: dict[str, Any] | None) -> list[str]:
    """从工具结果提取 explorer 条目，使用 ASCII 标记。"""
    if not isinstance(result_json, dict):
        return []

    lines: list[str] = []

    # inspect_excel_files 返回的 files 数组（含 sheet 详情）
    files = result_json.get("files")
    if isinstance(files, list):
        for item in files[:12]:
            if not isinstance(item, dict):
                continue
            file_name = str(item.get("file", "")).strip()
            if not file_name:
                continue
            modified = str(item.get("modified", "")).strip()
            size = str(item.get("size", "")).strip()
            details: list[str] = []
            if size:
                details.append(size)
            if modified:
                details.append(modified)
            detail_str = f" ({', '.join(details)})" if details else ""
            lines.append(f"{TAG_XLS} {file_name}{detail_str}")
            # 附加 sheet 详情
            sheets = item.get("sheets")
            if isinstance(sheets, list):
                for sheet in sheets[:6]:
                    if not isinstance(sheet, dict):
                        continue
                    sn = str(sheet.get("name", "")).strip()
                    rows = sheet.get("rows", 0)
                    cols = sheet.get("columns", 0)
                    header = sheet.get("header")
                    header_str = ""
                    if isinstance(header, list) and header:
                        header_str = " | header: [" + ", ".join(str(h) for h in header[:8]) + "]"
                    lines.append(f"  -- {sn}: {rows}r x {cols}c{header_str}")
        return lines

    # list_directory 返回的 entries 数组
    if isinstance(result_json.get("entries"), list):
        for item in result_json["entries"][:20]:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            if not name:
                continue
            item_type = str(item.get("type", "")).strip()
            tag = _entry_tag(name, item_type)
            size = str(item.get("size", "")).strip()
            if size:
                lines.append(f"{tag} {name} ({size})")
            else:
                lines.append(f"{tag} {name}")
        return lines

    # find_files 返回的 matches 数组
    if isinstance(result_json.get("matches"), list):
        for item in result_json["matches"][:20]:
            if not isinstance(item, dict):
                continue
            path = str(item.get("path", item.get("name", ""))).strip()
            if not path:
                continue
            item_type = str(item.get("type", "")).strip()
            tag = _entry_tag(path, item_type)
            lines.append(f"{tag} {path}")
        return lines

    return []


# ── ExplorerStrategy ──────────────────────────────────────


class ExplorerStrategy:
    """explorer 窗口策略：目录浏览 / 文件扫描。"""

    def should_replace_result(self) -> bool:
        return True

    def build_inline_confirmation(
        self,
        window: ExplorerWindow,
        tool_name: str,
        result_json: dict[str, Any] | None,
    ) -> str:
        """生成包含 entries 列表的 inline 确认。"""
        directory = window.directory or "."
        entries = _format_explorer_entries_ascii(result_json)
        count = len(entries)
        label = f"{window.id}: {directory}"

        lines = [f"{TAG_OK} [{label}] {tool_name} | {count} items"]
        # 限制条目数量，避免 token 膨胀
        for entry in entries[:15]:
            lines.append(entry)
        if count > 15:
            lines.append(f"  ... (+{count - 15} more)")
        return "\n".join(lines)

    def apply_ingest(
        self,
        window: ExplorerWindow,
        tool_name: str,
        arguments: dict[str, Any],
        result_json: dict[str, Any] | None,
        iteration: int,
    ) -> None:
        """更新 explorer 窗口的 entries 和元数据。"""
        entries = _format_explorer_entries_ascii(result_json)
        window.entries = entries
        window.total_rows = len(entries)
        window.total_cols = 0
        window.current_iteration = iteration

        # 记录操作和变更
        from .models import ChangeRecord

        window.operation_history.append(
            _make_op_entry(tool_name, arguments, iteration)
        )
        if len(window.operation_history) > window.max_history_entries:
            window.operation_history = window.operation_history[-window.max_history_entries:]

        change = make_change_record(
            operation="scan",
            tool_summary=f"{tool_name}({window.directory or '.'})",
            affected_range="-",
            change_type="refreshed",
            iteration=iteration,
            affected_row_indices=[],
        )
        window.change_log.append(change)
        if len(window.change_log) > window.max_change_records:
            window.change_log = window.change_log[-window.max_change_records:]

    def render_full(
        self,
        window: ExplorerWindow,
        *,
        max_rows: int = 25,
        current_iteration: int = 0,
        intent_profile: dict[str, Any] | None = None,
    ) -> str:
        """渲染完整目录列表。"""
        title = window.title or "资源管理器"
        directory = window.directory or "."
        lines = [
            f"[ACTIVE -- {title}]",
            f"path: {directory}",
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

    def render_background(
        self,
        window: ExplorerWindow,
        *,
        intent_profile: dict[str, Any] | None = None,
    ) -> str:
        """渲染背景摘要。"""
        title = window.title or "资源管理器"
        summary = window.summary or "目录视图"
        return f"[BG -- {title}] {summary}"

    def render_minimized(
        self,
        window: ExplorerWindow,
        *,
        intent_profile: dict[str, Any] | None = None,
    ) -> str:
        """渲染最小化摘要。"""
        title = window.title or "资源管理器"
        summary = window.summary or "目录视图"
        return f"[IDLE -- {title}] {summary}"


# ── SheetStrategy ─────────────────────────────────────────


class SheetStrategy:
    """sheet 窗口策略：Excel 工作表读写。

    封装现有 manager._apply_ingest 中 sheet 分支的逻辑。
    渲染委托回 renderer 模块的现有函数（替换 emoji 后）。
    """

    def should_replace_result(self) -> bool:
        return True

    def build_inline_confirmation(
        self,
        window: SheetWindow,
        tool_name: str,
        result_json: dict[str, Any] | None,
    ) -> str:
        """构建 sheet 确认文本（与现有 confirmation.py 格式一致，emoji→ASCII）。"""
        rows = int(
            window.total_rows
            or (window.viewport.total_rows if window.viewport else 0)
            or len(window.data_buffer)
        )
        cols = int(
            window.total_cols
            or (window.viewport.total_cols if window.viewport else 0)
            or len(window.columns or window.schema)
        )
        file_name = window.file_path or "未知文件"
        sheet_name = window.sheet_name or "未知Sheet"
        label = f"{window.id}: {file_name} / {sheet_name}"
        vp = window.viewport_range or "-"

        change_summary = "状态同步"
        if window.change_log:
            latest = window.change_log[-1]
            if latest.affected_range and latest.affected_range != "-":
                change_summary = f"{latest.change_type}@{latest.affected_range}"
            else:
                change_summary = latest.change_type or latest.tool_summary

        intent = window.intent_tag.value
        return (
            f"{TAG_OK} [{label}] {tool_name}: {vp} | "
            f"{rows}r x {cols}c | {change_summary} | intent={intent}"
        )

    def apply_ingest(
        self,
        window: SheetWindow,
        tool_name: str,
        arguments: dict[str, Any],
        result_json: dict[str, Any] | None,
        iteration: int,
        *,
        default_rows: int = 25,
        default_cols: int = 10,
    ) -> None:
        """将工具结果摄入 sheet 窗口数据容器。

        从 manager._apply_ingest 的 sheet 分支提取而来。
        """
        from .extractor import extract_range_ref

        window.current_iteration = iteration
        rows = extract_data_rows(result_json, tool_name)
        columns = extract_columns(result_json, rows)
        if columns:
            window.columns = columns
            window.schema = list(columns)

        range_ref = extract_range_ref(
            arguments,
            default_rows=default_rows,
            default_cols=default_cols,
        )
        window.viewport_range = range_ref

        if tool_name in {"filter_data"}:
            # 适配多条件模式：优先使用 conditions 数组
            if arguments.get("conditions"):
                filter_condition = {
                    "conditions": arguments["conditions"],
                    "logic": arguments.get("logic", "and"),
                }
            else:
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
                tool_summary=f"{tool_name}({filter_condition})",
                affected_range=range_ref,
                change_type="filtered",
                iteration=iteration,
                affected_row_indices=affected,
            )
        elif tool_name in _WRITE_TOOLS:
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
                tool_summary=f"{tool_name}({target_range})",
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
                tool_summary=f"{tool_name}({range_ref})",
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
            window.total_cols = len(window.columns or window.schema)
        window.detail_level = DetailLevel.FULL

        window.operation_history.append(
            _make_op_entry(tool_name, arguments, iteration)
        )
        if len(window.operation_history) > window.max_history_entries:
            window.operation_history = window.operation_history[-window.max_history_entries:]

        window.change_log.append(change)
        if len(window.change_log) > window.max_change_records:
            window.change_log = window.change_log[-window.max_change_records:]

    def render_full(
        self,
        window: SheetWindow,
        *,
        max_rows: int = 25,
        current_iteration: int = 0,
        intent_profile: dict[str, Any] | None = None,
    ) -> str:
        """委托 renderer.render_window_wurm_full（已替换 emoji）。"""
        from .renderer import render_window_wurm_full

        return render_window_wurm_full(
            window,
            max_rows=max_rows,
            current_iteration=current_iteration,
            intent_profile=intent_profile,
        )

    def render_background(
        self,
        window: SheetWindow,
        *,
        intent_profile: dict[str, Any] | None = None,
    ) -> str:
        """委托 renderer.render_window_background（已替换 emoji）。"""
        from .renderer import render_window_background

        return render_window_background(window, intent_profile=intent_profile)

    def render_minimized(
        self,
        window: SheetWindow,
        *,
        intent_profile: dict[str, Any] | None = None,
    ) -> str:
        """委托 renderer.render_window_minimized（已替换 emoji）。"""
        from .renderer import render_window_minimized

        return render_window_minimized(window, intent_profile=intent_profile)


# ── 辅助 ──────────────────────────────────────────────────


def _make_op_entry(
    tool_name: str, arguments: dict[str, Any], iteration: int
) -> "OpEntry":
    from .models import OpEntry

    return OpEntry(
        tool_name=tool_name,
        arguments=dict(arguments) if arguments else {},
        iteration=iteration,
        success=True,
    )


# ── 策略注册表 ────────────────────────────────────────────

_STRATEGIES: dict[WindowType, WindowTypeStrategy] = {
    WindowType.EXPLORER: ExplorerStrategy(),  # type: ignore[dict-item]
    WindowType.SHEET: SheetStrategy(),  # type: ignore[dict-item]
}


def get_strategy(window_type: WindowType) -> WindowTypeStrategy | None:
    """获取窗口类型对应的策略，未知类型返回 None。"""
    return _STRATEGIES.get(window_type)
