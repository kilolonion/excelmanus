"""工作表管理工具：提供工作表级别的查看、创建、复制、重命名、删除和跨表数据传输能力。"""

from __future__ import annotations

import json
from typing import Any

from openpyxl import Workbook, load_workbook
from openpyxl.utils import get_column_letter

from excelmanus.logger import get_logger
from excelmanus.security import FileAccessGuard
from excelmanus.tools.registry import ToolDef

logger = get_logger("tools.sheet")

# ── Skill 元数据 ──────────────────────────────────────────

SKILL_NAME = "sheet"
SKILL_DESCRIPTION = "工作表管理工具集：列表、创建、复制、重命名、删除和跨表数据传输"

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


# ── 工具函数 ──────────────────────────────────────────────


def list_sheets(file_path: str) -> str:
    """列出 Excel 文件中所有工作表的名称和基本信息。

    Args:
        file_path: Excel 文件路径。

    Returns:
        JSON 格式的工作表列表，包含名称、行列数和是否为活动表。
    """
    guard = _get_guard()
    safe_path = guard.resolve_and_validate(file_path)

    wb = load_workbook(safe_path, read_only=True, data_only=True)
    try:
        active_name = wb.active.title if wb.active else None
        sheets: list[dict[str, Any]] = []
        for ws in wb.worksheets:
            sheets.append({
                "name": ws.title,
                "rows": ws.max_row or 0,
                "columns": ws.max_column or 0,
                "is_active": ws.title == active_name,
            })
    finally:
        wb.close()

    return json.dumps(
        {
            "file": safe_path.name,
            "sheet_count": len(sheets),
            "sheets": sheets,
        },
        ensure_ascii=False,
        indent=2,
    )


def create_sheet(
    file_path: str,
    sheet_name: str,
    position: int | None = None,
) -> str:
    """在已有 Excel 文件中新建空白工作表。

    Args:
        file_path: Excel 文件路径。
        sheet_name: 新工作表名称。
        position: 插入位置索引（0 表示最前面），默认追加到最后。

    Returns:
        JSON 格式的操作结果。
    """
    guard = _get_guard()
    safe_path = guard.resolve_and_validate(file_path)

    wb = load_workbook(safe_path)

    if sheet_name in wb.sheetnames:
        wb.close()
        return json.dumps(
            {"status": "error", "message": f"工作表 '{sheet_name}' 已存在"},
            ensure_ascii=False,
        )

    kwargs: dict[str, Any] = {"title": sheet_name}
    if position is not None:
        kwargs["index"] = position

    wb.create_sheet(**kwargs)
    wb.save(safe_path)
    wb.close()

    logger.info("已在 %s 中创建工作表 '%s'", safe_path.name, sheet_name)

    return json.dumps(
        {
            "status": "success",
            "file": safe_path.name,
            "created_sheet": sheet_name,
            "total_sheets": len(wb.sheetnames),
            "all_sheets": wb.sheetnames,
        },
        ensure_ascii=False,
        indent=2,
    )


def copy_sheet(
    file_path: str,
    source_sheet: str,
    new_name: str | None = None,
) -> str:
    """复制工作表（同文件内）。

    Args:
        file_path: Excel 文件路径。
        source_sheet: 要复制的源工作表名称。
        new_name: 新工作表名称，默认自动生成（如 "Sheet1 Copy"）。

    Returns:
        JSON 格式的操作结果。
    """
    guard = _get_guard()
    safe_path = guard.resolve_and_validate(file_path)

    wb = load_workbook(safe_path)

    if source_sheet not in wb.sheetnames:
        wb.close()
        return json.dumps(
            {
                "status": "error",
                "message": f"源工作表 '{source_sheet}' 不存在，可用: {wb.sheetnames}",
            },
            ensure_ascii=False,
        )

    if new_name and new_name in wb.sheetnames:
        wb.close()
        return json.dumps(
            {"status": "error", "message": f"工作表 '{new_name}' 已存在"},
            ensure_ascii=False,
        )

    source_ws = wb[source_sheet]
    copied_ws = wb.copy_worksheet(source_ws)

    if new_name:
        copied_ws.title = new_name

    wb.save(safe_path)
    final_name = copied_ws.title
    wb.close()

    logger.info("已在 %s 中复制工作表 '%s' -> '%s'", safe_path.name, source_sheet, final_name)

    return json.dumps(
        {
            "status": "success",
            "file": safe_path.name,
            "source_sheet": source_sheet,
            "new_sheet": final_name,
            "all_sheets": wb.sheetnames,
        },
        ensure_ascii=False,
        indent=2,
    )


