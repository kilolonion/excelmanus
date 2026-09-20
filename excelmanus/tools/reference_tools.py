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
from excelmanus.workbook.refs import InvalidRefError

_scanner = Tier1Scanner()
_resolver = Tier2Resolver()


def init_guard(workspace_root: str) -> None:
    from excelmanus.tools.context import bind_workspace

    bind_workspace(workspace_root)


def _open_ref_snapshot(file_path: str):
    from excelmanus.workbook.data import _open_tool_snapshot

    return _open_tool_snapshot(file_path)


def _ensure_index(file_path: str, snapshot: Any = None) -> WorkbookRefIndex:
    """确保 Tier 1 索引已缓存。扫描快照 backing，键含 workspace+版本。"""
    snap, snap_err = (snapshot, None) if snapshot is not None else _open_ref_snapshot(file_path)
    if snap_err is not None or snap is None:
        raise RuntimeError(snap_err.model_text if snap_err is not None else "无法打开快照")
    cache = get_session_cache()
    key = snap.id.key()
    cached = cache.get_tier1(key)
    if cached is not None:
        return cached
    index = _scanner.scan(str(snap.backing_path))
    cache.put_tier1(key, index)
    return index


def _error_json(message: str, *, code: str = "EXECUTION_FAILED") -> ToolResult:
    return error_result(message, code=code)


def _parse_target(target: str) -> tuple[str | None, str]:
    """解析 'Sheet!Cell' 格式的目标。"""
    from excelmanus.workbook.address import parse_sheet_address

    parsed = parse_sheet_address(target)
    from openpyxl.utils.cell import range_boundaries
    try:
        c1, r1, c2, r2 = range_boundaries(parsed.address)
    except ValueError as exc:
        raise InvalidRefError("target 需要单个 A1 单元格，例如 'Sheet 1'!$B$2") from exc
    if None in (c1, r1, c2, r2) or (c1, r1) != (c2, r2):
        raise InvalidRefError("target 当前仅支持单个单元格；按单元格分别追踪范围内的公式")
    from openpyxl.utils import get_column_letter
    return parsed.sheet, f"{get_column_letter(c1)}{r1}"


def get_reference_map(file_path: str, detail: str = "summary") -> ToolResult:
    """获取工作簿引用全景图。"""
    try:
        snap, snap_err = _open_ref_snapshot(file_path)
        if snap_err is not None:
            return snap_err
        index = _ensure_index(file_path, snapshot=snap)
    except Exception as e:
        return _error_json(f"无法扫描文件: {e}")

    result: dict[str, Any] = {
        "file_path": file_path,
        "sheets": {},
        "cross_sheet_edges": [],
        "named_ranges": index.named_ranges,
        "content_version": snap.content_version,
        "coverage": {"kind": "partial", "reason": "静态公式引用解析，未重算；动态/外部引用不保证完整"},
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
    from excelmanus.workbook.snapshot import SnapshotError, require_default_sheet

    try:
        snap, snap_err = _open_ref_snapshot(file_path)
        if snap_err is not None or snap is None:
            return snap_err or _error_json("无法打开快照")
        backing = str(snap.backing_path)
        sheet_name, address = _parse_target(target)

        from openpyxl import load_workbook

        wb = load_workbook(backing, data_only=False, read_only=True)
        try:
            sheet_name = require_default_sheet(list(wb.sheetnames), sheet_name)
        except SnapshotError as exc:
            return error_result(str(exc), code=exc.code, fields=exc.fields or None)
        finally:
            wb.close()

        node = _resolver.resolve(
            backing, sheet_name, address,
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
            "resolved_sheet": sheet_name,
            "content_version": snap.content_version,
            "coverage": {"kind": "partial", "direction": direction, "depth": min(depth, _resolver._MAX_DEPTH), "reason": "静态引用解析，未重算"},
        }
        return ok_result(
            payload,
            model_text=(
                f"{target}: precedents={len(precedents)}, dependents={len(dependents)}"
            ),
        )
    except InvalidRefError as e:
        return _error_json(str(e), code="RANGE_INVALID")
    except Exception as e:
        return _error_json(f"追踪引用失败: {e}")


def get_impact_analysis(
    file_path: str,
    target: str,
    scope: str = "all",
) -> ToolResult:
    """分析修改影响范围。"""
    from excelmanus.workbook.snapshot import SnapshotError, require_default_sheet

    scope = str(scope or "all").strip().lower()
    if scope not in {"all", "sheet"}:
        return _error_json("scope 仅支持 all 或 sheet", code="INVALID_ARGS")

    try:
        snap, snap_err = _open_ref_snapshot(file_path)
        if snap_err is not None or snap is None:
            return snap_err or _error_json("无法打开快照")
        backing = str(snap.backing_path)
        sheet_name, address = _parse_target(target)

        from openpyxl import load_workbook

        wb = load_workbook(backing, data_only=False, read_only=True)
        try:
            sheet_name = require_default_sheet(list(wb.sheetnames), sheet_name)

            extractor = FormulaRefExtractor()
            direct: list[dict[str, Any]] = []
            affected_sheets: set[str] = set()

            for ws_name in wb.sheetnames:
                if scope == "sheet" and ws_name != sheet_name:
                    continue
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
                                break  # multiple references in one formula are one affected cell
        finally:
            wb.close()

        payload = {
            "target": target,
            "file_path": file_path,
            "direct_impact": direct,
            "total_affected_cells": len(direct),
            "affected_sheets": sorted(affected_sheets),
            "resolved_sheet": sheet_name,
            "content_version": snap.content_version,
            "scope": scope,
            "coverage": {"kind": "partial", "scope": "direct_dependents", "sheet_scope": scope, "reason": "仅直接依赖；静态解析，动态/外部引用不保证完整"},
        }
        return ok_result(
            payload,
            model_text=(
                f"{target}: {len(direct)} cells across {len(affected_sheets)} sheets"
            ),
        )
    except InvalidRefError as e:
        return _error_json(str(e), code="RANGE_INVALID")
    except SnapshotError as e:
        return error_result(str(e), code=e.code, fields=e.fields or None)
    except Exception as e:
        return _error_json(f"影响分析失败: {e}")


def get_cache() -> RefCache:
    """返回当前会话的缓存实例（供集成使用）。"""
    return get_session_cache()
