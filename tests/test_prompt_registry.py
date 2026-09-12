"""提示词注册表、正文快照、KV 前缀、plan 目录。"""

from __future__ import annotations

from pathlib import Path

import pytest

from excelmanus.prompt.canonical import (
    FORBIDDEN_MODEL_TERMS,
    IDENTITY,
    PERSONA,
    PLAN_POLICY,
    RUN_CODE_SECTION,
    TOOL_ANALYZE,
    TOOL_DESCRIPTIONS,
    TOOL_EDIT,
    TOOL_FORMAT,
    TOOL_INSPECT,
    TOOLS_CODE_ONLY,
    WORKBOOK_SPEC_CONTRACT,
)
from excelmanus.prompt.registry import PromptRegistry, UnknownPromptVariable, interpolate
from excelmanus.prompt.load import PromptComposer, PromptContext, parse_prompt_file
from excelmanus.tools.intent_tools import get_tools as get_intent_tools
from excelmanus.tools.plan_tools import exit_plan_mode


PROMPTS_DIR = Path(__file__).resolve().parent.parent / "excelmanus" / "prompts"
_VARS = {"workspace_root": "/tmp/excelmanus-ws", "model": "test-model"}


def _composer() -> PromptComposer:
    composer = PromptComposer(PROMPTS_DIR)
    composer.load_all()
    return composer


def _fill(text: str) -> str:
    return interpolate(text, _VARS, strict=True)


class TestCanonicalMarkdown:
    def test_md_matches_canonical_sections(self) -> None:
        mapping = {
            PROMPTS_DIR / "core" / "00_identity.md": IDENTITY,
            PROMPTS_DIR / "core" / "10_core_principles.md": PERSONA,
            PROMPTS_DIR / "strategies" / "15_plan_policy.md": PLAN_POLICY,
            PROMPTS_DIR / "strategies" / "16_inspect.md": TOOL_INSPECT,
            PROMPTS_DIR / "strategies" / "17_analyze.md": TOOL_ANALYZE,
            PROMPTS_DIR / "strategies" / "19_edit.md": TOOL_EDIT,
            PROMPTS_DIR / "strategies" / "21_format.md": TOOL_FORMAT,
            PROMPTS_DIR / "strategies" / "18_workbook_spec.md": WORKBOOK_SPEC_CONTRACT,
            PROMPTS_DIR / "strategies" / "35_run_code_patterns.md": RUN_CODE_SECTION,
        }
        for path, expected in mapping.items():
            seg = parse_prompt_file(path)
            assert seg.content == expected, path.name
        assert not (PROMPTS_DIR / "strategies" / "20_always_on.md").exists()


class TestSystemAssembly:
    def test_write_prefix_is_identity_persona_tool_sections(self) -> None:
        text = _composer().compose_system_text(PromptContext(chat_mode="write"), variables=_VARS)
        persona = _fill(PERSONA)
        assert text.startswith(IDENTITY)
        assert persona in text
        assert TOOL_INSPECT in text
        assert TOOL_ANALYZE in text
        assert TOOL_EDIT in text
        assert TOOL_FORMAT in text
        assert WORKBOOK_SPEC_CONTRACT in text
        assert RUN_CODE_SECTION in text
        assert PLAN_POLICY not in text
        assert TOOLS_CODE_ONLY not in text
        assert "inspect_spreadsheet" not in text
        parts = [
            IDENTITY,
            persona,
            TOOL_INSPECT,
            TOOL_ANALYZE,
            TOOL_EDIT,
            TOOL_FORMAT,
            WORKBOOK_SPEC_CONTRACT,
            RUN_CODE_SECTION,
        ]
        assert text == "\n\n".join(parts)
        assert "finish_task" not in text
        assert "没有轮次上限" in text
        assert "宿主有轮次上限" not in text
        assert "finish_task" not in TOOL_DESCRIPTIONS
        assert "activate_skill" not in TOOL_DESCRIPTIONS
        from excelmanus.memory import _DEFAULT_SYSTEM_PROMPT
        from excelmanus.prompt.load import PromptComposer

        assert "VERSION_CONFLICT" in _DEFAULT_SYSTEM_PROMPT
        assert not hasattr(PromptComposer, "compose_core_text")
        assert "固定步骤" not in text
        assert "先做" not in text
        assert "必须先" not in text
        assert "默认交能用的活表" not in text
        assert "先再读再 rebase" not in text
        assert "收口前最后一次" not in text
        assert "生疏工作簿先做" not in text
        assert "已有用户产物却还在探查" not in text

    def test_plan_prefix_inserts_policy_at_order_50(self) -> None:
        text = _composer().compose_system_text(PromptContext(chat_mode="plan"), variables=_VARS)
        persona = _fill(PERSONA)
        joined = "\n\n".join(
            [
                IDENTITY,
                persona,
                PLAN_POLICY,
                TOOL_INSPECT,
                TOOL_ANALYZE,
                TOOL_EDIT,
                TOOL_FORMAT,
                WORKBOOK_SPEC_CONTRACT,
                RUN_CODE_SECTION,
            ]
        )
        assert text == joined
        assert "先做" not in text
        assert "必须先" not in text
        assert "默认交能用的活表" not in text
        assert "先再读再 rebase" not in text

    def test_code_present_as_inserts_code_only(self) -> None:
        text = _composer().compose_system_text(
            PromptContext(chat_mode="write"),
            variables=_VARS,
            present_as="code",
        )
        assert TOOLS_CODE_ONLY in text
        persona = _fill(PERSONA)
        joined = "\n\n".join(
            [
                IDENTITY,
                persona,
                TOOLS_CODE_ONLY,
                TOOL_INSPECT,
                TOOL_ANALYZE,
                TOOL_EDIT,
                TOOL_FORMAT,
                WORKBOOK_SPEC_CONTRACT,
                RUN_CODE_SECTION,
            ]
        )
        assert text == joined

    def test_code_present_as_appends_generated_sdk_section(self) -> None:
        sdk = "- inspect_spreadsheet(file_path)\n- edit_spreadsheet(file_path, content_version)"
        text = _composer().compose_system_text(
            PromptContext(chat_mode="write"),
            variables=_VARS,
            present_as="code",
            sdk_section=sdk,
        )
        assert TOOLS_CODE_ONLY in text
        assert sdk in text
        assert text.endswith(sdk)

    def test_missing_workspace_root_fails_loud(self) -> None:
        with pytest.raises(UnknownPromptVariable) as exc:
            _composer().compose_system_text(PromptContext(), variables={})
        assert exc.value.name == "workspace_root"


