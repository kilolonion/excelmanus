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
    def test_extract_style_prompt_includes_summary(self):
        from excelmanus.vision_extractor import build_extract_style_prompt
        summary = "- Sheet1: 5行×3列, 15个单元格"
        prompt = build_extract_style_prompt(summary)
        assert summary in prompt
        assert "styles" in prompt


# ════════════════════════════════════════════════════════════════
# 4 阶段管线后处理测试（已从 vision_extractor 迁移到 pipeline/phases）
# ════════════════════════════════════════════════════════════════


class TestPipelinePhasesSkeleton:
    def test_build_skeleton_basic(self):
        from excelmanus.pipeline.phases import build_skeleton_spec
        data = {
            "tables": [{
                "name": "Sheet1",
                "dimensions": {"rows": 2, "cols": 2},
                "header_rows": [1],
                "merges": ["A1:B1"],
                "col_widths": [15, 10],
            }]
        }
        spec = build_skeleton_spec(data, _PROVENANCE)
        assert len(spec.sheets) == 1
        assert spec.sheets[0].name == "Sheet1"
        assert spec.sheets[0].column_widths == [15, 10]
        assert spec.sheets[0].semantic_hints.header_rows == [1]
        assert len(spec.sheets[0].merged_ranges) == 1

    def test_empty_tables_raises(self):
        from excelmanus.pipeline.phases import build_skeleton_spec
        with pytest.raises(ValueError, match="没有 tables"):
            build_skeleton_spec({"tables": []}, _PROVENANCE)


class TestPipelinePhasesFillData:
    def test_fill_data_into_skeleton(self):
        from excelmanus.pipeline.phases import build_skeleton_spec, fill_data_into_spec
        skeleton_data = {
            "tables": [{
                "name": "Sheet1",
                "dimensions": {"rows": 2, "cols": 2},
                "header_rows": [1],
                "merges": [],
                "col_widths": [15, 10],
            }]
        }
        skeleton = build_skeleton_spec(skeleton_data, _PROVENANCE)
        data = {
            "tables": [{
                "name": "Sheet1",
                "cells": [
                    {"addr": "A1", "val": "Name", "type": "string"},
                    {"addr": "B1", "val": "Age", "type": "string"},
                    {"addr": "A2", "val": "Alice", "type": "string"},
                    {"addr": "B2", "val": 30, "type": "number"},
                ],
            }]
        }
        spec = fill_data_into_spec(skeleton, data)
        assert len(spec.sheets[0].cells) == 4
        assert spec.sheets[0].cells[3].value_type == "number"

    def test_number_format_inferred(self):
        from excelmanus.pipeline.phases import build_skeleton_spec, fill_data_into_spec
        skeleton = build_skeleton_spec({
            "tables": [{"name": "S1", "dimensions": {"rows": 1, "cols": 1}, "merges": []}]
        }, _PROVENANCE)
        data = {
            "tables": [{
                "name": "S1",
                "cells": [{"addr": "A1", "val": 1200.5, "type": "number", "display": "$1,200.50"}],
            }]
        }
        spec = fill_data_into_spec(skeleton, data)
        assert spec.sheets[0].cells[0].number_format is not None
        assert "$" in spec.sheets[0].cells[0].number_format

    def test_invalid_address_becomes_uncertainty(self):
        from excelmanus.pipeline.phases import build_skeleton_spec, fill_data_into_spec
        skeleton = build_skeleton_spec({
            "tables": [{"name": "S1", "dimensions": {"rows": 1, "cols": 1}, "merges": []}]
        }, _PROVENANCE)
        data = {
            "tables": [{
                "name": "S1",
                "cells": [
                    {"addr": "A1", "val": "ok", "type": "string"},
                    {"addr": "INVALID", "val": "bad", "type": "string"},
                ],
            }]
        }
        spec = fill_data_into_spec(skeleton, data)
        assert len(spec.sheets[0].cells) == 1
        assert len(spec.uncertainties) == 1


