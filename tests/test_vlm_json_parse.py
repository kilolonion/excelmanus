"""回归测试：VLM JSON 解析与截断修复。"""

import json
import pytest

from excelmanus.engine_core.tool_dispatcher import (
    _is_likely_truncated,
    _parse_vlm_json,
    _repair_truncated_json,
)


# ════════════════════════════════════════════════════════════════
# _is_likely_truncated
# ════════════════════════════════════════════════════════════════


class TestIsLikelyTruncated:
    def test_finish_reason_length(self):
        assert _is_likely_truncated('{"a": 1}', "length") is True

    def test_finish_reason_max_tokens(self):
        assert _is_likely_truncated('{"a": 1}', "max_tokens") is True

    def test_finish_reason_stop_complete_json(self):
        assert _is_likely_truncated('{"a": 1}', "stop") is False

    def test_finish_reason_none_complete_json(self):
        assert _is_likely_truncated('{"a": 1}', None) is False

    def test_no_closing_brace(self):
        assert _is_likely_truncated('{"tables": [{"name": "Sheet1"', None) is True

    def test_trailing_whitespace_complete(self):
        assert _is_likely_truncated('{"a": 1}  \n', None) is False

    def test_empty_string(self):
        assert _is_likely_truncated("", None) is False


# ════════════════════════════════════════════════════════════════
# _parse_vlm_json — 正常解析
# ════════════════════════════════════════════════════════════════


class TestParseVlmJsonNormal:
    def test_clean_json(self):
        result = _parse_vlm_json('{"tables": []}')
        assert result == {"tables": []}

    def test_json_in_fence(self):
        text = '```json\n{"tables": [{"name": "Sheet1"}]}\n```'
        result = _parse_vlm_json(text)
        assert result is not None
        assert result["tables"][0]["name"] == "Sheet1"

    def test_json_with_prefix(self):
        text = '好的，以下是提取结果：\n{"tables": []}'
        result = _parse_vlm_json(text)
        assert result == {"tables": []}

    def test_json_with_suffix(self):
        text = '{"tables": []}\n\n以上是提取结果。'
        result = _parse_vlm_json(text)
        assert result == {"tables": []}

    def test_empty_returns_none(self):
        assert _parse_vlm_json("") is None
        assert _parse_vlm_json(None) is None

    def test_no_json_returns_none(self):
        assert _parse_vlm_json("这不是 JSON") is None

    def test_array_not_dict_returns_none(self):
        """JSON 数组不是 dict，应返回 None（但修复可能仍返回 None）。"""
        assert _parse_vlm_json('[1, 2, 3]') is None


# ════════════════════════════════════════════════════════════════
# _parse_vlm_json — 截断修复（关键场景）
# ════════════════════════════════════════════════════════════════


class TestParseVlmJsonTruncated:
    """模拟 VLM 输出被 max_tokens 截断的各种场景。"""

    def test_truncated_in_array_element(self):
        """截断在数组元素中间 → 修复应保留已完成的元素。"""
        full = {
            "tables": [
                {"name": "Sheet1", "cells": [
                    {"addr": "A1", "val": "hello"},
                    {"addr": "A2", "val": "world"},
                ]},
            ]
        }
        full_str = json.dumps(full, ensure_ascii=False)
        # 截断到第二个 cell 的 val 中间
        truncated = full_str[:full_str.index('"world"') + 3]
        result = _parse_vlm_json(truncated)
        assert result is not None
        assert "tables" in result
        cells = result["tables"][0].get("cells", [])
        # 至少保留第一个完整的 cell
        assert len(cells) >= 1
        assert cells[0]["addr"] == "A1"

    def test_truncated_after_comma(self):
        """截断在逗号后 → 修复应移除不完整的尾部。"""
        truncated = '{"tables": [{"name": "Sheet1", "cells": [{"addr": "A1", "val": "x"},'
        result = _parse_vlm_json(truncated)
        assert result is not None
        assert result["tables"][0]["cells"][0]["addr"] == "A1"

    def test_truncated_in_string_value(self):
        """截断在字符串值中间 → 修复应回退到上一个完整的分隔符。"""
        truncated = '{"tables": [{"name": "Sheet1", "title": "收款收'
        result = _parse_vlm_json(truncated)
        assert result is not None
        assert "tables" in result

    def test_truncated_minimal(self):
        """极端截断：只有开头括号。"""
        truncated = '{"tables": ['
        result = _parse_vlm_json(truncated)
        assert result is not None
        assert result == {"tables": []}

    def test_truncated_nested_objects(self):
        """嵌套对象截断。"""
        truncated = '{"tables": [{"name": "S1", "dimensions": {"rows": 8, "cols":'
        result = _parse_vlm_json(truncated)
        assert result is not None
        assert "tables" in result

    def test_try_repair_flag_only_affects_logging(self):
        """try_repair=False 也应尝试修复（只影响日志级别）。"""
        truncated = '{"tables": [{"name": "Sheet1"'
        result_no_flag = _parse_vlm_json(truncated, try_repair=False)
        result_with_flag = _parse_vlm_json(truncated, try_repair=True)
        assert result_no_flag is not None
        assert result_with_flag is not None
        assert result_no_flag == result_with_flag

    def test_user_reported_case(self):
        """复现用户报告的 case：收款收据表格截断。"""
        raw = json.dumps({
            "tables": [{
                "name": "Sheet1",
                "title": "收款收据",
                "dimensions": {"rows": 8, "cols": 6},
                "header_rows": [1],
                "total_rows": [],
                "cells": [
                    {"addr": "A1", "val": "收款收据", "type": "string"},
                    {"addr": "A2", "val": "日期", "type": "string"},
                    {"addr": "B2", "val": "2024-01-15", "type": "date"},
                ],
                "merges": ["A1:F1"],
            }]
        }, ensure_ascii=False)
        # 模拟在 merges 之后截断
        cut_point = raw.index('"merges"') + 20
        truncated = raw[:cut_point]
        result = _parse_vlm_json(truncated)
        assert result is not None
        assert result["tables"][0]["name"] == "Sheet1"
        assert len(result["tables"][0]["cells"]) == 3


# ════════════════════════════════════════════════════════════════
# _repair_truncated_json — 直接测试
# ════════════════════════════════════════════════════════════════


class TestRepairTruncatedJson:
    def test_complete_json_passes_through(self):
        """完整 JSON 应直接返回（不需要修复）。"""
        result = _repair_truncated_json('{"a": 1}')
        assert result == {"a": 1}

    def test_missing_closing_brace(self):
        result = _repair_truncated_json('{"a": 1, "b": 2')
        assert result is not None
        assert result["a"] == 1

    def test_missing_closing_bracket_and_brace(self):
        # 截断在 "3" 后面（无 ] 闭合）→ 修复回退到最后一个逗号，丢弃不完整的 "3"
        result = _repair_truncated_json('{"items": [1, 2, 3')
        assert result is not None
        assert result["items"] == [1, 2]

    def test_truncated_inside_string(self):
        result = _repair_truncated_json('{"name": "hello wor')
        assert result is not None
        # 字符串内截断会回退到更早的切点

    def test_empty_returns_none(self):
        assert _repair_truncated_json("") is None

    def test_not_starting_with_brace(self):
        assert _repair_truncated_json("not json") is None

    def test_deeply_nested_truncation(self):
        fragment = '{"a": {"b": {"c": [1, 2, {"d": "val'
        result = _repair_truncated_json(fragment)
        assert result is not None
        assert "a" in result
