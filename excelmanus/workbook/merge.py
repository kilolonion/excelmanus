"""Three-way review for canonical V2 cell patch operations.

The reviewer computes a plan only.  Publishing the plan is deliberately left to
``WorkbookService.apply`` so UI conflict resolution and agent writes share the
same ChangeSet transaction, version check, serialization checks, and receipt.
"""

from copy import copy
from io import BytesIO
import json
from typing import Any

from openpyxl import load_workbook
from openpyxl.utils.cell import coordinate_to_tuple

from excelmanus.workbook.mutation import execute_operation
from excelmanus.workbook.contracts import validate_operations

MAX_CELLS = 5000
FACETS = ("value", "font", "fill", "border", "alignment", "number_format", "protection")
LABELS = dict(zip(FACETS, ("内容", "字体", "填充", "边框", "对齐", "数字格式", "保护")))


def _shape(wb):
    return [(ws.title, ws.max_row, ws.max_column, sorted(str(r) for r in ws.merged_cells.ranges)) for ws in wb]


def _color(value: Any) -> str:
    if value is None:
        return "默认色"
    if value.type == "rgb":
        return "#" + str(value.rgb)[-6:]
    if value.type == "theme":
        return f"主题色 {value.theme}"
    return "自动颜色"


def _display(value: Any, facet: str = "value") -> Any:
    if facet == "font":
        return " · ".join(str(item) for item in (value.name, f"{value.sz:g} 磅" if value.sz else None,
            "加粗" if value.bold else "常规", "斜体" if value.italic else None,
            "下划线" if value.underline else None, "删除线" if value.strike else None, _color(value.color)) if item)
    if facet == "fill":
        return "无填充" if not value.patternType else f"{_color(value.fgColor)}（{value.patternType}）"
    if facet == "border":
        labels = {"left": "左", "right": "右", "top": "上", "bottom": "下", "diagonal": "对角线"}
        return " · ".join(f"{label}边框：{side.style} {_color(side.color)}" for name, label in labels.items()
                          if (side := getattr(value, name, None)) is not None and side.style) or "无边框"
    if facet == "alignment":
        labels = {"left": "左对齐", "right": "右对齐", "center": "居中", "top": "顶部", "bottom": "底部", "justify": "两端对齐"}
        return " · ".join([labels.get(value.horizontal, value.horizontal or "默认水平对齐"),
                           labels.get(value.vertical, value.vertical or "默认垂直对齐"),
                           "自动换行" if value.wrap_text else "不换行", f"旋转 {value.text_rotation or 0}°", f"缩进 {value.indent or 0:g}"])
    if facet == "protection":
        return ("锁定" if value.locked else "未锁定") + (" · 隐藏公式" if value.hidden else " · 显示公式")
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def review_merge(base: bytes, current: bytes, operations: list[dict], *,
                 choices: dict[str, str] | None = None, apply: bool = False,
                 keep_vba: bool = False) -> tuple[dict, bytes | None]:
    """Review canonical ``cells.patch`` changes against a newer workbook.

    ``operations`` are never committed here.  The returned ``operations`` field
    is a filtered V2 ChangeSet that the caller must submit through the domain
    service after checking the current content version.
    """
    cells: set[tuple[str, str]] = set()
    for op in operations:
        if op.get("kind") != "cells.patch":
            return {"status": "replan", "reason": "草稿包含行列、工作表或其他结构操作，请让 Agent 重新制定方案。"}, None
        sheet = op.get("sheet")
        if not isinstance(sheet, str) or not sheet:
            raise ValueError("合并需要明确的工作表")
        for item in op.get("cells") or []:
            address = str(item.get("cell") or "").upper()
            row, col = coordinate_to_tuple(address)
            if row < 1 or row > 1048576 or col < 1 or col > 16384:
                raise ValueError("单元格地址越界")
            cells.add((sheet, address))
            if len(cells) > MAX_CELLS:
                raise ValueError("一次最多核对 5000 个单元格，请分批处理")
    books = []
    try:
        for data in (base, base, current):
            books.append(load_workbook(BytesIO(data), keep_vba=keep_vba))
        original, local, remote = books
        if _shape(original) != _shape(remote):
            return {"status": "replan", "reason": "工作表结构或数据边界已改变，旧坐标需要重新核对，请让 Agent 重新制定方案。"}, None
        canonical_operations = validate_operations(operations)
        for operation in canonical_operations:
            execute_operation(local, operation)
        rows = []
        safe_count = 0
        conflict_count = 0
        updates = []
        decisions: dict[tuple[str, str, str], bool] = {}
        for sheet, address in sorted(cells):
            before, mine, theirs = (book[sheet][address] for book in books)
            for facet in FACETS:
                a, b, c = (getattr(cell, facet) for cell in (before, mine, theirs))
                # openpyxl StyleProxy compares to the underlying style, not another proxy.
                a, b, c = copy(a), copy(b), copy(c)
                base_value = (a, before.data_type) if facet == "value" else a
                local_value = (b, mine.data_type) if facet == "value" else b
                remote_value = (c, theirs.data_type) if facet == "value" else c
                if base_value == local_value:
                    decisions[(sheet, address, facet)] = False
                    continue
                conflict = remote_value != base_value and remote_value != local_value
                key = json.dumps([sheet, address, facet], ensure_ascii=False, separators=(",", ":"))
                rows.append({"id": key, "sheet": sheet, "cell": address, "field": LABELS[facet],
                             "base": _display(a, facet), "local": _display(b, facet), "remote": _display(c, facet), "conflict": conflict})
                if conflict:
                    conflict_count += 1
                    if apply and (choices or {}).get(key) not in {"local", "remote"}:
                        raise ValueError("请为每个冲突选择保留当前文件还是我的修改")
                else:
                    safe_count += 1
                keep_local = not conflict or (choices or {}).get(key) == "local"
                decisions[(sheet, address, facet)] = keep_local
                if keep_local:
                    updates.append((theirs, facet, b, mine.data_type))
        merged_operations: list[dict] = []
        for operation in canonical_operations:
            grouped: dict[str, list[dict]] = {}
            for item in operation["cells"]:
                address = str(item["cell"]).upper()
                merged: dict[str, Any] = {"cell": address}
                if "value" in item and decisions.get((operation["sheet"], address, "value"), False):
                    merged["value"] = item["value"]
                if "style" in item:
                    style = item["style"]
                    if style is None:
                        style_facets = {facet for facet in FACETS if facet != "value"}
                        if any(decisions.get((operation["sheet"], address, facet), False) for facet in style_facets):
                            merged["style"] = None
                    elif isinstance(style, dict):
                        style_fields = {
                            "font": "font", "fill": "fill", "border": "border",
                            "alignment": "alignment", "number_format": "number_format",
                        }
                        selected = {
                            field: value for field, value in style.items()
                            if decisions.get((operation["sheet"], address, style_fields.get(field, field)), False)
                        }
                        if selected:
                            merged["style"] = selected
                if len(merged) > 1:
                    grouped.setdefault(operation["sheet"], []).append(merged)
            for sheet_name, merged_cells in grouped.items():
                merged_operations.append({"kind": "cells.patch", "sheet": sheet_name, "cells": merged_cells})
        result = {"status": "review", "cells": rows, "safe_count": safe_count,
                  "conflict_count": conflict_count, "operations": merged_operations}
        if not apply:
            return result, None
        for cell, facet, value, data_type in updates:
            setattr(cell, facet, copy(value))
            if facet == "value":
                cell.data_type = data_type
        out = BytesIO()
        remote.save(out)
        return result, out.getvalue()
    finally:
        for book in books:
            book.close()
