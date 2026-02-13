"""数据工具：提供 Excel 读写、分析、过滤和转换能力。"""

from __future__ import annotations

import json
from typing import Any

import pandas as pd

from excelmanus.logger import get_logger
from excelmanus.security import FileAccessGuard
from excelmanus.tools.registry import ToolDef

logger = get_logger("tools.data")

# ── Skill 元数据 ──────────────────────────────────────────

SKILL_NAME = "data"
SKILL_DESCRIPTION = "Excel 数据操作工具集：读取、写入、分析、过滤和转换"

# ── 模块级 FileAccessGuard（延迟初始化） ─────────────────

_guard: FileAccessGuard | None = None


def _get_guard() -> FileAccessGuard:
    """获取或创建 FileAccessGuard 单例。"""
    global _guard
    if _guard is None:
        # 默认使用当前工作目录，可通过 init_guard() 覆盖
        _guard = FileAccessGuard(".")
    return _guard


def init_guard(workspace_root: str) -> None:
    """初始化文件访问守卫（供外部配置调用）。

    Args:
        workspace_root: 工作目录根路径。
    """
    global _guard
    _guard = FileAccessGuard(workspace_root)


# ── 工具函数 ──────────────────────────────────────────────


def read_excel(
    file_path: str,
    sheet_name: str | None = None,
    max_rows: int | None = None,
    include_style_summary: bool = False,
) -> str:
    """读取 Excel 文件并返回数据摘要。

    Args:
        file_path: Excel 文件路径（相对或绝对）。
        sheet_name: 工作表名称，默认读取第一个。
        max_rows: 最大读取行数，默认全部读取。
        include_style_summary: 是否附带样式概览（使用的颜色、合并单元格等）。

    Returns:
        JSON 格式的数据摘要字符串。
    """
    guard = _get_guard()
    safe_path = guard.resolve_and_validate(file_path)

    kwargs: dict[str, Any] = {"io": safe_path}
    if sheet_name is not None:
        kwargs["sheet_name"] = sheet_name
    if max_rows is not None:
        kwargs["nrows"] = max_rows

    df = pd.read_excel(**kwargs)

    # 构建摘要信息
    summary: dict[str, Any] = {
        "file": str(safe_path.name),
        "shape": {"rows": df.shape[0], "columns": df.shape[1]},
        "columns": list(df.columns),
        "dtypes": {col: str(dtype) for col, dtype in df.dtypes.items()},
        "preview": json.loads(df.head(10).to_json(orient="records", force_ascii=False)),
    }

    if include_style_summary:
        summary["style_summary"] = _collect_style_summary(safe_path, sheet_name)

    return json.dumps(summary, ensure_ascii=False, indent=2)


def write_excel(file_path: str, data: list[dict], sheet_name: str = "Sheet1") -> str:
    """将数据写入 Excel 文件。

    当目标文件已存在时，仅写入/替换指定工作表，保留其他工作表。
    当目标文件不存在时，创建新文件。

    Args:
        file_path: 目标 Excel 文件路径。
        data: 要写入的数据，每个字典代表一行。
        sheet_name: 工作表名称，默认 Sheet1。

    Returns:
        操作结果描述。
    """
    guard = _get_guard()
    safe_path = guard.resolve_and_validate(file_path)

    df = pd.DataFrame(data)

    if safe_path.exists() and safe_path.suffix.lower() in (".xlsx", ".xlsm"):
        # 已有文件：使用 append 模式，仅替换指定 sheet，保留其他 sheet
        with pd.ExcelWriter(
            safe_path,
            engine="openpyxl",
            mode="a",
            if_sheet_exists="replace",
        ) as writer:
            df.to_excel(writer, sheet_name=sheet_name, index=False)
    else:
        # 新文件：直接写入
        df.to_excel(safe_path, sheet_name=sheet_name, index=False)

    return json.dumps(
        {"status": "success", "file": str(safe_path.name), "rows": len(df), "columns": len(df.columns)},
        ensure_ascii=False,
    )


