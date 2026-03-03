"""PromptComposer 单元测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from excelmanus.prompt_composer import (
    PromptComposer,
    PromptContext,
    PromptSegment,
    parse_prompt_file,
)


# ── parse_prompt_file ────────────────────────────────────


class TestParsePromptFile:
    def test_parse_core_file(self, tmp_path: Path) -> None:
        md = tmp_path / "test.md"
        md.write_text(
            '---\nname: identity\nversion: "1.0.0"\npriority: 0\nlayer: core\n---\n'
            "你是 ExcelManus。\n",
            encoding="utf-8",
        )
        seg = parse_prompt_file(md)
        assert seg.name == "identity"
        assert seg.priority == 0
        assert seg.layer == "core"
        assert seg.content == "你是 ExcelManus。"
        assert seg.conditions == {}

    def test_parse_strategy_with_conditions(self, tmp_path: Path) -> None:
        md = tmp_path / "strat.md"
        md.write_text(
            '---\nname: cross_sheet\nversion: "1.0.0"\npriority: 50\nlayer: strategy\n'
            'max_tokens: 300\nconditions:\n  write_hint: "may_write"\n  sheet_count_gte: 2\n---\n'
            "跨 Sheet 策略正文。\n",
            encoding="utf-8",
        )
        seg = parse_prompt_file(md)
        assert seg.layer == "strategy"
        assert seg.conditions == {"write_hint": "may_write", "sheet_count_gte": 2}
        assert seg.max_tokens == 300

    def test_missing_required_field_raises(self, tmp_path: Path) -> None:
        md = tmp_path / "bad.md"
        md.write_text("---\nname: foo\n---\n内容\n", encoding="utf-8")
        with pytest.raises(ValueError, match="priority"):
            parse_prompt_file(md)

    def test_missing_frontmatter_raises(self, tmp_path: Path) -> None:
        md = tmp_path / "no_fm.md"
        md.write_text("没有 frontmatter 的文件\n", encoding="utf-8")
        with pytest.raises(ValueError, match="frontmatter"):
            parse_prompt_file(md)

    def test_defaults_for_optional_fields(self, tmp_path: Path) -> None:
        md = tmp_path / "minimal.md"
        md.write_text(
            '---\nname: test\npriority: 5\nlayer: core\n---\n内容\n',
            encoding="utf-8",
        )
        seg = parse_prompt_file(md)
        assert seg.version == "0.0.0"
        assert seg.max_tokens == 0
        assert seg.min_tokens == 0
        assert seg.conditions == {}


# ── PromptComposer ───────────────────────────────────────


def _make_prompts_dir(tmp_path: Path) -> Path:
    """创建测试用的 prompts 目录。"""
    core = tmp_path / "core"
    core.mkdir()
    (core / "00_id.md").write_text(
        '---\nname: id\nversion: "1.0"\npriority: 0\nlayer: core\n---\n身份。',
        encoding="utf-8",
    )
    (core / "10_rules.md").write_text(
        '---\nname: rules\nversion: "1.0"\npriority: 10\nlayer: core\n---\n规则。',
        encoding="utf-8",
    )
    strats = tmp_path / "strategies"
    strats.mkdir()
    (strats / "cross_sheet.md").write_text(
        '---\nname: cross_sheet\nversion: "1.0"\npriority: 50\nlayer: strategy\n'
        'conditions:\n  sheet_count_gte: 2\n  write_hint: "may_write"\n---\n跨表策略。',
        encoding="utf-8",
    )
    (strats / "formula.md").write_text(
        '---\nname: formula\nversion: "1.0"\npriority: 45\nlayer: strategy\n'
        'conditions:\n  write_hint: "may_write"\n---\n公式策略。',
        encoding="utf-8",
    )
    return tmp_path


class TestPromptComposerLoad:
    def test_load_all(self, tmp_path: Path) -> None:
        d = _make_prompts_dir(tmp_path)
        composer = PromptComposer(d)
        composer.load_all(auto_repair=False)
        assert len(composer.core_segments) == 2
        assert len(composer.strategy_segments) == 2

    def test_load_empty_dir(self, tmp_path: Path) -> None:
        composer = PromptComposer(tmp_path)
        composer.load_all(auto_repair=False)
        assert len(composer.core_segments) == 0
        assert len(composer.strategy_segments) == 0

    def test_invalid_file_skipped(self, tmp_path: Path) -> None:
        core = tmp_path / "core"
        core.mkdir()
        (core / "good.md").write_text(
            '---\nname: good\npriority: 0\nlayer: core\n---\nOK',
            encoding="utf-8",
        )
        (core / "bad.md").write_text("no frontmatter", encoding="utf-8")
        composer = PromptComposer(tmp_path)
        composer.load_all(auto_repair=False)
        assert len(composer.core_segments) == 1


class TestPromptComposerCompose:
    def test_core_only_for_read(self, tmp_path: Path) -> None:
        d = _make_prompts_dir(tmp_path)
        composer = PromptComposer(d)
        composer.load_all(auto_repair=False)
        ctx = PromptContext(write_hint="read_only")
        text = composer.compose_text(ctx)
        assert "身份。" in text
        assert "规则。" in text
        assert "跨表策略。" not in text
        assert "公式策略。" not in text

    def test_strategy_match_write_hint(self, tmp_path: Path) -> None:
        d = _make_prompts_dir(tmp_path)
        composer = PromptComposer(d)
        composer.load_all(auto_repair=False)
        ctx = PromptContext(write_hint="may_write", sheet_count=1)
        text = composer.compose_text(ctx)
        assert "公式策略。" in text  # write_hint 满足
        assert "跨表策略。" not in text  # sheet_count < 2

    def test_strategy_match_cross_sheet(self, tmp_path: Path) -> None:
        d = _make_prompts_dir(tmp_path)
        composer = PromptComposer(d)
        composer.load_all(auto_repair=False)
        ctx = PromptContext(write_hint="may_write", sheet_count=3)
        text = composer.compose_text(ctx)
        assert "跨表策略。" in text
        assert "公式策略。" in text

    def test_compose_strategies_text_only(self, tmp_path: Path) -> None:
        d = _make_prompts_dir(tmp_path)
        composer = PromptComposer(d)
        composer.load_all(auto_repair=False)
        ctx = PromptContext(write_hint="may_write", sheet_count=3)
        text = composer.compose_strategies_text(ctx)
        assert "跨表策略。" in text
        assert "公式策略。" in text
        assert "身份。" not in text  # core 不包含

    def test_compose_strategies_empty_for_read(self, tmp_path: Path) -> None:
        d = _make_prompts_dir(tmp_path)
        composer = PromptComposer(d)
        composer.load_all(auto_repair=False)
        ctx = PromptContext(write_hint="read_only")
        text = composer.compose_strategies_text(ctx)
        assert text == ""

    def test_priority_ordering(self, tmp_path: Path) -> None:
        d = _make_prompts_dir(tmp_path)
        composer = PromptComposer(d)
        composer.load_all(auto_repair=False)
        ctx = PromptContext(write_hint="may_write", sheet_count=3)
        segments = composer.compose(ctx)
        priorities = [s.priority for s in segments]
        assert priorities == sorted(priorities)


class TestPromptComposerBudget:
    def test_budget_drops_low_priority_first(self, tmp_path: Path) -> None:
        d = _make_prompts_dir(tmp_path)
        composer = PromptComposer(d)
        composer.load_all(auto_repair=False)
        ctx = PromptContext(write_hint="may_write", sheet_count=3)
        # 非常小的 budget 应该丢弃策略段但保留 core
        segments = composer.compose(ctx, token_budget=10)
        names = [s.name for s in segments]
        assert "id" in names  # priority=0, 永不丢弃
        # 策略段应该被丢弃
        assert "cross_sheet" not in names

    def test_large_budget_keeps_all(self, tmp_path: Path) -> None:
        d = _make_prompts_dir(tmp_path)
        composer = PromptComposer(d)
        composer.load_all(auto_repair=False)
        ctx = PromptContext(write_hint="may_write", sheet_count=3)
        segments = composer.compose(ctx, token_budget=999999)
        assert len(segments) == 4  # 2 core + 2 strategies


class TestMatchConditions:
    def test_empty_conditions_always_match(self) -> None:
        assert PromptComposer._match_conditions({}, PromptContext()) is True

    def test_write_hint_match(self) -> None:
        ctx = PromptContext(write_hint="may_write")
        assert PromptComposer._match_conditions({"write_hint": "may_write"}, ctx)
        assert not PromptComposer._match_conditions({"write_hint": "read_only"}, ctx)

    def test_sheet_count_gte(self) -> None:
        ctx = PromptContext(sheet_count=3)
        assert PromptComposer._match_conditions({"sheet_count_gte": 2}, ctx)
        assert PromptComposer._match_conditions({"sheet_count_gte": 3}, ctx)
        assert not PromptComposer._match_conditions({"sheet_count_gte": 4}, ctx)

    def test_total_rows_gte(self) -> None:
        ctx = PromptContext(total_rows=200)
        assert PromptComposer._match_conditions({"total_rows_gte": 100}, ctx)
        assert not PromptComposer._match_conditions({"total_rows_gte": 500}, ctx)

    def test_task_tags_match(self) -> None:
        ctx = PromptContext(task_tags=["cross_sheet", "data_fill"])
        assert PromptComposer._match_conditions(
            {"task_tags": ["cross_sheet"]}, ctx,
        )
        assert not PromptComposer._match_conditions(
            {"task_tags": ["chart"]}, ctx,
        )

    def test_combined_conditions_and_logic(self) -> None:
        ctx = PromptContext(write_hint="may_write", sheet_count=3)
        assert PromptComposer._match_conditions(
            {"write_hint": "may_write", "sheet_count_gte": 2}, ctx,
        )
        assert not PromptComposer._match_conditions(
            {"write_hint": "may_write", "sheet_count_gte": 5}, ctx,
        )

    def test_full_access_false_match(self) -> None:
        ctx_off = PromptContext(full_access=False)
        ctx_on = PromptContext(full_access=True)
        assert PromptComposer._match_conditions({"full_access": False}, ctx_off)
        assert not PromptComposer._match_conditions({"full_access": False}, ctx_on)

    def test_full_access_true_match(self) -> None:
        ctx_on = PromptContext(full_access=True)
        ctx_off = PromptContext(full_access=False)
        assert PromptComposer._match_conditions({"full_access": True}, ctx_on)
        assert not PromptComposer._match_conditions({"full_access": True}, ctx_off)


class TestSandboxAwarenessStrategy:
    """sandbox_awareness 策略文件加载与条件匹配测试。"""

    @staticmethod
    def _make_dir_with_sandbox(tmp_path: Path) -> Path:
        core = tmp_path / "core"
        core.mkdir()
        (core / "00_id.md").write_text(
            '---\nname: id\nversion: "1.0"\npriority: 0\nlayer: core\n---\n身份。',
            encoding="utf-8",
        )
        strats = tmp_path / "strategies"
        strats.mkdir()
        (strats / "20_sandbox_awareness.md").write_text(
            '---\nname: sandbox_awareness\nversion: "1.0.0"\npriority: 20\nlayer: strategy\n'
            'conditions: {}\n---\n沙箱安全机制内容。',
            encoding="utf-8",
        )
        return tmp_path

    def test_sandbox_included_when_full_access_off(self, tmp_path: Path) -> None:
        d = self._make_dir_with_sandbox(tmp_path)
        composer = PromptComposer(d)
        composer.load_all(auto_repair=False)
        ctx = PromptContext(full_access=False)
        text = composer.compose_strategies_text(ctx)
        assert "沙箱安全机制内容。" in text

    def test_sandbox_included_when_full_access_on(self, tmp_path: Path) -> None:
        """sandbox_awareness 现为无条件注入，full_access=True 时也应包含。"""
        d = self._make_dir_with_sandbox(tmp_path)
        composer = PromptComposer(d)
        composer.load_all(auto_repair=False)
        ctx = PromptContext(full_access=True)
        text = composer.compose_strategies_text(ctx)
        assert "沙箱安全机制内容。" in text

    def test_real_sandbox_awareness_file(self) -> None:
        """验证实际 prompts/strategies/20_sandbox_awareness.md 文件可正确加载。"""
        prompts_dir = Path(__file__).resolve().parent.parent / "excelmanus" / "prompts"
        strat_file = prompts_dir / "strategies" / "20_sandbox_awareness.md"
        if not strat_file.exists():
            pytest.skip("20_sandbox_awareness.md 不存在")
        seg = parse_prompt_file(strat_file)
        assert seg.name == "sandbox_awareness"
        assert seg.conditions == {}
        assert "GREEN" in seg.content
        assert "RED" in seg.content


# ── 回归测试：core 文件与 legacy prompt 一致性 ───────────


class TestComposeForSubagent:
    """compose_for_subagent 子代理提示词组装测试。"""

    def test_base_plus_specific(self, tmp_path: Path) -> None:
        sa_dir = tmp_path / "subagent"
        sa_dir.mkdir()
        (sa_dir / "_base.md").write_text(
            '---\nname: base\npriority: 0\nlayer: subagent\n---\n共享约束。',
            encoding="utf-8",
        )
        (sa_dir / "explorer.md").write_text(
            '---\nname: explorer\npriority: 10\nlayer: subagent\n---\n探查专用。',
            encoding="utf-8",
        )
        composer = PromptComposer(tmp_path)
        result = composer.compose_for_subagent("explorer")
        assert result is not None
        assert "共享约束。" in result
        assert "探查专用。" in result

    def test_specific_only_no_base(self, tmp_path: Path) -> None:
        sa_dir = tmp_path / "subagent"
        sa_dir.mkdir()
        (sa_dir / "writer.md").write_text(
            '---\nname: writer\npriority: 10\nlayer: subagent\n---\n写入专用。',
            encoding="utf-8",
        )
        composer = PromptComposer(tmp_path)
        result = composer.compose_for_subagent("writer")
        assert result is not None
        assert "写入专用。" in result

    def test_nonexistent_subagent_returns_none(self, tmp_path: Path) -> None:
        sa_dir = tmp_path / "subagent"
        sa_dir.mkdir()
        composer = PromptComposer(tmp_path)
        assert composer.compose_for_subagent("nonexistent") is None

    def test_no_subagent_dir_returns_none(self, tmp_path: Path) -> None:
        composer = PromptComposer(tmp_path)
        assert composer.compose_for_subagent("explorer") is None

    def test_real_subagent_files(self) -> None:
        """验证实际 prompts/subagent/ 文件可正确加载。"""
        prompts_dir = Path(__file__).resolve().parent.parent / "excelmanus" / "prompts"
        if not (prompts_dir / "subagent").is_dir():
            pytest.skip("prompts/subagent/ 不存在")
        composer = PromptComposer(prompts_dir)
        for name in ("subagent",):
            result = composer.compose_for_subagent(name)
            assert result is not None, f"{name} 子代理提示词加载失败"
            assert len(result) > 50, f"{name} 子代理提示词过短"
            # 应包含 _base.md 的共享约束
            assert "直接行动" in result, f"{name} 缺少共享约束"


class TestTaskTagsLexical:
    """词法 task_tags 分类测试。"""

    def test_cross_sheet_detected(self) -> None:
        from excelmanus.skillpacks.router import SkillRouter
        tags = SkillRouter._classify_task_tags_lexical("从Sheet2查找数据填入Sheet1")
        assert "cross_sheet" in tags

    def test_formatting_detected(self) -> None:
        from excelmanus.skillpacks.router import SkillRouter
        tags = SkillRouter._classify_task_tags_lexical("把A列加粗并标红")
        assert "formatting" in tags

    def test_chart_detected(self) -> None:
        from excelmanus.skillpacks.router import SkillRouter
        tags = SkillRouter._classify_task_tags_lexical("生成一个柱状图")
        assert "chart" in tags

    def test_data_fill_detected(self) -> None:
        from excelmanus.skillpacks.router import SkillRouter
        tags = SkillRouter._classify_task_tags_lexical("填充B列的空白单元格")
        assert "data_fill" in tags

    def test_large_data_detected(self) -> None:
        from excelmanus.skillpacks.router import SkillRouter
        tags = SkillRouter._classify_task_tags_lexical("批量处理所有行的数据")
        assert "large_data" in tags

    def test_multiple_tags(self) -> None:
        from excelmanus.skillpacks.router import SkillRouter
        tags = SkillRouter._classify_task_tags_lexical("从Sheet2批量填充数据到Sheet1")
        assert "cross_sheet" in tags
        assert "data_fill" in tags

    def test_empty_message(self) -> None:
        from excelmanus.skillpacks.router import SkillRouter
        assert SkillRouter._classify_task_tags_lexical("") == []

    def test_no_tags(self) -> None:
        from excelmanus.skillpacks.router import SkillRouter
        tags = SkillRouter._classify_task_tags_lexical("读取A1单元格的值")
        assert tags == []


class TestErrorRecoveryStrategy:
    """error_recovery 策略文件加载与无条件注入测试。"""

    def test_real_error_recovery_file(self) -> None:
        """验证实际 prompts/strategies/error_recovery.md 文件可正确加载。"""
        prompts_dir = Path(__file__).resolve().parent.parent / "excelmanus" / "prompts"
        strat_file = prompts_dir / "strategies" / "error_recovery.md"
        if not strat_file.exists():
            pytest.skip("error_recovery.md 不存在")
        seg = parse_prompt_file(strat_file)
        assert seg.name == "error_recovery"
        assert seg.priority == 25
        assert seg.conditions == {}
        assert "分级处理" in seg.content
        assert "重试上限" in seg.content

    def test_error_recovery_included_unconditionally(self, tmp_path: Path) -> None:
        """error_recovery 无条件注入，read_only 和 may_write 均应包含。"""
        core = tmp_path / "core"
        core.mkdir()
        (core / "00_id.md").write_text(
            '---\nname: id\nversion: "1.0"\npriority: 0\nlayer: core\n---\n身份。',
            encoding="utf-8",
        )
        strats = tmp_path / "strategies"
        strats.mkdir()
        (strats / "error_recovery.md").write_text(
            '---\nname: error_recovery\nversion: "1.0.0"\npriority: 25\nlayer: strategy\n'
            'conditions: {}\n---\n错误恢复策略内容。',
            encoding="utf-8",
        )
        composer = PromptComposer(tmp_path)
        composer.load_all(auto_repair=False)
        for hint in ("read_only", "may_write", "unknown"):
            ctx = PromptContext(write_hint=hint)
            text = composer.compose_strategies_text(ctx)
            assert "错误恢复策略内容。" in text, f"write_hint={hint} 时未注入 error_recovery"


class TestInheritStrategies:
    """compose_for_subagent 策略继承测试。"""

    @staticmethod
    def _make_full_dir(tmp_path: Path) -> Path:
        """创建含 core + strategies + subagent 的完整 prompts 目录。"""
        core = tmp_path / "core"
        core.mkdir()
        (core / "00_id.md").write_text(
            '---\nname: id\nversion: "1.0"\npriority: 0\nlayer: core\n---\n身份。',
            encoding="utf-8",
        )
        strats = tmp_path / "strategies"
        strats.mkdir()
        (strats / "error_recovery.md").write_text(
            '---\nname: error_recovery\nversion: "1.0"\npriority: 25\nlayer: strategy\n'
            'conditions: {}\n---\n错误恢复内容。',
            encoding="utf-8",
        )
        (strats / "sandbox_awareness.md").write_text(
            '---\nname: sandbox_awareness\nversion: "1.0"\npriority: 20\nlayer: strategy\n'
            'conditions: {}\n---\n沙盒感知内容。',
            encoding="utf-8",
        )
        (strats / "run_code_patterns.md").write_text(
            '---\nname: run_code_patterns\nversion: "1.0"\npriority: 35\nlayer: strategy\n'
            'conditions:\n  chat_mode: "write"\n---\nrun_code 模板。',
            encoding="utf-8",
        )
        sa = tmp_path / "subagent"
        sa.mkdir()
        (sa / "_base.md").write_text(
            '---\nname: base\npriority: 0\nlayer: subagent\n---\n共享约束。',
            encoding="utf-8",
        )
        (sa / "explorer.md").write_text(
            '---\nname: explorer\npriority: 10\nlayer: subagent\n---\n探查专用。',
            encoding="utf-8",
        )
        (sa / "worker.md").write_text(
            '---\nname: worker\npriority: 10\nlayer: subagent\n---\n写入专用。',
            encoding="utf-8",
        )
        return tmp_path

    def test_no_inherit_strategies_no_strategies_in_output(self, tmp_path: Path) -> None:
        d = self._make_full_dir(tmp_path)
        composer = PromptComposer(d)
        composer.load_all(auto_repair=False)
        result = composer.compose_for_subagent("explorer")
        assert result is not None
        assert "错误恢复内容。" not in result
        assert "沙盒感知内容。" not in result

    def test_explicit_strategy_names(self, tmp_path: Path) -> None:
        d = self._make_full_dir(tmp_path)
        composer = PromptComposer(d)
        composer.load_all(auto_repair=False)
        result = composer.compose_for_subagent(
            "explorer", inherit_strategies=["error_recovery", "sandbox_awareness"]
        )
        assert result is not None
        assert "错误恢复内容。" in result
        assert "沙盒感知内容。" in result
        assert "run_code 模板。" not in result  # 未指定，不应包含

    def test_universal_inherits_unconditional_only(self, tmp_path: Path) -> None:
        d = self._make_full_dir(tmp_path)
        composer = PromptComposer(d)
        composer.load_all(auto_repair=False)
        result = composer.compose_for_subagent(
            "worker", inherit_strategies=["__universal__"]
        )
        assert result is not None
        assert "错误恢复内容。" in result  # conditions: {}
        assert "沙盒感知内容。" in result  # conditions: {}
        assert "run_code 模板。" not in result  # has conditions → excluded

    def test_all_inherits_everything(self, tmp_path: Path) -> None:
        d = self._make_full_dir(tmp_path)
        composer = PromptComposer(d)
        composer.load_all(auto_repair=False)
        result = composer.compose_for_subagent(
            "worker", inherit_strategies=["__all__"]
        )
        assert result is not None
        assert "错误恢复内容。" in result
        assert "沙盒感知内容。" in result
        assert "run_code 模板。" in result  # __all__ includes conditional too

    def test_mixed_universal_and_explicit(self, tmp_path: Path) -> None:
        d = self._make_full_dir(tmp_path)
        composer = PromptComposer(d)
        composer.load_all(auto_repair=False)
        result = composer.compose_for_subagent(
            "explorer",
            inherit_strategies=["__universal__", "run_code_patterns"],
        )
        assert result is not None
        assert "错误恢复内容。" in result
        assert "沙盒感知内容。" in result
        assert "run_code 模板。" in result  # explicitly named

    def test_inherited_strategies_sorted_by_priority(self, tmp_path: Path) -> None:
        d = self._make_full_dir(tmp_path)
        composer = PromptComposer(d)
        composer.load_all(auto_repair=False)
        result = composer.compose_for_subagent(
            "worker", inherit_strategies=["__all__"]
        )
        assert result is not None
        # sandbox(priority=20) should come before error_recovery(priority=25)
        # which should come before run_code_patterns(priority=35)
        sandbox_pos = result.index("沙盒感知内容。")
        error_pos = result.index("错误恢复内容。")
        run_code_pos = result.index("run_code 模板。")
        assert sandbox_pos < error_pos < run_code_pos

    def test_empty_inherit_strategies_list(self, tmp_path: Path) -> None:
        d = self._make_full_dir(tmp_path)
        composer = PromptComposer(d)
        composer.load_all(auto_repair=False)
        result = composer.compose_for_subagent("explorer", inherit_strategies=[])
        assert result is not None
        assert "错误恢复内容。" not in result

    def test_real_subagent_files_with_strategy_inheritance(self) -> None:
        """验证实际 prompts/ 文件：子代理可继承策略。"""
        prompts_dir = Path(__file__).resolve().parent.parent / "excelmanus" / "prompts"
        if not (prompts_dir / "subagent").is_dir():
            pytest.skip("prompts/subagent/ 不存在")
        composer = PromptComposer(prompts_dir)
        composer.load_all()
        # subagent 应继承所有策略
        result = composer.compose_for_subagent("subagent", inherit_strategies=["__all__"])
        assert result is not None
        assert "继承策略" in result
        # explorer 应继承 error_recovery 和 sandbox_awareness
        result = composer.compose_for_subagent(
            "explorer", inherit_strategies=["error_recovery", "sandbox_awareness"]
        )
        assert result is not None
        assert "错误恢复策略" in result
        assert "沙盒安全机制" in result


class TestPlanModeStrategyRouting:
    """plan 模式策略分流测试。"""

    def test_plan_strategy_only_applies_for_plan_worthy(self) -> None:
        prompts_dir = Path(__file__).resolve().parent.parent / "excelmanus" / "prompts"
        if not prompts_dir.is_dir():
            pytest.skip("prompts/ 目录不存在")

        composer = PromptComposer(prompts_dir)
        composer.load_all()

        worthy_ctx = PromptContext(
            chat_mode="plan",
            write_hint="read_only",
            task_tags=["plan_worthy"],
        )
        worthy_text = composer.compose_strategies_text(worthy_ctx)
        assert "## 规划模式策略" in worthy_text

        not_needed_ctx = PromptContext(
            chat_mode="plan",
            write_hint="read_only",
            task_tags=["plan_not_needed"],
        )
        not_needed_text = composer.compose_strategies_text(not_needed_ctx)
        assert "## 规划模式策略" not in not_needed_text

    def test_plan_mode_fallback_strategy_applies_for_plan_not_needed(self) -> None:
        prompts_dir = Path(__file__).resolve().parent.parent / "excelmanus" / "prompts"
        if not prompts_dir.is_dir():
            pytest.skip("prompts/ 目录不存在")

        composer = PromptComposer(prompts_dir)
        composer.load_all()

        ctx = PromptContext(
            chat_mode="plan",
            write_hint="read_only",
            task_tags=["plan_not_needed"],
        )
        text = composer.compose_strategies_text(ctx)
        assert "## 规划模式轻量分流" in text


class TestVariableSubstitution:
    """变量替换机制测试。"""

    @staticmethod
    def _make_dir_with_placeholders(tmp_path: Path) -> Path:
        core = tmp_path / "core"
        core.mkdir()
        (core / "00_id.md").write_text(
            '---\nname: id\nversion: "1.0"\npriority: 0\nlayer: core\n---\n'
            '根目录：`{workspace_root}`。',
            encoding="utf-8",
        )
        strats = tmp_path / "strategies"
        strats.mkdir()
        (strats / "topo.md").write_text(
            '---\nname: topo\nversion: "1.0"\npriority: 15\nlayer: strategy\n'
            'conditions: {}\n---\n工作区 `{workspace_root}` 拓扑。',
            encoding="utf-8",
        )
        sa = tmp_path / "subagent"
        sa.mkdir()
        (sa / "_base.md").write_text(
            '---\nname: base\npriority: 0\nlayer: subagent\n---\n基础 {workspace_root}。',
            encoding="utf-8",
        )
        (sa / "worker.md").write_text(
            '---\nname: worker\npriority: 10\nlayer: subagent\n---\n工人 {workspace_root}。',
            encoding="utf-8",
        )
        return tmp_path

    def test_compose_text_substitutes_variables(self, tmp_path: Path) -> None:
        d = self._make_dir_with_placeholders(tmp_path)
        composer = PromptComposer(d)
        composer.load_all(auto_repair=False)
        ctx = PromptContext(write_hint="unknown")
        text = composer.compose_text(ctx, variables={"workspace_root": "/data/user1"})
        assert "/data/user1" in text
        assert "{workspace_root}" not in text

    def test_compose_text_without_variables_keeps_placeholder(self, tmp_path: Path) -> None:
        d = self._make_dir_with_placeholders(tmp_path)
        composer = PromptComposer(d)
        composer.load_all(auto_repair=False)
        ctx = PromptContext(write_hint="unknown")
        text = composer.compose_text(ctx)
        assert "{workspace_root}" in text

    def test_compose_strategies_text_substitutes_variables(self, tmp_path: Path) -> None:
        d = self._make_dir_with_placeholders(tmp_path)
        composer = PromptComposer(d)
        composer.load_all(auto_repair=False)
        ctx = PromptContext()
        text = composer.compose_strategies_text(ctx, variables={"workspace_root": "/ws"})
        assert "/ws" in text
        assert "{workspace_root}" not in text

    def test_compose_strategies_text_without_variables_keeps_placeholder(
        self, tmp_path: Path,
    ) -> None:
        d = self._make_dir_with_placeholders(tmp_path)
        composer = PromptComposer(d)
        composer.load_all(auto_repair=False)
        ctx = PromptContext()
        text = composer.compose_strategies_text(ctx)
        assert "{workspace_root}" in text

    def test_compose_for_subagent_substitutes_variables(self, tmp_path: Path) -> None:
        d = self._make_dir_with_placeholders(tmp_path)
        composer = PromptComposer(d)
        composer.load_all(auto_repair=False)
        result = composer.compose_for_subagent(
            "worker", variables={"workspace_root": "/agent/ws"},
        )
        assert result is not None
        assert "/agent/ws" in result
        assert "{workspace_root}" not in result

    def test_compose_for_subagent_with_inherited_strategies_substitutes(
        self, tmp_path: Path,
    ) -> None:
        d = self._make_dir_with_placeholders(tmp_path)
        composer = PromptComposer(d)
        composer.load_all(auto_repair=False)
        result = composer.compose_for_subagent(
            "worker",
            inherit_strategies=["__all__"],
            variables={"workspace_root": "/sub"},
        )
        assert result is not None
        assert "{workspace_root}" not in result
        # 策略中的占位符也应被替换
        assert "/sub" in result

    def test_substitute_static_method(self) -> None:
        assert PromptComposer._substitute("hello {x}", {"x": "world"}) == "hello world"
        assert PromptComposer._substitute("no placeholder", {"x": "v"}) == "no placeholder"
        assert PromptComposer._substitute("", {"x": "v"}) == ""
        assert PromptComposer._substitute("keep {x}", None) == "keep {x}"

    def test_real_files_no_unresolved_workspace_root(self) -> None:
        """验证实际 .md 文件中 {workspace_root} 经替换后不残留。"""
        prompts_dir = Path(__file__).resolve().parent.parent / "excelmanus" / "prompts"
        if not prompts_dir.is_dir():
            pytest.skip("prompts/ 目录不存在")
        composer = PromptComposer(prompts_dir)
        composer.load_all()
        variables = {"workspace_root": "/test/workspace", "auto_generated_capability_map": ""}
        # core + 无条件策略
        ctx = PromptContext()
        full_text = composer.compose_text(ctx, variables=variables)
        assert "{workspace_root}" not in full_text
        assert "{auto_generated_capability_map}" not in full_text
        # 策略文本
        strat_text = composer.compose_strategies_text(ctx, variables=variables)
        assert "{workspace_root}" not in strat_text


class TestCoreSegmentsMatchLegacy:
    def test_exact_match(self) -> None:
        from excelmanus.memory import _DEFAULT_SYSTEM_PROMPT

        prompts_dir = Path(__file__).resolve().parent.parent / "excelmanus" / "prompts"
        if not prompts_dir.is_dir():
            pytest.skip("prompts/ 目录不存在")
        composer = PromptComposer(prompts_dir)
        composer.load_all()
        if not composer.core_segments:
            pytest.skip("无 core 段可加载")
        # write_hint="unknown" 与 _load_system_prompt 一致，只匹配 core 段
        ctx = PromptContext(write_hint="unknown")
        core_text = composer.compose_text(ctx)
        assert core_text == _DEFAULT_SYSTEM_PROMPT, (
            "core/ 文件拼接结果与 _DEFAULT_SYSTEM_PROMPT 不一致！\n"
            f"长度: core={len(core_text)} vs legacy={len(_DEFAULT_SYSTEM_PROMPT)}"
        )
