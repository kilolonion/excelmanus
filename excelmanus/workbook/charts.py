"""Excel 原生图表实现：供 `manage_spreadsheet_objects` 内部调用。

模型面不注册本模块；PNG / matplotlib 出图走 `run_code`。
"""

from __future__ import annotations

from typing import Any

from excelmanus.engine_core.tool_result import ToolResult, error_result, ok_result
from excelmanus.logger import get_logger
from excelmanus.security import FileAccessGuard
from excelmanus.tools._guard_ctx import get_guard as _get_ctx_guard
from excelmanus.tools._helpers import (
    commit_error_result,
    commit_workbook_tool,
    get_worksheet,
    prepare_excel_commit_path,
    resolve_sheet_name,
)
from excelmanus.workbook_commit import CommitError

logger = get_logger("tools.chart")

_guard: FileAccessGuard | None = None

EXCEL_CHART_TYPES = ("bar", "line", "pie", "scatter", "area")


def _get_guard() -> FileAccessGuard:
    """获取或创建 FileAccessGuard（优先 per-session contextvar）。"""
    ctx_guard = _get_ctx_guard()
    if ctx_guard is not None:
        return ctx_guard
    global _guard
    if _guard is None:
        _guard = FileAccessGuard(".")
    return _guard


def init_guard(workspace_root: str) -> None:
    """初始化文件访问守卫（供外部配置调用）。"""
    global _guard
    _guard = FileAccessGuard(workspace_root)


def create_excel_chart(
    file_path: str,
    chart_type: str,
    data_range: str,
    categories_range: str | None = None,
    sheet_name: str | None = None,
    target_cell: str = "A1",
    target_sheet: str | None = None,
    title: str | None = None,
    x_title: str | None = None,
    y_title: str | None = None,
    style: int | None = None,
    width: float = 15.0,
    height: float = 10.0,
    from_rows: bool = False,
    expected_version: str | None = None,
) -> ToolResult:
    """在 Excel 工作表中插入原生图表对象（嵌入式图表，非图片）。"""
    from openpyxl.chart import (
        AreaChart,
        BarChart,
        LineChart,
        PieChart,
        Reference,
        ScatterChart,
    )

    if chart_type not in EXCEL_CHART_TYPES:
        return error_result(
            f"不支持的图表类型 '{chart_type}'，支持: {list(EXCEL_CHART_TYPES)}",
            code="INVALID_ARGS",
        )

    guard = _get_guard()
    safe_path, rel = prepare_excel_commit_path(guard, file_path)
    meta: dict[str, Any] = {}

    def mutate(wb) -> None:
        ws = get_worksheet(wb, sheet_name)

        chart_class_map = {
            "bar": BarChart,
            "line": LineChart,
            "pie": PieChart,
            "scatter": ScatterChart,
            "area": AreaChart,
        }
        chart = chart_class_map[chart_type]()

        if title:
            chart.title = title
        if style is not None:
            chart.style = style
        chart.width = width
        chart.height = height

        if chart_type not in ("pie",):
            if x_title:
                chart.x_axis.title = x_title
            if y_title:
                chart.y_axis.title = y_title

        from openpyxl.utils.cell import range_boundaries
        min_col, min_row, max_col, max_row = range_boundaries(data_range)

        data_ref = Reference(ws, min_col=min_col, min_row=min_row, max_col=max_col, max_row=max_row)

        cats_ref = None
        if categories_range:
            c_min_col, c_min_row, c_max_col, c_max_row = range_boundaries(categories_range)
            cats_ref = Reference(ws, min_col=c_min_col, min_row=c_min_row, max_col=c_max_col, max_row=c_max_row)

        if chart_type == "scatter":
            from openpyxl.chart import Series as ChartSeries
            if cats_ref is not None:
                x_values = cats_ref
            else:
                x_values = Reference(ws, min_col=min_col, min_row=min_row + 1, max_row=max_row)
            for col_idx in range(min_col if cats_ref else min_col + 1, max_col + 1):
                y_values = Reference(ws, min_col=col_idx, min_row=min_row + 1, max_row=max_row)
                series = ChartSeries(y_values, xvalues=x_values, title_from_data=False)
                chart.series.append(series)
        else:
            chart.add_data(data_ref, titles_from_data=True, from_rows=from_rows)
            if cats_ref is not None:
                chart.set_categories(cats_ref)

        target_ws = ws
        if target_sheet:
            resolved_target = resolve_sheet_name(target_sheet, wb.sheetnames)
            if resolved_target:
                target_ws = wb[resolved_target]
            else:
                target_ws = wb.create_sheet(title=target_sheet)

        target_ws.add_chart(chart, target_cell)

        from excelmanus.workbook.data import _collect_charts

        chart_info = _collect_charts(target_ws)
        meta["target_sheet"] = target_ws.title
        meta["chart_info"] = chart_info[-1] if chart_info else {}
        meta["total_charts"] = len(chart_info)

    try:
        cr = commit_workbook_tool(
            guard=guard,
            file_path=rel,
            mutate_fn=mutate,
            expected_version=expected_version,
        )
    except CommitError as exc:
        return commit_error_result(exc)

    logger.info(
        "create_excel_chart: %s[%s] %s at %s",
        safe_path.name, meta.get("target_sheet"), chart_type, target_cell,
    )
    payload = {
        "status": "success",
        "file": safe_path.name,
        "file_path": rel,
        "chart_type": chart_type,
        "data_range": data_range,
        "target_sheet": meta.get("target_sheet"),
        "target_cell": target_cell,
        "chart_info": meta.get("chart_info") or {},
        "total_charts_on_sheet": meta.get("total_charts", 0),
        "content_version": cr.content_version,
    }
    return ok_result(
        payload,
        model_text=(
            f"已在 {payload['target_sheet']}!{target_cell} 插入 {chart_type} 图"
        ),
    )
