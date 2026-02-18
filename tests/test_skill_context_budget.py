"""技能正文 Token 预算相关单元测试。"""

from __future__ import annotations

import pytest
from unittest.mock import patch

from excelmanus.skillpacks.context_builder import build_contexts_with_budget
from excelmanus.skillpacks.models import Skillpack


def _make_skillpack(
    name: str,
    *,
    description: str = "测试描述",
    instructions: str = "测试指引",
    priority: int = 0,
) -> Skillpack:
    return Skillpack(
        name=name,
        description=description,
        allowed_tools=["read_excel"],
        triggers=["测试"],
        instructions=instructions,
        source="system",
        root_dir="/tmp",
        priority=priority,
    )


class TestRenderContextMinimal:
    """render_context_minimal 测试。"""

    def test_contains_name_and_description_only(self) -> None:
        skill = _make_skillpack("foo", description="bar")
        out = skill.render_context_minimal()
        assert "[Skillpack] foo" in out
        assert "描述：bar" in out
        assert "授权工具" not in out
        assert "执行指引" not in out

    def test_no_instructions_in_minimal(self) -> None:
        skill = _make_skillpack("x", instructions="很长的指引内容" * 100)
        out = skill.render_context_minimal()
        assert "很长的指引" not in out


class TestRenderContextTruncated:
    """render_context_truncated 测试。"""

    def test_truncate_suffix_present(self) -> None:
        skill = _make_skillpack("a", instructions="line1\nline2\nline3" * 50)
        out = skill.render_context_truncated(200)
        assert "[正文已截断，完整内容见 SKILL.md]" in out

    def test_header_always_present(self) -> None:
        skill = _make_skillpack("test", instructions="x")
        out = skill.render_context_truncated(500)
        assert "[Skillpack] test" in out
        assert "描述：" in out
        assert "授权工具" not in out
        assert "执行指引：" in out

    def test_falls_back_to_minimal_when_budget_tiny(self) -> None:
        skill = _make_skillpack("tiny", instructions="content")
        out = skill.render_context_truncated(10)
        assert out == skill.render_context_minimal()

    def test_large_budget_includes_full_instructions(self) -> None:
        skill = _make_skillpack("short", instructions="brief")
        out = skill.render_context_truncated(2000)
        assert "brief" in out
        assert "[Skillpack] short" in out


class TestBuildContextsWithBudget:
    """build_contexts_with_budget 测试。"""

    def test_empty_skills_returns_empty(self) -> None:
        assert build_contexts_with_budget([], 1000) == []

    def test_budget_zero_returns_full_contexts(self) -> None:
        skill = _make_skillpack("a", instructions="full content")
        out = build_contexts_with_budget([skill], 0)
        assert len(out) == 1
        assert out[0] == skill.render_context()

    def test_budget_negative_returns_full_contexts(self) -> None:
        skill = _make_skillpack("a", instructions="full")
        out = build_contexts_with_budget([skill], -1)
        assert out[0] == skill.render_context()

    def test_sufficient_budget_all_full(self) -> None:
        skills = [
            _make_skillpack("a", instructions="short", priority=1),
            _make_skillpack("b", instructions="short", priority=0),
        ]
        total_len = sum(len(s.render_context()) for s in skills)
        out = build_contexts_with_budget(skills, total_len + 100)
        assert len(out) == 2
        assert out[0] == skills[0].render_context()
        assert out[1] == skills[1].render_context()

    def test_insufficient_budget_truncates_low_priority(self) -> None:
        long_instructions = "line\n" * 100
        high = _make_skillpack("high", instructions="short", priority=10)
        low = _make_skillpack("low", instructions=long_instructions, priority=0)
        budget = len(high.render_context()) + 150
        out = build_contexts_with_budget([high, low], budget)
        assert len(out) == 2
        assert out[0] == high.render_context()
        assert "[正文已截断" in out[1]
        assert sum(len(c) for c in out) <= budget + 50

    def test_very_small_budget_uses_minimal(self) -> None:
        skills = [
            _make_skillpack("a", instructions="x" * 500, priority=1),
            _make_skillpack("b", instructions="y" * 500, priority=0),
        ]
        budget = 70
        out = build_contexts_with_budget(skills, budget)
        assert len(out) == 2
        for ctx in out:
            assert "[Skillpack]" in ctx
            assert "描述：" in ctx
            assert "x" * 10 not in ctx and "y" * 10 not in ctx
        total = sum(len(c) for c in out)
        assert total <= budget, f"total {total} > budget {budget}"

    def test_sorted_by_priority(self) -> None:
        a = _make_skillpack("a", instructions="a", priority=0)
        b = _make_skillpack("b", instructions="b", priority=5)
        c = _make_skillpack("c", instructions="c", priority=3)
        out = build_contexts_with_budget([a, b, c], 500)
        assert len(out) == 3
        assert "b" in out[0]
        assert "c" in out[1]
        assert "a" in out[2]

    def test_minimal_context_called_once_when_minimal_branch_selected(self) -> None:
        skill = _make_skillpack("only_minimal", instructions="x" * 500, priority=1)
        minimal_text = skill.render_context_minimal()
        original = Skillpack.render_context_minimal
        call_count = 0

        def _spy(this: Skillpack) -> str:
            nonlocal call_count
            if this is skill:
                call_count += 1
            return original(this)

        # remaining < len(minimal)+50，触发 minimal 分支
        budget = len(minimal_text) + 10
        with patch.object(Skillpack, "render_context_minimal", autospec=True, side_effect=_spy):
            out = build_contexts_with_budget([skill], budget)

        assert out == [minimal_text]
        assert call_count == 1
