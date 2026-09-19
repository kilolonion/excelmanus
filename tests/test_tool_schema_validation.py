"""工具参数 schema 三态校验器单元测试。

覆盖：
- off/shadow/enforce 三态行为
- 类型/枚举/范围/必填/additionalProperties 校验
- oneOf 分支匹配
- 路径严格策略（strict_path）
- canary 灰度比例
- fork 继承校验配置
- 错误 payload 格式稳定性
"""

from __future__ import annotations

import pytest

from excelmanus.tools.registry import ToolDef, ToolRegistry


def _make_registry(
    *,
    mode: str = "off",
    canary_percent: int = 100,
    strict_path: bool = False,
) -> ToolRegistry:
    registry = ToolRegistry()
    registry.configure_schema_validation(
        mode=mode,
        canary_percent=canary_percent,
        strict_path=strict_path,
    )
    return registry


def _tool_with_schema(name: str = "test_tool", schema: dict | None = None) -> ToolDef:
    return ToolDef(
        name=name,
        description="测试工具",
        input_schema=schema or {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "文件路径"},
                "count": {"type": "integer", "minimum": 1, "maximum": 100},
                "mode": {"type": "string", "enum": ["read", "write"]},
            },
            "required": ["file_path"],
            "additionalProperties": False,
        },
        func=lambda **kw: "ok",
    )


class TestSchemaValidationOff:
    """mode=off 时不做任何校验。"""

    def test_off_mode_skips_validation(self) -> None:
        registry = _make_registry(mode="off")
        result = registry.validate_arguments_by_schema(
            tool_name="t",
            arguments={"bad_field": 123},
            schema={"type": "object", "properties": {}, "additionalProperties": False},
        )
        assert result is None

    def test_off_mode_no_error_on_missing_required(self) -> None:
        registry = _make_registry(mode="off")
        result = registry.validate_arguments_by_schema(
            tool_name="t",
            arguments={},
            schema={"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]},
        )
        assert result is None


class TestSchemaValidationShadow:
    """mode=shadow 时仅日志不阻断。"""

    def test_shadow_returns_none_on_violation(self) -> None:
        registry = _make_registry(mode="shadow")
        result = registry.validate_arguments_by_schema(
            tool_name="t",
            arguments={"x": 123},
            schema={
                "type": "object",
                "properties": {"x": {"type": "string"}},
                "required": ["x"],
            },
        )
        assert result is None

    def test_shadow_returns_none_on_valid(self) -> None:
        registry = _make_registry(mode="shadow")
        result = registry.validate_arguments_by_schema(
            tool_name="t",
            arguments={"x": "hello"},
            schema={
                "type": "object",
                "properties": {"x": {"type": "string"}},
                "required": ["x"],
            },
        )
        assert result is None


class TestSchemaValidationEnforce:
    """mode=enforce 时校验失败阻断并返回结构化错误。"""

    def test_enforce_passes_valid_arguments(self) -> None:
        registry = _make_registry(mode="enforce")
        result = registry.validate_arguments_by_schema(
            tool_name="t",
            arguments={"file_path": "a.xlsx", "count": 5, "mode": "read"},
            schema=_tool_with_schema().input_schema,
        )
        assert result is None

    def test_enforce_catches_missing_required(self) -> None:
        registry = _make_registry(mode="enforce")
        result = registry.validate_arguments_by_schema(
            tool_name="t",
            arguments={},
            schema=_tool_with_schema().input_schema,
        )
        assert result is not None
        payload = result.value
        assert payload["status"] == "error"
        assert payload["error_code"] == "TOOL_ARGUMENT_VALIDATION_ERROR"
        assert any("file_path" in v for v in payload["violations"])

    def test_enforce_catches_type_mismatch(self) -> None:
        registry = _make_registry(mode="enforce")
        result = registry.validate_arguments_by_schema(
            tool_name="t",
            arguments={"file_path": "a.xlsx", "count": "not_a_number"},
            schema=_tool_with_schema().input_schema,
        )
        assert result is not None
        payload = result.value
        assert any("类型不匹配" in v for v in payload["violations"])

    def test_enforce_catches_enum_violation(self) -> None:
        registry = _make_registry(mode="enforce")
        result = registry.validate_arguments_by_schema(
            tool_name="t",
            arguments={"file_path": "a.xlsx", "mode": "delete"},
            schema=_tool_with_schema().input_schema,
        )
        assert result is not None
        payload = result.value
        assert any("枚举" in v for v in payload["violations"])

    def test_enforce_catches_minimum_violation(self) -> None:
        registry = _make_registry(mode="enforce")
        result = registry.validate_arguments_by_schema(
            tool_name="t",
            arguments={"file_path": "a.xlsx", "count": 0},
            schema=_tool_with_schema().input_schema,
        )
        assert result is not None
        payload = result.value
        assert any("不能小于" in v for v in payload["violations"])

    def test_enforce_catches_maximum_violation(self) -> None:
        registry = _make_registry(mode="enforce")
        result = registry.validate_arguments_by_schema(
            tool_name="t",
            arguments={"file_path": "a.xlsx", "count": 200},
            schema=_tool_with_schema().input_schema,
        )
        assert result is not None
        payload = result.value
        assert any("不能超过" in v for v in payload["violations"])

    def test_enforce_catches_additional_properties(self) -> None:
        registry = _make_registry(mode="enforce")
        result = registry.validate_arguments_by_schema(
            tool_name="t",
            arguments={"file_path": "a.xlsx", "unknown_field": True},
            schema=_tool_with_schema().input_schema,
        )
        assert result is not None
        payload = result.value
        assert any("非法字段" in v for v in payload["violations"])

    def test_enforce_catches_array_min_items(self) -> None:
        registry = _make_registry(mode="enforce")
        schema = {
            "type": "object",
            "properties": {
                "items": {"type": "array", "items": {"type": "string"}, "minItems": 2},
            },
        }
        result = registry.validate_arguments_by_schema(
            tool_name="t",
            arguments={"items": ["one"]},
            schema=schema,
        )
        assert result is not None
        payload = result.value
        assert any("列表长度不能小于" in v for v in payload["violations"])

    def test_enforce_passes_one_of_match(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "value": {
                    "oneOf": [
                        {"type": "string"},
                        {"type": "array", "items": {"type": "string"}},
                    ],
                },
            },
        }
        registry = _make_registry(mode="enforce")
        assert registry.validate_arguments_by_schema(
            tool_name="t", arguments={"value": "hello"}, schema=schema,
        ) is None
        assert registry.validate_arguments_by_schema(
            tool_name="t", arguments={"value": ["a", "b"]}, schema=schema,
        ) is None

    def test_enforce_catches_one_of_no_match(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "value": {
                    "oneOf": [
                        {"type": "string"},
                        {"type": "integer"},
                    ],
                },
            },
        }
        registry = _make_registry(mode="enforce")
        result = registry.validate_arguments_by_schema(
            tool_name="t", arguments={"value": [1, 2]}, schema=schema,
        )
        assert result is not None
        payload = result.value
        assert any("oneOf" in v for v in payload["violations"])