def analyze_data(file_path: str, sheet_name: str | None = None) -> str:
    """对 Excel 数据进行基本统计分析。

    Args:
        file_path: Excel 文件路径。
        sheet_name: 工作表名称，默认第一个。

    Returns:
        JSON 格式的统计分析结果。
    """
    guard = _get_guard()
    safe_path = guard.resolve_and_validate(file_path)

    kwargs: dict[str, Any] = {"io": safe_path}
    if sheet_name is not None:
        kwargs["sheet_name"] = sheet_name

    df = pd.read_excel(**kwargs)

    # 基本统计信息
    result: dict[str, Any] = {
        "file": str(safe_path.name),
        "shape": {"rows": df.shape[0], "columns": df.shape[1]},
        "columns": list(df.columns),
        "missing_values": {col: int(count) for col, count in df.isnull().sum().items() if count > 0},
    }

    # 数值列统计
    numeric_df = df.select_dtypes(include=["number"])
    if not numeric_df.empty:
        stats = numeric_df.describe().to_dict()
        # 将 numpy 类型转为 Python 原生类型
        result["numeric_stats"] = {
            col: {k: float(v) for k, v in col_stats.items()}
            for col, col_stats in stats.items()
        }

    return json.dumps(result, ensure_ascii=False, indent=2)


def filter_data(
    file_path: str,
    column: str,
    operator: str,
    value: Any,
    sheet_name: str | None = None,
) -> str:
    """根据条件过滤 Excel 数据行。

    Args:
        file_path: Excel 文件路径。
        column: 要过滤的列名。
        operator: 比较运算符，支持 eq/ne/gt/ge/lt/le/contains。
        value: 比较值。
        sheet_name: 工作表名称，默认第一个。

    Returns:
        JSON 格式的过滤结果。
    """
    guard = _get_guard()
    safe_path = guard.resolve_and_validate(file_path)

    kwargs: dict[str, Any] = {"io": safe_path}
    if sheet_name is not None:
        kwargs["sheet_name"] = sheet_name

    df = pd.read_excel(**kwargs)

    if column not in df.columns:
        return json.dumps(
            {"error": f"列 '{column}' 不存在，可用列: {list(df.columns)}"},
            ensure_ascii=False,
        )

    # 根据运算符过滤
    ops = {
        "eq": lambda s, v: s == v,
        "ne": lambda s, v: s != v,
        "gt": lambda s, v: s > v,
        "ge": lambda s, v: s >= v,
        "lt": lambda s, v: s < v,
        "le": lambda s, v: s <= v,
        "contains": lambda s, v: s.astype(str).str.contains(str(v), na=False),
    }

    if operator not in ops:
        return json.dumps(
            {"error": f"不支持的运算符 '{operator}'，支持: {list(ops.keys())}"},
            ensure_ascii=False,
        )

    mask = ops[operator](df[column], value)
    filtered = df[mask]

    result = {
        "file": str(safe_path.name),
        "filter": {"column": column, "operator": operator, "value": value},
        "original_rows": len(df),
        "filtered_rows": len(filtered),
        "preview": json.loads(filtered.head(20).to_json(orient="records", force_ascii=False)),
    }
    return json.dumps(result, ensure_ascii=False, indent=2)


