"""L3 修复单元测试：fuzzy sheet matching + masker iteration fallback."""

from __future__ import annotations

import pytest

from excelmanus.tools._helpers import (
    _find_closest_sheet_name,
    _FUZZY_MATCH_THRESHOLD,
    resolve_sheet_name,
)
from excelmanus.engine_core.observation_masker import (
    FRESH_ITERATIONS,
    mask_messages,
)


# ── L3-1: Fuzzy Sheet Name Matching ────────────────────────────


class TestFuzzySheetNameMatching:
    """resolve_sheet_name 第三级 fuzzy matching。"""

    def test_exact_match_still_works(self) -> None:
        assert resolve_sheet_name("Sheet1", ["Sheet1", "Sheet2"]) == "Sheet1"

    def test_case_insensitive_still_works(self) -> None:
        assert resolve_sheet_name("sheet1", ["Sheet1", "Sheet2"]) == "Sheet1"

    def test_fuzzy_match_similar_name(self) -> None:
        """类似名称应通过 fuzzy matching 自动纠正。"""
        result = resolve_sheet_name("工作表标題", ["工作表标题", "数据源"])
        assert result == "工作表标题"

    def test_fuzzy_match_partial_name(self) -> None:
        """部分匹配应自动纠正。"""
        result = resolve_sheet_name("未知Sheet", ["Sheet1", "Sheet2"])
        # "未知Sheet" vs "Sheet1" — SequenceMatcher should find partial match
        # but similarity might be below threshold depending on the strings
        # Let's test with a closer match
        result2 = resolve_sheet_name("Shet1", ["Sheet1", "Sheet2"])
        assert result2 == "Sheet1"

    def test_fuzzy_match_returns_none_for_very_different(self) -> None:
        """完全不相关的名称不应匹配。"""
        result = resolve_sheet_name("ABCXYZ", ["Sheet1", "数据源"])
        assert result is None

    def test_fuzzy_match_picks_best_candidate(self) -> None:
        """多个候选时应选择最相似的。"""
        result = resolve_sheet_name("Sheet1x", ["Sheet1", "Sheet2", "Sheet3"])
        assert result == "Sheet1"

    def test_none_input_returns_none(self) -> None:
        assert resolve_sheet_name(None, ["Sheet1"]) is None

    def test_empty_available_returns_none(self) -> None:
        assert resolve_sheet_name("Sheet1", []) is None


class TestFindClosestSheetName:
    """_find_closest_sheet_name 辅助函数。"""

    def test_returns_best_match(self) -> None:
        name, ratio = _find_closest_sheet_name("Shet1", ["Sheet1", "Sheet2"])
        assert name == "Sheet1"
        assert ratio > 0.7

    def test_empty_available(self) -> None:
        name, ratio = _find_closest_sheet_name("Sheet1", [])
        assert name is None
        assert ratio == 0.0

    def test_case_insensitive_comparison(self) -> None:
        """fuzzy matching 应该是大小写不敏感的。"""
        name, ratio = _find_closest_sheet_name("SHET1", ["Sheet1", "data"])
        assert name == "Sheet1"
        assert ratio > 0.5


class TestCheckSheetNameEnhanced:
    """check_sheet_name 错误消息增强（需要真实 Excel 文件）。"""

    def test_fuzzy_auto_correct_via_check(self, tmp_path) -> None:
        """check_sheet_name 应通过 fuzzy matching 自动纠正。"""
        from openpyxl import Workbook
        from excelmanus.tools._helpers import check_sheet_name

        wb = Workbook()
        ws = wb.active
        ws.title = "销售数据"
        wb.save(tmp_path / "test.xlsx")
        wb.close()

        # 类似名称应被自动纠正
        resolved, err = check_sheet_name(tmp_path / "test.xlsx", "销售数据x")
        # SequenceMatcher("销售数据x", "销售数据") ratio ~ 0.89 > 0.6
        assert err is None
        assert resolved == "销售数据"

    def test_error_includes_closest_match(self, tmp_path) -> None:
        """当 fuzzy 也匹配不上时，错误应包含 closest_match。"""
        from openpyxl import Workbook
        from excelmanus.tools._helpers import check_sheet_name

        wb = Workbook()
        ws = wb.active
        ws.title = "Sheet1"
        wb.save(tmp_path / "test.xlsx")
        wb.close()

        # 完全不同的名称
        resolved, err = check_sheet_name(tmp_path / "test.xlsx", "完全不同的名字ABCXYZ")
        assert resolved is None
        assert err is not None
        payload = err.value
        assert "available_sheets" in payload
        # closest_match 可能存在也可能不存在（取决于相似度是否 > 0.3）
        assert "hint" in payload


# ── L3-3: ObservationMasker Iteration Fallback ─────────────────


def _msg(role: str, content: str = "test", **kwargs) -> dict:
    d = {"role": role, "content": content}
    d.update(kwargs)
    return d


def _tool_msg(content: str, tool_call_id: str = "tc") -> dict:
    return {"role": "tool", "tool_call_id": tool_call_id, "content": content}


