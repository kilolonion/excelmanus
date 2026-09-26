"""Regression tests for workbook field contracts and style aliases."""

from __future__ import annotations

from excelmanus.workbook.contracts import validate_operations
from excelmanus.workbook.styles import _build_alignment, _build_fill, _patch_font
from excelmanus.tools.workbook_query_schemas import query_schema_error_message
import jsonschema
from excelmanus.tools.workbook_tools import get_tools
from excelmanus.workbook.spec import WorkbookSpec
from excelmanus.workbook.service import _fold_query_aliases
from excelmanus.tools.workbook_query_schemas import QUERY_SCHEMAS


def test_operation_style_schema_exposes_runtime_aliases() -> None:
    operations = validate_operations(
        [
            {
                "kind": "format",
                "sheet": "Sheet1",
                "range": "A1",
                "font": {"strikethrough": True},
                "alignment": {"wrapText": True, "textRotation": 15},
            }
        ]
    )
    assert operations[0]["font"]["strikethrough"] is True


def test_style_alias_conflicts_are_rejected() -> None:
    try:
        _build_fill({"type": "solid", "fill_type": "none"})
    except ValueError as exc:
        assert "别名冲突" in str(exc)
    else:  # pragma: no cover - assertion branch
        raise AssertionError("conflicting fill aliases must fail")

    try:
        _patch_font(None, {"strike": True, "strikethrough": False})
    except ValueError as exc:
        assert "别名冲突" in str(exc)
    else:  # pragma: no cover - assertion branch
        raise AssertionError("conflicting font aliases must fail")


def test_alignment_aliases_are_normalized() -> None:
    alignment = _build_alignment({"horizontalAlignment": "center", "wrapText": True})
    assert alignment.horizontal == "center"
    assert alignment.wrap_text is True


def test_contract_errors_suggest_nearby_canonical_fields() -> None:
    try:
        validate_operations([{"kind": "write", "sheet": "Sheet1", "startCell": "A1", "values": [[1]]}])
    except ValueError as exc:
        assert "startCell" in str(exc)
        assert "start_cell" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("stale operation field must fail with a suggestion")

    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {"left_on": {"type": "string"}},
    }
    try:
        jsonschema.Draft202012Validator(schema).validate({"leftOn": "id"})
    except jsonschema.ValidationError as exc:
        message = query_schema_error_message(exc)
        assert "leftOn→left_on" in message
    else:  # pragma: no cover
        raise AssertionError("unknown query field must fail")


def test_workbook_spec_wire_schema_matches_alias_validators() -> None:
    tool = next(item for item in get_tools() if item.name == "apply_spreadsheet_changes")
    payload = {
        "file_path": "new.xlsx",
        "workbook_spec": {
            "sheets": [{
                "name": "Sheet1",
                "dimensions": {"rows": 2, "cols": 2},
                "styles": {
                    "receipt": {
                        "font": {"strikethrough": True},
                        "fill": {"patternType": "solid", "fgColor": "FFFF00"},
                        "alignment": {"wrapText": True},
                    }
                },
            }],
            "uncertainties": [],
        },
    }
    errors = list(jsonschema.Draft202012Validator(tool.input_schema).iter_errors(payload))
    assert not errors, errors
    spec = WorkbookSpec.model_validate(payload["workbook_spec"])
    style = spec.sheets[0].styles["receipt"]
    assert style.font and style.font.strike is True
    assert style.fill and style.fill.type == "solid" and style.fill.color == "FFFF00"
    assert style.alignment and style.alignment.wrap_text is True


def test_query_aliases_are_declared_and_fold_with_conflict_detection() -> None:
    schema = QUERY_SCHEMAS["analyze_spreadsheet"]
    errors = list(jsonschema.Draft202012Validator(schema).iter_errors({
        "path": "book.xlsx", "mode": "aggregate", "groupBy": ["区域"],
        "aggs": {"金额": "avg"},
        "join": {"path": "other.xlsx", "leftOn": "id", "rightOn": "id"},
    }))
    assert not errors, errors
    folded = _fold_query_aliases(
        {"path": "book.xlsx", "groupBy": ["区域"], "aggs": {"金额": "avg"}},
        {"path": "file_path", "groupBy": "group_by", "aggs": "aggregations"},
    )
    assert folded == {"file_path": "book.xlsx", "group_by": ["区域"], "aggregations": {"金额": "avg"}}
    try:
        _fold_query_aliases({"path": "a.xlsx", "file_path": "b.xlsx"}, {"path": "file_path"})
    except ValueError as exc:
        assert "别名冲突" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("conflicting query aliases must fail")