class TestStrictPath:
    """strict_path 开启时对路径参数做额外校验。"""

    def test_strict_path_rejects_absolute_path(self) -> None:
        registry = _make_registry(mode="enforce", strict_path=True)
        result = registry.validate_arguments_by_schema(
            tool_name="t",
            arguments={"file_path": "/etc/passwd"},
            schema={
                "type": "object",
                "properties": {"file_path": {"type": "string"}},
            },
        )
        assert result is not None
        payload = result.value
        assert any("相对路径" in v for v in payload["violations"])

    def test_strict_path_rejects_parent_traversal(self) -> None:
        registry = _make_registry(mode="enforce", strict_path=True)
        result = registry.validate_arguments_by_schema(
            tool_name="t",
            arguments={"file_path": "../secret.xlsx"},
            schema={
                "type": "object",
                "properties": {"file_path": {"type": "string"}},
            },
        )
        assert result is not None
        payload = result.value
        assert any(".." in v for v in payload["violations"])

    def test_strict_path_allows_relative_path(self) -> None:
        registry = _make_registry(mode="enforce", strict_path=True)
        result = registry.validate_arguments_by_schema(
            tool_name="t",
            arguments={"file_path": "data/sales.xlsx"},
            schema={
                "type": "object",
                "properties": {"file_path": {"type": "string"}},
            },
        )
        assert result is None

    def test_strict_path_off_allows_absolute(self) -> None:
        registry = _make_registry(mode="enforce", strict_path=False)
        result = registry.validate_arguments_by_schema(
            tool_name="t",
            arguments={"file_path": "/etc/passwd"},
            schema={
                "type": "object",
                "properties": {"file_path": {"type": "string"}},
            },
        )
        assert result is None


