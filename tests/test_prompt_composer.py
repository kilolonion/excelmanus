"""PromptComposer 单元测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from excelmanus.prompt.load import (
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
        text = composer.compose_system_text(ctx)
        assert "身份。" in text
        assert "规则。" in text
        assert "跨表策略。" not in text
        assert "公式策略。" not in text

    def test_compose_text_default_excludes_conditioned_strategies(self, tmp_path: Path) -> None:
        d = _make_prompts_dir(tmp_path)
        composer = PromptComposer(d)
        composer.load_all(auto_repair=False)
        ctx = PromptContext(chat_mode="write")
        text = composer.compose_system_text(ctx)
        assert "身份。" in text
        assert "公式策略。" in text
        assert "跨表策略。" not in text

    def test_conditional_strategies_are_skipped(self, tmp_path: Path) -> None:
        d = _make_prompts_dir(tmp_path)
        composer = PromptComposer(d)
        composer.load_all(auto_repair=False)
        ctx = PromptContext(chat_mode="read")
        text = composer.compose_system_text(ctx)
        assert "身份。" in text
        assert "公式策略。" not in text
        assert "跨表策略。" not in text

    def test_plan_policy_only_when_plan_active(self, tmp_path: Path) -> None:
        d = _make_prompts_dir(tmp_path)
        (d / "strategies" / "plan.md").write_text(
            '---\nname: plan:policy\nversion: "1.0"\npriority: 50\nlayer: strategy\n'
            'conditions:\n  chat_mode: "plan"\n---\n当前是计划模式。',
            encoding="utf-8",
        )
        composer = PromptComposer(d)
        composer.load_all(auto_repair=False)
        write_text = composer.compose_system_text(PromptContext(chat_mode="write"))
        plan_text = composer.compose_system_text(PromptContext(chat_mode="plan"))
        assert "当前是计划模式。" not in write_text
        assert "当前是计划模式。" in plan_text

    def test_compose_system_text_includes_core_for_read(self, tmp_path: Path) -> None:
        d = _make_prompts_dir(tmp_path)
        composer = PromptComposer(d)
        composer.load_all(auto_repair=False)
        ctx = PromptContext(chat_mode="read")
        text = composer.compose_system_text(ctx)
        assert "身份。" in text
        assert "当前是计划模式" not in text

    def test_core_segment_order(self, tmp_path: Path) -> None:
        d = _make_prompts_dir(tmp_path)
        composer = PromptComposer(d)
        composer.load_all(auto_repair=False)
        orders = [s.order for s in composer.core_segments]
        assert orders == sorted(orders)


class TestUnconditionalStrategy:
    """无条件策略段加载与注入测试。"""

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
        (strats / "16_inspect.md").write_text(
            '---\nname: tool:inspect\nversion: "1.0.0"\npriority: 100\nlayer: strategy\n'
            'conditions: {}\n---\n沙箱安全机制内容。',
            encoding="utf-8",
        )
        return tmp_path

    def test_always_on_included_when_full_access_off(self, tmp_path: Path) -> None:
        d = self._make_dir_with_always_on(tmp_path)
        composer = PromptComposer(d)
        composer.load_all(auto_repair=False)
        ctx = PromptContext()
        text = composer.compose_system_text(ctx)
        assert "沙箱安全机制内容。" in text

    def test_unconditional_included_regardless_of_mode(self, tmp_path: Path) -> None:
        d = self._make_dir_with_always_on(tmp_path)
        composer = PromptComposer(d)
        composer.load_all(auto_repair=False)
        ctx = PromptContext(chat_mode="plan")
        text = composer.compose_system_text(ctx)
        assert "沙箱安全机制内容。" in text

    def test_real_tool_and_spec_files(self) -> None:
        prompts_dir = Path(__file__).resolve().parent.parent / "excelmanus" / "prompts"
        inspect_file = prompts_dir / "strategies" / "16_inspect.md"
        spec_file = prompts_dir / "strategies" / "18_workbook_spec.md"
        edit_file = prompts_dir / "strategies" / "19_edit.md"
        if not inspect_file.exists():
            pytest.skip("16_inspect.md 不存在")
        inspect_seg = parse_prompt_file(inspect_file)
        assert inspect_seg.name == "tool:inspect"
        assert inspect_seg.conditions == {"tool": "inspect_spreadsheet"}
        assert "inspect_spreadsheet" not in inspect_seg.content
        spec_seg = parse_prompt_file(spec_file)
        assert "WorkbookSpec" in spec_seg.content
        edit_seg = parse_prompt_file(edit_file)
        assert "VERSION_CONFLICT" in edit_seg.content


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
            assert "忠于工具" in result, f"{name} 缺少共享约束"


class TestErrorRecoveryInUnconditional:
    """无条件策略含错误恢复硬约束。"""

    def test_edit_and_spec_contain_recovery(self) -> None:
        prompts_dir = Path(__file__).resolve().parent.parent / "excelmanus" / "prompts"
        inspect_file = prompts_dir / "strategies" / "16_inspect.md"
        if not inspect_file.exists():
            pytest.skip("16_inspect.md 不存在")
        inspect_seg = parse_prompt_file(inspect_file)
        assert "inspect_spreadsheet" not in inspect_seg.content
        spec_seg = parse_prompt_file(prompts_dir / "strategies" / "18_workbook_spec.md")
        assert "WorkbookSpec" in spec_seg.content
        edit_seg = parse_prompt_file(prompts_dir / "strategies" / "19_edit.md")
        assert "VERSION_CONFLICT" in edit_seg.content

    def test_unconditional_included(self, tmp_path: Path) -> None:
        core = tmp_path / "core"
        core.mkdir()
        (core / "00_id.md").write_text(
            '---\nname: id\nversion: "1.0"\npriority: 0\nlayer: core\n---\n身份。',
            encoding="utf-8",
        )
        strats = tmp_path / "strategies"
        strats.mkdir()
        (strats / "16_inspect.md").write_text(
            '---\nname: tool:inspect\nversion: "1.0.0"\npriority: 100\nlayer: strategy\n'
            'conditions: {}\n---\n错误恢复策略内容。',
            encoding="utf-8",
        )
        composer = PromptComposer(tmp_path)
        composer.load_all(auto_repair=False)
        for mode in ("read", "write", "plan"):
            ctx = PromptContext(chat_mode=mode)
            text = composer.compose_system_text(ctx)
            assert "错误恢复策略内容。" in text, f"chat_mode={mode} 时未注入无条件策略"


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
        (strats / "16_inspect.md").write_text(
            '---\nname: tool:inspect\nversion: "1.0"\npriority: 15\nlayer: strategy\n'
            'conditions: {}\n---\n默认约束内容。',
            encoding="utf-8",
        )
        (strats / "run_code_patterns.md").write_text(
            '---\nname: tool:run_code\nversion: "1.0"\npriority: 35\nlayer: strategy\n'
            'conditions: {}\n---\nrun_code 模板。',
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
            "explorer", inherit_strategies=["tool:inspect"]
        )
        assert result is not None
        assert "默认约束内容。" in result
        assert "run_code 模板。" not in result  # 未指定，不应包含

    def test_magic_tags_do_not_inherit(self, tmp_path: Path) -> None:
        d = self._make_full_dir(tmp_path)
        composer = PromptComposer(d)
        composer.load_all(auto_repair=False)
        result = composer.compose_for_subagent(
            "worker", inherit_strategies=["__universal__", "__all__"]
        )
        assert result is not None
        assert "默认约束内容。" not in result
        assert "run_code 模板。" not in result

    def test_explicit_named_sections(self, tmp_path: Path) -> None:
        d = self._make_full_dir(tmp_path)
        composer = PromptComposer(d)
        composer.load_all(auto_repair=False)
        result = composer.compose_for_subagent(
            "worker", inherit_strategies=["tool:inspect", "tool:run_code"]
        )
        assert result is not None
        assert "默认约束内容。" in result
        assert "run_code 模板。" in result

    def test_inherited_strategies_sorted_by_priority(self, tmp_path: Path) -> None:
        d = self._make_full_dir(tmp_path)
        composer = PromptComposer(d)
        composer.load_all(auto_repair=False)
        result = composer.compose_for_subagent(
            "worker", inherit_strategies=["tool:inspect", "tool:run_code"]
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
        result = composer.compose_for_subagent(
            "subagent",
            inherit_strategies=[
                "tool:inspect",
                "tool:analyze",
                "tool:edit",
                "tool:format",
                "spreadsheet:workbook_spec",
                "tool:run_code",
            ],
        )
        assert result is not None
        assert "WorkbookSpec" in result
        assert "写入串行" in result
        result = composer.compose_for_subagent(
            "explorer", inherit_strategies=["tool:inspect", "tool:analyze"]
        )
        assert result is not None
        assert "WorkbookSpec" not in result
        assert "overview" in result
        explorer_with_run = composer.compose_for_subagent(
            "explorer",
            inherit_strategies=["tool:inspect", "tool:analyze", "tool:run_code"],
        )
        assert explorer_with_run is not None
        assert "写入串行" in explorer_with_run
        full = composer.compose_for_subagent(
            "subagent",
            inherit_strategies=["spreadsheet:workbook_spec", "tool:edit"],
        )
        assert full is not None
        assert "WorkbookSpec" in full
        assert "VERSION_CONFLICT" in full


class TestPromptArchitectureNoTagStrategies:
    """全局段无条件注入；不再按 task_tags 补偿规划/复刻策略。"""

    def test_plan_tags_do_not_inject_legacy_plan_sections(self) -> None:
        prompts_dir = Path(__file__).resolve().parent.parent / "excelmanus" / "prompts"
        if not prompts_dir.is_dir():
            pytest.skip("prompts/ 目录不存在")

        composer = PromptComposer(prompts_dir)
        composer.load_all()

        worthy_text = composer.compose_system_text(
            PromptContext(chat_mode="plan")
        )
        not_needed_text = composer.compose_system_text(
            PromptContext(chat_mode="plan")
        )
        write_text = composer.compose_system_text(PromptContext(chat_mode="write"))
        assert "WorkbookSpec" not in worthy_text
        assert "WorkbookSpec" not in not_needed_text
        assert "WorkbookSpec" in write_text
        assert "当前是计划模式" in worthy_text
        assert "当前是计划模式" in not_needed_text
        assert "当前是计划模式" not in write_text
        assert "## 规划模式策略" not in worthy_text
        assert "## 规划模式轻量分流" not in not_needed_text
        assert worthy_text == not_needed_text

    def test_workflow_and_run_code_are_unconditional(self) -> None:
        prompts_dir = Path(__file__).resolve().parent.parent / "excelmanus" / "prompts"
        if not prompts_dir.is_dir():
            pytest.skip("prompts/ 目录不存在")

        composer = PromptComposer(prompts_dir)
        composer.load_all()
        plan_text = composer.compose_system_text(PromptContext(chat_mode="plan"))
        write_text = composer.compose_system_text(PromptContext(chat_mode="write"))
        assert "VERSION_CONFLICT" not in plan_text
        assert "写入串行" not in plan_text
        assert "WorkbookSpec" not in plan_text
        assert "当前是计划模式" in plan_text
        assert "快速模式" not in plan_text
        assert "VERSION_CONFLICT" in write_text
        assert "写入串行" in write_text
        assert "WorkbookSpec" in write_text

    def test_plan_policy_segment_order(self) -> None:
        prompts_dir = Path(__file__).resolve().parent.parent / "excelmanus" / "prompts"
        if not prompts_dir.is_dir():
            pytest.skip("prompts/ 目录不存在")

        composer = PromptComposer(prompts_dir)
        composer.load_all()
        names = {seg.name: seg.order for seg in composer.strategy_segments}
        assert names["plan:policy"] == 50
        assert names["tool:inspect"] == 100
        assert names["tool:analyze"] == 102
        assert names["tool:edit"] == 104
        assert names["tool:format"] == 106
        assert names["spreadsheet:workbook_spec"] == 110
        assert names["tool:run_code"] == 150
        core_names = {seg.name: seg.order for seg in composer.core_segments}
        assert core_names["spreadsheet:invariants"] == 50
        assert "spreadsheet:invariants" not in names


class TestVariableSubstitution:
    """变量替换机制测试。"""

    @staticmethod
    def _make_dir_with_placeholders(tmp_path: Path) -> Path:
        core = tmp_path / "core"
        core.mkdir()
        (core / "00_id.md").write_text(
            '---\nname: id\nversion: "1.0"\npriority: 0\nlayer: core\n---\n'
            '根目录：`{{workspace_root}}`。',
            encoding="utf-8",
        )
        strats = tmp_path / "strategies"
        strats.mkdir()
        (strats / "topo.md").write_text(
            '---\nname: topo\nversion: "1.0"\npriority: 15\nlayer: strategy\n'
            'conditions: {}\n---\n工作区 `{{workspace_root}}` 拓扑。',
            encoding="utf-8",
        )
        sa = tmp_path / "subagent"
        sa.mkdir()
        (sa / "_base.md").write_text(
            '---\nname: base\npriority: 0\nlayer: subagent\n---\n基础 {{workspace_root}}。',
            encoding="utf-8",
        )
        (sa / "worker.md").write_text(
            '---\nname: worker\npriority: 10\nlayer: subagent\n---\n工人 {{workspace_root}}。',
            encoding="utf-8",
        )
        return tmp_path

    def test_compose_text_substitutes_variables(self, tmp_path: Path) -> None:
        d = self._make_dir_with_placeholders(tmp_path)
        composer = PromptComposer(d)
        composer.load_all(auto_repair=False)
        ctx = PromptContext()
        text = composer.compose_system_text(ctx, variables={"workspace_root": "/data/user1"})
        assert "/data/user1" in text
        assert "{{workspace_root}}" not in text

    def test_compose_text_without_variables_keeps_placeholder(self, tmp_path: Path) -> None:
        d = self._make_dir_with_placeholders(tmp_path)
        composer = PromptComposer(d)
        composer.load_all(auto_repair=False)
        ctx = PromptContext()
        text = composer.compose_system_text(ctx)
        assert "{{workspace_root}}" in text

    def test_compose_system_text_substitutes_variables(self, tmp_path: Path) -> None:
        d = self._make_dir_with_placeholders(tmp_path)
        composer = PromptComposer(d)
        composer.load_all(auto_repair=False)
        ctx = PromptContext()
        text = composer.compose_system_text(ctx, variables={"workspace_root": "/ws"})
        assert "/ws" in text
        assert "{{workspace_root}}" not in text

    def test_compose_system_text_without_variables_keeps_placeholder(
        self, tmp_path: Path,
    ) -> None:
        d = self._make_dir_with_placeholders(tmp_path)
        composer = PromptComposer(d)
        composer.load_all(auto_repair=False)
        ctx = PromptContext()
        text = composer.compose_system_text(ctx)
        assert "{{workspace_root}}" in text

    def test_compose_for_subagent_substitutes_variables(self, tmp_path: Path) -> None:
        d = self._make_dir_with_placeholders(tmp_path)
        composer = PromptComposer(d)
        composer.load_all(auto_repair=False)
        result = composer.compose_for_subagent(
            "worker", variables={"workspace_root": "/agent/ws"},
        )
        assert result is not None
        assert "/agent/ws" in result
        assert "{{workspace_root}}" not in result

    def test_compose_for_subagent_with_inherited_strategies_substitutes(
        self, tmp_path: Path,
    ) -> None:
        d = self._make_dir_with_placeholders(tmp_path)
        composer = PromptComposer(d)
        composer.load_all(auto_repair=False)
        result = composer.compose_for_subagent(
            "worker",
            inherit_strategies=["topo"],
            variables={"workspace_root": "/sub"},
        )
        assert result is not None
        assert "{{workspace_root}}" not in result
        assert "/sub" in result

    def test_real_files_no_unresolved_workspace_root(self) -> None:
        """验证实际 .md 文件中 {{workspace_root}} 经替换后不残留。"""
        prompts_dir = Path(__file__).resolve().parent.parent / "excelmanus" / "prompts"
        if not prompts_dir.is_dir():
            pytest.skip("prompts/ 目录不存在")
        composer = PromptComposer(prompts_dir)
        composer.load_all()
        variables = {
            "workspace_root": "/test/workspace",
            "model": "test-model",
        }
        ctx = PromptContext()
        text = composer.compose_system_text(ctx, variables=variables)
        assert "{{workspace_root}}" not in text
        assert "{workspace_root}" not in text


class TestDefaultSystemMatchesComposer:
    def test_exact_match(self) -> None:
        from excelmanus.memory import _DEFAULT_SYSTEM_PROMPT

        prompts_dir = Path(__file__).resolve().parent.parent / "excelmanus" / "prompts"
        if not prompts_dir.is_dir():
            pytest.skip("prompts/ 目录不存在")
        composer = PromptComposer(prompts_dir)
        composer.load_all()
        if not composer.core_segments:
            pytest.skip("无 core 段可加载")
        ctx = PromptContext(chat_mode="write")
        text = composer.compose_system_text(ctx)
        assert text == _DEFAULT_SYSTEM_PROMPT
        assert "VERSION_CONFLICT" in text
        assert not hasattr(composer, "compose_core_text")
