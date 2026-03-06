"""引用图谱核心数据模型。"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum


class RefType(str, Enum):
    """引用类型。"""

    FORMULA = "formula"
    DATA_LINK = "data_link"
    VALIDATION = "validation"
    COND_FORMAT = "cond_format"
    NAMED_RANGE = "named_range"
    EXTERNAL = "external"


@dataclass
class CellRef:
    """单元格或区域引用。"""

    cell_or_range: str
    file_path: str | None = None
    sheet_name: str | None = None
    is_absolute_row: bool = False
    is_absolute_col: bool = False

    def display(self) -> str:
        parts: list[str] = []
        if self.file_path:
            parts.append(f"[{self.file_path}]")
        if self.sheet_name:
            parts.append(f"{self.sheet_name}!")
        parts.append(self.cell_or_range)
        return "".join(parts)


@dataclass
class ExternalRef:
    """外部工作簿引用。"""

    book_name: str
    sheet_name: str
    cell_or_range: str
    source_sheet: str
    source_cell: str


@dataclass
class SheetRefEdge:
    """工作表之间的引用边。"""

    source_sheet: str
    target_sheet: str
    ref_type: RefType
    ref_count: int
    sample_formulas: list[str] = field(default_factory=list)
    column_pairs: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class SheetRefSummary:
    """单个工作表的引用摘要。"""

    sheet_name: str
    formula_count: int
    outgoing_refs: list[SheetRefEdge] = field(default_factory=list)
    incoming_refs: list[SheetRefEdge] = field(default_factory=list)
    self_refs: int = 0
    formula_patterns: list[str] = field(default_factory=list)


@dataclass
class WorkbookRefIndex:
    """工作簿级引用索引 — Tier 1。"""

    file_path: str
    sheets: dict[str, SheetRefSummary] = field(default_factory=dict)
    cross_sheet_edges: list[SheetRefEdge] = field(default_factory=list)
    external_refs: list[ExternalRef] = field(default_factory=list)
    named_ranges: dict[str, str] = field(default_factory=dict)
    built_at: float = field(default_factory=time.time)

    def render_summary(self) -> str:
        """渲染适合注入系统提示词的摘要文本。"""
        if not self.sheets:
            return ""
        lines: list[str] = []
        for edge in self.cross_sheet_edges:
            pattern = ""
            if edge.sample_formulas:
                funcs: set[str] = set()
                for f in edge.sample_formulas:
                    for name in ("VLOOKUP", "HLOOKUP", "XLOOKUP", "INDEX", "MATCH",
                                 "SUMIFS", "SUMIF", "COUNTIFS", "COUNTIF", "IF",
                                 "SUM", "AVERAGE", "INDIRECT", "OFFSET"):
                        if name in f.upper():
                            funcs.add(name)
                            break
                pattern = ",".join(sorted(funcs)) if funcs else "REF"
            else:
                pattern = edge.ref_type.value.upper()
            lines.append(f"{edge.source_sheet} ──{pattern}──→ {edge.target_sheet}")
        for ext in self.external_refs:
            lines.append(
                f"[{ext.book_name}]{ext.sheet_name}!{ext.cell_or_range} → {ext.source_sheet}"
            )
        return "\n".join(lines)


@dataclass
class CellNode:
    """依赖图中的单元格节点 — Tier 2。"""

    sheet: str
    address: str
    formula: str | None = None
    precedents: list[CellRef] = field(default_factory=list)
    dependents: list[CellRef] = field(default_factory=list)
