"""引用关系图工具：get_reference_map, trace_references, get_impact_analysis。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from excelmanus.engine_core.tool_result import ToolResult, ToolUiMeta, error_result, ok_result
from excelmanus.reference_graph.cache import RefCache, get_session_cache
from excelmanus.reference_graph.formula_parser import FormulaRefExtractor, address_in_ref
from excelmanus.reference_graph.models import WorkbookRefIndex
from excelmanus.reference_graph.scanner import Tier1Scanner, Tier2Resolver

_workspace_root: str | None = None
_scanner = Tier1Scanner()
_resolver = Tier2Resolver()


def init_guard(workspace_root: str) -> None:
    """设置工具的工作空间根目录。"""
    global _workspace_root
    _workspace_root = workspace_root


def _resolve_path(file_path: str) -> str:
    """将相对路径解析为绝对路径。"""
    if _workspace_root and not Path(file_path).is_absolute():
        return str(Path(_workspace_root) / file_path)
    return file_path


def _ensure_index(file_path: str) -> WorkbookRefIndex:
    """确保 Tier 1 索引已缓存。"""
    abs_path = _resolve_path(file_path)
    cache = get_session_cache()
    cached = cache.get_tier1(abs_path)
    if cached is not None:
        return cached
    index = _scanner.scan(abs_path)
    cache.put_tier1(abs_path, index)
    return index


def _error_json(message: str) -> ToolResult:
    return error_result(message, code="EXECUTION_FAILED")


def _parse_target(target: str) -> tuple[str | None, str]:
    """解析 'Sheet!Cell' 格式的目标。"""
    from excelmanus.workbook.address import parse_sheet_address

    parsed = parse_sheet_address(target)
    return parsed.sheet, parsed.address


def get_reference_map(file_path: str, detail: str = "summary") -> ToolResult:
    """获取工作簿引用全景图。"""
    try:
        index = _ensure_index(file_path)
    except Exception as e:
        return _error_json(f"无法扫描文件: {e}")

    result: dict[str, Any] = {
        "file_path": file_path,
        "sheets": {},
        "cross_sheet_edges": [],
        "named_ranges": index.named_ranges,
    }

    for name, summary in index.sheets.items():
        result["sheets"][name] = {
            "formula_count": summary.formula_count,
            "self_refs": summary.self_refs,
            "formula_patterns": summary.formula_patterns,
            "outgoing": [e.target_sheet for e in summary.outgoing_refs],
            "incoming": [e.source_sheet for e in summary.incoming_refs],
        }

    for edge in index.cross_sheet_edges:
        result["cross_sheet_edges"].append({
            "source_sheet": edge.source_sheet,
            "target_sheet": edge.target_sheet,
            "ref_type": edge.ref_type.value,
            "ref_count": edge.ref_count,
            "sample_formulas": edge.sample_formulas,
        })

    summary_text = ""
    if detail == "summary":
        summary_text = index.render_summary() or ""
        if summary_text:
            result["summary_text"] = summary_text

    return ok_result(
        result,
        model_text=summary_text or json.dumps(result, ensure_ascii=False, default=str),
        ui_meta=ToolUiMeta(files=[file_path]),
    )


def trace_references(
    file_path: str,
    target: str,
    direction: str = "both",
    depth: int = 2,
) -> ToolResult:
    """追踪单元格引用链。"""
    try:
        abs_path = _resolve_path(file_path)
        sheet_name, address = _parse_target(target)

        if sheet_name is None:
            from openpyxl import load_workbook
            wb = load_workbook(abs_path, data_only=False, read_only=True)
            try:
                sheet_name = wb.sheetnames[0]
            finally:
                wb.close()

        node = _resolver.resolve(
            abs_path, sheet_name, address,
            direction=direction, depth=depth,
        )

        precedents = [
            {
                "cell_or_range": r.cell_or_range,
                "sheet_name": r.sheet_name or sheet_name,
                "display": r.display(),
            }
            for r in node.precedents
        ]
        dependents = [
            {
                "cell": r.cell_or_range,
                "sheet": r.sheet_name or sheet_name,
            }
            for r in node.dependents
        ]

        payload = {
            "target": target,
            "file_path": file_path,
            "formula": node.formula,
            "precedents": precedents,
            "dependents": dependents,
        }
        return ok_result(
            payload,
            model_text=(
                f"{target}: precedents={len(precedents)}, dependents={len(dependents)}"
            ),
        )
    except Exception as e:
        return _error_json(f"追踪引用失败: {e}")


def get_impact_analysis(
    file_path: str,
    target: str,
    scope: str = "all",
) -> ToolResult:
    """分析修改影响范围。"""
    try:
        abs_path = _resolve_path(file_path)
        sheet_name, address = _parse_target(target)

        from openpyxl import load_workbook
        wb = load_workbook(abs_path, data_only=False, read_only=True)
        try:
            if sheet_name is None:
                sheet_name = wb.sheetnames[0]

            extractor = FormulaRefExtractor()
            direct: list[dict[str, Any]] = []
            affected_sheets: set[str] = set()

            for ws_name in wb.sheetnames:
                ws = wb[ws_name]
                for row in ws.iter_rows():
                    for cell in row:
                        val = cell.value
                        if not isinstance(val, str) or not val.startswith("="):
                            continue
                        refs = extractor.extract(val)
                        for ref in refs:
                            ref_sheet = ref.sheet_name or ws_name
                            if ref_sheet == sheet_name and address_in_ref(address, ref.cell_or_range):
                                coord = cell.coordinate if hasattr(cell, "coordinate") else ""
                                direct.append({
                                    "cell": coord,
                                    "sheet": ws_name,
                                    "formula": val,
                                })
                                affected_sheets.add(ws_name)
        finally:
            wb.close()

        payload = {
            "target": target,
            "file_path": file_path,
            "direct_impact": direct,
            "total_affected_cells": len(direct),
            "affected_sheets": sorted(affected_sheets),
        }
        return ok_result(
            payload,
            model_text=(
                f"{target}: {len(direct)} cells across {len(affected_sheets)} sheets"
            ),
        )
    except Exception as e:
        return _error_json(f"影响分析失败: {e}")


def get_cache() -> RefCache:
    """返回当前会话的缓存实例（供集成使用）。"""
    return get_session_cache()