class TestCanaryPercent:
    """灰度比例控制。"""

    def test_canary_0_always_shadow(self) -> None:
        registry = _make_registry(mode="enforce", canary_percent=0)
        result = registry.validate_arguments_by_schema(
            tool_name="t",
            arguments={"file_path": 123},
            schema={
                "type": "object",
                "properties": {"file_path": {"type": "string"}},
                "required": ["file_path"],
            },
        )
        assert result is None  # shadow 模式不阻断

    def test_canary_100_always_enforce(self) -> None:
        registry = _make_registry(mode="enforce", canary_percent=100)
        result = registry.validate_arguments_by_schema(
            tool_name="t",
            arguments={"file_path": 123},
            schema={
                "type": "object",
                "properties": {"file_path": {"type": "string"}},
                "required": ["file_path"],
            },
        )
        assert result is not None


class TestForkInheritsValidation:
    """fork() 继承校验配置。"""

    def test_fork_inherits_mode_and_strict_path(self) -> None:
        registry = _make_registry(mode="enforce", strict_path=True, canary_percent=50)
        registry.register_tool(_tool_with_schema())
        child = registry.fork()
        assert child._schema_validation_mode == "enforce"
        assert child._schema_strict_path is True
        assert child._schema_validation_canary_percent == 50


class TestCallToolIntegration:
    """call_tool 集成：enforce 模式下 schema 错误优先于签名绑定错误。"""

    def test_call_tool_with_enforce_blocks_bad_enum(self) -> None:
        registry = _make_registry(mode="enforce")
        registry.register_tool(_tool_with_schema())
        result = registry.call_tool(
            "test_tool",
            {"file_path": "a.xlsx", "mode": "invalid_mode"},
        )
        parsed = result.value
        assert parsed["status"] == "error"
        assert parsed["error_code"] == "TOOL_ARGUMENT_VALIDATION_ERROR"

    def test_call_tool_with_off_executes_normally(self) -> None:
        registry = _make_registry(mode="off")
        registry.register_tool(
            ToolDef(
                name="echo",
                description="echo",
                input_schema={
                    "type": "object",
                    "properties": {"msg": {"type": "string"}},
                    "required": ["msg"],
                },
                func=lambda msg: f"echo: {msg}",
            )
        )
        result = registry.call_tool("echo", {"msg": "hello"})
        assert result.model_text == "echo: hello"


class TestErrorPayloadFormat:
    """确保错误 payload 格式稳定。"""

    def test_error_payload_has_all_required_fields(self) -> None:
        registry = _make_registry(mode="enforce")
        result = registry.validate_arguments_by_schema(
            tool_name="test_tool",
            arguments={"bad": True},
            schema=_tool_with_schema().input_schema,
        )
        assert result is not None
        payload = result.value
        assert set(payload.keys()) >= {
            "status", "error_code", "tool", "message",
            "detail", "violations", "required_fields",
            "accepted_fields", "provided_fields",
        }
        assert payload["status"] == "error"
        assert payload["error_code"] == "TOOL_ARGUMENT_VALIDATION_ERROR"
        assert isinstance(payload["violations"], list)
        assert isinstance(payload["required_fields"], list)
        assert isinstance(payload["accepted_fields"], list)
        assert isinstance(payload["provided_fields"], list)


class TestConfigureValidation:
    """configure_schema_validation 参数校验。"""

    def test_invalid_mode_raises(self) -> None:
        registry = ToolRegistry()
        with pytest.raises(ValueError, match="off/shadow/enforce"):
            registry.configure_schema_validation(
                mode="invalid", canary_percent=100, strict_path=False,
            )

    def test_canary_out_of_range_raises(self) -> None:
        registry = ToolRegistry()
        with pytest.raises(ValueError, match="0..100"):
            registry.configure_schema_validation(
                mode="shadow", canary_percent=200, strict_path=False,
            )

    def test_canary_negative_raises(self) -> None:
        registry = ToolRegistry()
        with pytest.raises(ValueError, match="0..100"):
            registry.configure_schema_validation(
                mode="shadow", canary_percent=-1, strict_path=False,
            )


