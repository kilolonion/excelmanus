"""schema_adapter 统一适配层的测试。

覆盖维度：
  - normalize_schema: 通用规范化 + 各 provider 特定规则
  - adapt_tools:      各 provider 格式封装 + schema 规范化联动
  - adapt_tool_choice: 各 provider 的 tool_choice 映射（参数化）
"""

from __future__ import annotations

from typing import Any

import pytest

from excelmanus.providers.schema_adapter import (
    adapt_tool_choice,
    adapt_tools,
    normalize_schema,
)

ALL_PROVIDERS = ("claude", "gemini", "openai_responses", "openai_chat")

# ── normalize_schema: Phase 1 通用规范化 ─────────────────────────


class TestNormalizeBase:
    """所有 provider 共享的 base 规范化行为。"""

    @pytest.mark.parametrize("provider", ALL_PROVIDERS)
    def test_strips_meta_keys(self, provider: str) -> None:
        schema: dict[str, Any] = {
            "type": "object",
            "$schema": "http://json-schema.org/draft-07",
            "$id": "urn:test",
            "$comment": "internal",
            "properties": {},
        }
        result = normalize_schema(schema, provider)  # type: ignore[arg-type]
        for key in ("$schema", "$id", "$comment"):
            assert key not in result

    @pytest.mark.parametrize("provider", ALL_PROVIDERS)
    def test_removes_examples(self, provider: str) -> None:
        schema: dict[str, Any] = {"type": "string", "examples": ["foo", "bar"]}
        result = normalize_schema(schema, provider)  # type: ignore[arg-type]
        assert "examples" not in result

    @pytest.mark.parametrize("provider", ALL_PROVIDERS)
    def test_merges_default_into_description(self, provider: str) -> None:
        schema: dict[str, Any] = {
            "type": "integer",
            "description": "超时秒数",
            "default": 30,
        }
        result = normalize_schema(schema, provider)  # type: ignore[arg-type]
        assert "default" not in result
        assert "默认 30" in result["description"]

    @pytest.mark.parametrize("provider", ALL_PROVIDERS)
    def test_skips_merge_when_description_already_has_default(self, provider: str) -> None:
        schema: dict[str, Any] = {
            "type": "integer",
            "description": "分页大小，默认 100，最大 500",
            "default": 100,
        }
        result = normalize_schema(schema, provider)  # type: ignore[arg-type]
        assert "default" not in result
        assert result["description"] == "分页大小，默认 100，最大 500"

    @pytest.mark.parametrize("provider", ALL_PROVIDERS)
    def test_removes_default_without_description(self, provider: str) -> None:
        schema: dict[str, Any] = {"type": "boolean", "default": False}
        result = normalize_schema(schema, provider)  # type: ignore[arg-type]
        assert "default" not in result

    @pytest.mark.parametrize("provider", ALL_PROVIDERS)
    def test_recursive_default_in_properties(self, provider: str) -> None:
        schema: dict[str, Any] = {
            "type": "object",
            "properties": {
                "timeout": {
                    "type": "integer",
                    "description": "超时",
                    "default": 30,
                },
                "cwd": {
                    "type": "string",
                    "description": "工作目录",
                    "default": ".",
                },
            },
        }
        result = normalize_schema(schema, provider)  # type: ignore[arg-type]
        assert "default" not in result["properties"]["timeout"]
        assert "默认 30" in result["properties"]["timeout"]["description"]
        assert "default" not in result["properties"]["cwd"]
        assert "默认 ." in result["properties"]["cwd"]["description"]

    @pytest.mark.parametrize("provider", ALL_PROVIDERS)
    def test_boolean_default_formatting(self, provider: str) -> None:
        schema: dict[str, Any] = {
            "type": "boolean",
            "description": "是否递归",
            "default": True,
        }
        result = normalize_schema(schema, provider)  # type: ignore[arg-type]
        assert "默认 true" in result["description"]

    @pytest.mark.parametrize("provider", ALL_PROVIDERS)
    def test_does_not_mutate_original(self, provider: str) -> None:
        schema: dict[str, Any] = {
            "type": "object",
            "$schema": "draft-07",
            "properties": {"x": {"type": "integer", "default": 5, "description": "x"}},
        }
        normalize_schema(schema, provider)  # type: ignore[arg-type]
        assert "$schema" in schema
        assert "default" in schema["properties"]["x"]


# ── normalize_schema: Gemini 特定规范化 ──────────────────────────


