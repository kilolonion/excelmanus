"""引用图谱扫描器 — Tier 1 工作表级 + Tier 2 单元格级。"""
from __future__ import annotations

import time
from collections import Counter, defaultdict
from typing import Any

from .formula_parser import FormulaRefExtractor
from .models import (
    CellNode,
    CellRef,
    ExternalRef,
    RefType,
    SheetRefEdge,
    SheetRefSummary,
    WorkbookRefIndex,
)

_MAX_SAMPLE_FORMULAS = 3


class Tier1Scanner:
    """轻量级工作表级引用扫描（文件上传时执行）。"""

    def __init__(self) -> None:
        self._extractor = FormulaRefExtractor()

    def scan(self, file_path: str) -> WorkbookRefIndex:
        from openpyxl import load_workbook

        wb = load_workbook(file_path, data_only=False, read_only=True)
        try:
            return self._scan_workbook(file_path, wb)
        finally:
            wb.close()

    def _scan_workbook(self, file_path: str, wb: Any) -> WorkbookRefIndex:
        sheets: dict[str, SheetRefSummary] = {}
        edge_map: dict[tuple[str, str], dict[str, Any]] = {}
        external_refs: list[ExternalRef] = []
        named_ranges: dict[str, str] = {}

        for dn in wb.defined_names.values():
            if dn.attr_text and not dn.attr_text.startswith("#"):
                named_ranges[dn.name] = dn.attr_text

        for ws_name in wb.sheetnames:
            ws = wb[ws_name]
            formula_count = 0
            self_ref_count = 0
            func_counter: Counter[str] = Counter()
            outgoing: dict[str, int] = defaultdict(int)
            outgoing_samples: dict[str, list[str]] = defaultdict(list)

            for row in ws.iter_rows():
                for cell in row:
                    val = cell.value
                    if not isinstance(val, str) or not val.startswith("="):
                        continue
                    formula_count += 1
                    refs = self._extractor.extract(val)
                    funcs = self._extractor.extract_functions(val)
                    for f in funcs:
                        func_counter[f] += 1

                    has_self = False
                    for ref in refs:
                        if ref.file_path:
                            external_refs.append(ExternalRef(
                                book_name=ref.file_path,
                                sheet_name=ref.sheet_name or "",
                                cell_or_range=ref.cell_or_range,
                                source_sheet=ws_name,
                                source_cell=cell.coordinate if hasattr(cell, "coordinate") else "",
                            ))
                        elif ref.sheet_name and ref.sheet_name != ws_name:
                            target = ref.sheet_name
                            outgoing[target] += 1
                            if len(outgoing_samples[target]) < _MAX_SAMPLE_FORMULAS:
                                outgoing_samples[target].append(val)
                        else:
                            has_self = True
                    if has_self:
                        self_ref_count += 1

            top_funcs = [f for f, _ in func_counter.most_common(10)]
            sheets[ws_name] = SheetRefSummary(
                sheet_name=ws_name,
                formula_count=formula_count,
                outgoing_refs=[],
                incoming_refs=[],
                self_refs=self_ref_count,
                formula_patterns=top_funcs,
            )

            for target, count in outgoing.items():
                key = (ws_name, target)
                edge_map[key] = {
                    "source": ws_name,
                    "target": target,
                    "count": count,
                    "samples": outgoing_samples[target],
                }

        cross_sheet_edges: list[SheetRefEdge] = []
        for (src, tgt), info in edge_map.items():
            edge = SheetRefEdge(
                source_sheet=src,
                target_sheet=tgt,
                ref_type=RefType.FORMULA,
                ref_count=info["count"],
                sample_formulas=info["samples"],
                column_pairs=[],
            )
            cross_sheet_edges.append(edge)
            if src in sheets:
                sheets[src].outgoing_refs.append(edge)
            if tgt in sheets:
                sheets[tgt].incoming_refs.append(edge)

        return WorkbookRefIndex(
            file_path=file_path,
            sheets=sheets,
            cross_sheet_edges=cross_sheet_edges,
            external_refs=external_refs,
            named_ranges=named_ranges,
            built_at=time.time(),
        )
