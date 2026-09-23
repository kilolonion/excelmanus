"""Deferred schemas must not advertise object for arrays, strings or nulls."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from excelmanus.config import ExcelManusConfig
from excelmanus.engine import AgentEngine
from excelmanus.engine_core.meta_tools import MetaToolBuilder
from excelmanus.tools.catalog import _prune_parameters_schema, derive_effective_catalog
from excelmanus.tools.registry import ToolDef, ToolRegistry


def _project(field: dict, definitions: dict | None = None) -> tuple[dict, dict]:
    schema = {"type": "object", "properties": {"value": field}, "required": ["value"],
              "$defs": definitions or {"Row": {"type": "object"}}}
    before = copy.deepcopy(schema)
    output = _prune_parameters_schema(schema, schema_name="shape_probe")
    assert schema == before
    assert output["required"] == ["value"]
    assert "$defs" not in output
    assert "$ref" not in json.dumps(output["properties"])
    Draft202012Validator.check_schema(output)
    return schema, output


@pytest.mark.parametrize("field,values,expected", [
    ({"type": "array", "items": {"$ref": "#/$defs/Row"}}, [[{}, {}], []], "array"),
    ({"type": ["array", "string"], "items": {"$ref": "#/$defs/Row"}}, [[{}], "[]"], ["array", "string"]),
    ({"type": ["object", "string"], "properties": {"row": {"$ref": "#/$defs/Row"}}},
     [{"row": {}}, "{}"], ["object", "string"]),
    ({"anyOf": [{"$ref": "#/$defs/Row"}, {"type": "null"}], "default": None},
     [{}, None], ["object", "null"]),
])
def test_declared_outer_types_continue_to_accept_valid_input(field, values, expected) -> None:
    original, projected = _project(field)
    shape = projected["properties"]["value"]
    assert shape["type"] == expected
    if "default" in field:
        assert shape["default"] is None
    for value in values:
        Draft202012Validator(original).validate({"value": value})
        Draft202012Validator(projected).validate({"value": value})


def test_local_reference_chain_keeps_primitive_null_union() -> None:
    original, projected = _project({"$ref": "#/$defs/Alias"}, {
        "Alias": {"$ref": "#/$defs/NullableCount"},
        "NullableCount": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
    })
    assert projected["properties"]["value"]["type"] == ["integer", "null"]
    for value in (0, 7, None):
        Draft202012Validator(original).validate({"value": value})
        Draft202012Validator(projected).validate({"value": value})


def test_local_reference_uses_json_pointer_escaping() -> None:
    _, projected = _project({"$ref": "#/$defs/array~1of~0rows"}, {
        "array/of~rows": {"type": "array", "items": {"type": "string"}},
    })
    assert projected["properties"]["value"]["type"] == "array"
    assert projected["properties"]["value"]["items"] == {}


def test_oneof_object_variants_do_not_become_exclusive_broad_objects() -> None:
    original, projected = _project({"oneOf": [{"$ref": "#/$defs/Left"}, {"$ref": "#/$defs/Right"}]}, {
        "Left": {"type": "object", "properties": {"left": {"type": "number"}}, "required": ["left"]},
        "Right": {"type": "object", "properties": {"right": {"type": "number"}}, "required": ["right"]},
    })
    assert projected["properties"]["value"]["type"] == "object"
    for value in ({"left": 1}, {"right": 2}):
        Draft202012Validator(original).validate({"value": value})
        Draft202012Validator(projected).validate({"value": value})


@pytest.mark.parametrize("field,definitions", [
    ({"$ref": "https://invalid.example/schema.json"}, {}),
    ({"$ref": "#/$defs/Missing"}, {}),
    ({"$ref": "#/$defs/Cycle"}, {"Cycle": {"$ref": "#/$defs/Cycle"}}),
    ({"$ref": "#/$defs/A"}, {"A": {"$ref": "#/$defs/B"}, "B": {"$ref": "#/$defs/A"}}),
    ({"anyOf": [{"type": "null"}, {"$ref": "#/$defs/Missing"}]}, {}),
])
def test_unknown_shapes_remain_untyped_and_never_fetch(field, definitions, monkeypatch) -> None:
    import socket
    import urllib.request

    def no_network(*args, **kwargs):
        raise AssertionError("Schema disclosure must not fetch references")

    monkeypatch.setattr(socket, "create_connection", no_network)
    monkeypatch.setattr(urllib.request, "urlopen", no_network)
    _, projected = _project(field, definitions)
    shape = projected["properties"]["value"]
    assert "type" not in shape
    assert "tool_detail" in shape["description"]


def test_recursive_object_preserves_known_type_without_expanding_children() -> None:
    _, projected = _project({"$ref": "#/$defs/Node"}, {
        "Node": {"type": "object", "properties": {"child": {"$ref": "#/$defs/Node"}}},
    })
    shape = projected["properties"]["value"]
    assert shape["type"] == "object"
    assert "properties" not in shape


def test_allof_retains_a_known_outer_constraint_with_an_unknown_branch() -> None:
    _, projected = _project({"allOf": [
        {"$ref": "#/$defs/Missing"}, {"type": "array", "items": {"type": "string"}},
    ]})
    assert projected["properties"]["value"]["type"] == "array"


def test_long_reference_chain_is_bounded_without_fabricating_type() -> None:
    definitions = {f"Type{i}": {"$ref": f"#/$defs/Type{i + 1}"} for i in range(100)}
    definitions["Type100"] = {"type": "string"}
    _, projected = _project({"$ref": "#/$defs/Type0"}, definitions)
    assert "type" not in projected["properties"]["value"]


def test_fanout_references_do_not_expand_exponentially() -> None:
    definitions = {f"Type{i}": {"anyOf": [
        {"$ref": f"#/$defs/Type{i + 1}"} for _ in range(32)
    ]} for i in range(20)}
    _, projected = _project({"$ref": "#/$defs/Type0"}, definitions)
    assert "type" not in projected["properties"]["value"]


def test_deferred_large_definition_keeps_examples_without_copying_tree() -> None:
    example = {"row": {"field0": "sample"}}
    _, projected = _project({
        "type": ["object", "string"], "properties": {"row": {"$ref": "#/$defs/HugeRow"}},
        "examples": [example],
    }, {"HugeRow": {"type": "object", "properties": {
        f"field{i}": {"type": "string", "description": "long schema detail " * 100} for i in range(200)
    }}})
    shape = projected["properties"]["value"]
    assert shape["examples"] == [example]
    assert len(json.dumps(projected)) < 1600
    assert "x-excelmanus-schema-ref" in projected


@pytest.mark.parametrize("schema_mode", ["chat_completions", "responses"])
def test_catalog_wire_projection_preserves_array_and_nullable_types(schema_mode: str) -> None:
    input_schema = {"type": "object", "$defs": {"Row": {"type": "object"}}, "properties": {
        "rows": {"type": "array", "items": {"$ref": "#/$defs/Row"}},
        "optional_row": {"anyOf": [{"$ref": "#/$defs/Row"}, {"type": "null"}]},
    }, "required": ["rows"]}
    before = copy.deepcopy(input_schema)
    tool = ToolDef(name="shape_probe", description="Shapes", input_schema=input_schema,
                   func=lambda **kwargs: kwargs, write_effect="none")
    catalog = derive_effective_catalog(tools=[tool], mode="write")
    wire = catalog.tool_schemas(schema_mode=schema_mode)[0]
    parameters = (wire.get("function") or wire)["parameters"]
    assert parameters["properties"]["rows"]["type"] == "array"
    assert parameters["properties"]["optional_row"]["type"] == ["object", "null"]
    assert parameters["required"] == ["rows"]
    assert tool.input_schema is input_schema and input_schema == before


def test_real_first_disclosure_keeps_workbook_object_or_json_string(tmp_path: Path) -> None:
    from excelmanus.replica_spec import validate_workbook_spec

    registry = ToolRegistry()
    registry.register_builtin_tools(str(tmp_path))
    engine = AgentEngine(ExcelManusConfig(
        api_key="test", base_url="https://test.invalid/v1", model="test",
        workspace_root=str(tmp_path), jev_enabled="off",
    ), registry)
    tool = registry.get_tool("edit_spreadsheet")
    original = tool.input_schema
    before = copy.deepcopy(original)
    wire = next(row["function"] for row in MetaToolBuilder(engine).build_v5_tools()
                if row["function"]["name"] == "edit_spreadsheet")
    parameters = wire["parameters"]
    spec = parameters["properties"]["workbook_spec"]
    assert spec["type"] == ["object", "string"]
    assert spec["examples"] == original["properties"]["workbook_spec"]["examples"]
    assert "$defs" not in parameters
    for value in (spec["examples"][0], json.dumps(spec["examples"][0])):
        arguments = {"file_path": "receipt.xlsx", "workbook_spec": value}
        # The host also normalizes documented shorthand such as merged range
        # strings before validating WorkbookSpec's nested Pydantic model.
        assert validate_workbook_spec(value).sheets
        Draft202012Validator({"type": original["properties"]["workbook_spec"]["type"]}).validate(value)
        Draft202012Validator(parameters).validate(arguments)
    assert tool.input_schema is original and original == before
