"""Token-aware A1 reference transformations for workbook structural edits.

Insertion/deletion changes even absolute references: dollar signs control copying,
not structural edits. All transformations are pure so callers can preflight every
dependent object before changing the workbook.
"""
from __future__ import annotations

from dataclasses import dataclass
import re

from openpyxl.formula.tokenizer import Tokenizer, TokenizerError
from openpyxl.utils.cell import column_index_from_string, get_column_letter, quote_sheetname

MAX_ROW = 1048576
MAX_COL = 16384
_CELL = r"\$?[A-Za-z]{1,3}\$?[1-9][0-9]*"
_COL = r"\$?[A-Za-z]{1,3}"
_ROW = r"\$?[1-9][0-9]*"
A1_RE = re.compile(rf"^(?:{_CELL}(?::{_CELL})?|{_COL}:{_COL}|{_ROW}:{_ROW})$")
_POINT_RE = re.compile(r"^(\$?)([A-Za-z]*)(\$?)([0-9]*)$")


def split_reference(value: str) -> tuple[str | None, str]:
    if "!" not in value:
        return None, value
    sheet, address = value.rsplit("!", 1)
    if sheet.startswith("'") and sheet.endswith("'"):
        sheet = sheet[1:-1].replace("''", "'")
    return sheet, address


@dataclass(frozen=True)
class ReferenceTransform:
    sheet: str
    axis: str | None = None
    at: int = 1
    count: int = 1
    delete: bool = False
    new_name: str | None = None

    def interval(self, start: int, end: int) -> tuple[int, int] | None:
        """Transform an inclusive interval, shrinking a partially deleted range."""
        if not self.delete:
            return (start + (self.count if start >= self.at else 0),
                    end + (self.count if end >= self.at else 0))
        last = self.at + self.count - 1
        if end < self.at:
            return start, end
        if start > last:
            return start - self.count, end - self.count
        if start >= self.at and end <= last:
            return None
        return (start if start < self.at else self.at,
                end - self.count if end > last else self.at - 1)

    def point(self, value: int, *, anchor: bool = False) -> int | None:
        interval = self.interval(value, value)
        return interval[0] if interval else (self.at if anchor else None)

    def address(self, address: str) -> str:
        if not A1_RE.fullmatch(address):
            raise ValueError(f"Unsupported A1 reference: {address}")
        parts = address.split(":")
        points = [_POINT_RE.fullmatch(part).groups() for part in parts]
        whole_col = not points[0][3]
        whole_row = not points[0][1]
        for _, col, _, row in points:
            if (col and column_index_from_string(col) > MAX_COL) or (row and int(row) > MAX_ROW):
                raise ValueError(f"Reference exceeds Excel limits: {address}")
        if self.new_name or (whole_col and self.axis == "row") or (whole_row and self.axis == "column"):
            return address
        values = [int(p[3]) if self.axis == "row" else column_index_from_string(p[1]) for p in points]
        if len(values) == 1:
            values *= 2
        reverse = values[0] > values[1]
        transformed = self.interval(min(values), max(values))
        if transformed is None:
            return "#REF!"
        if max(transformed) > (MAX_ROW if self.axis == "row" else MAX_COL):
            raise ValueError(f"Structural edit would move reference beyond Excel limits: {address}")
        if reverse:
            transformed = transformed[::-1]
        result = []
        for index, (dcol, col, drow, row) in enumerate(points):
            coordinate = transformed[index]
            if self.axis == "row":
                row = str(coordinate)
            else:
                col = get_column_letter(coordinate)
            result.append(f"{dcol}{col}{drow}{row}")
        return ":".join(result)

    def formula(self, formula: str, context_sheet: str | None) -> str:
        if not isinstance(formula, str):
            raise ValueError("Array/data-table formulas require an Excel engine for structural edits")
        if not formula:
            return formula
        leading_equals = formula.startswith("=")
        try:
            tokens = Tokenizer(formula if leading_equals else "=" + formula).items
        except (TokenizerError, IndexError) as exc:
            raise ValueError(f"Cannot safely parse formula: {formula}") from exc
        for token in tokens:
            if token.type == "FUNC" and token.subtype == "OPEN":
                function = token.value[:-1].upper().removeprefix("_XLFN.").removeprefix("_XLWS.")
                if function in {"INDIRECT", "OFFSET"}:
                    raise ValueError(f"Dynamic reference {function} requires an Excel engine before structural editing")
            if token.type != "OPERAND" or token.subtype != "RANGE":
                continue
            sheet, address = split_reference(token.value)
            if sheet and ("[" in sheet or ":" in sheet):
                raise ValueError(f"External/3D reference cannot be maintained: {token.value}")
            if address.endswith("#"):
                raise ValueError(f"Spill reference cannot be maintained: {token.value}")
            if (sheet or context_sheet or "").casefold() != self.sheet.casefold():
                # Workbook-scoped relative A1 names have an implicit active-cell
                # base that openpyxl does not preserve reliably.
                if sheet is None and context_sheet is None and A1_RE.fullmatch(address):
                    raise ValueError(f"Unqualified workbook-scoped reference cannot be maintained: {address}")
                continue
            if self.new_name:
                if sheet is not None:
                    token.value = quote_sheetname(self.new_name) + "!" + address
            elif A1_RE.fullmatch(address):
                rewritten = self.address(address)
                token.value = (token.value[:token.value.rfind("!") + 1] if sheet else "") + rewritten
            # Named/structured references remain stable while their definitions
            # and Table ranges are transformed by the workbook object planner.
        return ("=" if leading_equals else "") + "".join(token.value for token in tokens)