class TestPipelinePhasesStyles:
    def test_semantic_color_resolved(self):
        from excelmanus.pipeline.phases import build_skeleton_spec, fill_data_into_spec, apply_styles_to_spec
        skeleton = build_skeleton_spec({
            "tables": [{"name": "S1", "dimensions": {"rows": 1, "cols": 1}, "merges": []}]
        }, _PROVENANCE)
        data_spec = fill_data_into_spec(skeleton, {
            "tables": [{"name": "S1", "cells": [{"addr": "A1", "val": "H", "type": "string"}]}]
        })
        style = {
            "styles": {"header": {"font": {"bold": True, "color": "white"}, "fill": {"color": "dark_blue"}}},
            "cell_styles": {"A1": "header"},
        }
        spec = apply_styles_to_spec(data_spec, style)
        h = spec.sheets[0].styles["header"]
        assert h.font.color == "#FFFFFF"
        assert h.fill.color == "#1F4E79"
        assert spec.sheets[0].cells[0].style_id == "header"

    def test_range_expansion(self):
        from excelmanus.pipeline.phases import build_skeleton_spec, fill_data_into_spec, apply_styles_to_spec
        skeleton = build_skeleton_spec({
            "tables": [{"name": "S1", "dimensions": {"rows": 2, "cols": 2}, "merges": []}]
        }, _PROVENANCE)
        data_spec = fill_data_into_spec(skeleton, {
            "tables": [{"name": "S1", "cells": [
                {"addr": "A1", "val": "a", "type": "string"},
                {"addr": "B1", "val": "b", "type": "string"},
                {"addr": "A2", "val": "c", "type": "string"},
                {"addr": "B2", "val": "d", "type": "string"},
            ]}]
        })
        style = {"styles": {"d": {"border": {"style": "thin"}}}, "cell_styles": {"A1:B2": "d"}}
        spec = apply_styles_to_spec(data_spec, style)
        for cell in spec.sheets[0].cells:
            assert cell.style_id == "d"

    def test_default_font(self):
        from excelmanus.pipeline.phases import build_skeleton_spec, fill_data_into_spec, apply_styles_to_spec
        skeleton = build_skeleton_spec({
            "tables": [{"name": "S1", "dimensions": {"rows": 1, "cols": 1}, "merges": []}]
        }, _PROVENANCE)
        data_spec = fill_data_into_spec(skeleton, {
            "tables": [{"name": "S1", "cells": [{"addr": "A1", "val": "x", "type": "string"}]}]
        })
        style = {"default_font": {"name": "等线", "size": 11}, "styles": {}, "cell_styles": {}}
        spec = apply_styles_to_spec(data_spec, style)
        assert spec.workbook.default_font.name == "等线"


class TestPipelineMultiTable:
    def test_two_tables_two_sheets(self):
        from excelmanus.pipeline.phases import build_skeleton_spec
        data = {
            "tables": [
                {"name": "Sales", "dimensions": {"rows": 1, "cols": 1}, "merges": []},
                {"name": "Summary", "dimensions": {"rows": 1, "cols": 1}, "merges": []},
            ]
        }
        spec = build_skeleton_spec(data, _PROVENANCE)
        assert len(spec.sheets) == 2
        assert spec.sheets[0].name == "Sales"
        assert spec.sheets[1].name == "Summary"

    def test_auto_naming(self):
        from excelmanus.pipeline.phases import build_skeleton_spec
        data = {
            "tables": [
                {"dimensions": {"rows": 1, "cols": 1}, "merges": []},
                {"dimensions": {"rows": 1, "cols": 1}, "merges": []},
            ]
        }
        spec = build_skeleton_spec(data, _PROVENANCE)
        assert spec.sheets[0].name == "Table1"
        assert spec.sheets[1].name == "Table2"


class TestPerSideBorder:
    """D2: 四边独立边框解析。"""

    def test_unified_border(self):
        from excelmanus.pipeline.phases import _build_style_class
        sc = _build_style_class({"border": {"style": "thin", "color": "black"}})
        assert sc.border is not None
        assert sc.border.style == "thin"
        assert sc.border.color == "#000000"
        assert sc.border.top is None

    def test_per_side_border(self):
        from excelmanus.pipeline.phases import _build_style_class
        sc = _build_style_class({"border": {
            "top": {"style": "medium", "color": "black"},
            "bottom": {"style": "double", "color": "dark_blue"},
            "left": {"style": "thin", "color": "light_gray"},
            "right": {"style": "thin", "color": "light_gray"},
        }})
        assert sc.border is not None
        assert sc.border.top.style == "medium"
        assert sc.border.top.color == "#000000"
        assert sc.border.bottom.style == "double"
        assert sc.border.bottom.color == "#1F4E79"
        assert sc.border.left.style == "thin"
        assert sc.border.right.style == "thin"

    def test_mixed_unified_and_sides(self):
        """统一 style + 部分四边覆盖。"""
        from excelmanus.pipeline.phases import _build_style_class
        sc = _build_style_class({"border": {
            "style": "thin",
            "color": "gray",
            "bottom": {"style": "double", "color": "black"},
        }})
        assert sc.border.style == "thin"
        assert sc.border.bottom.style == "double"
        assert sc.border.top is None

    def test_prompt_mentions_per_side_border(self):
        from excelmanus.vision_extractor import build_extract_style_prompt
        prompt = build_extract_style_prompt("- S1: 5行×3列")
        assert "top" in prompt
        assert "bottom" in prompt
        assert "double" in prompt