class TestForbiddenTerms:
    def test_system_and_descriptions_have_no_forbidden_terms(self) -> None:
        text = _composer().compose_system_text(PromptContext(chat_mode="plan"), variables=_VARS)
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
        engine._effective_system_mode.return_value = "multi"
        engine.state.prompt_injection_snapshots = []
        engine.state.injected_context_fingerprint = None
        engine._task_store.current = None
        engine.state.last_iteration_count = 0
        engine._current_chat_mode = "write"
        engine._runtime_vars = dict(_VARS)
        engine._last_route_result = None
        engine._present_as = "native"
        engine.config.workspace_root = _VARS["workspace_root"]
        engine.active_model = _VARS["model"]

        route = MagicMock()
        route.route_mode = "all_tools"
        route.system_contexts = []

        first, err1 = prepare_system_prompts_for_request(engine, [])
        second, err2 = prepare_system_prompts_for_request(engine, [])
        assert err1 is None and err2 is None
        assert first[0] == second[0]
        assert "文件全景" not in first[0]
        assert TOOL_INSPECT in first[0]
        assert WORKBOOK_SPEC_CONTRACT in first[0]
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
        engine._effective_system_mode.return_value = "multi"
        engine.state.prompt_injection_snapshots = []
        engine.state.injected_context_fingerprint = None
        engine._task_store.current = None
        engine.state.last_iteration_count = 0
        engine._current_chat_mode = "write"
        engine._runtime_vars = dict(_VARS)
        engine._last_route_result = None
        engine._present_as = "native"
        engine._tool_runtime = None
        engine.config.workspace_root = _VARS["workspace_root"]
        engine.active_model = _VARS["model"]

        prompts, err = prepare_system_prompts_for_request(
            engine, ["[Skillpack] data_basic\n描述：测试"]
        )
        assert err is None
        assert len(prompts) == 3
        assert engine._prompt_user_contexts == [
            "## Hook 上下文\nhook-notice",
            "[Skillpack] data_basic\n描述：测试",
        ]
        assert prompts[0] not in engine._prompt_user_contexts


class TestRegistryToolsSnapshot:
    def test_code_present_as_collapses_bound_tools(self) -> None:
        from excelmanus.prompt.registry import AssembleContext

        composer = _composer()
        composer.registry.tools(
            lambda: [
                {"function": {"name": "edit_spreadsheet"}},
                {"function": {"name": "run_code"}},
            ]
        )
        native = composer.registry.assemble(AssembleContext(present_as="native"))
        assert {s["function"]["name"] for s in native.tools} == {
            "edit_spreadsheet",
            "run_code",
        }
        from excelmanus.tools.runtime import collapse_schemas

        code_tools = collapse_schemas(native.tools, "code")
        assert {s["function"]["name"] for s in code_tools} == {"run_code"}
