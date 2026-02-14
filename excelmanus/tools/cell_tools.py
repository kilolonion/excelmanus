"""单元格级操作工具：提供原地写入值/公式、插入/删除行列的能力。

与 data_tools 的 DataFrame 范式不同，本模块直接使用 openpyxl 操作单元格，
适用于需要保留工作表其他区域数据不变的场景。
"""

from __future__ import annotations

import json
import re
from typing import Any

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter
from openpyxl.utils.cell import coordinate_to_tuple, range_boundaries

from excelmanus.logger import get_logger
from excelmanus.security import FileAccessGuard
from excelmanus.tools.registry import ToolDef

logger = get_logger("tools.cell")

# ── Skill 元数据 ──────────────────────────────────────────

SKILL_NAME = "cell"
SKILL_DESCRIPTION = "单元格级操作工具集：原地写入值/公式、插入行列、删除行列"

# ── 模块级 FileAccessGuard（延迟初始化） ─────────────────

_guard: FileAccessGuard | None = None


def _get_guard() -> FileAccessGuard:
    """获取或创建 FileAccessGuard 单例。"""
    global _guard
    if _guard is None:
        _guard = FileAccessGuard(".")
    return _guard


def init_guard(workspace_root: str) -> None:
    """初始化文件访问守卫（供外部配置调用）。

    Args:
        workspace_root: 工作目录根路径。
    """
    global _guard
    _guard = FileAccessGuard(workspace_root)


# ── 内部辅助 ──────────────────────────────────────────────


def _parse_single_cell(ref: str) -> tuple[int, int]:
    """解析单个单元格引用（如 'A1'）为 (row, col) 元组（1-indexed）。"""
    return coordinate_to_tuple(ref.upper())


def _coerce_value(raw: Any) -> Any:
    """尝试将字符串值自动转换为合适的 Python 类型。

    - 以 '=' 开头的字符串保留为公式
    - 纯数字字符串转为 int/float
    - 其他保留为字符串
    """
    if not isinstance(raw, str):
        return raw
    stripped = raw.strip()
    if not stripped:
        return stripped
    # 公式原样保留
    if stripped.startswith("="):
        return stripped
    # 尝试数值转换
    try:
        if "." in stripped or "e" in stripped.lower():
            return float(stripped)
        return int(stripped)
    except (ValueError, OverflowError):
        pass
    return raw


# ── 工具函数 ──────────────────────────────────────────────