class TestFormatUtils:
    """D3: 共享 format_utils 模块。"""

    def test_percentage(self):
        from excelmanus.format_utils import infer_number_format
        assert infer_number_format("85%") == "0%"
        assert infer_number_format("12.5%") == "0.0%"

    def test_currency(self):
        from excelmanus.format_utils import infer_number_format
        assert infer_number_format("$1,200") == "$#,##0"
        assert infer_number_format("¥1,200.00") == "¥#,##0.00"

    def test_comma_decimal(self):
        from excelmanus.format_utils import infer_number_format
        assert infer_number_format("1,200") == "#,##0"
        assert infer_number_format("12.50") == "0.00"
        assert infer_number_format("1,200.50") == "#,##0.00"

    def test_pure_integer_returns_none(self):
        from excelmanus.format_utils import infer_number_format
        assert infer_number_format("42") is None

    def test_empty_returns_none(self):
        from excelmanus.format_utils import infer_number_format
        assert infer_number_format("") is None

    def test_image_tools_delegates_to_format_utils(self):
        """image_tools._infer_number_format 应委托给 format_utils。"""
        from excelmanus.tools.image_tools import _infer_number_format
        assert _infer_number_format("$1,200.50") == "$#,##0.00"

    def test_phases_delegates_to_format_utils(self):
        from excelmanus.pipeline.phases import _infer_number_format
        assert _infer_number_format("85%") == "0%"


class TestFormulaDetector:
    """D4: 独立公式模式检测。"""

    @staticmethod
    def _make_spec_with_sum():
        from excelmanus.pipeline.phases import build_skeleton_spec, fill_data_into_spec
        skeleton = build_skeleton_spec({
            "tables": [{
                "name": "S1",
                "dimensions": {"rows": 5, "cols": 2},
                "header_rows": [1],
                "total_rows": [5],
                "merges": [],
            }]
        }, _PROVENANCE)
        data = {
            "tables": [{
                "name": "S1",
                "cells": [
                    {"addr": "A1", "val": "项目", "type": "string"},
                    {"addr": "B1", "val": "金额", "type": "string"},
                    {"addr": "A2", "val": "A", "type": "string"},
                    {"addr": "B2", "val": 100, "type": "number"},
                    {"addr": "A3", "val": "B", "type": "string"},
                    {"addr": "B3", "val": 200, "type": "number"},
                    {"addr": "A4", "val": "C", "type": "string"},
                    {"addr": "B4", "val": 300, "type": "number"},
                    {"addr": "A5", "val": "合计", "type": "string"},
                    {"addr": "B5", "val": 600, "type": "number"},
                ],
            }]
        }
        return fill_data_into_spec(skeleton, data)

    def test_sum_detection(self):
        from excelmanus.pipeline.formula_detector import detect_formulas
        spec = self._make_spec_with_sum()
        detect_formulas(spec)
        b5 = next(c for c in spec.sheets[0].cells if c.address == "B5")
        assert b5.formula_candidate is not None
        assert "SUM" in b5.formula_candidate
        assert "B2" in b5.formula_candidate
        assert "B4" in b5.formula_candidate

    def test_sum_populates_formula_patterns(self):
        from excelmanus.pipeline.formula_detector import detect_formulas
        spec = self._make_spec_with_sum()
        detect_formulas(spec)
        patterns = spec.sheets[0].semantic_hints.formula_patterns
        assert len(patterns) >= 1
        assert any("SUM" in p.pattern for p in patterns)

    def test_no_false_positive_on_mismatch(self):
        """合计值与上方数据之和不匹配时不应推断。"""
        from excelmanus.pipeline.phases import build_skeleton_spec, fill_data_into_spec
        from excelmanus.pipeline.formula_detector import detect_formulas
        skeleton = build_skeleton_spec({
            "tables": [{
                "name": "S1",
                "dimensions": {"rows": 4, "cols": 1},
                "header_rows": [1],
                "total_rows": [4],
                "merges": [],
            }]
        }, _PROVENANCE)
        data = {
            "tables": [{
                "name": "S1",
                "cells": [
                    {"addr": "A1", "val": "X", "type": "string"},
                    {"addr": "A2", "val": 100, "type": "number"},
                    {"addr": "A3", "val": 200, "type": "number"},
                    {"addr": "A4", "val": 999, "type": "number"},
                ],
            }]
        }
        spec = fill_data_into_spec(skeleton, data)
        detect_formulas(spec)
        a4 = next(c for c in spec.sheets[0].cells if c.address == "A4")
        assert a4.formula_candidate is None

    def test_column_arithmetic_detection(self):
        """C = A * B 模式检测。"""
        from excelmanus.pipeline.phases import build_skeleton_spec, fill_data_into_spec
        from excelmanus.pipeline.formula_detector import detect_formulas
        skeleton = build_skeleton_spec({
            "tables": [{
                "name": "S1",
                "dimensions": {"rows": 5, "cols": 3},
                "header_rows": [1],
                "total_rows": [],
                "merges": [],
            }]
        }, _PROVENANCE)
        data = {
            "tables": [{
                "name": "S1",
                "cells": [
                    {"addr": "A1", "val": "数量", "type": "string"},
                    {"addr": "B1", "val": "单价", "type": "string"},
                    {"addr": "C1", "val": "金额", "type": "string"},
                    {"addr": "A2", "val": 10, "type": "number"},
                    {"addr": "B2", "val": 5, "type": "number"},
                    {"addr": "C2", "val": 50, "type": "number"},
                    {"addr": "A3", "val": 20, "type": "number"},
                    {"addr": "B3", "val": 3, "type": "number"},
                    {"addr": "C3", "val": 60, "type": "number"},
                    {"addr": "A4", "val": 15, "type": "number"},
                    {"addr": "B4", "val": 4, "type": "number"},
                    {"addr": "C4", "val": 60, "type": "number"},
                    {"addr": "A5", "val": 8, "type": "number"},
                    {"addr": "B5", "val": 7, "type": "number"},
                    {"addr": "C5", "val": 56, "type": "number"},
                ],
            }]
        }
        spec = fill_data_into_spec(skeleton, data)
        detect_formulas(spec)
        c2 = next(c for c in spec.sheets[0].cells if c.address == "C2")
        assert c2.formula_candidate is not None
        assert "A2" in c2.formula_candidate
        assert "B2" in c2.formula_candidate


