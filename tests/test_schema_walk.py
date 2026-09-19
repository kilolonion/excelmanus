"""本地 JSON Schema 行走：WorkbookSpec $ref / 数组 / additionalProperties。"""

from __future__ import annotations

from excelmanus.replica_spec import workbook_spec_json_schema
from excelmanus.tools.intent_tools import get_tools
from excelmanus.tools.schema_walk import walk_schema_path


def _edit_schema() -> dict:
    tool = next(item for item in get_tools() if item.name == "edit_spreadsheet")
    schema = tool.input_schema
    assert isinstance(schema, dict)
    return schema


def test_workbook_spec_schema_has_local_defs() -> None:
    spec = workbook_spec_json_schema()
    assert "properties" in spec
    schema = _edit_schema()
    assert isinstance(schema.get("$defs"), dict)
    node, available, err = walk_schema_path(schema, "workbook_spec")
    assert node is not None, err
    assert "sheets" in available
    assert "uncertainties" in available


def test_walk_workbook_spec_nested_fields() -> None:
    schema = _edit_schema()
    paths = (
        "workbook_spec.sheets",
        "workbook_spec.sheets.value_blocks",
        "workbook_spec.sheets.styles",
        "workbook_spec.sheets.styles.border",
        "workbook_spec.sheets.conditional_formats",
        "workbook_spec.uncertainties",
    )
    for path in paths:
        node, available, err = walk_schema_path(schema, path)
        assert node is not None, f"{path}: {err} available={available}"
        assert isinstance(node, dict)


def test_missing_field_lists_queryable() -> None:
    schema = _edit_schema()
    node, available, err = walk_schema_path(schema, "workbook_spec.styles")
    assert node is None
    assert "sheets" in available
    assert err


def test_join_and_format_rule_are_objects() -> None:
    tools = {item.name: item for item in get_tools()}
    analyze = tools["analyze_spreadsheet"].input_schema
    join, _, err = walk_schema_path(analyze, "join")
    assert join is not None, err
    assert "on" in (join.get("properties") or {})
    fmt = tools["format_spreadsheet"].input_schema
    rule, available, err = walk_schema_path(fmt, "operations.rule")
    assert rule is not None, err
    assert "type" in (rule.get("properties") or {}) or "type" in available