def transform_data(
    file_path: str,
    operations: list[dict[str, Any]],
    sheet_name: str | None = None,
    output_path: str | None = None,
) -> str:
    """对 Excel 数据执行转换操作。

    支持的操作类型：
    - rename: 重命名列，参数 {"columns": {"旧名": "新名"}}
    - add_column: 添加新列，参数 {"name": "列名", "value": 值或表达式}
    - drop_columns: 删除列，参数 {"columns": ["列名1", "列名2"]}
    - sort: 排序，参数 {"by": "列名", "ascending": true/false}

    Args:
        file_path: 源 Excel 文件路径。
        operations: 转换操作列表，每项包含 type 和对应参数。
        sheet_name: 工作表名称，默认第一个。
        output_path: 输出文件路径，默认覆盖源文件。

    Returns:
        JSON 格式的转换结果。
    """
    guard = _get_guard()
    safe_path = guard.resolve_and_validate(file_path)

    kwargs: dict[str, Any] = {"io": safe_path}
    if sheet_name is not None:
        kwargs["sheet_name"] = sheet_name

    df = pd.read_excel(**kwargs)

    applied: list[str] = []
    for op in operations:
        op_type = op.get("type", "")

        if op_type == "rename":
            columns_map = op.get("columns", {})
            df = df.rename(columns=columns_map)
            applied.append(f"rename: {columns_map}")

        elif op_type == "add_column":
            col_name = op.get("name", "")
            col_value = op.get("value", None)
            df[col_name] = col_value
            applied.append(f"add_column: {col_name}")

        elif op_type == "drop_columns":
            cols = op.get("columns", [])
            existing = [c for c in cols if c in df.columns]
            df = df.drop(columns=existing)
            applied.append(f"drop_columns: {existing}")

        elif op_type == "sort":
            by = op.get("by", "")
            ascending = op.get("ascending", True)
            if by in df.columns:
                df = df.sort_values(by=by, ascending=ascending)
                applied.append(f"sort: {by} {'asc' if ascending else 'desc'}")

        else:
            applied.append(f"unknown_op: {op_type}")

    # 写入输出文件
    if output_path is not None:
        out_safe = guard.resolve_and_validate(output_path)
    else:
        out_safe = safe_path

    df.to_excel(out_safe, index=False, sheet_name=sheet_name or "Sheet1")

    result = {
        "status": "success",
        "file": str(out_safe.name),
        "operations_applied": applied,
        "shape": {"rows": df.shape[0], "columns": df.shape[1]},
    }
    return json.dumps(result, ensure_ascii=False, indent=2)


# ── 样式概览辅助函数 ──────────────────────────────────────


def _collect_style_summary(file_path: Any, sheet_name: str | None) -> dict[str, Any]:
    """用 openpyxl 扫描工作表，收集样式概览信息。"""
    from pathlib import Path

    from openpyxl import load_workbook
    from openpyxl.utils import get_column_letter

    path = Path(file_path) if not isinstance(file_path, Path) else file_path
    wb = load_workbook(path, data_only=True)
    ws = wb[sheet_name] if sheet_name and sheet_name in wb.sheetnames else wb.active

    fill_colors: set[str] = set()
    font_colors: set[str] = set()
    has_colored_cells = False

    # 抽样扫描（最多前 100 行）避免大文件性能问题
    max_scan_rows = min(ws.max_row or 0, 100)
    for row in ws.iter_rows(min_row=1, max_row=max_scan_rows):
        for cell in row:
            # 填充色
            if cell.fill and cell.fill.fgColor:
                fg = cell.fill.fgColor
                if hasattr(fg, "rgb") and fg.rgb and fg.rgb not in ("00000000", "FFFFFFFF"):
                    rgb = str(fg.rgb)
                    color = rgb[2:] if len(rgb) == 8 else rgb
                    fill_colors.add(color)
                    has_colored_cells = True
            # 字体色
            if cell.font and cell.font.color:
                fc = cell.font.color
                if hasattr(fc, "rgb") and fc.rgb and fc.rgb not in ("00000000", "FF000000"):
                    rgb = str(fc.rgb)
                    color = rgb[2:] if len(rgb) == 8 else rgb
                    font_colors.add(color)
                    has_colored_cells = True

    # 合并单元格
    merged_ranges = [str(mr) for mr in ws.merged_cells.ranges]

    # 条件格式
    has_conditional = len(ws.conditional_formatting) > 0

    wb.close()

    return {
        "has_colored_cells": has_colored_cells,
        "fill_colors_used": sorted(fill_colors),
        "font_colors_used": sorted(font_colors),
        "has_merged_cells": len(merged_ranges) > 0,
        "merged_ranges": merged_ranges,
        "has_conditional_formatting": has_conditional,
        "rows_scanned": max_scan_rows,
    }