class TestBuildFullSummaryB4:
    """B4: build_full_summary 应对大表格做采样+统计，不只输出前 30 个 cells。"""

    @staticmethod
    def _make_spec_with_cells(n: int):
        from excelmanus.pipeline.phases import build_skeleton_spec, fill_data_into_spec
        skeleton = build_skeleton_spec({
            "tables": [{"name": "S1", "dimensions": {"rows": n, "cols": 1}, "merges": []}]
        }, _PROVENANCE)
        cells = [{"addr": f"A{i}", "val": f"v{i}", "type": "string"} for i in range(1, n + 1)]
        return fill_data_into_spec(skeleton, {"tables": [{"name": "S1", "cells": cells}]})

    def test_small_table_outputs_all_cells(self):
        from excelmanus.pipeline.phases import build_full_summary
        spec = self._make_spec_with_cells(20)
        summary = build_full_summary(spec)
        # 20 个 cells < default threshold，应全部输出
        for i in range(1, 21):
            assert f"A{i}:" in summary
        assert "采样" not in summary
        assert "统计" not in summary

    def test_large_table_has_head_sample_tail_and_stats(self):
        from excelmanus.pipeline.phases import build_full_summary
        spec = self._make_spec_with_cells(200)
        summary = build_full_summary(spec, head_cells=10, tail_cells=10, sample_cells=5)
        # 首部
        assert "A1:" in summary
        assert "A10:" in summary
        # 尾部
        assert "A200:" in summary
        assert "A191:" in summary
        # 采样标记
        assert "采样" in summary
        # 统计摘要
        assert "统计" in summary
        assert "string:" in summary

    def test_large_table_coverage_exceeds_old_30(self):
        """默认参数下，大表格的覆盖单元格数远超旧实现的 30 个。"""
        from excelmanus.pipeline.phases import build_full_summary
        spec = self._make_spec_with_cells(500)
        summary = build_full_summary(spec)
        # 至少 head(50) + sample + tail(30) ≈ 90，远超旧实现的 30
        cell_lines = [l for l in summary.splitlines() if ": 'v" in l]
        assert len(cell_lines) >= 80