def write_cells(
    file_path: str,
    sheet_name: str | None = None,
    cell: str | None = None,
    value: Any = None,
    cell_range: str | None = None,
    values: list[list[Any]] | None = None,
) -> str:
    """向指定单元格或范围写入值/公式，不影响工作表其他区域的数据。

    两种模式（互斥）：
    - **单元格模式**：传 cell + value，写入单个单元格。
    - **范围模式**：传 cell_range + values，批量写入二维数据。
      cell_range 可以只指定起始单元格（如 "A1"），values 的行列数决定实际写入范围。

    Args:
        file_path: Excel 文件路径。
        sheet_name: 工作表名称，默认活动工作表。
        cell: 目标单元格引用（如 "A1"），单元格模式。
        value: 要写入的值（数字、字符串、公式），单元格模式。
        cell_range: 目标范围起始位置或完整范围（如 "A1" 或 "A1:C3"），范围模式。
        values: 二维数组，范围模式。每个内层列表代表一行。

    Returns:
        JSON 格式的操作结果。
    """
    # 参数校验
    single_mode = cell is not None
    range_mode = cell_range is not None or values is not None

    if single_mode and range_mode:
        return json.dumps(
            {"error": "cell/value 与 cell_range/values 互斥，请只使用其中一种模式"},
            ensure_ascii=False,
        )
    if not single_mode and not range_mode:
        return json.dumps(
            {"error": "必须指定 cell+value（单元格模式）或 cell_range+values（范围模式）"},
            ensure_ascii=False,
        )

    guard = _get_guard()
    safe_path = guard.resolve_and_validate(file_path)

    wb = load_workbook(safe_path)
    ws = wb[sheet_name] if sheet_name and sheet_name in wb.sheetnames else wb.active

    try:
        if single_mode:
            # 单元格模式
            row, col = _parse_single_cell(cell)  # type: ignore[arg-type]
            ws.cell(row=row, column=col, value=_coerce_value(value))
            wb.save(safe_path)
            result = {
                "status": "success",
                "file": safe_path.name,
                "cell": cell,
                "value_written": value,
                "cells_written": 1,
            }
        else:
            # 范围模式
            if values is None or not values:
                return json.dumps(
                    {"error": "范围模式下 values 不能为空"},
                    ensure_ascii=False,
                )
            # 解析起始位置
            start_ref = cell_range or "A1"
            # 如果是范围（如 A1:C3），取左上角
            if ":" in start_ref:
                start_ref = start_ref.split(":")[0]
            start_row, start_col = _parse_single_cell(start_ref)

            cells_written = 0
            for r_idx, row_data in enumerate(values):
                if not isinstance(row_data, list):
                    row_data = [row_data]
                for c_idx, val in enumerate(row_data):
                    ws.cell(
                        row=start_row + r_idx,
                        column=start_col + c_idx,
                        value=_coerce_value(val),
                    )
                    cells_written += 1

            wb.save(safe_path)
            end_row = start_row + len(values) - 1
            max_cols = max(len(r) if isinstance(r, list) else 1 for r in values)
            end_col = start_col + max_cols - 1
            actual_range = (
                f"{get_column_letter(start_col)}{start_row}"
                f":{get_column_letter(end_col)}{end_row}"
            )
            result = {
                "status": "success",
                "file": safe_path.name,
                "range": actual_range,
                "rows_written": len(values),
                "cells_written": cells_written,
            }
    finally:
        wb.close()

    logger.info("write_cells: %s", result)
    return json.dumps(result, ensure_ascii=False, indent=2)


def insert_rows(
    file_path: str,
    row: int,
    count: int = 1,
    sheet_name: str | None = None,
) -> str:
    """在指定行号前插入空行，已有数据自动下移。

    Args:
        file_path: Excel 文件路径。
        row: 插入位置行号（1-indexed），新行将插入到此行之前。
        count: 插入行数，默认 1。
        sheet_name: 工作表名称，默认活动工作表。

    Returns:
        JSON 格式的操作结果。
    """
    if row < 1:
        return json.dumps({"error": "row 必须 >= 1"}, ensure_ascii=False)
    if count < 1:
        return json.dumps({"error": "count 必须 >= 1"}, ensure_ascii=False)
    if count > 10000:
        return json.dumps({"error": "单次插入行数不能超过 10000"}, ensure_ascii=False)

    guard = _get_guard()
    safe_path = guard.resolve_and_validate(file_path)

    wb = load_workbook(safe_path)
    ws = wb[sheet_name] if sheet_name and sheet_name in wb.sheetnames else wb.active

    rows_before = ws.max_row or 0
    ws.insert_rows(row, amount=count)
    wb.save(safe_path)
    wb.close()

    logger.info("insert_rows: %s 行 %d 处插入 %d 行", safe_path.name, row, count)

    return json.dumps(
        {
            "status": "success",
            "file": safe_path.name,
            "inserted_at_row": row,
            "count": count,
            "rows_before": rows_before,
            "rows_after": rows_before + count,
        },
        ensure_ascii=False,
        indent=2,
    )