class TestNormalizeGemini:
    """Gemini 最严格的 schema 规范化。"""

    def test_strips_additional_properties(self) -> None:
        schema: dict[str, Any] = {
            "type": "object",
            "properties": {"a": {"type": "string"}},
            "additionalProperties": False,
        }
        result = normalize_schema(schema, "gemini")
        assert "additionalProperties" not in result

    def test_strips_title(self) -> None:
        schema: dict[str, Any] = {
            "type": "object",
            "title": "MyTool",
            "properties": {},
        }
        result = normalize_schema(schema, "gemini")
        assert "title" not in result

    def test_strips_nested_additional_properties(self) -> None:
        schema: dict[str, Any] = {
            "type": "object",
            "properties": {
                "child": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {"x": {"type": "string"}},
                }
            },
        }
        result = normalize_schema(schema, "gemini")
        assert "additionalProperties" not in result["properties"]["child"]

    def test_flattens_oneOf_preferring_object(self) -> None:
        schema: dict[str, Any] = {
            "oneOf": [
                {"type": "string"},
                {
                    "type": "object",
                    "properties": {"a": {"type": "string"}},
                    "required": ["a"],
                },
            ],
        }
        result = normalize_schema(schema, "gemini")
        assert "oneOf" not in result
        assert result["type"] == "object"
        assert "a" in result["properties"]

    def test_flattens_oneOf_falls_back_to_first_typed(self) -> None:
        schema: dict[str, Any] = {
            "oneOf": [
                {"type": "string"},
                {"type": "integer"},
            ],
        }
        result = normalize_schema(schema, "gemini")
        assert "oneOf" not in result
        assert result["type"] == "string"

    def test_flattens_anyOf(self) -> None:
        schema: dict[str, Any] = {
            "anyOf": [
                {"type": "number"},
                {"type": "string"},
            ],
        }
        result = normalize_schema(schema, "gemini")
        assert "anyOf" not in result
        assert result["type"] == "number"

    def test_preserves_existing_description_on_flatten(self) -> None:
        schema: dict[str, Any] = {
            "description": "验证条件",
            "oneOf": [
                {"type": "string"},
                {"type": "object", "properties": {}},
            ],
        }
        result = normalize_schema(schema, "gemini")
        assert result["description"] == "验证条件"

    def test_empty_items_gets_type(self) -> None:
        schema: dict[str, Any] = {"type": "array", "items": {}}
        result = normalize_schema(schema, "gemini")
        assert result["items"] == {"type": "string"}

    def test_none_items_gets_type(self) -> None:
        schema: dict[str, Any] = {"type": "array", "items": None}
        result = normalize_schema(schema, "gemini")
        assert result["items"] == {"type": "string"}

    def test_ensures_type_for_properties_without_type(self) -> None:
        schema: dict[str, Any] = {
            "properties": {"a": {"type": "string"}},
            "required": ["a"],
        }
        result = normalize_schema(schema, "gemini")
        assert result["type"] == "object"

    def test_task_tools_nested_oneOf_e2e(self) -> None:
        """task_tools.py 中实际使用的 nested oneOf schema 端到端验证。"""
        schema: dict[str, Any] = {
            "type": "object",
            "properties": {
                "subtasks": {
                    "type": "array",
                    "items": {
                        "oneOf": [
                            {"type": "string"},
                            {
                                "type": "object",
                                "properties": {
                                    "title": {"type": "string"},
                                    "verification": {
                                        "oneOf": [
                                            {"type": "string", "description": "自由文本"},
                                            {
                                                "type": "object",
                                                "properties": {
                                                    "check_type": {
                                                        "type": "string",
                                                        "enum": ["row_count", "value_match"],
                                                    },
                                                },
                                                "required": ["check_type"],
                                            },
                                        ],
                                        "description": "验证条件：字符串或结构化对象",
                                    },
                                },
                                "required": ["title"],
                            },
                        ],
                        "description": "子任务列表",
                    },
                },
            },
            "additionalProperties": False,
        }
        result = normalize_schema(schema, "gemini")

        items = result["properties"]["subtasks"]["items"]
        assert "oneOf" not in items
        assert items["type"] == "object"

        verification = items["properties"]["verification"]
        assert "oneOf" not in verification
        assert "additionalProperties" not in result


# ── normalize_schema: Claude / OpenAI 不做额外变换 ──────────────


class TestNormalizeClaude:
    """Claude 仅做 base 规范化，不做结构变换。"""

    def test_preserves_additional_properties(self) -> None:
        schema: dict[str, Any] = {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        }
        result = normalize_schema(schema, "claude")
        assert result["additionalProperties"] is False

    def test_preserves_oneOf(self) -> None:
        schema: dict[str, Any] = {
            "oneOf": [{"type": "string"}, {"type": "integer"}],
        }
        result = normalize_schema(schema, "claude")
        assert "oneOf" in result


class TestNormalizeOpenAI:
    """OpenAI 保持 schema 结构不变。"""

    def test_preserves_all_standard_fields(self) -> None:
        schema: dict[str, Any] = {
            "type": "object",
            "properties": {"x": {"type": "integer"}},
            "additionalProperties": False,
            "title": "Test",
            "oneOf": [{"type": "string"}],
        }
        for provider in ("openai_responses", "openai_chat"):
            result = normalize_schema(schema, provider)  # type: ignore[arg-type]
            assert result["additionalProperties"] is False
            assert result["title"] == "Test"
            assert "oneOf" in result


# ── adapt_tools ──────────────────────────────────────────────────


_SAMPLE_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "run_shell",
            "description": "执行命令",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "命令"},
                    "cwd": {"type": "string", "description": "工作目录", "default": "."},
                    "timeout": {"type": "integer", "description": "超时", "default": 30},
                },
                "required": ["command"],
                "additionalProperties": False,
            },
        },
    }
]