class TestApplyStylesPerSheetB3:
    """B3: apply_styles_to_spec 应按 sheet 独立解析样式。"""

    @staticmethod
    def _make_two_sheet_spec():
        from excelmanus.pipeline.phases import build_skeleton_spec, fill_data_into_spec
        skeleton = build_skeleton_spec({
            "tables": [
                {"name": "Sales", "dimensions": {"rows": 2, "cols": 1}, "merges": []},
                {"name": "Summary", "dimensions": {"rows": 3, "cols": 1}, "merges": []},
            ]
        }, _PROVENANCE)
        return fill_data_into_spec(skeleton, {
            "tables": [
                {"name": "Sales", "cells": [
                    {"addr": "A1", "val": "Item", "type": "string"},
                    {"addr": "A2", "val": "Apple", "type": "string"},
                ]},
                {"name": "Summary", "cells": [
                    {"addr": "A1", "val": "Total", "type": "string"},
                    {"addr": "A2", "val": 100, "type": "number"},
                    {"addr": "A3", "val": 200, "type": "number"},
                ]},
            ]
        })

    def test_per_sheet_format(self):
        """per-sheet 格式时各 sheet 获得独立样式。"""
        from excelmanus.pipeline.phases import apply_styles_to_spec
        spec = self._make_two_sheet_spec()
        style_json = {
            "sheets": [
                {
                    "name": "Sales",
                    "styles": {"hdr": {"font": {"bold": True}}},
                    "cell_styles": {"A1": "hdr"},
                    "row_heights": {"1": 30},
                },
                {
                    "name": "Summary",
                    "styles": {"total": {"font": {"bold": True, "color": "red"}}},
                    "cell_styles": {"A1": "total"},
                    "row_heights": {"1": 25, "3": 20},
                },
            ]
        }
        result = apply_styles_to_spec(spec, style_json)
        # Sales: 只有 hdr 样式
        assert "hdr" in result.sheets[0].styles
        assert "total" not in result.sheets[0].styles
        assert result.sheets[0].cells[0].style_id == "hdr"
        assert result.sheets[0].row_heights.get("1") == 30.0
        # Summary: 只有 total 样式
        assert "total" in result.sheets[1].styles
        assert "hdr" not in result.sheets[1].styles
        assert result.sheets[1].cells[0].style_id == "total"
        assert result.sheets[1].row_heights.get("3") == 20.0

    def test_flat_format_row_heights_filtered_by_sheet_rows(self):
        """flat 格式下 row_heights 应按各 sheet 的实际行数过滤。"""
        from excelmanus.pipeline.phases import apply_styles_to_spec
        spec = self._make_two_sheet_spec()
        # Sales 只有 2 行，Summary 有 3 行
        style_json = {
            "styles": {"s": {"font": {"bold": True}}},
            "cell_styles": {},
            "row_heights": {"1": 25, "3": 20},
        }
        result = apply_styles_to_spec(spec, style_json)
        # Sales（2行）: row 3 超出，不应包含
        assert "3" not in result.sheets[0].row_heights
        assert result.sheets[0].row_heights.get("1") == 25.0
        # Summary（3行）: row 3 在范围内
        assert result.sheets[1].row_heights.get("3") == 20.0


class TestImageContentHashB2:
    """B2: 统一 hash 计算方式。"""

    def test_raw_and_b64_produce_same_hash(self):
        """同一张图片的 raw bytes 和 base64 编码应产生相同 hash。"""
        import base64
        from excelmanus.engine_core.tool_dispatcher import (
            _image_content_hash,
            _image_content_hash_b64,
        )
        raw = b"\x89PNG\r\n\x1a\nfake_image_data_1234567890"
        b64 = base64.b64encode(raw).decode("ascii")
        assert _image_content_hash(raw) == _image_content_hash_b64(b64)

    def test_different_images_produce_different_hash(self):
        from excelmanus.engine_core.tool_dispatcher import _image_content_hash
        h1 = _image_content_hash(b"image_A")
        h2 = _image_content_hash(b"image_B")
        assert h1 != h2

    def test_hash_is_16_hex_chars(self):
        from excelmanus.engine_core.tool_dispatcher import _image_content_hash
        h = _image_content_hash(b"test")
        assert len(h) == 16
        assert all(c in "0123456789abcdef" for c in h)

    def test_full_content_hash_not_prefix(self):
        """hash 基于全文，而非前 1024 字节——两张仅尾部不同的图片应有不同 hash。"""
        from excelmanus.engine_core.tool_dispatcher import _image_content_hash
        common = b"x" * 2000
        img_a = common + b"AAA"
        img_b = common + b"BBB"
        assert _image_content_hash(img_a) != _image_content_hash(img_b)