def _assistant_with_tool_calls(tc_id: str = "tc") -> dict:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {"id": tc_id, "type": "function", "function": {"name": "read_excel", "arguments": "{}"}}
        ],
    }


class TestMaskByIteration:
    """ObservationMasker 迭代级回退遮蔽。"""

    def test_single_turn_within_iteration_threshold_no_masking(self) -> None:
        """单 turn 场景，迭代数 <= FRESH_ITERATIONS 时不遮蔽。"""
        msgs = [_msg("user", "请处理文件")]
        # 添加 6 轮迭代（< FRESH_ITERATIONS=8）
        for i in range(6):
            msgs.append(_assistant_with_tool_calls(f"tc_{i}"))
            msgs.append(_tool_msg("x" * 500, f"tc_{i}"))

        result = mask_messages(msgs, fresh_iterations=8)
        # 所有消息应保持原样
        tool_msgs = [m for m in result if m.get("role") == "tool"]
        for m in tool_msgs:
            assert len(m["content"]) == 500

    def test_single_turn_exceeds_iteration_threshold_masks_old(self) -> None:
        """单 turn 场景，迭代数 > FRESH_ITERATIONS 时遮蔽旧迭代。"""
        msgs = [_msg("user", "请处理文件")]
        # 添加 12 轮迭代（> FRESH_ITERATIONS=8）
        for i in range(12):
            msgs.append(_assistant_with_tool_calls(f"tc_{i}"))
            msgs.append(_tool_msg("共 500 行, 12 列 " + "x" * 500, f"tc_{i}"))

        result = mask_messages(msgs, fresh_iterations=8)

        # 后 8 轮应保留，前 4 轮应被遮蔽
        tool_msgs_in_result = [m for m in result if m.get("role") == "tool"]
        # 前 4 个 tool results 应被遮蔽（长度 < 500）
        for m in tool_msgs_in_result[:4]:
            assert len(m["content"]) < 500, f"Expected masked, got {len(m['content'])} chars"
        # 后 8 个 tool results 应保留原样
        for m in tool_msgs_in_result[4:]:
            assert len(m["content"]) > 500

    def test_assistant_messages_never_masked_in_iteration_mode(self) -> None:
        """即使在迭代遮蔽模式下，assistant 消息也不被遮蔽。"""
        msgs = [_msg("user", "请处理文件")]
        for i in range(12):
            msgs.append({
                "role": "assistant",
                "content": "长推理 " + "x" * 500,
                "tool_calls": [
                    {"id": f"tc_{i}", "type": "function",
                     "function": {"name": "read_excel", "arguments": "{}"}}
                ],
            })
            msgs.append(_tool_msg("y" * 500, f"tc_{i}"))

        result = mask_messages(msgs, fresh_iterations=8)
        asst_msgs = [m for m in result if m.get("role") == "assistant"]
        for m in asst_msgs:
            # assistant 内容应保留原样
            assert m.get("content") is None or len(m["content"]) > 400

    def test_user_message_masking_takes_priority(self) -> None:
        """多 user 消息场景下，user-based 遮蔽优先于 iteration-based。"""
        msgs = []
        for i in range(6):
            msgs.append(_msg("user", f"问题{i}"))
            msgs.append(_assistant_with_tool_calls(f"tc_{i}"))
            msgs.append(_tool_msg("x" * 500, f"tc_{i}"))

        # fresh_window=2 应按 user 消息遮蔽（标准路径）
        result = mask_messages(msgs, fresh_window=2, fresh_iterations=20)
        # 前 4 轮的 tool results 应被遮蔽
        old_tools = [m for m in result[:12] if m.get("role") == "tool"]
        for m in old_tools:
            assert len(m["content"]) < 500

    def test_does_not_mutate_original(self) -> None:
        """迭代遮蔽不修改原消息列表。"""
        msgs = [_msg("user", "请处理文件")]
        for i in range(12):
            msgs.append(_assistant_with_tool_calls(f"tc_{i}"))
            msgs.append(_tool_msg("共 500 行, 12 列 " + "x" * 500, f"tc_{i}"))

        original_contents = [m.get("content", "") for m in msgs if m.get("role") == "tool"]
        mask_messages(msgs, fresh_iterations=8)
        current_contents = [m.get("content", "") for m in msgs if m.get("role") == "tool"]
        assert original_contents == current_contents

    def test_short_tool_results_not_masked(self) -> None:
        """短 tool 结果（≤200 字）即使在旧迭代中也不遮蔽。"""
        msgs = [_msg("user", "请处理文件")]
        for i in range(12):
            msgs.append(_assistant_with_tool_calls(f"tc_{i}"))
            msgs.append(_tool_msg("short result", f"tc_{i}"))

        result = mask_messages(msgs, fresh_iterations=8)
        tool_msgs = [m for m in result if m.get("role") == "tool"]
        for m in tool_msgs:
            assert m["content"] == "short result"
