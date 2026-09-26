"""Regressions for retrospective follow-ups 2 and 5."""
import copy
import json

from excelmanus.engine_core.spill import (
    SpillStore, _decision_preview, expose_spreadsheet_value, retrieve_spill,
)
from excelmanus.engine_core.tool_result import from_payload


def test_spilled_csv_preserves_inferred_type_without_coercing_values(tmp_path):
    cells = {
        "1,1": {"t": "s", "v": "投入", "inferred_type": "text"},
        "2,1": {"t": "s", "v": "007", "inferred_type": "number"},
        "3,1": {"t": "s", "v": "2026-09-25", "inferred_type": "date"},
    }
    cells.update({f"{r},1": {"t": "s", "v": "x" * 300, "inferred_type": "text"}
                  for r in range(4, 100)})
    payload = {"status": "success", "file_path": "data.csv",
               "regions": [{"sheet": "Sheet1", "cells": cells}],
               "coverage": {"facets": {"data": {"columns": [
                   {"column": "A", "inferred_type": "mixed"}]}}}}
    original = copy.deepcopy(payload)
    store = SpillStore(tmp_path)
    result = expose_spreadsheet_value(from_payload(payload), store=store)
    envelope = json.loads(result.model_text)
    preview = envelope["data_preview"]
    shown = preview["regions"][0]["cells"]
    assert shown["2,1"] == cells["2,1"]  # 007 remains text, not 7.
    assert shown["1,1"]["inferred_type"] == "text"
    assert shown["3,1"]["inferred_type"] == "date"
    assert preview["complete"] is False
    assert envelope["coverage"] == original["coverage"]
    assert result.value == original == payload
    # Store retrieval is the full original, never the sampled decision view.
    assert json.loads(store.get(envelope["result_spill"])) == original


def test_decision_preview_does_not_invent_inference_for_xlsx():
    cell = {"t": "n", "v": 2, "f": "=1+1", "cached": "yes"}
    result = _decision_preview({"regions": [{"cells": {"1,1": cell}}]})
    assert result["regions"][0]["cells"]["1,1"] == cell
    assert result["complete"] is True


def test_validate_description_has_one_runtime_source(monkeypatch):
    from excelmanus.prompt.canonical import TOOL_DESCRIPTIONS
    from excelmanus.tools.spreadsheet_data_tools import get_tools

    tool = next(t for t in get_tools() if t.name == "validate_spreadsheet")
    assert tool.description == TOOL_DESCRIPTIONS[tool.name]
    for fact in ("cell", "A1", "整列求和", "allow_uncached", "case_sensitive"):
        assert fact in tool.description
    monkeypatch.setitem(TOOL_DESCRIPTIONS, tool.name, "single-source sentinel")
    rebuilt = next(t for t in get_tools() if t.name == tool.name)
    assert rebuilt.description == "single-source sentinel"
