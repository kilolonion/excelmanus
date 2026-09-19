"""Shared structure-edit boundary for tools and the workbook UI."""
from typing import Any


def _formula_mentions_sheet(formula: str, name: str) -> bool:
    # 引用形态 表!A1 或 '表 名'!A1；去引号后统一匹配。
    # 误报方向是拒绝（字符串字面量撞名），与安全侧一致。
    return f"{name}!" in formula.replace("'", "")


def _iter_ref_strings(obj: Any, depth: int = 0):
    """递归收集对象图里的引用公式串（chart series 的 numRef.f 等）。"""
    if depth > 6 or obj is None:
        return
    text = getattr(obj, "f", None)
    if isinstance(text, str) and text:
        yield text
    for value in getattr(obj, "__dict__", {}).values():
        if isinstance(value, (list, tuple)):
            for item in value:
                yield from _iter_ref_strings(item, depth + 1)
        elif hasattr(value, "__dict__"):
            yield from _iter_ref_strings(value, depth + 1)


def _defined_names(wb: Any):
    for dn in getattr(wb, "defined_names", {}).values():
        yield dn, None
    for index, ws in enumerate(wb.worksheets):
        for dn in getattr(ws, "defined_names", {}).values():
            yield dn, index


def _assert_delete_supported(wb: Any, action: str, target: str) -> None:
    """删除目标工作表前，检查是否有对象/引用依赖它。"""
    ws_target = wb[target]
    blockers: list[str] = []
    tables = len(getattr(ws_target, "tables", {}) or {})
    if tables:
        blockers.append(f"目标表含 {tables} 个表对象")
    charts_on_target = len(getattr(ws_target, "_charts", None) or [])
    if charts_on_target:
        blockers.append(f"目标表含 {charts_on_target} 个图表")

    formula_refs = 0
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                if cell.data_type == "f" and _formula_mentions_sheet(
                    str(cell.value or ""), target
                ):
                    formula_refs += 1
    if formula_refs:
        blockers.append(f"{formula_refs} 个公式引用 {target}!")

    name_refs = 0
    for dn, sheet_index in _defined_names(wb):
        attr_text = str(getattr(dn, "attr_text", "") or "")
        scoped = sheet_index is not None and wb.worksheets[sheet_index].title == target
        if scoped or _formula_mentions_sheet(attr_text, target):
            name_refs += 1
    if name_refs:
        blockers.append(f"{name_refs} 个名称引用/绑定 {target}")

    chart_refs = 0
    for ws in wb.worksheets:
        if ws.title == target:
            continue
        for chart in getattr(ws, "_charts", None) or []:
            for series in getattr(chart, "series", None) or []:
                if any(
                    _formula_mentions_sheet(text, target)
                    for text in _iter_ref_strings(series)
                ):
                    chart_refs += 1
                    break
    if chart_refs:
        blockers.append(f"{chart_refs} 个图表引用 {target}!")

    if blockers:
        raise ValueError(
            f"{action} 不会自动维护引用，拒绝删除工作表 {target}：" + "、".join(blockers)
        )


def assert_structure_supported(wb: Any, action: str, target_sheet: str | None = None) -> None:
    if target_sheet is not None:
        _assert_delete_supported(wb, action, target_sheet)
        return
    formulas = sum(1 for ws in wb.worksheets for row in ws.iter_rows() for c in row
                   if c.data_type == "f")
    charts = sum(len(getattr(ws, "_charts", [])) for ws in wb.worksheets)
    tables = sum(len(ws.tables) for ws in wb.worksheets)
    names = len(wb.defined_names)
    if formulas or charts or tables or names:
        raise ValueError(
            f"{action} 不会自动维护引用；工作簿含 {formulas} 个公式、{charts} 个图表、"
            f"{tables} 个表对象和 {names} 个名称，拒绝结构修改。"
        )
