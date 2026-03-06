"""Excel 公式引用提取器。"""
from __future__ import annotations

import re

from .models import CellRef

_CELL_ADDR = r"\$?[A-Z]{1,3}\$?\d+"
_RANGE_ADDR = rf"{_CELL_ADDR}(?::{_CELL_ADDR})?"
_COL_RANGE = r"[A-Z]{1,3}:[A-Z]{1,3}"

_EXTERNAL_RE = re.compile(
    rf"\[(?P<book>[^\]]+)\](?P<sheet>[^!]+)!(?P<range>{_RANGE_ADDR}|{_COL_RANGE})"
)

_QUOTED_SHEET_RE = re.compile(
    rf"'(?P<sheet>[^']+)'!(?P<range>{_RANGE_ADDR}|{_COL_RANGE})"
)

_SHEET_RE = re.compile(
    rf"(?<![A-Za-z0-9_'\]])(?P<sheet>[A-Za-z_\u4e00-\u9fff][\w\u4e00-\u9fff]*)!(?P<range>{_RANGE_ADDR}|{_COL_RANGE})"
)

_COL_RANGE_RE = re.compile(
    rf"(?<![A-Za-z0-9!])(?P<range>{_COL_RANGE})(?![A-Za-z0-9])"
)

_CELL_RANGE_RE = re.compile(
    rf"(?<![A-Za-z0-9!])(?P<range>{_RANGE_ADDR})(?![A-Za-z0-9(])"
)

_FUNC_RE = re.compile(r"([A-Z][A-Z0-9_.]+)\s*\(", re.IGNORECASE)

_KNOWN_FUNCTIONS = frozenset({
    "SUM", "SUMIF", "SUMIFS", "SUMPRODUCT",
    "COUNT", "COUNTA", "COUNTIF", "COUNTIFS", "COUNTBLANK",
    "AVERAGE", "AVERAGEIF", "AVERAGEIFS",
    "MIN", "MAX", "LARGE", "SMALL",
    "IF", "IFS", "IFERROR", "IFNA",
    "VLOOKUP", "HLOOKUP", "XLOOKUP", "LOOKUP",
    "INDEX", "MATCH", "XMATCH",
    "INDIRECT", "OFFSET", "ADDRESS", "ROW", "COLUMN", "ROWS", "COLUMNS",
    "LEFT", "RIGHT", "MID", "LEN", "FIND", "SEARCH", "SUBSTITUTE", "REPLACE",
    "TEXT", "VALUE", "TRIM", "CLEAN", "UPPER", "LOWER", "PROPER", "CONCATENATE",
    "DATE", "YEAR", "MONTH", "DAY", "TODAY", "NOW", "DATEDIF", "EDATE", "EOMONTH",
    "AND", "OR", "NOT", "TRUE", "FALSE",
    "ABS", "ROUND", "ROUNDUP", "ROUNDDOWN", "INT", "MOD", "CEILING", "FLOOR",
    "RANK", "PERCENTILE", "MEDIAN", "STDEV",
    "UNIQUE", "SORT", "FILTER", "SEQUENCE", "LET", "LAMBDA",
    "TEXTJOIN", "CONCAT",
})


def _strip_absolute(addr: str) -> tuple[str, bool, bool]:
    """去除 $ 符号并返回绝对标志。"""
    abs_col = addr.startswith("$") or (":$" in addr)
    abs_row = bool(re.search(r"\$\d", addr))
    clean = addr.replace("$", "")
    return clean, abs_row, abs_col


class FormulaRefExtractor:
    """从 Excel 公式中提取所有单元格/区域引用（去重）。"""

    def extract(self, formula: str) -> list[CellRef]:
        if not formula or not isinstance(formula, str):
            return []
        body = formula.lstrip("=").strip() if formula.startswith("=") else formula
        if not body:
            return []

        seen: set[str] = set()
        results: list[CellRef] = []

        remaining = body

        for m in _EXTERNAL_RE.finditer(body):
            addr, abs_row, abs_col = _strip_absolute(m.group("range"))
            ref = CellRef(
                file_path=m.group("book"),
                sheet_name=m.group("sheet"),
                cell_or_range=addr,
                is_absolute_row=abs_row,
                is_absolute_col=abs_col,
            )
            key = ref.display()
            if key not in seen:
                seen.add(key)
                results.append(ref)
            remaining = remaining.replace(m.group(0), " ", 1)

        for m in _QUOTED_SHEET_RE.finditer(remaining):
            addr, abs_row, abs_col = _strip_absolute(m.group("range"))
            ref = CellRef(
                sheet_name=m.group("sheet"),
                cell_or_range=addr,
                is_absolute_row=abs_row,
                is_absolute_col=abs_col,
            )
            key = ref.display()
            if key not in seen:
                seen.add(key)
                results.append(ref)
            remaining = remaining.replace(m.group(0), " ", 1)

        for m in _SHEET_RE.finditer(remaining):
            cand = m.group("sheet")
            if cand.upper() in _KNOWN_FUNCTIONS:
                continue
            addr, abs_row, abs_col = _strip_absolute(m.group("range"))
            ref = CellRef(
                sheet_name=cand,
                cell_or_range=addr,
                is_absolute_row=abs_row,
                is_absolute_col=abs_col,
            )
            key = ref.display()
            if key not in seen:
                seen.add(key)
                results.append(ref)
            remaining = remaining.replace(m.group(0), " ", 1)

        for m in _COL_RANGE_RE.finditer(remaining):
            addr = m.group("range")
            ref = CellRef(cell_or_range=addr)
            key = ref.display()
            if key not in seen:
                seen.add(key)
                results.append(ref)
            remaining = remaining.replace(m.group(0), " ", 1)

        for m in _CELL_RANGE_RE.finditer(remaining):
            raw = m.group("range")
            addr, abs_row, abs_col = _strip_absolute(raw)
            ref = CellRef(
                cell_or_range=addr,
                is_absolute_row=abs_row,
                is_absolute_col=abs_col,
            )
            key = ref.display()
            if key not in seen:
                seen.add(key)
                results.append(ref)

        return results

    def extract_functions(self, formula: str) -> list[str]:
        if not formula:
            return []
        found: list[str] = []
        seen: set[str] = set()
        for m in _FUNC_RE.finditer(formula):
            name = m.group(1).upper()
            if name in _KNOWN_FUNCTIONS and name not in seen:
                seen.add(name)
                found.append(name)
        return found
