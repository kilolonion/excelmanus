"""Excel 原生图表实现：供 `manage_spreadsheet_objects` 内部调用。

模型面不注册本模块；PNG / matplotlib 出图走 `run_code`。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from excelmanus.engine_core.tool_result import ToolResult, error_result, ok_result
from excelmanus.logger import get_logger
from excelmanus.security import FileAccessGuard
from excelmanus.tools.context import bind_workspace, require_guard
from excelmanus.tools._helpers import (
    MutationAborted,
    commit_error_result,
    commit_workbook_tool,
    get_worksheet,
    prepare_excel_commit_path,
    resolve_sheet_name,
    unwrap_mutation_abort,
)
from excelmanus.workbook_commit import CommitError
from excelmanus.workbook.refs import InvalidRefError

logger = get_logger("tools.chart")

EXCEL_CHART_TYPES = ("bar", "line", "pie", "scatter", "area")
_CHART_TYPE_ALIASES = {
    "column": "bar",
    "col": "bar",
    "barchart": "bar",
    "columnclustered": "bar",
    "clusteredcolumn": "bar",
    "linechart": "line",
    "piechart": "pie",
    "areachart": "area",
    "scatterchart": "scatter",
    "xy": "scatter",
}


def _get_guard() -> FileAccessGuard:
    return require_guard()


def init_guard(workspace_root: str) -> None:
    bind_workspace(workspace_root)


@dataclass(frozen=True)
class ChartSpec:
    chart_type: str
    data_range: str
    categories_range: str | None
    sheet_name: str | None
    target_cell: str
    target_sheet: str | None
    title: str | None
    x_title: str | None
    y_title: str | None
    style: int | None
    width: float
    height: float
    from_rows: bool


def normalize_chart_args(
    *,
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
) -> ChartSpec | ToolResult:
    """校验图表参数；失败返回 ToolResult，成功返回 ChartSpec。"""
    from excelmanus.workbook.address import (
        combine_sheet_names,
        parse_sheet_address,
        top_left_cell,
    )

    normalized_type = _CHART_TYPE_ALIASES.get(
        (chart_type or "").strip().lower(),
        (chart_type or "").strip().lower(),
    )
    import math

    if any(isinstance(n, bool) or not isinstance(n, (int, float)) or not math.isfinite(n) or n <= 0 for n in (width, height)):
        return error_result("图表 width/height 必须为正数，单位厘米", code="INVALID_ARGS")
    if normalized_type not in EXCEL_CHART_TYPES:
        return error_result(
            f"不支持的图表类型 '{chart_type}'，支持: {list(EXCEL_CHART_TYPES)}；column 视为 bar",
            code="INVALID_ARGS",
        )
    try:
        parsed_data = parse_sheet_address(data_range)
        sheet_name = combine_sheet_names(sheet_name, parsed_data.sheet)
        data_range = parsed_data.address
        if categories_range:
            parsed_cats = parse_sheet_address(categories_range)
            categories_range = parsed_cats.address
            sheet_name = combine_sheet_names(sheet_name, parsed_cats.sheet)
        parsed_target = parse_sheet_address(target_cell)
        if parsed_target.sheet:
            target_sheet = combine_sheet_names(target_sheet, parsed_target.sheet)
        target_cell = parsed_target.address or "A1"
        from openpyxl.utils.cell import range_boundaries as _rb

        bounds = _rb(data_range)
        if all(value is None for value in bounds):
            return error_result(f"data_range 不是合法区域: {data_range!r}", code="RANGE_INVALID")
        if categories_range:
            cat_bounds = _rb(categories_range)
            if all(value is None for value in cat_bounds):
                return error_result(f"categories_range 不是合法区域: {categories_range!r}", code="RANGE_INVALID")
        target_cell = top_left_cell(target_cell) or "A1"
    except InvalidRefError as exc:
        return error_result(str(exc), code="RANGE_INVALID")
    except ValueError as exc:
        return error_result(str(exc), code="INVALID_ARGS")
    except Exception as exc:
        return error_result(
            f"data_range/categories_range 不是合法坐标：{exc}。"
            "请用 A1:B12，表名放在 sheet，或写成 数据!A1:B12。",
            code="RANGE_INVALID",
        )
    return ChartSpec(
        chart_type=normalized_type,
        data_range=data_range,
        categories_range=categories_range,
        sheet_name=sheet_name,
        target_cell=target_cell,
        target_sheet=target_sheet,
        title=title,
        x_title=x_title,
        y_title=y_title,
        style=style,
        width=width,
        height=height,
        from_rows=from_rows,
    )


def add_chart_to_workbook(wb: Any, spec: ChartSpec) -> dict[str, Any]:
    """在已打开的工作簿上插入一张图，不提交。"""
    from openpyxl.chart import (
        AreaChart,
        BarChart,
        LineChart,
        PieChart,
        Reference,
        ScatterChart,
    )
    from openpyxl.utils.cell import range_boundaries

    def _bounds(address: str) -> tuple[int, int, int, int]:
        raw = range_boundaries(address)
        min_col, min_row, max_col, max_row = raw
        # openpyxl leaves one axis open for A:A / 1:1.  Clip it to the used
        # region so charts remain finite and portable across Excel engines.
        min_col = int(min_col or 1)
        min_row = int(min_row or 1)
        max_col = int(max_col or ws.max_column or min_col)
        max_row = int(max_row or ws.max_row or min_row)
        if ":" not in address:
            max_col = max_col or min_col
            max_row = max_row or min_row
        return min_col, min_row, max_col, max_row

    try:
        ws = get_worksheet(wb, spec.sheet_name)
    except ValueError as exc:
        raise MutationAborted(error_result(str(exc), code="INVALID_ARGS")) from exc

    chart_class_map = {
        "bar": BarChart,
        "line": LineChart,
        "pie": PieChart,
        "scatter": ScatterChart,
        "area": AreaChart,
    }
    chart = chart_class_map[spec.chart_type]()
    if spec.title:
        chart.title = spec.title
    if spec.style is not None:
        chart.style = spec.style
    chart.width = spec.width
    chart.height = spec.height
    if spec.chart_type not in ("pie",):
        if spec.x_title:
            chart.x_axis.title = spec.x_title
        if spec.y_title:
            chart.y_axis.title = spec.y_title

    min_col, min_row, max_col, max_row = _bounds(spec.data_range)
    data_ref = Reference(ws, min_col=min_col, min_row=min_row, max_col=max_col, max_row=max_row)
    cats_ref = None
    if spec.categories_range:
        c_min_col, c_min_row, c_max_col, c_max_row = _bounds(spec.categories_range)
        cats_ref = Reference(
            ws, min_col=c_min_col, min_row=c_min_row, max_col=c_max_col, max_row=c_max_row
        )

    if spec.chart_type == "scatter":
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
        chart.add_data(data_ref, titles_from_data=True, from_rows=spec.from_rows)
        if cats_ref is not None:
            chart.set_categories(cats_ref)

    target_ws = ws
    if spec.target_sheet:
        resolved_target = resolve_sheet_name(spec.target_sheet, wb.sheetnames)
        if resolved_target:
            target_ws = wb[resolved_target]
        else:
            target_ws = wb.create_sheet(title=spec.target_sheet)
    target_ws.add_chart(chart, spec.target_cell)

    from excelmanus.workbook.data import _collect_charts

    chart_info = _collect_charts(target_ws)
    return {
        "chart_type": spec.chart_type,
        "data_range": spec.data_range,
        "target_sheet": target_ws.title,
        "target_cell": spec.target_cell,
        "chart_info": chart_info[-1] if chart_info else {},
        "total_charts": len(chart_info),
    }


def _select_chart(ws: Any, *, index: int | None = None, target_cell: str | None = None, title: str | None = None) -> tuple[int, Any] | None:
    charts = list(getattr(ws, "_charts", None) or [])
    if index is not None:
        if 0 <= int(index) < len(charts):
            return int(index), charts[int(index)]
        return None
    for pos, chart in enumerate(charts):
        if title and str(getattr(chart, "title", "") or "") == title:
            return pos, chart
        anchor = getattr(getattr(chart, "anchor", None), "_from", None)
        if target_cell and anchor is not None:
            from openpyxl.utils import get_column_letter
            cell = f"{get_column_letter(anchor.col + 1)}{anchor.row + 1}"
            if cell.upper() == str(target_cell).upper():
                return pos, chart
    return None


def delete_chart_from_workbook(
    wb: Any, *, sheet_name: str | None = None, index: int | None = None,
    target_cell: str | None = None, title: str | None = None,
) -> dict[str, Any]:
    ws = get_worksheet(wb, sheet_name)
    selected = _select_chart(ws, index=index, target_cell=target_cell, title=title)
    if selected is None:
        raise MutationAborted(error_result("未找到要删除的图表", code="NOT_FOUND"))
    pos, chart = selected
    ws._charts.remove(chart)
    return {"deleted_index": pos, "target_sheet": ws.title, "remaining_charts": len(ws._charts)}


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
    from excelmanus.workbook.address import looks_like_coordinate_error

    spec = normalize_chart_args(
        chart_type=chart_type,
        data_range=data_range,
        categories_range=categories_range,
        sheet_name=sheet_name,
        target_cell=target_cell,
        target_sheet=target_sheet,
        title=title,
        x_title=x_title,
        y_title=y_title,
        style=style,
        width=width,
        height=height,
        from_rows=from_rows,
    )
    if isinstance(spec, ToolResult):
        return spec

    guard = _get_guard()
    safe_path, rel = prepare_excel_commit_path(guard, file_path)
    meta: dict[str, Any] = {}

    def mutate(wb: Any) -> None:
        meta.update(add_chart_to_workbook(wb, spec))

    try:
        cr = commit_workbook_tool(
            guard=guard,
            file_path=rel,
            mutate_fn=mutate,
            expected_version=expected_version,
        )
    except CommitError as exc:
        aborted = unwrap_mutation_abort(exc)
        if aborted is not None:
            return aborted.result
        if looks_like_coordinate_error(exc):
            return error_result(
                f"{getattr(exc, 'message', exc)}。"
                "请用 A1:B12，表名放在 sheet，或写成 数据!A1:B12。",
                code="INVALID_ARGS",
            )
        return commit_error_result(exc)

    logger.info(
        "create_excel_chart: %s[%s] %s at %s",
        safe_path.name, meta.get("target_sheet"), spec.chart_type, spec.target_cell,
    )
    payload = {
        "status": "success",
        "file": safe_path.name,
        "file_path": rel,
        "chart_type": spec.chart_type,
        "data_range": spec.data_range,
        "target_sheet": meta.get("target_sheet"),
        "target_cell": spec.target_cell,
        "chart_info": meta.get("chart_info") or {},
        "total_charts_on_sheet": meta.get("total_charts", 0),
        "content_version": cr.content_version,
    }
    return ok_result(
        payload,
        model_text=(
            f"已在 {payload['target_sheet']}!{spec.target_cell} 插入 {spec.chart_type} 图"
        ),
    )