class TestRunCodeSandboxTierStrip:
    """sandbox_tier 对 schema 校验豁免（仅 run_code），执行参数原样透传。"""

    def _run_code_registry(self) -> ToolRegistry:
        registry = _make_registry(mode="enforce")
        registry.register_tool(
            ToolDef(
                name="run_code",
                description="run code",
                input_schema={
                    "type": "object",
                    "properties": {"code": {"type": "string"}},
                    "required": ["code"],
                    "additionalProperties": False,
                },
                func=lambda code, sandbox_tier=None: f"ran:{code}:{sandbox_tier}",
            )
        )
        return registry

    def test_sandbox_tier_ignored_by_validation_but_passed_through(self) -> None:
        registry = self._run_code_registry()
        result = registry.call_tool(
            "run_code",
            {"code": "print(1)", "sandbox_tier": "YELLOW"},
        )
        # 校验不报警，且审批重放注入的档位原样到达执行函数
        assert result.model_text == "ran:print(1):YELLOW"

    def test_other_tool_same_field_still_rejected(self) -> None:
        registry = _make_registry(mode="enforce")
        registry.register_tool(_tool_with_schema(name="other_tool"))
        result = registry.call_tool(
            "other_tool",
            {"file_path": "a.xlsx", "sandbox_tier": "host"},
        )
        assert result.value["error_code"] == "TOOL_ARGUMENT_VALIDATION_ERROR"