class TestAdaptTools:
    def test_none_returns_none(self) -> None:
        for p in ALL_PROVIDERS:
            assert adapt_tools(None, p) is None  # type: ignore[arg-type]

    def test_empty_returns_none(self) -> None:
        for p in ALL_PROVIDERS:
            assert adapt_tools([], p) is None  # type: ignore[arg-type]

    def test_claude_format(self) -> None:
        result = adapt_tools(_SAMPLE_TOOLS, "claude")
        assert result is not None
        tool = result[0]
        assert tool["name"] == "run_shell"
        assert "input_schema" in tool
        schema = tool["input_schema"]
        assert "default" not in schema["properties"]["cwd"]
        assert "default" not in schema["properties"]["timeout"]
        assert "默认 ." in schema["properties"]["cwd"]["description"]

    def test_claude_missing_params(self) -> None:
        tools = [{"type": "function", "function": {"name": "noop", "description": "..."}}]
        result = adapt_tools(tools, "claude")
        assert result is not None
        assert result[0]["input_schema"] == {"type": "object", "properties": {}}

    def test_gemini_format(self) -> None:
        result = adapt_tools(_SAMPLE_TOOLS, "gemini")
        assert result is not None
        assert "functionDeclarations" in result[0]
        decl = result[0]["functionDeclarations"][0]
        assert decl["name"] == "run_shell"
        params = decl["parameters"]
        assert "additionalProperties" not in params
        assert "default" not in params["properties"]["cwd"]

    def test_responses_format(self) -> None:
        result = adapt_tools(_SAMPLE_TOOLS, "openai_responses")
        assert result is not None
        tool = result[0]
        assert tool["type"] == "function"
        assert tool["name"] == "run_shell"
        assert "parameters" in tool
        assert "function" not in tool

    def test_chat_format(self) -> None:
        result = adapt_tools(_SAMPLE_TOOLS, "openai_chat")
        assert result is not None
        tool = result[0]
        assert tool["type"] == "function"
        assert "function" in tool
        assert tool["function"]["name"] == "run_shell"

    def test_skips_non_function_tools(self) -> None:
        tools: list[dict[str, Any]] = [
            {"type": "retrieval"},
            {"type": "function", "function": {"name": "ok", "description": "ok"}},
        ]
        result = adapt_tools(tools, "claude")
        assert result is not None
        assert len(result) == 1
        assert result[0]["name"] == "ok"


# ── adapt_tool_choice ────────────────────────────────────────────


class TestAdaptToolChoice:

    @pytest.mark.parametrize(
        "tc_input,expected",
        [
            ("auto", {"type": "auto"}),
            ("required", {"type": "any"}),
            ("none", {"type": "auto"}),
            ({"type": "function", "function": {"name": "ask"}}, {"type": "tool", "name": "ask"}),
            ({"type": "tool", "name": "ask"}, {"type": "tool", "name": "ask"}),
            (None, None),
            (42, None),
        ],
    )
    def test_claude(self, tc_input: Any, expected: Any) -> None:
        assert adapt_tool_choice(tc_input, "claude") == expected

    @pytest.mark.parametrize(
        "tc_input,expected",
        [
            ("auto", {"functionCallingConfig": {"mode": "AUTO"}}),
            ("required", {"functionCallingConfig": {"mode": "ANY"}}),
            ("none", {"functionCallingConfig": {"mode": "NONE"}}),
            (
                {"type": "function", "function": {"name": "ask"}},
                {"functionCallingConfig": {"mode": "ANY", "allowedFunctionNames": ["ask"]}},
            ),
            (None, None),
            (42, None),
        ],
    )
    def test_gemini(self, tc_input: Any, expected: Any) -> None:
        assert adapt_tool_choice(tc_input, "gemini") == expected

    @pytest.mark.parametrize(
        "tc_input,expected",
        [
            ("auto", "auto"),
            ("required", "required"),
            ("none", "none"),
            (
                {"type": "function", "function": {"name": "ask"}},
                {"type": "function", "name": "ask"},
            ),
            (None, None),
            (42, None),
        ],
    )
    def test_openai_responses(self, tc_input: Any, expected: Any) -> None:
        assert adapt_tool_choice(tc_input, "openai_responses") == expected

    def test_openai_chat_passthrough(self) -> None:
        tc: dict[str, Any] = {"type": "function", "function": {"name": "ask"}}
        assert adapt_tool_choice(tc, "openai_chat") is tc

    @pytest.mark.parametrize(
        "tc_input",
        [
            {"type": "function", "function": {"name": "ask_user"}},
            {"type": "function", "name": "ask_user"},
        ],
    )
    def test_dict_tool_choice_with_nested_and_flat_name(self, tc_input: Any) -> None:
        claude_result = adapt_tool_choice(tc_input, "claude")
        assert claude_result == {"type": "tool", "name": "ask_user"}

    @pytest.mark.parametrize("provider", ALL_PROVIDERS)
    def test_whitespace_handling(self, provider: str) -> None:
        result = adapt_tool_choice("  auto  ", provider)  # type: ignore[arg-type]
        assert result is not None