class TestPipelineChunking:
    def test_estimate_total_cells(self):
        from excelmanus.pipeline.progressive import ProgressivePipeline
        from excelmanus.pipeline.phases import build_skeleton_spec
        spec = build_skeleton_spec({
            "tables": [
                {"name": "S1", "dimensions": {"rows": 50, "cols": 10}, "merges": []},
                {"name": "S2", "dimensions": {"rows": 20, "cols": 5}, "merges": []},
            ]
        }, _PROVENANCE)
        assert ProgressivePipeline._estimate_total_cells(spec) == 600

    def test_chunked_prompt_contains_row_range(self):
        from excelmanus.pipeline.phases import build_phase2_chunked_prompt
        prompt = build_phase2_chunked_prompt("- S1: 100行×5列", 51, 100)
        assert "第 51 行" in prompt
        assert "第 100 行" in prompt


class TestAverageDetection:
    """AVERAGE 合计行公式检测。"""

    @staticmethod
    def _make_spec_with_average():
        from excelmanus.pipeline.phases import build_skeleton_spec, fill_data_into_spec
        skeleton = build_skeleton_spec({
            "tables": [{
                "name": "S1",
                "dimensions": {"rows": 5, "cols": 2},
                "header_rows": [1],
                "total_rows": [5],
                "merges": [],
            }]
        }, _PROVENANCE)
        data = {
            "tables": [{
                "name": "S1",
                "cells": [
                    {"addr": "A1", "val": "科目", "type": "string"},
                    {"addr": "B1", "val": "分数", "type": "string"},
                    {"addr": "A2", "val": "语文", "type": "string"},
                    {"addr": "B2", "val": 80, "type": "number"},
                    {"addr": "A3", "val": "数学", "type": "string"},
                    {"addr": "B3", "val": 90, "type": "number"},
                    {"addr": "A4", "val": "英语", "type": "string"},
                    {"addr": "B4", "val": 70, "type": "number"},
                    {"addr": "A5", "val": "平均", "type": "string"},
                    {"addr": "B5", "val": 80, "type": "number"},  # (80+90+70)/3 = 80
                ],
            }]
        }
        return fill_data_into_spec(skeleton, data)

    def test_average_detection(self):
        from excelmanus.pipeline.formula_detector import detect_formulas
        spec = self._make_spec_with_average()
        detect_formulas(spec)
        b5 = next(c for c in spec.sheets[0].cells if c.address == "B5")
        assert b5.formula_candidate is not None
        assert "AVERAGE" in b5.formula_candidate
        assert "B2" in b5.formula_candidate
        assert "B4" in b5.formula_candidate

    def test_average_populates_formula_patterns(self):
        from excelmanus.pipeline.formula_detector import detect_formulas
        spec = self._make_spec_with_average()
        detect_formulas(spec)
        patterns = spec.sheets[0].semantic_hints.formula_patterns
        assert any("AVERAGE" in p.pattern for p in patterns)

    def test_sum_takes_priority_over_average(self):
        """当合计值同时匹配 SUM 时，SUM 优先。"""
        from excelmanus.pipeline.phases import build_skeleton_spec, fill_data_into_spec
        from excelmanus.pipeline.formula_detector import detect_formulas
        skeleton = build_skeleton_spec({
            "tables": [{
                "name": "S1",
                "dimensions": {"rows": 4, "cols": 1},
                "header_rows": [1],
                "total_rows": [4],
                "merges": [],
            }]
        }, _PROVENANCE)
        # 50+50=100，average=50——合计值100匹配SUM而非AVERAGE
        data = {
            "tables": [{
                "name": "S1",
                "cells": [
                    {"addr": "A1", "val": "X", "type": "string"},
                    {"addr": "A2", "val": 50, "type": "number"},
                    {"addr": "A3", "val": 50, "type": "number"},
                    {"addr": "A4", "val": 100, "type": "number"},
                ],
            }]
        }
        spec = fill_data_into_spec(skeleton, data)
        detect_formulas(spec)
        a4 = next(c for c in spec.sheets[0].cells if c.address == "A4")
        assert "SUM" in a4.formula_candidate


