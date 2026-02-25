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

import json

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
        payload = json.loads(result)
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
        payload = json.loads(result)
        assert any("类型不匹配" in v for v in payload["violations"])

    def test_enforce_catches_enum_violation(self) -> None:
        registry = _make_registry(mode="enforce")
        result = registry.validate_arguments_by_schema(
            tool_name="t",
            arguments={"file_path": "a.xlsx", "mode": "delete"},
            schema=_tool_with_schema().input_schema,
        )
        assert result is not None
        payload = json.loads(result)
        assert any("枚举" in v for v in payload["violations"])

    def test_enforce_catches_minimum_violation(self) -> None:
        registry = _make_registry(mode="enforce")
        result = registry.validate_arguments_by_schema(
            tool_name="t",
            arguments={"file_path": "a.xlsx", "count": 0},
            schema=_tool_with_schema().input_schema,
        )
        assert result is not None
        payload = json.loads(result)
        assert any("不能小于" in v for v in payload["violations"])

    def test_enforce_catches_maximum_violation(self) -> None:
        registry = _make_registry(mode="enforce")
        result = registry.validate_arguments_by_schema(
            tool_name="t",
            arguments={"file_path": "a.xlsx", "count": 200},
            schema=_tool_with_schema().input_schema,
        )
        assert result is not None
        payload = json.loads(result)
        assert any("不能超过" in v for v in payload["violations"])

    def test_enforce_catches_additional_properties(self) -> None:
        registry = _make_registry(mode="enforce")
        result = registry.validate_arguments_by_schema(
            tool_name="t",
            arguments={"file_path": "a.xlsx", "unknown_field": True},
            schema=_tool_with_schema().input_schema,
        )
        assert result is not None
        payload = json.loads(result)
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
        payload = json.loads(result)
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
        payload = json.loads(result)
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
        payload = json.loads(result)
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
        payload = json.loads(result)
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
        parsed = json.loads(result)
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
        assert result == "echo: hello"


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
        payload = json.loads(result)
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
