"""vision_extractor 单元测试（B+C 混合架构）。"""

from __future__ import annotations

import json

import pytest


class TestBuildDescribePrompt:
    def test_prompt_contains_key_sections(self) -> None:
        from excelmanus.vision_extractor import build_describe_prompt

        prompt = build_describe_prompt()
        assert "概览" in prompt
        assert "逐行结构描述" in prompt
        assert "Markdown 表格" in prompt
        assert "样式特征" in prompt
        assert "不确定项" in prompt

    def test_prompt_warns_about_merge_vs_label_value(self) -> None:
        """Prompt 应包含防止将 label-value 对误判为合并的警告。"""
        from excelmanus.vision_extractor import build_describe_prompt

        prompt = build_describe_prompt()
        assert "标签" in prompt or "label" in prompt.lower()
        assert "不要" in prompt and "合并" in prompt

    def test_prompt_precision_requirement(self) -> None:
        from excelmanus.vision_extractor import build_describe_prompt

        prompt = build_describe_prompt()
        assert "12.50" in prompt
        assert "[?]" in prompt


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


class TestInferVisionCapable:
    """测试 engine 中的视觉能力推断逻辑。"""

    @staticmethod
    def _infer(model: str, main_model_vision: str = "auto") -> bool:
        from unittest.mock import MagicMock
        from excelmanus.engine import AgentEngine

        config = MagicMock()
        config.main_model_vision = main_model_vision
        config.model = model
        return AgentEngine._infer_vision_capable(config)

    def test_auto_detects_gpt4o(self):
        assert self._infer("gpt-4o-2024-08-06") is True

    def test_auto_detects_qwen_vl(self):
        assert self._infer("qwen2.5-vl-72b-instruct") is True

    def test_auto_rejects_text_only(self):
        assert self._infer("gpt-4-0613") is False

    def test_auto_rejects_openai_o1_mini(self):
        assert self._infer("o1-mini-2024-09-12") is False

    def test_auto_rejects_openai_o3_mini(self):
        assert self._infer("o3-mini-2025-01-31") is False

    def test_auto_rejects_amazon_nova_micro(self):
        assert self._infer("amazon.nova-micro-v1:0") is False

    def test_auto_rejects_amazon_nova_sonic(self):
        assert self._infer("amazon.nova-sonic-v1:0") is False

    def test_auto_rejects_llama_3_2_text_only(self):
        assert self._infer("Llama-3.2-1B-Instruct") is False

    def test_auto_rejects_gemini_embedding(self):
        assert self._infer("gemini-embedding-001") is False

    def test_auto_rejects_step_3_5_flash(self):
        assert self._infer("step-3.5-flash") is False

    def test_auto_detects_ministral_vision(self):
        assert self._infer("ministral-8b-2512") is True

    def test_auto_detects_qwen2_5_omni(self):
        assert self._infer("qwen2.5-omni-7b") is True

    def test_auto_detects_step_1o_turbo_vision(self):
        assert self._infer("step-1o-turbo-vision") is True

    def test_force_true(self):
        assert self._infer("deepseek-chat", "true") is True

    def test_force_false(self):
        assert self._infer("gpt-4o", "false") is False


# ════════════════════════════════════════════════════════════════
# 结构化提取 Prompt + 后处理
# ════════════════════════════════════════════════════════════════

_PROVENANCE = {
    "source_image_hash": "sha256:abc123",
    "model": "test-model",
    "timestamp": "2026-01-01T00:00:00Z",
}


class TestExtractPromptBuilders:
    def test_extract_data_prompt_non_empty(self):
        from excelmanus.vision_extractor import build_extract_data_prompt
        prompt = build_extract_data_prompt()
        assert len(prompt) > 100
        assert "tables" in prompt
        assert "JSON" in prompt

    def test_extract_style_prompt_includes_summary(self):
        from excelmanus.vision_extractor import build_extract_style_prompt
        summary = "- Sheet1: 5行×3列, 15个单元格"
        prompt = build_extract_style_prompt(summary)
        assert summary in prompt
        assert "styles" in prompt

    def test_build_table_summary(self):
        from excelmanus.vision_extractor import build_table_summary
        data = {
            "tables": [
                {"name": "Sheet1", "dimensions": {"rows": 5, "cols": 3},
                 "cells": [{"addr": "A1"}] * 10, "merges": ["A1:C1"]},
            ]
        }
        summary = build_table_summary(data)
        assert "Sheet1" in summary
        assert "10个单元格" in summary


class TestPostprocessSingleTable:
    def test_basic_conversion(self):
        from excelmanus.vision_extractor import postprocess_extraction_to_spec
        data = {
            "tables": [{
                "name": "Sheet1",
                "dimensions": {"rows": 2, "cols": 2},
                "header_rows": [1],
                "cells": [
                    {"addr": "A1", "val": "Name", "type": "string"},
                    {"addr": "B1", "val": "Age", "type": "string"},
                    {"addr": "A2", "val": "Alice", "type": "string"},
                    {"addr": "B2", "val": 30, "type": "number"},
                ],
                "merges": [],
                "col_widths": [15, 10],
            }]
        }
        spec = postprocess_extraction_to_spec(data, None, _PROVENANCE)
        assert len(spec.sheets) == 1
        assert len(spec.sheets[0].cells) == 4
        assert spec.sheets[0].cells[3].value_type == "number"
        assert spec.sheets[0].column_widths == [15, 10]
        assert spec.sheets[0].semantic_hints.header_rows == [1]

    def test_number_format_inferred(self):
        from excelmanus.vision_extractor import postprocess_extraction_to_spec
        data = {
            "tables": [{
                "name": "Sheet1",
                "dimensions": {"rows": 1, "cols": 1},
                "cells": [{"addr": "A1", "val": 1200.5, "type": "number", "display": "$1,200.50"}],
                "merges": [],
            }]
        }
        spec = postprocess_extraction_to_spec(data, None, _PROVENANCE)
        assert spec.sheets[0].cells[0].number_format is not None
        assert "$" in spec.sheets[0].cells[0].number_format


