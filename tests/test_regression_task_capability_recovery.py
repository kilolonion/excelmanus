"""2026-09-25 regression logs: (1) seq39/77 and (3) seq99/100.

File presence is not authorization. Navigation must not turn a descriptive
mention or a malformed field query into an unrelated positive capability.
"""
from __future__ import annotations

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from excelmanus.tools import introspection_tools as intro
from excelmanus.tools.catalog import (
    catalog_from_engine, execution_catalog_from_engine, gated_tool_reason,
)
from excelmanus.tools.context import (
    CallerCapability, bind_call, bind_workspace, current_call, reset_call,
)
from excelmanus.tools.registry import ToolDef, ToolRegistry

WRITER = "apply_spreadsheet_changes"


@pytest.fixture
def csv_engine(tmp_path, monkeypatch):
    (tmp_path / "uploads").mkdir()
    (tmp_path / "uploads" / "sales.csv").write_text("spend,sales\n1,2\n2,4\n", encoding="utf-8")
    registry = ToolRegistry()
    registry.register_builtin_tools(str(tmp_path))
    engine = SimpleNamespace(
        _registry=registry, registry=registry, _current_chat_mode="write",
        _fixed_capability=CallerCapability(), _skill_router=None,
        _skill_resolver=None, _subagent_config=None, _active_skills=[],
        _tools_cache=None, config=SimpleNamespace(workspace_root=str(tmp_path)),
    )
    monkeypatch.setattr(intro, "_registry", registry)
    intro.register_introspection_tools(registry, engine=engine)
    catalog_from_engine(engine)
    return engine


def query(engine, query_type, text):
    result = engine.registry.call_tool(
        "introspect_capability", {"query_type": query_type, "query": text},
    )
    assert result.success, result.model_text
    return result.model_text


def next_call(text):
    lines = [line for line in text.splitlines() if line.startswith("next_call: ")]
    assert len(lines) == 1, text
    call = json.loads(lines[0].removeprefix("next_call: "))
    assert call["tool"] == "introspect_capability"
    return call


def follow(engine, text):
    call = next_call(text)
    result = engine.registry.call_tool(call["tool"], call["arguments"])
    assert result.success, result.model_text
    assert "字段不存在" not in result.model_text
    assert "工具不可用" not in result.model_text
    return result.model_text


def test_csv_creator_is_visible_in_all_authoritative_catalogs(csv_engine):
    projected = catalog_from_engine(csv_engine)
    executed = execution_catalog_from_engine(csv_engine)
    bound = csv_engine.registry.effective_catalog()
    assert projected is not None and executed is not None
    assert csv_engine._catalog_profile == "csv"
    assert csv_engine._catalog_new_workbook is True
    for catalog in (projected, executed, bound):
        assert WRITER in catalog.name_set()
        assert WRITER in catalog.introspection_source()
        assert catalog.gated_reason(WRITER) == ""
    assert projected.digest() == executed.digest() == bound.digest()
    assert "workbook_spec" in query(csv_engine, "tool_detail", WRITER)
    assert gated_tool_reason(csv_engine, WRITER, catalog=projected) == ""
    for category in ("edit", "objects", "format"):
        text = query(csv_engine, "category_tools", category)
        assert WRITER in text and "无可用工具" not in text


def test_csv_creator_can_really_create_first_workbook(csv_engine, tmp_path):
    token = bind_workspace(tmp_path)
    try:
        result = csv_engine.registry.call_tool(WRITER, {
            "file_path": "outputs/regression.xlsx",
            "workbook_spec": {
                "sheets": [{"name": "Report", "dimensions": {"rows": 2, "cols": 2},
                            "value_blocks": [{"start": "A1", "values": [["slope", 2]]}]}],
                "uncertainties": [],
            },
        }, tool_scope=execution_catalog_from_engine(csv_engine).names())
        assert result.success, result.model_text
        assert (tmp_path / "outputs" / "regression.xlsx").is_file()
    finally:
        reset_call(token)


@pytest.mark.parametrize("cap", [
    CallerCapability(catalog_mode="read"),
    CallerCapability(catalog_mode="plan"),
    CallerCapability(tool_access="read_only"),
    CallerCapability(disallowed_tools=frozenset({WRITER})),
    CallerCapability(allowed_tools=frozenset({"introspect_capability"})),
])
def test_no_file_gate_change_can_expand_real_authorization(csv_engine, cap):
    csv_engine._fixed_capability = cap
    bound = catalog_from_engine(csv_engine)
    executed = execution_catalog_from_engine(csv_engine)
    for catalog in (bound, executed, csv_engine.registry.effective_catalog()):
        assert WRITER not in catalog.name_set()
    text = query(csv_engine, "tool_detail", WRITER + " operations chart")
    assert "被门控" in text and "工具不存在于当前目录" not in text
    assert '"properties"' not in text
    assert "next_call:" not in text
    assert WRITER not in csv_engine.registry.effective_catalog().name_set()


