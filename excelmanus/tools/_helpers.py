"""工具层共享辅助函数。"""

from __future__ import annotations

from typing import Any, Sequence


def resolve_sheet_name(
    requested: str | None,
    available: Sequence[str],
) -> str | None:
    """对 sheet 名做 case-insensitive 模糊匹配。

    优先精确匹配；若失败则尝试忽略大小写匹配。
    匹配成功返回 *实际* sheet 名（保留原始大小写），
    匹配失败返回 ``None``。

    Args:
        requested: LLM / 用户传入的 sheet 名，可为 None。
        available: workbook 中实际存在的 sheet 名列表。

    Returns:
        匹配到的实际 sheet 名，或 None。
    """
    if requested is None:
        return None

    # 精确匹配
    if requested in available:
        return requested

    # case-insensitive fallback
    lower = requested.lower()
    for name in available:
        if name.lower() == lower:
            return name

    return None


def get_worksheet(wb: Any, sheet_name: str | None) -> Any:
    """从 workbook 中获取工作表，支持 case-insensitive fallback。

    若 sheet_name 为 None 或无法匹配，返回 active sheet。

    Args:
        wb: openpyxl Workbook 对象。
        sheet_name: 请求的 sheet 名。

    Returns:
        匹配到的 Worksheet 对象。
    """
    if not sheet_name:
        return wb.active
    resolved = resolve_sheet_name(sheet_name, wb.sheetnames)
    if resolved is not None:
        return wb[resolved]
    return wb.active