class TestPostprocessMultiTable:
    def test_two_tables_two_sheets(self):
        from excelmanus.vision_extractor import postprocess_extraction_to_spec
        data = {
            "tables": [
                {"name": "Sales", "dimensions": {"rows": 1, "cols": 1},
                 "cells": [{"addr": "A1", "val": "x", "type": "string"}], "merges": []},
                {"name": "Summary", "dimensions": {"rows": 1, "cols": 1},
                 "cells": [{"addr": "A1", "val": "y", "type": "string"}], "merges": []},
            ]
        }
        spec = postprocess_extraction_to_spec(data, None, _PROVENANCE)
        assert len(spec.sheets) == 2
        assert spec.sheets[0].name == "Sales"
        assert spec.sheets[1].name == "Summary"

    def test_auto_naming(self):
        from excelmanus.vision_extractor import postprocess_extraction_to_spec
        data = {
            "tables": [
                {"dimensions": {"rows": 1, "cols": 1},
                 "cells": [{"addr": "A1", "val": "x", "type": "string"}], "merges": []},
                {"dimensions": {"rows": 1, "cols": 1},
                 "cells": [{"addr": "A1", "val": "y", "type": "string"}], "merges": []},
            ]
        }
        spec = postprocess_extraction_to_spec(data, None, _PROVENANCE)
        assert spec.sheets[0].name == "Table1"
        assert spec.sheets[1].name == "Table2"


class TestPostprocessStyles:
    def test_semantic_color_resolved(self):
        from excelmanus.vision_extractor import postprocess_extraction_to_spec
        data = {"tables": [{"name": "S1", "dimensions": {"rows": 1, "cols": 1},
                            "cells": [{"addr": "A1", "val": "H", "type": "string"}], "merges": []}]}
        style = {
            "styles": {"header": {"font": {"bold": True, "color": "white"}, "fill": {"color": "dark_blue"}}},
            "cell_styles": {"A1": "header"},
        }
        spec = postprocess_extraction_to_spec(data, style, _PROVENANCE)
        h = spec.sheets[0].styles["header"]
        assert h.font.color == "#FFFFFF"
        assert h.fill.color == "#1F4E79"
        assert spec.sheets[0].cells[0].style_id == "header"

    def test_range_expansion(self):
        from excelmanus.vision_extractor import postprocess_extraction_to_spec
        data = {"tables": [{"name": "S1", "dimensions": {"rows": 2, "cols": 2},
                            "cells": [
                                {"addr": "A1", "val": "a", "type": "string"},
                                {"addr": "B1", "val": "b", "type": "string"},
                                {"addr": "A2", "val": "c", "type": "string"},
                                {"addr": "B2", "val": "d", "type": "string"},
                            ], "merges": []}]}
        style = {"styles": {"d": {"border": {"style": "thin"}}}, "cell_styles": {"A1:B2": "d"}}
        spec = postprocess_extraction_to_spec(data, style, _PROVENANCE)
        for cell in spec.sheets[0].cells:
            assert cell.style_id == "d"

    def test_default_font(self):
        from excelmanus.vision_extractor import postprocess_extraction_to_spec
        data = {"tables": [{"name": "S1", "dimensions": {"rows": 1, "cols": 1},
                            "cells": [{"addr": "A1", "val": "x", "type": "string"}], "merges": []}]}
        style = {"default_font": {"name": "等线", "size": 11}, "styles": {}, "cell_styles": {}}
        spec = postprocess_extraction_to_spec(data, style, _PROVENANCE)
        assert spec.workbook.default_font.name == "等线"


class TestPostprocessEdgeCases:
    def test_invalid_address_becomes_uncertainty(self):
        from excelmanus.vision_extractor import postprocess_extraction_to_spec
        data = {"tables": [{"name": "S1", "dimensions": {"rows": 1, "cols": 1},
                            "cells": [
                                {"addr": "A1", "val": "ok", "type": "string"},
                                {"addr": "INVALID", "val": "bad", "type": "string"},
                            ], "merges": []}]}
        spec = postprocess_extraction_to_spec(data, None, _PROVENANCE)
        assert len(spec.sheets[0].cells) == 1
        assert len(spec.uncertainties) == 1

    def test_empty_tables_raises(self):
        from excelmanus.vision_extractor import postprocess_extraction_to_spec
        with pytest.raises(ValueError, match="没有 tables"):
            postprocess_extraction_to_spec({"tables": []}, None, _PROVENANCE)

    def test_spec_serializable(self):
        from excelmanus.vision_extractor import postprocess_extraction_to_spec
        data = {"tables": [{"name": "S1", "dimensions": {"rows": 1, "cols": 1},
                            "cells": [{"addr": "A1", "val": "test", "type": "string"}], "merges": []}]}
        spec = postprocess_extraction_to_spec(data, None, _PROVENANCE)
        json_str = spec.model_dump_json()
        assert '"S1"' in json_str
