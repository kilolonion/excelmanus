"""ask_user 问题流测试：校验、FIFO 队列与答案解析。"""

from __future__ import annotations

import pytest

from excelmanus.question_flow import QuestionFlowManager


def _payload(
    *,
    header: str = "实现方案",
    text: str = "请选择实现方案",
    options: list[dict[str, str]] | None = None,
    multi_select: bool = False,
) -> dict:
    return {
        "header": header,
        "text": text,
        "options": options
        or [
            {"label": "方案A", "description": "快速"},
            {"label": "方案B", "description": "稳健"},
        ],
        "multiSelect": multi_select,
    }


class TestQuestionValidation:
    """问题参数校验。"""

    def test_header_must_not_be_empty(self) -> None:
        manager = QuestionFlowManager()
        with pytest.raises(ValueError, match="header"):
            manager.enqueue(_payload(header=""), tool_call_id="call_1")

    def test_header_length_must_be_at_most_12(self) -> None:
        manager = QuestionFlowManager()
        with pytest.raises(ValueError, match="12"):
            manager.enqueue(_payload(header="X" * 13), tool_call_id="call_1")

    def test_options_count_must_be_2_to_4(self) -> None:
        manager = QuestionFlowManager()
        with pytest.raises(ValueError, match="2 到 4"):
            manager.enqueue(
                _payload(options=[{"label": "A", "description": "x"}]),
                tool_call_id="call_1",
            )

    def test_duplicate_labels_are_rejected(self) -> None:
        manager = QuestionFlowManager()
        with pytest.raises(ValueError, match="不能重复"):
            manager.enqueue(
                _payload(
                    options=[
                        {"label": "A", "description": "x"},
                        {"label": " a ", "description": "y"},
                    ]
                ),
                tool_call_id="call_1",
            )

    def test_model_passed_other_is_ignored_and_system_other_appended(self) -> None:
        manager = QuestionFlowManager()
        pending = manager.enqueue(
            _payload(
                options=[
                    {"label": "方案A", "description": "a"},
                    {"label": "Other", "description": "ignored"},
                    {"label": "方案B", "description": "b"},
                ]
            ),
            tool_call_id="call_1",
        )
        assert [o.label for o in pending.options] == ["方案A", "方案B", "Other"]
        assert pending.options[-1].description == "可输入其他答案"

    def test_queue_limit(self) -> None:
        manager = QuestionFlowManager(max_queue_size=2)
        manager.enqueue(_payload(), tool_call_id="call_1")
        manager.enqueue(_payload(header="问题2"), tool_call_id="call_2")
        with pytest.raises(ValueError, match="上限"):
            manager.enqueue(_payload(header="问题3"), tool_call_id="call_3")


class TestSingleSelectParsing:
    """单选解析：编号、label、Other。"""

    def test_match_by_index(self) -> None:
        manager = QuestionFlowManager()
        pending = manager.enqueue(_payload(), tool_call_id="call_1")
        parsed = manager.parse_answer("2", question=pending)
        assert parsed.selected_options == [{"index": 2, "label": "方案B"}]
        assert parsed.other_text is None

    def test_match_by_label(self) -> None:
        manager = QuestionFlowManager()
        pending = manager.enqueue(_payload(), tool_call_id="call_1")
        parsed = manager.parse_answer("方案A", question=pending)
        assert parsed.selected_options == [{"index": 1, "label": "方案A"}]

    def test_unmatched_text_falls_back_to_other(self) -> None:
        manager = QuestionFlowManager()
        pending = manager.enqueue(_payload(), tool_call_id="call_1")
        parsed = manager.parse_answer("我想走自定义方案", question=pending)
        assert parsed.selected_options == [{"index": 3, "label": "Other"}]
        assert parsed.other_text == "我想走自定义方案"

    def test_multiple_matched_options_are_invalid(self) -> None:
        manager = QuestionFlowManager()
        pending = manager.enqueue(_payload(), tool_call_id="call_1")
        with pytest.raises(ValueError, match="单选题只能选择一个选项"):
            manager.parse_answer("1,2", question=pending)


class TestMultiSelectParsing:
    """多选解析：多行输入、编号/label 混用、Other 并存。"""

    def test_multiline_with_selected_and_other(self) -> None:
        manager = QuestionFlowManager()
        pending = manager.enqueue(_payload(multi_select=True), tool_call_id="call_1")
        parsed = manager.parse_answer("1\n方案B\n自定义约束", question=pending)
        assert parsed.selected_options == [
            {"index": 1, "label": "方案A"},
            {"index": 2, "label": "方案B"},
            {"index": 3, "label": "Other"},
        ]
        assert parsed.other_text == "自定义约束"

    def test_multi_select_requires_at_least_one_choice(self) -> None:
        manager = QuestionFlowManager()
        pending = manager.enqueue(_payload(multi_select=True), tool_call_id="call_1")
        with pytest.raises(ValueError, match="回答不能为空"):
            manager.parse_answer("", question=pending)

    def test_multi_select_allows_other_only(self) -> None:
        manager = QuestionFlowManager()
        pending = manager.enqueue(_payload(multi_select=True), tool_call_id="call_1")
        parsed = manager.parse_answer("other", question=pending)
        assert parsed.selected_options == [{"index": 3, "label": "Other"}]
        assert parsed.other_text is None


class TestQueueAndPrompt:
    """FIFO 队列与提示文本。"""

    def test_fifo_pop_current(self) -> None:
        manager = QuestionFlowManager()
        q1 = manager.enqueue(_payload(header="Q1"), tool_call_id="call_1")
        q2 = manager.enqueue(_payload(header="Q2"), tool_call_id="call_2")

        assert manager.current() == q1
        first = manager.pop_current()
        assert first == q1
        assert manager.current() == q2
        second = manager.pop_current()
        assert second == q2
        assert manager.current() is None

    def test_format_prompt_contains_options_and_queue_hint(self) -> None:
        manager = QuestionFlowManager()
        manager.enqueue(_payload(header="Q1"), tool_call_id="call_1")
        manager.enqueue(_payload(header="Q2"), tool_call_id="call_2")
        prompt = manager.format_prompt()
        assert "Q1" in prompt
        assert "1. 方案A" in prompt
        assert "Other" in prompt
        assert "还有 1 个问题" in prompt
