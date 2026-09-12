"""Excel A1 / Sheet!A1 解析：接受公式与 Office 常见写法，输出 openpyxl 可用坐标。"""

from __future__ import annotations

import re
from typing import NamedTuple

from openpyxl.utils import column_index_from_string, get_column_letter

_SHEET_BANG = re.compile(
    r"""^\s*(?:
            '(?P<quoted>(?:[^']|'')+)'
          | (?P<unquoted>[^'!]+)
        )\s*!\s*(?P<addr>.+?)\s*$
    """,
    re.VERBOSE,
)

_INVALID_COORD_MARKERS = (
    "is not a valid coordinate",
    "is not a valid range",
    "invalid coordinates",
)


class SheetAddress(NamedTuple):
    sheet: str | None
    address: str


def parse_sheet_address(value: str | None) -> SheetAddress:
    """拆分 ``表!A1:C5`` / ``'表名'!A1`` / ``A1:C5``。

    去掉 ``$``；工作表名两侧单引号按 Excel 规则解开（``''`` → ``'``）。
    """
    raw = str(value or "").strip()
    if not raw:
        return SheetAddress(None, "")
    match = _SHEET_BANG.match(raw)
    if match:
        quoted = match.group("quoted")
        unquoted = match.group("unquoted")
        if quoted is not None:
            sheet = quoted.replace("''", "'").strip()
        else:
            sheet = (unquoted or "").strip()
        addr = (match.group("addr") or "").strip()
    else:
        sheet = None
        addr = raw
    addr = addr.replace("$", "").strip()
    return SheetAddress(sheet or None, addr)


def strip_sheet_qualifier(value: str | None) -> str:
    """只保留 A1 / A1:C5 部分。"""
    return parse_sheet_address(value).address


def combine_sheet_names(explicit: str | None, from_address: str | None) -> str | None:
    """合并独立 sheet 字段与 ``表!A1`` 中的表名；冲突则抛 ValueError。"""
    explicit_name = str(explicit).strip() if explicit not in (None, "") else None
    address_name = str(from_address).strip() if from_address not in (None, "") else None
    if explicit_name and address_name and explicit_name != address_name:
        raise ValueError(
            f"工作表不一致：sheet={explicit_name!r} 与 range 中的 {address_name!r}。"
            "请只保留一处，或两者写成同一个表名。"
        )
    return address_name or explicit_name


def looks_like_coordinate_error(exc: BaseException | str) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in _INVALID_COORD_MARKERS)


def column_map_to_list(raw: dict[object, object]) -> list[float]:
    """``{"A": 9, "C": 14}`` → ``[9, 0, 14]``（缺列填 0，编译时跳过）。"""
    widths: dict[int, float] = {}
    for key, value in raw.items():
        try:
            width = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        col = _column_key_to_index(key)
        if col is None:
            continue
        widths[col] = width
    if not widths:
        return []
    max_col = max(widths)
    return [float(widths.get(index, 0.0)) for index in range(1, max_col + 1)]


def _column_key_to_index(key: object) -> int | None:
    if isinstance(key, bool):
        return None
    if isinstance(key, int):
        return key if key >= 1 else key + 1
    text = str(key).strip()
    if not text:
        return None
    if text.isdigit():
        number = int(text)
        return number if number >= 1 else number + 1
    try:
        return int(column_index_from_string(text.upper()))
    except ValueError:
        return None


def top_left_cell(address: str) -> str:
    """``A1:C5`` → ``A1``；单格原样返回。"""
    from openpyxl.utils.cell import range_boundaries

    text = strip_sheet_qualifier(address).upper()
    if not text:
        return ""
    if ":" not in text:
        return text
    min_col, min_row, _max_col, _max_row = range_boundaries(text)
    return f"{get_column_letter(min_col)}{min_row}"