def insert_columns(
    file_path: str,
    column: int | str,
    count: int = 1,
    sheet_name: str | None = None,
) -> str:
    """在指定列前插入空列，已有数据自动右移。

    Args:
        file_path: Excel 文件路径。
        column: 插入位置，可以是列号（1-indexed 整数）或列字母（如 "C"）。
        count: 插入列数，默认 1。
        sheet_name: 工作表名称，默认活动工作表。

    Returns:
        JSON 格式的操作结果。
    """
    # 解析列号
    if isinstance(column, str):
        column_str = column.strip().upper()
        if column_str.isdigit():
            col_idx = int(column_str)
        else:
            # 字母转数字：A=1, B=2, ..., Z=26, AA=27, ...
            col_idx = 0
            for ch in column_str:
                if not ch.isalpha():
                    return json.dumps(
                        {"error": f"无效的列标识: '{column}'，应为列字母（如 'C'）或数字"},
                        ensure_ascii=False,
                    )
                col_idx = col_idx * 26 + (ord(ch) - ord("A") + 1)
    else:
        col_idx = int(column)

    if col_idx < 1:
        return json.dumps({"error": "column 必须 >= 1"}, ensure_ascii=False)
    if count < 1:
        return json.dumps({"error": "count 必须 >= 1"}, ensure_ascii=False)
    if count > 1000:
        return json.dumps({"error": "单次插入列数不能超过 1000"}, ensure_ascii=False)

    guard = _get_guard()
    safe_path = guard.resolve_and_validate(file_path)

    wb = load_workbook(safe_path)
    ws = wb[sheet_name] if sheet_name and sheet_name in wb.sheetnames else wb.active

    cols_before = ws.max_column or 0
    ws.insert_cols(col_idx, amount=count)
    wb.save(safe_path)
    wb.close()

    col_letter = get_column_letter(col_idx)
    logger.info("insert_columns: %s 列 %s(%d) 处插入 %d 列", safe_path.name, col_letter, col_idx, count)

    return json.dumps(
        {
            "status": "success",
            "file": safe_path.name,
            "inserted_at_column": col_letter,
            "inserted_at_column_index": col_idx,
            "count": count,
            "columns_before": cols_before,
            "columns_after": cols_before + count,
        },
        ensure_ascii=False,
        indent=2,
    )


# ── get_tools() 导出 ──────────────────────────────────────


def get_tools() -> list[ToolDef]:
    """返回单元格级操作工具的所有工具定义。"""
    return [
        ToolDef(
            name="write_cells",
            description=(
                "向 Excel 指定单元格或范围写入值/公式，不影响其他区域数据。"
                "两种模式：(1) cell+value 写单个单元格；"
                "(2) cell_range+values 批量写入二维数据。"
                "支持数字、字符串和公式（以 = 开头）"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Excel 文件路径",
                    },
                    "sheet_name": {
                        "type": "string",
                        "description": "工作表名称，默认活动工作表",
                    },
                    "cell": {
                        "type": "string",
                        "description": "目标单元格引用（如 'A1'），单元格模式",
                    },
                    "value": {
                        "description": "要写入的值（数字、字符串或公式），单元格模式",
                    },
                    "cell_range": {
                        "type": "string",
                        "description": "目标范围起始位置（如 'A1' 或 'A1:C3'），范围模式",
                    },
                    "values": {
                        "type": "array",
                        "items": {
                            "type": "array",
                            "items": {},
                        },
                        "description": "二维数组数据，每个内层列表代表一行，范围模式",
                    },
                },
                "required": ["file_path"],
                "additionalProperties": False,
            },
            func=write_cells,
        ),
        ToolDef(
            name="insert_rows",
            description="在 Excel 指定行号前插入空行，已有数据自动下移",
            input_schema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Excel 文件路径",
                    },
                    "row": {
                        "type": "integer",
                        "description": "插入位置行号（从 1 开始），新行将插入到此行之前",
                    },
                    "count": {
                        "type": "integer",
                        "description": "插入行数，默认 1",
                        "default": 1,
                    },
                    "sheet_name": {
                        "type": "string",
                        "description": "工作表名称，默认活动工作表",
                    },
                },
                "required": ["file_path", "row"],
                "additionalProperties": False,
            },
            func=insert_rows,
        ),
        ToolDef(
            name="insert_columns",
            description="在 Excel 指定列前插入空列，已有数据自动右移。列可以用字母（如 'C'）或数字（如 3）指定",
            input_schema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Excel 文件路径",
                    },
                    "column": {
                        "description": "插入位置，列字母（如 'C'）或列号（如 3），新列插入到此列之前",
                    },
                    "count": {
                        "type": "integer",
                        "description": "插入列数，默认 1",
                        "default": 1,
                    },
                    "sheet_name": {
                        "type": "string",
                        "description": "工作表名称，默认活动工作表",
                    },
                },
                "required": ["file_path", "column"],
                "additionalProperties": False,
            },
            func=insert_columns,
        ),
    ]