# ── get_tools() 导出 ──────────────────────────────────────


def get_tools() -> list[ToolDef]:
    """返回数据操作 Skill 的所有工具定义。"""
    return [
        ToolDef(
            name="read_excel",
            description="读取 Excel 文件并返回数据摘要（形状、列名、类型、前10行预览），可选附带样式概览",
            input_schema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Excel 文件路径",
                    },
                    "sheet_name": {
                        "type": "string",
                        "description": "工作表名称，默认读取第一个",
                    },
                    "max_rows": {
                        "type": "integer",
                        "description": "最大读取行数，默认全部",
                    },
                    "include_style_summary": {
                        "type": "boolean",
                        "description": "是否附带样式概览（填充色、字体色、合并单元格等），默认关闭",
                        "default": False,
                    },
                },
                "required": ["file_path"],
                "additionalProperties": False,
            },
            func=read_excel,
        ),
        ToolDef(
            name="write_excel",
            description="将数据写入 Excel 文件。写入前必须先用 read_excel 确认目标区域现有内容；优先批量写入，避免逐行调用。",
            input_schema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "目标 Excel 文件路径",
                    },
                    "data": {
                        "type": "array",
                        "items": {"type": "object"},
                        "description": "要写入的数据，每个对象代表一行",
                    },
                    "sheet_name": {
                        "type": "string",
                        "description": "工作表名称，默认 Sheet1",
                        "default": "Sheet1",
                    },
                },
                "required": ["file_path", "data"],
                "additionalProperties": False,
            },
            func=write_excel,
        ),
        ToolDef(
            name="analyze_data",
            description="对 Excel 数据进行基本统计分析（描述性统计、缺失值等）",
            input_schema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Excel 文件路径",
                    },
                    "sheet_name": {
                        "type": "string",
                        "description": "工作表名称，默认第一个",
                    },
                },
                "required": ["file_path"],
                "additionalProperties": False,
            },
            func=analyze_data,
        ),
        ToolDef(
            name="filter_data",
            description="根据条件过滤 Excel 数据行，支持 eq/ne/gt/ge/lt/le/contains 运算符",
            input_schema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Excel 文件路径",
                    },
                    "column": {
                        "type": "string",
                        "description": "要过滤的列名",
                    },
                    "operator": {
                        "type": "string",
                        "enum": ["eq", "ne", "gt", "ge", "lt", "le", "contains"],
                        "description": "比较运算符",
                    },
                    "value": {
                        "description": "比较值（数字或字符串）",
                    },
                    "sheet_name": {
                        "type": "string",
                        "description": "工作表名称，默认第一个",
                    },
                },
                "required": ["file_path", "column", "operator", "value"],
                "additionalProperties": False,
            },
            func=filter_data,
        ),
        ToolDef(
            name="transform_data",
            description="对 Excel 数据执行转换操作（重命名列、添加列、删除列、排序）",
            input_schema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "源 Excel 文件路径",
                    },
                    "operations": {
                        "type": "array",
                        "description": "转换操作列表",
                        "items": {
                            "type": "object",
                            "properties": {
                                "type": {
                                    "type": "string",
                                    "enum": ["rename", "add_column", "drop_columns", "sort"],
                                    "description": "操作类型",
                                },
                            },
                            "required": ["type"],
                        },
                    },
                    "sheet_name": {
                        "type": "string",
                        "description": "工作表名称，默认第一个",
                    },
                    "output_path": {
                        "type": "string",
                        "description": "输出文件路径，默认覆盖源文件",
                    },
                },
                "required": ["file_path", "operations"],
                "additionalProperties": False,
            },
            func=transform_data,
        ),
    ]
