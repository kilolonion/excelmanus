"""Lossless model-only spreadsheet projections, including the native wire path."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from openpyxl import Workbook

from excelmanus.engine_core.spill import (
    SpillStore,
    expose_spreadsheet_value,
    retrieve_spill_result,
    should_spill,
)
from excelmanus.engine_core.tool_result import ToolResult, ToolUiMeta, result_value


def _range_payload(values, formulas=None, *, cell_range="A1:B2"):
    payload = {
        "status": "success",
        "range": cell_range,
        "resolved_sheet": "Sheet1",
        "values": values,
        "data": copy.deepcopy(values),
        "content_version": "sha256:test",
        "coverage": {"kind": "complete"},
    }
    if formulas is not None:
        payload.update(formulas=formulas, formula_grid=copy.deepcopy(formulas))
    return payload


def test_projection_keeps_sdk_ui_coverage_and_all_facts(tmp_path: Path) -> None:
    values = [["客户", "金额"], ["甲", 12]]
    formulas = [[None, None], [None, "=3*4"]]
    payload = _range_payload(values, formulas)
    before = copy.deepcopy(payload)
    ui = ToolUiMeta(preview={"rows": values}, content_version=payload["content_version"])
    result = ToolResult(success=True, value=payload, model_text="summary", ui_meta=ui,
                        coverage={"kind": "complete"})

    output = expose_spreadsheet_value(result, store=SpillStore(tmp_path))
    model = json.loads(output.model_text)

    assert model["values"] == values
    assert model["formulas"] == formulas
    assert "data" not in model and "formula_grid" not in model
    assert model["model_field_aliases"] == {"data": "values", "formula_grid": "formulas"}
    assert model["content_version"] == payload["content_version"]
    assert output.value is result.value and result_value(output) is result.value
    assert result.value == before
    assert output.ui_meta is ui
    assert output.coverage is result.coverage
    assert not output.truncated
    assert result.model_text == "summary"


@pytest.mark.parametrize("values,data", [
    ([[1]], [[True]]),
    ([[1]], [[1.0]]),
    ([["1"]], [[1]]),
    ([[None]], [[]]),
    ([[1]], [[2]]),
])
def test_unequal_or_differently_typed_grids_are_preserved(tmp_path: Path, values, data) -> None:
    payload = {"values": values, "data": data}
    output = expose_spreadsheet_value(ToolResult(success=True, value=payload, model_text=""),
                                     store=SpillStore(tmp_path))
    assert json.loads(output.model_text) == payload


def test_cell_records_and_metadata_are_not_rewritten(tmp_path: Path) -> None:
    record = {"data": [1], "values": [1], "formulas": ["x"], "formula_grid": ["x"]}
    payload = _range_payload([[record]])
    payload["meta"] = copy.deepcopy(record)
    payload["warnings"] = ["公式缓存缺失，不代表数值为零"]
    output = expose_spreadsheet_value(ToolResult(success=True, value=payload, model_text=""),
                                     store=SpillStore(tmp_path))
    model = json.loads(output.model_text)
    assert model["values"][0][0] == record
    assert model["meta"] == record
    assert model["warnings"] == payload["warnings"]


def test_union_keeps_each_coordinate_and_grid_once(tmp_path: Path) -> None:
    areas = [
        _range_payload([[1], [2]], [[None], ["=1+1"]], cell_range="A1:A2"),
        _range_payload([[3]], [[None]], cell_range="C7"),
    ]
    payload = _range_payload([a["values"] for a in areas], [a["formulas"] for a in areas],
                             cell_range="A1:A2,C7")
    payload["areas"] = areas
    before = copy.deepcopy(payload)
    output = expose_spreadsheet_value(ToolResult(success=True, value=payload, model_text=""),
                                     store=SpillStore(tmp_path))
    model = json.loads(output.model_text)

    assert not {"data", "values", "formulas", "formula_grid"}.intersection(model)
    assert model["model_field_aliases"] == {
        "data": "areas[*].values", "values": "areas[*].values",
        "formula_grid": "areas[*].formulas", "formulas": "areas[*].formulas",
    }
    for projected, original in zip(model["areas"], areas):
        assert projected["range"] == original["range"]
        assert projected["values"] == original["values"]
        assert projected["formulas"] == original["formulas"]
        assert "data" not in projected and "formula_grid" not in projected
    assert output.value == before


def test_union_different_aggregate_is_not_discarded(tmp_path: Path) -> None:
    payload = _range_payload([[[2]]])
    payload["areas"] = [_range_payload([[1]], cell_range="A1")]
    output = expose_spreadsheet_value(ToolResult(success=True, value=payload, model_text=""),
                                     store=SpillStore(tmp_path))
    assert json.loads(output.model_text)["values"] == [[[2]]]


def test_deduplication_avoids_unnecessary_spill_and_retrieval(tmp_path: Path) -> None:
    payload = _range_payload([["x" * 4500]], [[None]])
    original_text = json.dumps(payload, ensure_ascii=False)
    assert should_spill(original_text)
    store = SpillStore(tmp_path)
    output = expose_spreadsheet_value(ToolResult(success=True, value=payload, model_text="summary"),
                                     store=store)
    model = json.loads(output.model_text)
    assert not should_spill(output.model_text)
    assert "result_spill" not in model
    assert model["values"] == payload["values"]
    assert len(output.model_text) < len(original_text) * 0.55
    assert not list(store.directory.glob("*"))


def test_large_projection_retrieves_original_aliases_without_changing_sdk(tmp_path: Path) -> None:
    payload = _range_payload([["x" * 10_000]], [[None]])
    result = ToolResult(success=True, value=payload, model_text="summary")
    store = SpillStore(tmp_path)
    output = expose_spreadsheet_value(result, store=store)
    model = json.loads(output.model_text)
    assert output.value is payload
    assert "result_spill" not in payload
    assert json.loads(store.get(model["result_spill"])) == payload
    retrieved = retrieve_spill_result(model["result_spill"], workspace_root=tmp_path)
    assert json.loads(retrieved.model_text) == payload
    assert expose_spreadsheet_value(retrieved, store=store) is retrieved


def test_pre_shaping_projection_is_lossless_without_spilling(tmp_path: Path) -> None:
    payload = _range_payload([["x" * 10_000]])
    store = SpillStore(tmp_path)
    output = expose_spreadsheet_value(ToolResult(success=True, value=payload, model_text=""),
                                     store=store, project_large=False)
    model = json.loads(output.model_text)
    assert model["values"] == payload["values"]
    assert "data" not in model and "result_spill" not in model
    assert not list(store.directory.glob("*"))


@pytest.mark.asyncio
async def test_native_dispatcher_sends_projected_result_with_original_sdk_value(tmp_path: Path) -> None:
    from excelmanus.config import ExcelManusConfig
    from excelmanus.engine import AgentEngine
    from excelmanus.tools.registry import ToolRegistry

    wb = Workbook()
    wb.active.append(["客户", "金额"])
    wb.active.append(["甲", "=3*4"])
    wb.save(tmp_path / "receipt.xlsx")
    wb.close()
    registry = ToolRegistry()
    registry.register_builtin_tools(str(tmp_path))
    engine = AgentEngine(ExcelManusConfig(
        api_key="test", base_url="https://test.invalid/v1", model="test",
        workspace_root=str(tmp_path), jev_enabled="off",
    ), registry)
    call = SimpleNamespace(id="read", function=SimpleNamespace(
        name="observe_spreadsheet", arguments=json.dumps({
            "file_path": "receipt.xlsx", "mode": "range", "range": "A1:B2", "facets": ["data"],
        }),
    ))
    output = await engine._tool_runtime.execute(call, None, None, 1)
    assert output.success, output.result
    model = json.loads(output.result)
    from tests.workbook_support import region_matrix
    assert region_matrix(model["regions"][0]) == [["客户","金额"],["甲",None]]
    assert model["regions"][0]["cells"]["2,2"]["f"] == "=3*4"
    assert model["regions"] == output.structured.value["regions"]
    assert output.structured.ui_meta.files == ["receipt.xlsx"]
    assert output.structured.ui_meta.content_version == model["content_version"]