@pytest.mark.parametrize("text", [WRITER, "create chart", "写入单元格 修改工作簿 创建图表"])
def test_existing_routes_return_only_relevant_available_tool(csv_engine, text):
    result = query(csv_engine, "can_i_do", text)
    assert "available" in result and WRITER in result
    assert "write_text_file" not in result
    assert "task_create" not in result
    assert "validate_spreadsheet" not in result


def test_unregistered_exact_tool_name_never_matches_mention(monkeypatch):
    registry = ToolRegistry()
    registry.register_tool(ToolDef(
        name="write_text_file", description="不适用：直接写入 Excel 数据（改用 SDK：apply_spreadsheet_changes）。",
        input_schema={"type": "object", "properties": {}}, func=lambda: None,
    ))
    monkeypatch.setattr(intro, "_registry", registry)
    intro.register_introspection_tools(registry)
    engine = SimpleNamespace(registry=registry)
    result = query(engine, "can_i_do", WRITER)
    assert "unknown" in result and "不表示能力不存在" in result
    assert "available" not in result and "write_text_file" not in result


@pytest.mark.parametrize("path", ["operations chart", "operations image", "operations chart title",
                                      "$defs", "$defs StyleClass", "output status"])
def test_whitespace_field_queries_normalize_and_next_call_resolves(csv_engine, path):
    text = query(csv_engine, "tool_detail", WRITER + " " + path)
    canonical = WRITER + "." + path.replace(" ", ".")
    assert "规范化查询: " + canonical in text
    assert next_call(text)["arguments"]["query"] == canonical
    assert "工具不可用" not in text
    follow(csv_engine, text)


def test_bare_defs_query_lists_named_schema_types(csv_engine):
    text = query(csv_engine, "tool_detail", WRITER + ".$defs")
    assert "字段不存在" not in text
    assert "StyleClass" in text
    assert "当前节点可查" in text


def test_category_field_misroute_provides_exact_readonly_next_call(csv_engine):
    text = query(csv_engine, "category_tools", WRITER + " operations chart fields")
    assert "分类不存在" in text
    assert next_call(text)["arguments"] == {
        "query_type": "tool_detail", "query": WRITER + ".operations.chart",
    }
    assert "chart_type" in follow(csv_engine, text)


def test_free_keyword_category_provides_can_i_do_next_call(csv_engine):
    text = query(csv_engine, "category_tools", "workbook write create chart")
    assert next_call(text)["arguments"]["query_type"] == "can_i_do"
    assert WRITER in follow(csv_engine, text)


@pytest.mark.parametrize("path,ancestor", [
    ("operations.chart.missing", "operations.chart"),
    ("operations.charrt", "operations.kind"),
    ("workbook_spec.styles", "workbook_spec"),
    ("output.no_such_field", "output"),
    ("missing", ""),
])
def test_unknown_fields_return_existing_ancestor_without_guessing(csv_engine, path, ancestor):
    text = query(csv_engine, "tool_detail", WRITER + "." + path)
    assert "字段" in text
    assert next_call(text)["arguments"]["query"] == WRITER + ("." + ancestor if ancestor else "")
    follow(csv_engine, text)


def test_normalization_only_loads_successful_authorized_details(csv_engine, tmp_path):
    token = bind_workspace(tmp_path)
    loaded = set()
    scoped = bind_call(replace(current_call(), loaded_tool_names=loaded))
    try:
        query(csv_engine, "category_tools", WRITER + " operations chart fields")
        query(csv_engine, "tool_detail", WRITER + " operations chart missing")
        assert loaded == set()
        query(csv_engine, "tool_detail", WRITER + " operations chart")
        assert loaded == {WRITER}
        loaded.clear()
        csv_engine._fixed_capability = CallerCapability(disallowed_tools=frozenset({WRITER}))
        catalog_from_engine(csv_engine)
        text = query(csv_engine, "tool_detail", WRITER + " operations image")
        assert "disallowed_tools" in text and "csv-only" not in text
        assert loaded == set()
    finally:
        reset_call(scoped)
        reset_call(token)


def test_dotted_plugin_names_and_real_fields_are_not_reinterpreted(csv_engine):
    csv_engine.registry.register_tool(ToolDef(
        name="plugin.chart", description="plugin", write_effect="none",
        input_schema={"type": "object", "properties": {"config": {
            "type": "object", "properties": {"fields": {"type": "integer"}},
        }}}, func=lambda **_: None,
    ))
    catalog_from_engine(csv_engine)
    text = query(csv_engine, "tool_detail", "plugin.chart config fields")
    assert next_call(text)["arguments"]["query"] == "plugin.chart.config.fields"
    assert '"integer"' in follow(csv_engine, text)