class TestPercentageDetection:
    """百分比公式检测（colC = colA / colB）。"""

    def test_percentage_formula(self):
        from excelmanus.pipeline.phases import build_skeleton_spec, fill_data_into_spec
        from excelmanus.pipeline.formula_detector import detect_formulas
        skeleton = build_skeleton_spec({
            "tables": [{
                "name": "S1",
                "dimensions": {"rows": 5, "cols": 3},
                "header_rows": [1],
                "total_rows": [],
                "merges": [],
            }]
        }, _PROVENANCE)
        # C = A / B（百分比）
        data = {
            "tables": [{
                "name": "S1",
                "cells": [
                    {"addr": "A1", "val": "实际", "type": "string"},
                    {"addr": "B1", "val": "预算", "type": "string"},
                    {"addr": "C1", "val": "完成率", "type": "string"},
                    {"addr": "A2", "val": 80, "type": "number"},
                    {"addr": "B2", "val": 100, "type": "number"},
                    {"addr": "C2", "val": 0.8, "type": "number"},
                    {"addr": "A3", "val": 150, "type": "number"},
                    {"addr": "B3", "val": 200, "type": "number"},
                    {"addr": "C3", "val": 0.75, "type": "number"},
                    {"addr": "A4", "val": 90, "type": "number"},
                    {"addr": "B4", "val": 120, "type": "number"},
                    {"addr": "C4", "val": 0.75, "type": "number"},
                    {"addr": "A5", "val": 60, "type": "number"},
                    {"addr": "B5", "val": 80, "type": "number"},
                    {"addr": "C5", "val": 0.75, "type": "number"},
                ],
            }]
        }
        spec = fill_data_into_spec(skeleton, data)
        detect_formulas(spec)
        c2 = next(c for c in spec.sheets[0].cells if c.address == "C2")
        assert c2.formula_candidate is not None
        assert "/" in c2.formula_candidate


class TestConditionalFormats:
    """条件格式解析与集成。"""

    def test_color_scale_parsed(self):
        from excelmanus.pipeline.phases import _parse_conditional_formats
        rules = _parse_conditional_formats([{
            "type": "color_scale",
            "range": "C2:C9",
            "min_color": "red",
            "mid_color": "#FFEB84",
            "max_color": "green",
        }])
        assert len(rules) == 1
        assert rules[0].type == "color_scale"
        assert rules[0].range == "C2:C9"
        assert rules[0].min_color == "#FF0000"
        assert rules[0].mid_color == "#FFEB84"
        assert rules[0].max_color == "#70AD47"

    def test_data_bar_parsed(self):
        from excelmanus.pipeline.phases import _parse_conditional_formats
        rules = _parse_conditional_formats([{
            "type": "data_bar",
            "range": "D2:D9",
            "bar_color": "blue",
        }])
        assert len(rules) == 1
        assert rules[0].type == "data_bar"
        assert rules[0].bar_color == "#4472C4"

    def test_icon_set_parsed(self):
        from excelmanus.pipeline.phases import _parse_conditional_formats
        rules = _parse_conditional_formats([{
            "type": "icon_set",
            "range": "E2:E9",
            "icon_style": "3_arrows",
        }])
        assert len(rules) == 1
        assert rules[0].icon_style == "3_arrows"

    def test_cell_value_rule_parsed(self):
        from excelmanus.pipeline.phases import _parse_conditional_formats
        rules = _parse_conditional_formats([{
            "type": "cell_value",
            "range": "C2:C9",
            "operator": "less_than",
            "value": 0,
            "font_color": "red",
            "fill_color": "light_red",
        }])
        assert len(rules) == 1
        assert rules[0].operator == "less_than"
        assert rules[0].value == 0
        assert rules[0].font_color == "#FF0000"
        assert rules[0].fill_color == "#FFC7CE"

    def test_invalid_type_skipped(self):
        from excelmanus.pipeline.phases import _parse_conditional_formats
        rules = _parse_conditional_formats([
            {"type": "invalid_type", "range": "A1:A5"},
            {"type": "color_scale", "range": "B1:B5", "min_color": "red", "max_color": "green"},
        ])
        assert len(rules) == 1
        assert rules[0].type == "color_scale"

    def test_missing_range_skipped(self):
        from excelmanus.pipeline.phases import _parse_conditional_formats
        rules = _parse_conditional_formats([
            {"type": "data_bar", "bar_color": "blue"},  # no range
        ])
        assert len(rules) == 0

    def test_apply_styles_integrates_conditional_formats(self):
        """apply_styles_to_spec 应将 conditional_formats 填入 SheetSpec。"""
        from excelmanus.pipeline.phases import build_skeleton_spec, fill_data_into_spec, apply_styles_to_spec
        skeleton = build_skeleton_spec({
            "tables": [{"name": "S1", "dimensions": {"rows": 3, "cols": 2}, "merges": []}]
        }, _PROVENANCE)
        data_spec = fill_data_into_spec(skeleton, {
            "tables": [{"name": "S1", "cells": [
                {"addr": "A1", "val": "H", "type": "string"},
                {"addr": "B1", "val": "V", "type": "string"},
            ]}]
        })
        style = {
            "styles": {"d": {"border": {"style": "thin"}}},
            "cell_styles": {"A1:B1": "d"},
            "conditional_formats": [
                {"type": "data_bar", "range": "B2:B3", "bar_color": "#638EC6"},
            ],
        }
        spec = apply_styles_to_spec(data_spec, style)
        assert len(spec.sheets[0].conditional_formats) == 1
        assert spec.sheets[0].conditional_formats[0].type == "data_bar"
        assert spec.sheets[0].conditional_formats[0].bar_color == "#638EC6"

    def test_per_sheet_conditional_formats(self):
        """per-sheet 格式中的条件格式正确分配。"""
        from excelmanus.pipeline.phases import build_skeleton_spec, fill_data_into_spec, apply_styles_to_spec
        skeleton = build_skeleton_spec({
            "tables": [
                {"name": "Sales", "dimensions": {"rows": 2, "cols": 1}, "merges": []},
                {"name": "Summary", "dimensions": {"rows": 2, "cols": 1}, "merges": []},
            ]
        }, _PROVENANCE)
        data_spec = fill_data_into_spec(skeleton, {
            "tables": [
                {"name": "Sales", "cells": [{"addr": "A1", "val": "x", "type": "string"}]},
                {"name": "Summary", "cells": [{"addr": "A1", "val": "y", "type": "string"}]},
            ]
        })
        style = {
            "sheets": [
                {
                    "name": "Sales",
                    "styles": {},
                    "cell_styles": {},
                    "conditional_formats": [
                        {"type": "color_scale", "range": "A1:A2", "min_color": "red", "max_color": "green"},
                    ],
                },
                {
                    "name": "Summary",
                    "styles": {},
                    "cell_styles": {},
                },
            ]
        }
        spec = apply_styles_to_spec(data_spec, style)
        assert len(spec.sheets[0].conditional_formats) == 1
        assert spec.sheets[0].conditional_formats[0].type == "color_scale"
        assert len(spec.sheets[1].conditional_formats) == 0