def rename_sheet(
    file_path: str,
    old_name: str,
    new_name: str,
) -> str:
    """重命名工作表。

    Args:
        file_path: Excel 文件路径。
        old_name: 当前工作表名称。
        new_name: 新名称。

    Returns:
        JSON 格式的操作结果。
    """
    guard = _get_guard()
    safe_path = guard.resolve_and_validate(file_path)

    wb = load_workbook(safe_path)

    if old_name not in wb.sheetnames:
        wb.close()
        return json.dumps(
            {
                "status": "error",
                "message": f"工作表 '{old_name}' 不存在，可用: {wb.sheetnames}",
            },
            ensure_ascii=False,
        )

    if new_name in wb.sheetnames:
        wb.close()
        return json.dumps(
            {"status": "error", "message": f"工作表 '{new_name}' 已存在"},
            ensure_ascii=False,
        )

    wb[old_name].title = new_name
    wb.save(safe_path)
    wb.close()

    logger.info("已在 %s 中重命名工作表 '%s' -> '%s'", safe_path.name, old_name, new_name)

    return json.dumps(
        {
            "status": "success",
            "file": safe_path.name,
            "old_name": old_name,
            "new_name": new_name,
            "all_sheets": wb.sheetnames,
        },
        ensure_ascii=False,
        indent=2,
    )


def delete_sheet(
    file_path: str,
    sheet_name: str,
    confirm: bool = False,
) -> str:
    """删除工作表（需二次确认）。

    Args:
        file_path: Excel 文件路径。
        sheet_name: 要删除的工作表名称。
        confirm: 是否确认删除，必须为 True 才执行。

    Returns:
        JSON 格式的操作结果。
    """
    guard = _get_guard()
    safe_path = guard.resolve_and_validate(file_path)

    wb = load_workbook(safe_path)

    if sheet_name not in wb.sheetnames:
        wb.close()
        return json.dumps(
            {
                "status": "error",
                "message": f"工作表 '{sheet_name}' 不存在，可用: {wb.sheetnames}",
            },
            ensure_ascii=False,
        )

    if len(wb.sheetnames) <= 1:
        wb.close()
        return json.dumps(
            {"status": "error", "message": "不能删除唯一的工作表"},
            ensure_ascii=False,
        )

    if not confirm:
        # 返回待删除工作表信息，供 LLM 二次确认
        ws = wb[sheet_name]
        wb.close()
        return json.dumps(
            {
                "status": "pending_confirmation",
                "message": "请将 confirm 设为 true 以确认删除",
                "sheet_name": sheet_name,
                "rows": ws.max_row or 0,
                "columns": ws.max_column or 0,
            },
            ensure_ascii=False,
            indent=2,
        )

    del wb[sheet_name]
    wb.save(safe_path)
    remaining = wb.sheetnames
    wb.close()

    logger.info("已在 %s 中删除工作表 '%s'", safe_path.name, sheet_name)

    return json.dumps(
        {
            "status": "success",
            "file": safe_path.name,
            "deleted_sheet": sheet_name,
            "remaining_sheets": remaining,
        },
        ensure_ascii=False,
        indent=2,
    )


def copy_range_between_sheets(
    source_file: str,
    source_sheet: str,
    source_range: str,
    target_file: str | None = None,
    target_sheet: str = "Sheet1",
    target_start: str = "A1",
) -> str:
    """从源工作表复制指定范围的数据到目标工作表，支持跨文件。

    Args:
        source_file: 源 Excel 文件路径。
        source_sheet: 源工作表名称。
        source_range: 源数据范围，如 "A1:D10"。
        target_file: 目标 Excel 文件路径，默认与源文件相同。
        target_sheet: 目标工作表名称，默认 Sheet1。
        target_start: 目标起始单元格，默认 A1。

    Returns:
        JSON 格式的操作结果。
    """
    guard = _get_guard()
    safe_source = guard.resolve_and_validate(source_file)
    same_file = target_file is None or str(guard.resolve_and_validate(target_file)) == str(safe_source)
    safe_target = safe_source if same_file else guard.resolve_and_validate(target_file)  # type: ignore[arg-type]

    # 读取源数据
    src_wb = load_workbook(safe_source, data_only=True)
    if source_sheet not in src_wb.sheetnames:
        src_wb.close()
        return json.dumps(
            {
                "status": "error",
                "message": f"源工作表 '{source_sheet}' 不存在，可用: {src_wb.sheetnames}",
            },
            ensure_ascii=False,
        )

    src_ws = src_wb[source_sheet]

    # 解析源范围，提取值
    from openpyxl.utils.cell import range_boundaries
    min_col, min_row, max_col, max_row = range_boundaries(source_range)

    data: list[list[Any]] = []
    for row in src_ws.iter_rows(
        min_row=min_row, max_row=max_row, min_col=min_col, max_col=max_col, values_only=True
    ):
        data.append(list(row))
    src_wb.close()

    if not data:
        return json.dumps(
            {"status": "error", "message": f"源范围 '{source_range}' 无数据"},
            ensure_ascii=False,
        )

    # 写入目标
    if safe_target.exists():
        tgt_wb = load_workbook(safe_target)
    else:
        tgt_wb = Workbook()

    if target_sheet not in tgt_wb.sheetnames:
        tgt_wb.create_sheet(title=target_sheet)
        # 如果是新建的 Workbook 且自带默认 Sheet，可移除
        if "Sheet" in tgt_wb.sheetnames and target_sheet != "Sheet" and len(tgt_wb.sheetnames) > 1:
            del tgt_wb["Sheet"]

    tgt_ws = tgt_wb[target_sheet]

    # 解析目标起始位置
    from openpyxl.utils.cell import coordinate_to_tuple
    start_row, start_col = coordinate_to_tuple(target_start.upper())

    # 写入数据
    cells_written = 0
    for r_idx, row_data in enumerate(data):
        for c_idx, value in enumerate(row_data):
            tgt_ws.cell(row=start_row + r_idx, column=start_col + c_idx, value=value)
            cells_written += 1

    tgt_wb.save(safe_target)
    tgt_wb.close()

    logger.info(
        "已从 %s[%s]%s 复制 %d 个单元格到 %s[%s]%s",
        safe_source.name, source_sheet, source_range,
        cells_written,
        safe_target.name, target_sheet, target_start,
    )

    return json.dumps(
        {
            "status": "success",
            "source": {
                "file": safe_source.name,
                "sheet": source_sheet,
                "range": source_range,
            },
            "target": {
                "file": safe_target.name,
                "sheet": target_sheet,
                "start_cell": target_start,
            },
            "rows_copied": len(data),
            "cells_written": cells_written,
        },
        ensure_ascii=False,
        indent=2,
    )


