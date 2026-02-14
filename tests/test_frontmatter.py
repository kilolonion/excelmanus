"""frontmatter 序列化/反序列化测试。"""

from __future__ import annotations

import pytest

from excelmanus.skillpacks.frontmatter import (
    FrontmatterError,
    parse_frontmatter,
    serialize_frontmatter,
)
from excelmanus.skillpacks.loader import SkillpackLoader, SkillpackValidationError


def test_frontmatter_round_trip() -> None:
    payload = {
        "name": "data_basic",
        "description": "测试",
        "allowed_tools": ["read_excel", "create_chart"],
        "triggers": ["分析", "图表"],
        "priority": 2,
        "disable_model_invocation": True,
        "user_invocable": False,
    }
    text = serialize_frontmatter(payload)
    parsed = parse_frontmatter(text)
    assert parsed == payload


def test_frontmatter_supports_multiline_and_nested_yaml() -> None:
    parsed = parse_frontmatter(
        "\n".join(
            [
                "name: demo",
                "description: |",
                "  第一行",
                "  第二行",
                "hooks:",
                "  PreToolUse:",
                "    - matcher: read_*",
                "      hooks:",
                "        - type: prompt",
                "          decision: allow",
            ]
        )
    )
    assert parsed["name"] == "demo"
    assert parsed["description"] == "第一行\n第二行\n"
    assert isinstance(parsed["hooks"], dict)
    assert "PreToolUse" in parsed["hooks"]


def test_loader_private_frontmatter_helpers_delegate_public_api() -> None:
    payload = {
        "name": "demo",
        "description": "测试",
        "allowed_tools": ["read_excel"],
        "triggers": ["分析"],
    }
    text = SkillpackLoader._format_frontmatter(payload)
    assert SkillpackLoader._parse_frontmatter(text) == payload


def test_loader_public_frontmatter_api_keeps_validation_error_type() -> None:
    with pytest.raises(SkillpackValidationError):
        SkillpackLoader.parse_frontmatter("bad line without colon")