class TestPhase3PromptEnhancements:
    """Phase 3 prompt 增强验证。"""

    def test_prompt_has_per_side_border_examples(self):
        from excelmanus.vision_extractor import build_extract_style_prompt
        prompt = build_extract_style_prompt("- S1: 5行×3列")
        # header 底边粗线示例
        assert "medium" in prompt
        assert "double" in prompt
        # subtotal_row 示例（仅上下有线，左右无线）
        assert "subtotal_row" in prompt
        assert '"none"' in prompt
        # outline_cell 示例
        assert "outline_cell" in prompt

    def test_prompt_has_per_side_scenario_guide(self):
        from excelmanus.vision_extractor import build_extract_style_prompt
        prompt = build_extract_style_prompt("- S1: 5行×3列")
        assert "表头行" in prompt and "底边粗线" in prompt
        assert "外框粗内框细" in prompt
        assert "小计行" in prompt

    def test_prompt_has_conditional_format_section(self):
        from excelmanus.vision_extractor import build_extract_style_prompt
        prompt = build_extract_style_prompt("- S1: 5行×3列")
        assert "conditional_formats" in prompt
        assert "color_scale" in prompt
        assert "data_bar" in prompt
        assert "icon_set" in prompt
        assert "cell_value" in prompt
        assert "颜色刻度" in prompt or "热力图" in prompt

    def test_conditional_format_rule_model(self):
        """ConditionalFormatRule 模型可正常实例化。"""
        from excelmanus.replica_spec import ConditionalFormatRule
        rule = ConditionalFormatRule(
            type="color_scale",
            range="C2:C9",
            min_color="#FF0000",
            max_color="#00FF00",
        )
        assert rule.type == "color_scale"
        assert rule.mid_color is None

    def test_sheet_spec_has_conditional_formats_field(self):
        from excelmanus.pipeline.phases import build_skeleton_spec
        spec = build_skeleton_spec({
            "tables": [{"name": "S1", "dimensions": {"rows": 1, "cols": 1}, "merges": []}]
        }, _PROVENANCE)
        assert hasattr(spec.sheets[0], "conditional_formats")
        assert spec.sheets[0].conditional_formats == []
