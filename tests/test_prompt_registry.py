"""提示词注册表、正文快照、KV 前缀、plan 目录。"""

from __future__ import annotations

from pathlib import Path

import pytest

from excelmanus.prompt.canonical import (
    FORBIDDEN_MODEL_TERMS,
    TOOL_DESCRIPTIONS,
)
from excelmanus.prompt.registry import PromptRegistry, UnknownPromptVariable, interpolate
from excelmanus.prompt.load import PromptComposer, PromptContext, parse_prompt_file
from excelmanus.tools.intent_tools import get_tools as get_intent_tools
from excelmanus.tools.plan_tools import exit_plan_mode
from tests.prompt_support import (
    PLAN_SECTION_NAMES,
    PROMPTS_DIR,
    VARS,
    WRITE_SECTION_NAMES,
    composer as _composer,
    filled,
    read_snapshot,
    section_body,
    system_text,
)

_VARS = VARS


def _fill(text: str) -> str:
    return interpolate(text, _VARS, strict=True)


class TestCanonicalMarkdown:
    @pytest.mark.parametrize("condition", ["catalog_mode: code", "chat_mode: unknown", "present_as: code"])
    def test_removed_or_invalid_mode_conditions_fail_loud(self, tmp_path: Path, condition: str) -> None:
        path = tmp_path / "invalid.md"
        path.write_text(
            "---\nname: invalid-mode\npriority: 1\nlayer: strategy\n"
            f"conditions:\n  {condition}\n---\n正文。\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="conditions 含未知键|必须是 read/plan/write"):
            parse_prompt_file(path)

    def test_prompt_files_have_required_frontmatter(self) -> None:
        required = [
            PROMPTS_DIR / "core" / "00_identity.md",
            PROMPTS_DIR / "core" / "10_core_principles.md",
            PROMPTS_DIR / "core" / "20_spreadsheet_principles.md",
            PROMPTS_DIR / "strategies" / "15_plan_policy.md",
            PROMPTS_DIR / "strategies" / "16_inspect.md",
            PROMPTS_DIR / "strategies" / "17_analyze.md",
            PROMPTS_DIR / "strategies" / "19_edit.md",
            PROMPTS_DIR / "strategies" / "21_format.md",
            PROMPTS_DIR / "strategies" / "22_split.md",
            PROMPTS_DIR / "strategies" / "18_workbook_spec.md",
            PROMPTS_DIR / "strategies" / "35_run_code_patterns.md",
        ]
        for path in required:
            seg = parse_prompt_file(path)
            assert seg.content.strip(), path.name
            assert seg.max_tokens > 0, path.name
        assert not (PROMPTS_DIR / "strategies" / "20_always_on.md").exists()
        identity = section_body("core/00_identity.md")
        assert "ExcelManus" in identity
        assert "inspect_spreadsheet" not in section_body("strategies/16_inspect.md")
        from importlib.resources import files

        packaged = files("excelmanus") / "prompts" / "core" / "00_identity.md"
        assert packaged.is_file()


class TestSystemAssembly:
    def test_write_prefix_matches_snapshot_and_section_order(self) -> None:
        text = system_text("write")
        assert text == read_snapshot("write.txt").rstrip("\n")
        assert text.startswith(section_body("core/00_identity.md"))
        assert filled(section_body("core/10_core_principles.md")) in text
        assert "spreadsheet:invariants" not in text
        assert "结论以实际读取的数据为依据" in text
        assert "VERSION_CONFLICT" in text
        assert "当前是计划模式" not in text
        assert "唯一可以直接调用" not in text
        assert "import em" in text
        from excelmanus.prompt.registry import AssembleContext

        names = [
            sec.name
            for sec in _composer().registry.assemble(
                AssembleContext(chat_mode="write", variables=_VARS)
            ).sections
        ]
        assert names == list(WRITE_SECTION_NAMES)
        assert "finish_task" not in text
        assert "宿主有轮次上限" not in text
        assert "finish_task" not in TOOL_DESCRIPTIONS
        assert "activate_skill" not in TOOL_DESCRIPTIONS
        from excelmanus.memory import _DEFAULT_SYSTEM_PROMPT
        from excelmanus.prompt.load import PromptComposer

        assert "VERSION_CONFLICT" in _DEFAULT_SYSTEM_PROMPT
        assert not hasattr(PromptComposer, "compose_core_text")
        assert "固定步骤" not in text
        assert "必须先" not in text
        assert "默认交能用的活表" not in text
        assert "先再读再 rebase" not in text
        assert "收口前最后一次" not in text

    def test_plan_prefix_matches_snapshot(self) -> None:
        text = system_text("plan")
        assert text == read_snapshot("plan.txt").rstrip("\n")
        from excelmanus.prompt.registry import AssembleContext

        names = [
            sec.name
            for sec in _composer().registry.assemble(
                AssembleContext(chat_mode="plan", plan_active=True, variables=_VARS)
            ).sections
        ]
        assert names == list(PLAN_SECTION_NAMES)
        assert "VERSION_CONFLICT" not in text
        assert "当前是计划模式" in text
        assert "WorkbookSpec 经 edit_spreadsheet" not in text

    def test_read_prefix_matches_snapshot(self) -> None:
        text = system_text("read")
        assert text == read_snapshot("read.txt").rstrip("\n")
        assert "VERSION_CONFLICT" not in text

    def test_sdk_signatures_are_not_injected_into_system_prompt(self) -> None:
        text = _composer().compose_system_text(
            PromptContext(chat_mode="write"),
            variables=_VARS,
        )
        assert "def inspect_spreadsheet(" not in text
        assert "def edit_spreadsheet(" not in text
        assert "import em" in text
        assert "tool_detail" in text

    def test_missing_workspace_root_fails_loud(self) -> None:
        with pytest.raises(UnknownPromptVariable) as exc:
            _composer().compose_system_text(PromptContext(), variables={})
        assert exc.value.name == "workspace_root"


class TestForbiddenTerms:
    def test_system_and_descriptions_have_no_forbidden_terms(self) -> None:
        text = system_text("plan")
        blob = text + "\n" + "\n".join(TOOL_DESCRIPTIONS.values())
        for term in FORBIDDEN_MODEL_TERMS:
            assert term not in blob, term


class TestToolDescriptionSnapshots:
    def test_intent_descriptions_match_canonical(self) -> None:
        tools = {tool.name: tool.description for tool in get_intent_tools()}
        for name in (
            "inspect_spreadsheet",
            "analyze_spreadsheet",
            "compare_spreadsheets",
            "trace_spreadsheet_formulas",
            "edit_spreadsheet",
            "format_spreadsheet",
            "split_spreadsheet",
            "manage_spreadsheet_objects",
            "manage_spreadsheet_versions",
        ):
            assert tools[name] == TOOL_DESCRIPTIONS[name]


class TestExitPlanMode:
    def test_fails_outside_plan(self) -> None:
        result = exit_plan_mode("# 计划", is_plan_active=lambda: False)
        assert result.success is False
        assert result.error is not None
        assert result.error.code == "PLAN_INACTIVE"

    def test_accepts_inside_plan(self) -> None:
        result = exit_plan_mode("# 计划\n范围：全表", is_plan_active=lambda: True)
        assert result.success is True
        assert "呈交" in result.model_text


class TestPromptRegistry:
    def test_empty_section_dropped(self) -> None:
        registry = PromptRegistry()
        registry.section("keep", 0, "hello")
        registry.section("empty", 1, lambda ctx: "")
        text = registry.render_system(registry.assemble())
        assert text == "hello"

    def test_duplicate_section_raises(self) -> None:
        from excelmanus.prompt.registry import DuplicatePromptName

        registry = PromptRegistry()
        registry.section("a", 0, "x")
        with pytest.raises(DuplicatePromptName):
            registry.section("a", 1, "y")


class TestAssembleKv:
    def test_two_steps_same_prefix_when_files_would_change(self) -> None:
        from excelmanus.prompt.assemble import prepare_system_prompts_for_request
        from unittest.mock import MagicMock

        composer = _composer()
        engine = MagicMock()
        engine.memory.system_prompt = "unused"
        engine._session_turn = 1
        engine._prompt_composer = composer
        engine._transient_hook_contexts = []
        engine.full_access_enabled = False
        engine.max_context_tokens = 100000
        engine.state.prompt_injection_snapshots = []
        engine.state.injected_context_fingerprint = None
        engine._task_store.current = None
        engine.state.last_iteration_count = 0
        engine._current_chat_mode = "write"
        engine._runtime_vars = dict(_VARS)
        engine._last_route_result = None
        engine.config.workspace_root = _VARS["workspace_root"]
        engine.active_model = _VARS["model"]

        first, err1 = prepare_system_prompts_for_request(engine, [])
        second, err2 = prepare_system_prompts_for_request(engine, [])
        assert err1 is None and err2 is None
        assert first[0] == second[0]
        assert "文件全景" not in first[0]
        assert "结论以实际读取的数据为依据" in first[0]
        assert "WorkbookSpec 经 edit_spreadsheet" in first[0]
        assert len(first) == 1
        assert len(second) == 1

    def test_hooks_and_skills_are_user_role_contexts(self) -> None:
        from excelmanus.prompt.assemble import prepare_system_prompts_for_request
        from unittest.mock import MagicMock

        composer = _composer()
        engine = MagicMock()
        engine.memory.system_prompt = "unused"
        engine._session_turn = 1
        engine._prompt_composer = composer
        engine._transient_hook_contexts = ["hook-notice"]
        engine.full_access_enabled = False
        engine.max_context_tokens = 100000
        engine.state.prompt_injection_snapshots = []
        engine.state.injected_context_fingerprint = None
        engine._task_store.current = None
        engine.state.last_iteration_count = 0
        engine._current_chat_mode = "write"
        engine._runtime_vars = dict(_VARS)
        engine._last_route_result = None
        engine._tool_runtime = None
        engine.config.workspace_root = _VARS["workspace_root"]
        engine.active_model = _VARS["model"]

        prompts, err = prepare_system_prompts_for_request(
            engine, ["[Skillpack] data_basic\n描述：测试"]
        )
        assert err is None
        assert len(prompts) == 1
        assert engine._prompt_user_contexts == [
            "## Hook 上下文\nhook-notice",
            "[Skillpack] data_basic\n描述：测试",
        ]
        assert prompts[0] not in engine._prompt_user_contexts


class TestRegistryToolsSnapshot:
    def test_direct_and_programmatic_tools_remain_bound(self) -> None:
        from excelmanus.prompt.registry import AssembleContext

        composer = _composer()
        composer.registry.tools(
            lambda: [
                {"function": {"name": "edit_spreadsheet"}},
                {"function": {"name": "run_code"}},
            ]
        )
        assembly = composer.registry.assemble(AssembleContext())
        assert {s["function"]["name"] for s in assembly.tools} == {
            "edit_spreadsheet",
            "run_code",
        }

def test_system_uses_capability_map_not_tool_index() -> None:
    from excelmanus.prompt.assemble import build_stable_system_prompt
    from excelmanus.tools.catalog import derive_effective_catalog
    from excelmanus.tools.intent_tools import get_tools as get_intent_tools
    from excelmanus.tools.word_tools import get_tools as get_word_tools
    from excelmanus.tools.registry import ToolRegistry

    registry = ToolRegistry()
    registry.register_tools(get_intent_tools() + get_word_tools())
    catalog = derive_effective_catalog(
        tools=registry.get_all_tools(),
        mode="write",
        families=frozenset({"xlsx"}),
    )
    nav = catalog.capability_map_text()
    assert "## 能力地图" in nav
    assert "write_word" not in nav
    assert " — " not in nav
    long_word = next(
        (tool.description for tool in get_word_tools() if tool.name == "write_word"),
        "",
    )
    assert not long_word or long_word[:40] not in nav
    source = Path(__file__).resolve().parent.parent / "excelmanus" / "prompt" / "assemble.py"
    text = source.read_text(encoding="utf-8")
    assert "capability_map_text()" in text
    assert "catalog.tool_index_text()" not in text
    _ = build_stable_system_prompt
