"""单元测试：TaskReflector 执行反思器（纯解析逻辑，无 LLM 调用）。"""

from __future__ import annotations

import pytest

from excelmanus.playbook.reflector import (
    PlaybookDelta,
    _build_reflector_user_prompt,
    parse_reflector_output,
)


class TestParseReflectorOutput:
    """测试 LLM 输出解析。"""

    def test_valid_json_array(self) -> None:
        raw = '[{"category": "cross_sheet", "content": "跨表匹配前先 strip key 列", "confidence": 0.9, "source_summary": "客户汇总任务"}]'
        deltas = parse_reflector_output(raw)
        assert len(deltas) == 1
        assert deltas[0].category == "cross_sheet"
        assert deltas[0].content == "跨表匹配前先 strip key 列"
        assert deltas[0].confidence == 0.9

    def test_multiple_items(self) -> None:
        raw = """[
            {"category": "formula", "content": "VLOOKUP 前检查 key 列数据类型", "confidence": 0.8, "source_summary": "公式任务"},
            {"category": "error_recovery", "content": "BadZipFile 时从备份恢复", "confidence": 0.7, "source_summary": "恢复"}
        ]"""
        deltas = parse_reflector_output(raw)
        assert len(deltas) == 2

    def test_max_three_items(self) -> None:
        items = [
            {"category": "general", "content": f"策略{i}", "confidence": 0.9, "source_summary": ""}
            for i in range(5)
        ]
        raw = str(items).replace("'", '"')
        deltas = parse_reflector_output(raw)
        assert len(deltas) == 3

    def test_filters_low_confidence(self) -> None:
        raw = '[{"category": "general", "content": "低置信度", "confidence": 0.3, "source_summary": ""}]'
        deltas = parse_reflector_output(raw)
        assert len(deltas) == 0

    def test_empty_array(self) -> None:
        deltas = parse_reflector_output("[]")
        assert deltas == []

    def test_no_json(self) -> None:
        deltas = parse_reflector_output("没有可提取的策略")
        assert deltas == []

    def test_json_with_surrounding_text(self) -> None:
        raw = '根据分析，提取到以下策略：\n[{"category": "general", "content": "分批处理大数据", "confidence": 0.8, "source_summary": ""}]\n以上是提取结果。'
        deltas = parse_reflector_output(raw)
        assert len(deltas) == 1
        assert deltas[0].content == "分批处理大数据"

    def test_invalid_json(self) -> None:
        deltas = parse_reflector_output("[{invalid json}]")
        assert deltas == []

    def test_invalid_category_defaults_to_general(self) -> None:
        raw = '[{"category": "unknown_cat", "content": "测试", "confidence": 0.8, "source_summary": ""}]'
        deltas = parse_reflector_output(raw)
        assert len(deltas) == 1
        assert deltas[0].category == "general"

    def test_empty_content_filtered(self) -> None:
        raw = '[{"category": "general", "content": "", "confidence": 0.9, "source_summary": ""}]'
        deltas = parse_reflector_output(raw)
        assert len(deltas) == 0

    def test_content_truncated_to_200(self) -> None:
        raw = f'[{{"category": "general", "content": "{"x" * 300}", "confidence": 0.9, "source_summary": ""}}]'
        deltas = parse_reflector_output(raw)
        assert len(deltas) == 1
        assert len(deltas[0].content) == 200

    def test_confidence_clamped(self) -> None:
        raw = '[{"category": "general", "content": "测试", "confidence": 1.5, "source_summary": ""}]'
        deltas = parse_reflector_output(raw)
        assert deltas[0].confidence == 1.0


class TestBuildReflectorUserPrompt:
    """测试用户提示词构建。"""

    def test_basic_prompt(self) -> None:
        prompt = _build_reflector_user_prompt(
            trajectory=[
                {"role": "user", "content": "汇总订单数据"},
                {"role": "assistant", "content": "好的，我来分析..."},
            ],
            task_outcome="pass",
            task_tags=("cross_sheet",),
            write_ops_log=[
                {"tool_name": "run_code", "file_path": "report.xlsx", "sheet": "汇总", "summary": "写入38行"}
            ],
        )
        assert "pass" in prompt
        assert "cross_sheet" in prompt
        assert "run_code" in prompt
        assert "汇总订单数据" in prompt

    def test_empty_trajectory(self) -> None:
        prompt = _build_reflector_user_prompt(
            trajectory=[], task_outcome="fail", task_tags=(),
            write_ops_log=[],
        )
        assert "fail" in prompt

    def test_multimodal_message_extracts_text(self) -> None:
        prompt = _build_reflector_user_prompt(
            trajectory=[
                {"role": "user", "content": [
                    {"type": "text", "text": "请处理这个表格"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}
                ]},
            ],
            task_outcome="pass", task_tags=(), write_ops_log=[],
        )
        assert "请处理这个表格" in prompt

    def test_tool_message(self) -> None:
        prompt = _build_reflector_user_prompt(
            trajectory=[
                {"role": "tool", "name": "read_excel", "content": "Sheet1: 500 rows"},
            ],
            task_outcome="pass", task_tags=(), write_ops_log=[],
        )
        assert "[工具:read_excel]" in prompt
