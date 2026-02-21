"""vision_extractor 单元测试。"""

from __future__ import annotations

import json

import pytest


class TestBuildExtractionPrompt:
    def test_prompt_contains_schema(self) -> None:
        from excelmanus.vision_extractor import build_extraction_prompt

        prompt = build_extraction_prompt(focus="full")
        assert "ReplicaSpec" in prompt or "cells" in prompt
        assert "uncertainties" in prompt

    def test_prompt_data_focus(self) -> None:
        from excelmanus.vision_extractor import build_extraction_prompt

        prompt = build_extraction_prompt(focus="data")
        assert "只需提取数据" in prompt

    def test_prompt_style_focus(self) -> None:
        from excelmanus.vision_extractor import build_extraction_prompt

        prompt = build_extraction_prompt(focus="style")
        assert "只需提取样式" in prompt


class TestParseExtractionResult:
    def test_valid_json_parses(self) -> None:
        from excelmanus.vision_extractor import parse_extraction_result

        raw = json.dumps({
            "version": "1.0",
            "provenance": {
                "source_image_hash": "sha256:x",
                "model": "test",
                "timestamp": "2026-01-01T00:00:00Z",
            },
            "workbook": {"name": "test"},
            "sheets": [{
                "name": "Sheet1",
                "dimensions": {"rows": 1, "cols": 1},
                "cells": [
                    {"address": "A1", "value": "hello", "value_type": "string", "confidence": 0.9},
                ],
                "styles": {},
            }],
            "uncertainties": [],
        })
        spec = parse_extraction_result(raw)
        assert spec.sheets[0].cells[0].value == "hello"

    def test_json_in_code_block(self) -> None:
        """LLM 输出被包裹在 ```json ... ``` 中也能解析。"""
        from excelmanus.vision_extractor import parse_extraction_result

        inner = json.dumps({
            "version": "1.0",
            "provenance": {
                "source_image_hash": "sha256:x",
                "model": "test",
                "timestamp": "2026-01-01T00:00:00Z",
            },
            "sheets": [{
                "name": "Sheet1",
                "dimensions": {"rows": 1, "cols": 1},
            }],
        })
        raw = f"这是提取结果：\n```json\n{inner}\n```\n完成。"
        spec = parse_extraction_result(raw)
        assert spec.version == "1.0"

    def test_invalid_json_raises(self) -> None:
        from excelmanus.vision_extractor import parse_extraction_result

        with pytest.raises(ValueError, match="JSON"):
            parse_extraction_result("not json at all")

    def test_invalid_schema_raises(self) -> None:
        from excelmanus.vision_extractor import parse_extraction_result

        with pytest.raises(ValueError, match="校验失败"):
            parse_extraction_result('{"version": "1.0"}')


class TestSemanticColorMap:
    def test_map_contains_common_colors(self):
        from excelmanus.vision_extractor import SEMANTIC_COLOR_MAP
        assert "dark_blue" in SEMANTIC_COLOR_MAP
        assert "white" in SEMANTIC_COLOR_MAP
        assert "light_gray" in SEMANTIC_COLOR_MAP

    def test_map_values_are_valid_hex(self):
        import re
        from excelmanus.vision_extractor import SEMANTIC_COLOR_MAP
        for name, hex_val in SEMANTIC_COLOR_MAP.items():
            if hex_val:  # 跳过空字符串（none/transparent）
                assert re.match(r'^#[0-9A-Fa-f]{6}$', hex_val), \
                    f"{name}: {hex_val} 不是合法 hex"

    def test_resolve_semantic_color_exact(self):
        from excelmanus.vision_extractor import resolve_semantic_color
        assert resolve_semantic_color("dark_blue") == "#1F4E79"

    def test_resolve_semantic_color_passthrough_hex(self):
        from excelmanus.vision_extractor import resolve_semantic_color
        # 已经是 hex 的直接透传
        assert resolve_semantic_color("#FF0000") == "#FF0000"

    def test_resolve_semantic_color_unknown_returns_none(self):
        from excelmanus.vision_extractor import resolve_semantic_color
        assert resolve_semantic_color("rainbow_sparkle") is None
