"""CLI question 模块测试。"""

from __future__ import annotations

from excelmanus.cli.question import (
    InteractiveSelectResult,
    build_answer_from_select,
    _is_interactive,
)


class TestInteractiveSelectResult:
    def test_default_values(self):
        r = InteractiveSelectResult()
        assert r.selected_indices == []
        assert r.other_text is None
        assert r.escaped is False

    def test_with_indices(self):
        r = InteractiveSelectResult(selected_indices=[0, 2])
        assert r.selected_indices == [0, 2]

    def test_escaped(self):
        r = InteractiveSelectResult(escaped=True)
        assert r.escaped is True

    def test_other_text(self):
        r = InteractiveSelectResult(other_text="自定义答案")
        assert r.other_text == "自定义答案"


class _FakeQuestion:
    """模拟 PendingQuestion。"""
    def __init__(self, multi_select=False):
        self.multi_select = multi_select
        self.options = []


class TestBuildAnswerFromSelect:
    def test_single_select(self):
        q = _FakeQuestion(multi_select=False)
        r = InteractiveSelectResult(selected_indices=[0])
        assert build_answer_from_select(q, r) == "1"

    def test_multi_select(self):
        q = _FakeQuestion(multi_select=True)
        r = InteractiveSelectResult(selected_indices=[0, 2])
        result = build_answer_from_select(q, r)
        assert "1" in result
        assert "3" in result

    def test_other_text_single(self):
        q = _FakeQuestion(multi_select=False)
        r = InteractiveSelectResult(other_text="自定义")
        assert build_answer_from_select(q, r) == "自定义"

    def test_other_text_multi(self):
        q = _FakeQuestion(multi_select=True)
        r = InteractiveSelectResult(selected_indices=[0], other_text="自定义")
        result = build_answer_from_select(q, r)
        assert "1" in result
        assert "自定义" in result

    def test_empty_indices(self):
        q = _FakeQuestion(multi_select=False)
        r = InteractiveSelectResult(selected_indices=[])
        assert build_answer_from_select(q, r) == ""


class TestIsInteractive:
    def test_returns_bool(self):
        assert isinstance(_is_interactive(), bool)