class TestIntentSchemaCompat:
    """intent 工具 schema 必须覆盖处理器已接受的兼容输入（_maybe_json/_op_get/别名）。

    处理器在 enforce 之前就支持 JSON 字符串、spill 句柄与别名键；
    schema 若更窄，合法调用会被 enforce 拦死，属契约回归。
    """

    def _violations(self, tool_name: str, arguments: dict) -> list[str]:
        from excelmanus.tools.intent_tools import get_tools

        tools = {t.name: t for t in get_tools()}
        registry = _make_registry(mode="enforce")
        out: list[str] = []
        registry._collect_schema_violations(
            value=arguments,
            schema=tools[tool_name].input_schema,
            path="$",
            violations=out,
        )
        return out

    # ── format_spreadsheet.rule：条件格式 ∪ 数据验证 ──

    def test_dv_list_rule_accepted(self) -> None:
        bad = self._violations("format_spreadsheet", {
            "file_path": "a.xlsx",
            "operations": [{"kind": "data_validation", "sheet": "S", "range": "D2:D10",
                            "rule": {"type": "list", "values": ["A", "B"], "allow_blank": True}}],
        })
        assert not bad, bad

    def test_dv_numeric_and_alias_fields_accepted(self) -> None:
        bad = self._violations("format_spreadsheet", {
            "file_path": "a.xlsx",
            "operations": [{"kind": "data_validation", "sheet": "S", "range": "D2:D10",
                            "rule": {"type": "whole", "operator": "between", "value": 1, "value2": 10}}],
        })
        assert not bad, bad
        bad = self._violations("format_spreadsheet", {
            "file_path": "a.xlsx",
            "operations": [{"kind": "data_validation", "sheet": "S", "range": "D2:D10",
                            "rule": {"type": "list", "source": "Sheet2!A1:A5"}}],
        })
        assert not bad, bad

    def test_cf_rule_still_accepted(self) -> None:
        bad = self._violations("format_spreadsheet", {
            "file_path": "a.xlsx",
            "operations": [{"kind": "conditional_format", "sheet": "S", "range": "B2:B10",
                            "rule": {"type": "cell_value", "operator": "greater_than", "value": 100,
                                     "font": {"bold": True}}}],
        })
        assert not bad, bad

    def test_dv_bogus_type_still_rejected(self) -> None:
        bad = self._violations("format_spreadsheet", {
            "file_path": "a.xlsx",
            "operations": [{"kind": "data_validation", "range": "A1",
                            "rule": {"type": "bogus"}}],
        })
        assert any("rule.type" in m for m in bad)

    def test_format_alias_fields_accepted(self) -> None:
        bad = self._violations("format_spreadsheet", {
            "file_path": "a.xlsx",
            "operations": [{"kind": "format", "sheet": "S", "cell_range": "A1",
                            "numberFormat": "0.00"},
                           {"kind": "size", "sheet": "S", "column_widths": {"A": 18}, "autoFit": True},
                           {"kind": "freeze", "sheet": "S", "panes": "A2"},
                           {"kind": "data_validation", "sheet": "S", "range": "A2",
                            "validation": {"type": "list", "values": ["x"]}, "delete": True}],
        })
        assert not bad, bad

    # ── edit_spreadsheet：JSON 字符串 / spill / 别名 ──

    def test_edit_values_matrix_json_and_spill(self) -> None:
        base = {"kind": "write", "sheet": "S", "start_cell": "A1"}
        for values in ([[1, 2]], "[[1,2]]", "spill:abc"):
            bad = self._violations("edit_spreadsheet", {
                "file_path": "a.xlsx", "operations": [dict(base, values=values)],
            })
            assert not bad, f"values={values!r}: {bad}"

    def test_edit_values_dict_still_rejected(self) -> None:
        bad = self._violations("edit_spreadsheet", {
            "file_path": "a.xlsx",
            "operations": [{"kind": "write", "start_cell": "A1", "values": {"r": 1}}],
        })
        assert any("values" in m for m in bad)

    def test_edit_pivot_values_are_column_names(self) -> None:
        bad = self._violations("edit_spreadsheet", {
            "file_path": "a.xlsx",
            "operations": [{"kind": "pivot", "sheet": "S", "values": ["金额"],
                            "index": ["区域"], "target_sheet": "T"}],
        })
        assert not bad, bad

    def test_edit_operations_json_string(self) -> None:
        bad = self._violations("edit_spreadsheet", {
            "file_path": "a.xlsx",
            "operations": '[{"kind":"write","sheet":"S","start_cell":"A1","values":[[1]]}]',
        })
        assert not bad, bad

    def test_edit_selection_json_string(self) -> None:
        bad = self._violations("edit_spreadsheet", {
            "file_path": "a.xlsx",
            "operations": [{"kind": "delete_rows", "sheet": "S",
                            "selection": '{"rows":[2],"file":"a.xlsx"}',
                            "content_version": "sha256:x"}],
        })
        assert not bad, bad

    def test_edit_workbook_spec_json_string(self) -> None:
        bad = self._violations("edit_spreadsheet", {
            "file_path": "n.xlsx",
            "workbook_spec": '{"sheets":[{"name":"S","dimensions":{"rows":3,"cols":2}}],"uncertainties":[]}',
        })
        assert not bad, bad

    def test_edit_alias_fields_accepted(self) -> None:
        bad = self._violations("edit_spreadsheet", {
            "file_path": "a.xlsx",
            "operations": [{"kind": "write", "sheet": "S", "cell": "A1", "values": [[1]]},
                           {"kind": "transform", "sheet": "S", "transform": "trim", "column": "c"},
                           {"kind": "insert", "sheet": "S", "row": 2},
                           {"kind": "transform", "sheet": "S", "transform": "split",
                            "column": "c", "sep": ","}],
        })
        assert not bad, bad

    # ── analyze_spreadsheet：join 单键 + JSON 兼容 ──

    def test_join_single_key_and_header_row(self) -> None:
        bad = self._violations("analyze_spreadsheet", {
            "file_path": "a.xlsx", "mode": "aggregate", "group_by": ["区域"],
            "aggregations": {"金额": "sum"},
            "join": {"file_path": "b.xlsx", "on": "键", "header_row": 2},
        })
        assert not bad, bad

    def test_join_multi_key_list_rejected(self) -> None:
        # _normalize_join 只支持单键：schema 不得宣称数组键
        bad = self._violations("analyze_spreadsheet", {
            "file_path": "a.xlsx", "mode": "aggregate", "aggregations": {"x": "sum"},
            "join": {"on": ["a", "b"]},
        })
        assert any("join.on" in m for m in bad)

    def test_join_json_string(self) -> None:
        bad = self._violations("analyze_spreadsheet", {
            "file_path": "a.xlsx", "mode": "aggregate", "aggregations": {"x": "sum"},
            "join": '{"sheet":"R","on":"键"}',
        })
        assert not bad, bad

    def test_analyze_aggregations_shapes(self) -> None:
        for aggs in ({"金额": "sum"}, [{"column": "金额", "func": "sum"}], '{"金额":"sum"}'):
            bad = self._violations("analyze_spreadsheet", {
                "file_path": "a.xlsx", "mode": "aggregate",
                "group_by": ["区域"], "aggregations": aggs,
            })
            assert not bad, f"aggregations={aggs!r}: {bad}"

    def test_analyze_conditions_and_paths_json_string(self) -> None:
        bad = self._violations("analyze_spreadsheet", {
            "file_path": "a.xlsx", "mode": "filter",
            "conditions": '[{"column":"c","operator":"eq","value":1}]',
        })
        assert not bad, bad
        bad = self._violations("analyze_spreadsheet", {
            "mode": "files", "file_paths": '["a.xlsx","b.xlsx"]',
        })
        assert not bad, bad

    def test_inspect_include_json_string(self) -> None:
        bad = self._violations("inspect_spreadsheet", {
            "file_path": "a.xlsx", "mode": "overview", "include": '["columns"]',
        })
        assert not bad, bad
