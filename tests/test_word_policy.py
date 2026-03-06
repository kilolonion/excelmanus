"""Word policy integration tests."""

from __future__ import annotations

import importlib

from excelmanus.tools import policy
from excelmanus.tools.policy import (
    AUDIT_TARGET_ARG_RULES_ALL,
    MUTATING_AUDIT_ONLY_TOOLS,
    READ_ONLY_SAFE_TOOLS,
    ROUTE_TOOL_SCOPE,
    TOOL_CATEGORIES,
    TOOL_SHORT_DESCRIPTIONS,
)


class TestPolicyIntegration:
    def test_read_only_safe_tools_include_word_reads(self) -> None:
        assert {"read_word", "inspect_word", "search_word"} <= READ_ONLY_SAFE_TOOLS

    def test_mutating_audit_only_tools_include_write_word(self) -> None:
        assert "write_word" in MUTATING_AUDIT_ONLY_TOOLS

    def test_audit_target_rules_include_write_word(self) -> None:
        assert AUDIT_TARGET_ARG_RULES_ALL["write_word"] == ("file_path",)

    def test_tool_categories_have_word_bucket(self) -> None:
        assert TOOL_CATEGORIES["word"] == (
            "read_word",
            "inspect_word",
            "search_word",
            "write_word",
        )

    def test_import_policy_assertions_pass(self) -> None:
        assert importlib.reload(policy) is policy


class TestRouteToolScope:
    def test_data_read_includes_word_read_tools(self) -> None:
        assert {"read_word", "inspect_word", "search_word"} <= ROUTE_TOOL_SCOPE["data_read"]

    def test_data_write_includes_write_word(self) -> None:
        assert "write_word" in ROUTE_TOOL_SCOPE["data_write"]

    def test_all_tools_not_in_mapping(self) -> None:
        assert "all_tools" not in ROUTE_TOOL_SCOPE


class TestToolDescriptions:
    def test_word_tools_have_descriptions(self) -> None:
        for tool_name in ("read_word", "write_word", "inspect_word", "search_word"):
            assert tool_name in TOOL_SHORT_DESCRIPTIONS

    def test_word_tool_descriptions_are_non_empty(self) -> None:
        for tool_name in ("read_word", "write_word", "inspect_word", "search_word"):
            assert TOOL_SHORT_DESCRIPTIONS[tool_name].strip()
