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
            'max_tokens: 300\nconditions:\n  chat_mode: "write"\n  full_access: true\n---\n'
            "跨 Sheet 策略正文。\n",
            encoding="utf-8",
        )
        seg = parse_prompt_file(md)
        assert seg.layer == "strategy"
        assert seg.conditions == {"chat_mode": "write", "full_access": True}
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
        'conditions:\n  full_access: true\n  chat_mode: "write"\n---\n跨表策略。',
        encoding="utf-8",
    )
    (strats / "formula.md").write_text(
        '---\nname: formula\nversion: "1.0"\npriority: 45\nlayer: strategy\n'
        'conditions:\n  chat_mode: "write"\n---\n公式策略。',
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
        ctx = PromptContext(chat_mode="read")
        text = composer.compose_text(ctx)
        assert "身份。" in text
        assert "规则。" in text
        assert "跨表策略。" not in text
        assert "公式策略。" not in text

    def test_compose_text_default_excludes_strategies(self, tmp_path: Path) -> None:
        d = _make_prompts_dir(tmp_path)
        composer = PromptComposer(d)
        composer.load_all(auto_repair=False)
        ctx = PromptContext(chat_mode="write")
        text = composer.compose_text(ctx)
        assert "身份。" in text
        assert "跨表策略。" not in text
        assert "公式策略。" not in text

    def test_strategy_match_write_mode(self, tmp_path: Path) -> None:
        d = _make_prompts_dir(tmp_path)
        composer = PromptComposer(d)
        composer.load_all(auto_repair=False)
        ctx = PromptContext(chat_mode="write")
        text = composer.compose_strategies_text(ctx)
        assert "公式策略。" in text
        assert "跨表策略。" not in text  # 需要 full_access

    def test_strategy_match_cross_sheet(self, tmp_path: Path) -> None:
        d = _make_prompts_dir(tmp_path)
        composer = PromptComposer(d)
        composer.load_all(auto_repair=False)
        ctx = PromptContext(chat_mode="write", full_access=True)
        text = composer.compose_strategies_text(ctx)
        assert "跨表策略。" in text
        assert "公式策略。" in text

    def test_compose_strategies_text_only(self, tmp_path: Path) -> None:
        d = _make_prompts_dir(tmp_path)
        composer = PromptComposer(d)
        composer.load_all(auto_repair=False)
        ctx = PromptContext(chat_mode="write", full_access=True)
        text = composer.compose_strategies_text(ctx)
        assert "跨表策略。" in text
        assert "公式策略。" in text
        assert "身份。" not in text  # core 不包含

    def test_compose_strategies_empty_for_read(self, tmp_path: Path) -> None:
        d = _make_prompts_dir(tmp_path)
        composer = PromptComposer(d)
        composer.load_all(auto_repair=False)
        ctx = PromptContext(chat_mode="read")
        text = composer.compose_strategies_text(ctx)
        assert text == ""

    def test_priority_ordering(self, tmp_path: Path) -> None:
        d = _make_prompts_dir(tmp_path)
        composer = PromptComposer(d)
        composer.load_all(auto_repair=False)
        ctx = PromptContext(chat_mode="write")
        segments = composer.compose(ctx)
        priorities = [s.priority for s in segments]
        assert priorities == sorted(priorities)


class TestPromptComposerBudget:
    def test_budget_drops_low_priority_first(self, tmp_path: Path) -> None:
        d = _make_prompts_dir(tmp_path)
        composer = PromptComposer(d)
        composer.load_all(auto_repair=False)
        ctx = PromptContext(chat_mode="write")
        # 非常小的 budget 应该丢弃策略段但保留 core
        segments = composer.compose(ctx, token_budget=10, include_strategies=True)
        names = [s.name for s in segments]
        assert "id" in names  # priority=0, 永不丢弃
        # 策略段应该被丢弃
        assert "cross_sheet" not in names

    def test_large_budget_keeps_all(self, tmp_path: Path) -> None:
        d = _make_prompts_dir(tmp_path)
        composer = PromptComposer(d)
        composer.load_all(auto_repair=False)
        ctx = PromptContext(chat_mode="write", full_access=True)
        segments = composer.compose(ctx, token_budget=999999, include_strategies=True)
        assert len(segments) == 4  # 2 core + 2 strategies


class TestMatchConditions:
    def test_empty_conditions_always_match(self) -> None:
        assert PromptComposer._match_conditions({}, PromptContext()) is True

    def test_chat_mode_match(self) -> None:
        ctx = PromptContext(chat_mode="write")
        assert PromptComposer._match_conditions({"chat_mode": "write"}, ctx)
        assert not PromptComposer._match_conditions({"chat_mode": "read"}, ctx)

    def test_unknown_condition_does_not_match(self) -> None:
        ctx = PromptContext(chat_mode="write")
        assert not PromptComposer._match_conditions({"sheet_count_gte": 2}, ctx)
        assert not PromptComposer._match_conditions({"total_rows_gte": 100}, ctx)
        assert not PromptComposer._match_conditions({"task_tags": ["chart"]}, ctx)

    def test_combined_conditions_and_logic(self) -> None:
        ctx = PromptContext(chat_mode="write", full_access=True)
        assert PromptComposer._match_conditions(
            {"chat_mode": "write", "full_access": True}, ctx,
        )
        assert not PromptComposer._match_conditions(
            {"chat_mode": "write", "full_access": False}, ctx,
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


class TestAlwaysOnStrategy:
    """always_on 合并策略文件加载与无条件注入测试。"""

    @staticmethod
    def _make_dir_with_always_on(tmp_path: Path) -> Path:
        core = tmp_path / "core"
        core.mkdir()
        (core / "00_id.md").write_text(
            '---\nname: id\nversion: "1.0"\npriority: 0\nlayer: core\n---\n身份。',
            encoding="utf-8",
        )
        strats = tmp_path / "strategies"
        strats.mkdir()
        (strats / "20_always_on.md").write_text(
            '---\nname: always_on\nversion: "1.0.0"\npriority: 15\nlayer: strategy\n'
            'conditions: {}\n---\n沙箱安全机制内容。',
            encoding="utf-8",
        )
        return tmp_path

    def test_always_on_included_when_full_access_off(self, tmp_path: Path) -> None:
        d = self._make_dir_with_always_on(tmp_path)
        composer = PromptComposer(d)
        composer.load_all(auto_repair=False)
        ctx = PromptContext(full_access=False)
        text = composer.compose_strategies_text(ctx)
        assert "沙箱安全机制内容。" in text

    def test_always_on_included_when_full_access_on(self, tmp_path: Path) -> None:
        d = self._make_dir_with_always_on(tmp_path)
        composer = PromptComposer(d)
        composer.load_all(auto_repair=False)
        ctx = PromptContext(full_access=True)
        text = composer.compose_strategies_text(ctx)
        assert "沙箱安全机制内容。" in text

    def test_real_always_on_file(self) -> None:
        """验证实际 prompts/strategies/20_always_on.md 可正确加载并含沙盒约束。"""
        prompts_dir = Path(__file__).resolve().parent.parent / "excelmanus" / "prompts"
        strat_file = prompts_dir / "strategies" / "20_always_on.md"
        if not strat_file.exists():
            pytest.skip("20_always_on.md 不存在")
        seg = parse_prompt_file(strat_file)
        assert seg.name == "spreadsheet:workflow"
        assert seg.conditions == {}
        assert "inspect_spreadsheet" in seg.content
        assert "edit_spreadsheet" in seg.content
        assert "WorkbookSpec" in seg.content


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


class TestErrorRecoveryInAlwaysOn:
    """合并后的 always_on 策略含错误恢复硬约束。"""

    def test_always_on_contains_error_recovery(self) -> None:
        prompts_dir = Path(__file__).resolve().parent.parent / "excelmanus" / "prompts"
        strat_file = prompts_dir / "strategies" / "20_always_on.md"
        if not strat_file.exists():
            pytest.skip("20_always_on.md 不存在")
        seg = parse_prompt_file(strat_file)
        assert "inspect_spreadsheet" in seg.content
        assert "edit_spreadsheet" in seg.content
        assert "WorkbookSpec" in seg.content

    def test_always_on_included_unconditionally(self, tmp_path: Path) -> None:
        core = tmp_path / "core"
        core.mkdir()
        (core / "00_id.md").write_text(
            '---\nname: id\nversion: "1.0"\npriority: 0\nlayer: core\n---\n身份。',
            encoding="utf-8",
        )
        strats = tmp_path / "strategies"
        strats.mkdir()
        (strats / "20_always_on.md").write_text(
            '---\nname: always_on\nversion: "1.0.0"\npriority: 15\nlayer: strategy\n'
            'conditions: {}\n---\n错误恢复策略内容。',
            encoding="utf-8",
        )
        composer = PromptComposer(tmp_path)
        composer.load_all(auto_repair=False)
        for mode in ("read", "write", "plan"):
            ctx = PromptContext(chat_mode=mode)
            text = composer.compose_strategies_text(ctx)
            assert "错误恢复策略内容。" in text, f"chat_mode={mode} 时未注入 always_on"


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
        (strats / "20_always_on.md").write_text(
            '---\nname: always_on\nversion: "1.0"\npriority: 15\nlayer: strategy\n'
            'conditions: {}\n---\n默认约束内容。',
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
        assert "默认约束内容。" not in result

    def test_explicit_strategy_names(self, tmp_path: Path) -> None:
        d = self._make_full_dir(tmp_path)
        composer = PromptComposer(d)
        composer.load_all(auto_repair=False)
        result = composer.compose_for_subagent(
            "explorer", inherit_strategies=["always_on"]
        )
        assert result is not None
        assert "默认约束内容。" in result
        assert "run_code 模板。" not in result  # 未指定，不应包含

    def test_universal_inherits_unconditional_only(self, tmp_path: Path) -> None:
        d = self._make_full_dir(tmp_path)
        composer = PromptComposer(d)
        composer.load_all(auto_repair=False)
        result = composer.compose_for_subagent(
            "worker", inherit_strategies=["__universal__"]
        )
        assert result is not None
        assert "默认约束内容。" in result  # conditions: {}
        assert "run_code 模板。" not in result  # has conditions → excluded

    def test_all_inherits_everything(self, tmp_path: Path) -> None:
        d = self._make_full_dir(tmp_path)
        composer = PromptComposer(d)
        composer.load_all(auto_repair=False)
        result = composer.compose_for_subagent(
            "worker", inherit_strategies=["__all__"]
        )
        assert result is not None
        assert "默认约束内容。" in result
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
        assert "默认约束内容。" in result
        assert "run_code 模板。" in result  # explicitly named

    def test_inherited_strategies_sorted_by_priority(self, tmp_path: Path) -> None:
        d = self._make_full_dir(tmp_path)
        composer = PromptComposer(d)
        composer.load_all(auto_repair=False)
        result = composer.compose_for_subagent(
            "worker", inherit_strategies=["__all__"]
        )
        assert result is not None
        always_on_pos = result.index("默认约束内容。")
        run_code_pos = result.index("run_code 模板。")
        assert always_on_pos < run_code_pos

    def test_empty_inherit_strategies_list(self, tmp_path: Path) -> None:
        d = self._make_full_dir(tmp_path)
        composer = PromptComposer(d)
        composer.load_all(auto_repair=False)
        result = composer.compose_for_subagent("explorer", inherit_strategies=[])
        assert result is not None
        assert "默认约束内容。" not in result

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
        result = composer.compose_for_subagent(
            "explorer", inherit_strategies=["spreadsheet:workflow"]
        )
        assert result is not None
        assert "继承策略" in result
        assert "inspect_spreadsheet" in result
        assert "WorkbookSpec" in result


class TestPromptArchitectureNoTagStrategies:
    """全局段无条件注入；不再按 task_tags 补偿规划/复刻策略。"""

    def test_plan_tags_do_not_inject_legacy_plan_sections(self) -> None:
        prompts_dir = Path(__file__).resolve().parent.parent / "excelmanus" / "prompts"
        if not prompts_dir.is_dir():
            pytest.skip("prompts/ 目录不存在")

        composer = PromptComposer(prompts_dir)
        composer.load_all()

        worthy_text = composer.compose_strategies_text(
            PromptContext(chat_mode="plan")
        )
        not_needed_text = composer.compose_strategies_text(
            PromptContext(chat_mode="plan")
        )
        write_text = composer.compose_strategies_text(PromptContext(chat_mode="write"))
        assert "## Spreadsheet agent" in worthy_text
        assert "## Spreadsheet agent" in not_needed_text
        assert "## Plan mode" in worthy_text
        assert "## Plan mode" in not_needed_text
        assert "## Plan mode" not in write_text
        assert "## 规划模式策略" not in worthy_text
        assert "## 规划模式轻量分流" not in not_needed_text
        assert worthy_text == not_needed_text

    def test_workflow_and_run_code_are_unconditional(self) -> None:
        prompts_dir = Path(__file__).resolve().parent.parent / "excelmanus" / "prompts"
        if not prompts_dir.is_dir():
            pytest.skip("prompts/ 目录不存在")

        composer = PromptComposer(prompts_dir)
        composer.load_all()
        text = composer.compose_strategies_text(PromptContext(chat_mode="plan"))
        assert "inspect_spreadsheet" in text
        assert "Code Mode" in text
        assert "## Plan mode" in text
        assert "快速模式" not in text

    def test_plan_policy_segment_order(self) -> None:
        prompts_dir = Path(__file__).resolve().parent.parent / "excelmanus" / "prompts"
        if not prompts_dir.is_dir():
            pytest.skip("prompts/ 目录不存在")

        composer = PromptComposer(prompts_dir)
        composer.load_all()
        names = {seg.name: seg.order for seg in composer.strategy_segments}
        assert names["plan:policy"] == 50
        assert names["spreadsheet:workflow"] == 125
        assert names["tool:run_code"] == 150


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
        ctx = PromptContext()
        text = composer.compose_text(ctx, variables={"workspace_root": "/data/user1"})
        assert "/data/user1" in text
        assert "{workspace_root}" not in text

    def test_compose_text_without_variables_keeps_placeholder(self, tmp_path: Path) -> None:
        d = self._make_dir_with_placeholders(tmp_path)
        composer = PromptComposer(d)
        composer.load_all(auto_repair=False)
        ctx = PromptContext()
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
        assert PromptComposer._substitute("root {{workspace_root}}", {"workspace_root": "/ws"}) == "root /ws"
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
        core_text = composer.compose_core_text(ctx, variables=variables)
        assert "{workspace_root}" not in core_text
        assert "{auto_generated_capability_map}" not in core_text
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
        ctx = PromptContext()
        core_text = composer.compose_core_text(ctx)
        assert core_text == _DEFAULT_SYSTEM_PROMPT, (
            "core/ 文件拼接结果与 _DEFAULT_SYSTEM_PROMPT 不一致！\n"
            f"长度: core={len(core_text)} vs legacy={len(_DEFAULT_SYSTEM_PROMPT)}"
        )
        # compose_text 默认不含策略，避免与 context_builder 双注入
        full_default = composer.compose_text(ctx)
        assert full_default == core_text
        strat_only = composer.compose_strategies_text(ctx)
        if strat_only:
            assert strat_only not in full_default