# ── get_tools() 导出 ──────────────────────────────────────


def get_tools() -> list[ToolDef]:
    """返回工作表管理工具的所有工具定义。"""
    return [
        ToolDef(
            name="list_sheets",
            description="列出 Excel 文件中所有工作表的名称、行列数和是否为活动表",
            input_schema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Excel 文件路径",
                    },
                },
                "required": ["file_path"],
                "additionalProperties": False,
            },
            func=list_sheets,
        ),
        ToolDef(
            name="create_sheet",
            description="在已有 Excel 文件中新建空白工作表，支持指定插入位置",
            input_schema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Excel 文件路径",
                    },
                    "sheet_name": {
                        "type": "string",
                        "description": "新工作表名称",
                    },
                    "position": {
                        "type": "integer",
                        "description": "插入位置索引（0 表示最前面），默认追加到最后",
                    },
                },
                "required": ["file_path", "sheet_name"],
                "additionalProperties": False,
            },
            func=create_sheet,
        ),
        ToolDef(
            name="copy_sheet",
            description="复制工作表（同文件内），生成副本",
            input_schema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Excel 文件路径",
                    },
                    "source_sheet": {
                        "type": "string",
                        "description": "要复制的源工作表名称",
                    },
                    "new_name": {
                        "type": "string",
                        "description": "新工作表名称，默认自动生成",
                    },
                },
                "required": ["file_path", "source_sheet"],
                "additionalProperties": False,
            },
            func=copy_sheet,
        ),
        ToolDef(
            name="rename_sheet",
            description="重命名 Excel 文件中的工作表",
            input_schema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Excel 文件路径",
                    },
                    "old_name": {
                        "type": "string",
                        "description": "当前工作表名称",
                    },
                    "new_name": {
                        "type": "string",
                        "description": "新名称",
                    },
                },
                "required": ["file_path", "old_name", "new_name"],
                "additionalProperties": False,
            },
            func=rename_sheet,
        ),
        ToolDef(
            name="delete_sheet",
            description="删除 Excel 文件中的工作表（需 confirm=true 二次确认）",
            input_schema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Excel 文件路径",
                    },
                    "sheet_name": {
                        "type": "string",
                        "description": "要删除的工作表名称",
                    },
                    "confirm": {
                        "type": "boolean",
                        "description": "是否确认删除，必须为 true 才执行",
                        "default": False,
                    },
                },
                "required": ["file_path", "sheet_name"],
                "additionalProperties": False,
            },
            func=delete_sheet,
        ),
        ToolDef(
            name="copy_range_between_sheets",
            description="从源工作表复制指定范围的数据到目标工作表，支持跨文件复制",
            input_schema={
                "type": "object",
                "properties": {
                    "source_file": {
                        "type": "string",
                        "description": "源 Excel 文件路径",
                    },
                    "source_sheet": {
                        "type": "string",
                        "description": "源工作表名称",
                    },
                    "source_range": {
                        "type": "string",
                        "description": "源数据范围，如 'A1:D10'",
                    },
                    "target_file": {
                        "type": "string",
                        "description": "目标 Excel 文件路径，默认与源文件相同",
                    },
                    "target_sheet": {
                        "type": "string",
                        "description": "目标工作表名称，默认 Sheet1",
                        "default": "Sheet1",
                    },
                    "target_start": {
                        "type": "string",
                        "description": "目标起始单元格，默认 A1",
                        "default": "A1",
                    },
                },
                "required": ["source_file", "source_sheet", "source_range"],
                "additionalProperties": False,
            },
            func=copy_range_between_sheets,
        ),
    ]
