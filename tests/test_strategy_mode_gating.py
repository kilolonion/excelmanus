"""strategy 段按目录模式（write / read / plan / code）门控。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from excelmanus.prompt.canonical import TOOLS_CODE_ONLY
from excelmanus.prompt.load import (
    PromptComposer,
    PromptContext,
    parse_prompt_file,
    strategy_conditions_match,
)
from excelmanus.prompt.registry import AssembleContext
from tests.prompt_support import (
    PLAN_SECTION_NAMES,
    PROMPTS_DIR,
    READ_SECTION_NAMES,
    VARS,
    WRITE_SECTION_NAMES,
    composer as _composer,
    system_text,
)

_VARS = VARS
_EDIT_MARKER = "VERSION_CONFLICT 表示这次没有落盘"
_SPEC_MARKER = "WorkbookSpec 经 edit_spreadsheet 的 workbook_spec 一次编译出新工作簿"
_FORMAT_MARKER = "一次性指定某格外观用直接 format"
_RUN_CODE_MARKER = "写入串行。stdout 不证明业务正确"
_INVARIANT_MARKER = "结论以实际读取的数据为依据"


def _system(mode: str, *, present_as: str = "native") -> str:
    return system_text(mode, present_as=present_as)


def _section_names(mode: str, *, present_as: str = "native") -> list[str]:
    assembly = _composer().registry.assemble(
        AssembleContext(
            chat_mode=mode,
            plan_active=mode == "plan",
            present_as=present_as,
            variables=_VARS,
        )
    )
    return [sec.name for sec in assembly.sections]


def test_edit_frontmatter_gates_write_and_code_catalog_modes() -> None:
    seg = parse_prompt_file(PROMPTS_DIR / "strategies" / "19_edit.md")
    assert seg.name == "tool:edit"
    modes = set(seg.conditions["catalog_mode"])
    assert modes == {"write", "code"}
    assert "read" not in modes
    assert "plan" not in modes
    assert _EDIT_MARKER in seg.content


def test_write_related_frontmatter_gates_write_and_code_catalog_modes() -> None:
    for rel, name, marker in (
        ("18_workbook_spec.md", "spreadsheet:workbook_spec", _SPEC_MARKER),
        ("21_format.md", "tool:format", _FORMAT_MARKER),
        ("35_run_code_patterns.md", "tool:run_code", _RUN_CODE_MARKER),
    ):
        seg = parse_prompt_file(PROMPTS_DIR / "strategies" / rel)
        assert seg.name == name
        modes = set(seg.conditions["catalog_mode"])
        assert modes == {"write", "code"}
        assert "read" not in modes
        assert "plan" not in modes
        assert marker in seg.content


def test_plan_policy_frontmatter_still_chat_mode_plan() -> None:
    seg = parse_prompt_file(PROMPTS_DIR / "strategies" / "15_plan_policy.md")
    assert seg.conditions["chat_mode"] == "plan"
    assert "catalog_mode" not in seg.conditions


def test_invariants_are_core_not_strategy() -> None:
    composer = _composer()
    core = {seg.name: seg.order for seg in composer.core_segments}
    strategy = {seg.name: seg.order for seg in composer.strategy_segments}
    assert core["spreadsheet:invariants"] == 50
    assert "spreadsheet:invariants" not in strategy


def test_read_system_omits_edit_strategy() -> None:
    text = _system("read")
    assert _EDIT_MARKER not in text
    assert "当前是计划模式" not in text
    assert _INVARIANT_MARKER in text
    assert _section_names("read") == list(READ_SECTION_NAMES)


def test_read_and_plan_system_omit_write_related_strategies() -> None:
    write_text = _system("write")
    for mode in ("read", "plan"):
        text = _system(mode)
        assert _FORMAT_MARKER not in text
        assert _SPEC_MARKER not in text
        assert _RUN_CODE_MARKER not in text
        assert "overview" in text
    assert _FORMAT_MARKER in write_text
    assert _SPEC_MARKER in write_text
    assert _RUN_CODE_MARKER in write_text
    assert _section_names("write") == list(WRITE_SECTION_NAMES)


def test_plan_system_omits_edit_but_keeps_plan_policy() -> None:
    text = _system("plan")
    assert _EDIT_MARKER not in text
    assert "当前是计划模式" in text
    assert _section_names("plan") == list(PLAN_SECTION_NAMES)


def test_write_system_keeps_edit_strategy() -> None:
    text = _system("write")
    assert _EDIT_MARKER in text
    assert "当前是计划模式" not in text
    assert TOOLS_CODE_ONLY not in text


def test_code_catalog_mode_keeps_edit_strategy() -> None:
    text = _system("write", present_as="code")
    assert _EDIT_MARKER in text
    assert TOOLS_CODE_ONLY in text


def test_code_presented_prompt_restores_strategies_via_execution_catalog(
    tmp_path: Path,
) -> None:
    """生产路径：catalog_from_engine → build_stable_system_prompt。

    code wire 坍缩为 run_code-only，但策略段 / 能力地图 / SDK 声明必须
    按执行目录可达集门控——不设 visible_tools 的组装路径覆盖不到这条。
    """
    from types import SimpleNamespace

    from excelmanus.code_mode import render_sdk_section
    from excelmanus.prompt.assemble import build_stable_system_prompt
    from excelmanus.prompt.budget import _make_engine, _prepare_workspace
    from excelmanus.tools.catalog import (
        catalog_from_engine,
        execution_catalog_from_engine,
    )
    from excelmanus.tools.runtime import collapse_schemas

    workspace = tmp_path / "ws"
    _prepare_workspace(workspace, profile="xlsx", has_workbook=True)
    engine = _make_engine(
        chat_mode="write",
        present_as="code",
        workspace=workspace,
        composer=_composer(),
    )
    catalog = catalog_from_engine(engine)
    assert catalog is not None
    assert catalog.mode == "write"
    assert "inspect_spreadsheet" in catalog.name_set()
    wire = collapse_schemas(catalog.tool_schemas(), "code")
    wire_names = sorted(
        (s.get("function") or {}).get("name") or s.get("name") or ""
        for s in wire
    )
    assert wire_names == ["run_code"]

    exec_catalog = execution_catalog_from_engine(engine)
    assert exec_catalog is not None
    reachable = set(exec_catalog.names())
    assert {"inspect_spreadsheet", "edit_spreadsheet", "format_spreadsheet"} <= reachable
    assert "run_code" in reachable

    engine._tool_runtime = SimpleNamespace(
        render_sdk_section=lambda: render_sdk_section(list(exec_catalog.tools)),
    )
    text = build_stable_system_prompt(engine)
    assert _EDIT_MARKER in text
    assert _FORMAT_MARKER in text
    assert _SPEC_MARKER in text
    assert "edit_spreadsheet(" in text  # SDK 声明按执行目录绑定
    nav = exec_catalog.capability_map_text()
    assert "edit_spreadsheet" in nav


def test_code_presented_prompt_missing_strategy_when_visible_tools_wire_only(
    tmp_path: Path,
) -> None:
    """反向证据：若 visible_tools 退回 wire 目录，tool: 段会再次丢失。"""
    from excelmanus.prompt.load import strategy_conditions_match

    ctx = AssembleContext(
        chat_mode="write",
        present_as="code",
        visible_tools=frozenset({"run_code"}),
    )
    assert not strategy_conditions_match({"tool": ["edit_spreadsheet"]}, ctx)
    assert strategy_conditions_match(
        {"tool": ["edit_spreadsheet"]},
        AssembleContext(
            chat_mode="write",
            present_as="code",
            visible_tools=frozenset({"edit_spreadsheet", "run_code"}),
        ),
    )


def test_same_mode_system_bytes_are_stable() -> None:
    composer = _composer()
    for mode in ("write", "read", "plan"):
        a = composer.compose_system_text(PromptContext(chat_mode=mode), variables=_VARS)
        b = composer.compose_system_text(PromptContext(chat_mode=mode), variables=_VARS)
        assert a == b
        assert a.encode("utf-8") == b.encode("utf-8")


def test_plan_active_flag_still_enables_plan_policy() -> None:
    composer = _composer()
    assembly = composer.registry.assemble(
        AssembleContext(plan_active=True, chat_mode="write", variables=_VARS)
    )
    text = composer.registry.render_system(assembly)
    assert "当前是计划模式" in text
    assert _EDIT_MARKER in text


def test_gating_is_not_hardcoded_filenames() -> None:
    source = Path(__file__).resolve().parent.parent / "excelmanus" / "prompt" / "load.py"
    blob = source.read_text(encoding="utf-8")
    assert "19_edit.md" not in blob
    assert "tool:edit" not in blob
    assert 'seg.name == "plan:policy"' not in blob


def test_catalog_mode_matcher_and_list_or() -> None:
    ctx_write = AssembleContext(chat_mode="write", present_as="native")
    ctx_code = AssembleContext(chat_mode="write", present_as="code")
    ctx_read = AssembleContext(chat_mode="read")
    ctx_plan = AssembleContext(chat_mode="plan", plan_active=True)
    write_code = {"catalog_mode": ["write", "code"]}
    assert strategy_conditions_match(write_code, ctx_write)
    assert strategy_conditions_match(write_code, ctx_code)
    assert not strategy_conditions_match(write_code, ctx_read)
    assert not strategy_conditions_match(write_code, ctx_plan)
    assert strategy_conditions_match({"chat_mode": "plan"}, ctx_plan)
    assert not strategy_conditions_match({"chat_mode": "plan"}, ctx_write)
    assert strategy_conditions_match(
        {"chat_mode": "plan"},
        AssembleContext(plan_active=True, chat_mode="write"),
    )
    assert strategy_conditions_match({}, ctx_read)
    assert strategy_conditions_match({"base_sections": ["a"]}, ctx_read)


def _mock_engine(composer: PromptComposer, chat_mode: str) -> MagicMock:
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
    engine._current_chat_mode = chat_mode
    engine._runtime_vars = dict(_VARS)
    engine._last_route_result = None
    engine._present_as = "native"
    engine._tool_runtime = None
    engine.config.workspace_root = _VARS["workspace_root"]
    engine.active_model = _VARS["model"]
    engine._registry = None
    engine.registry = None
    engine._child_system_prompt = None
    return engine


def test_assemble_path_respects_mode_gating() -> None:
    from excelmanus.prompt.assemble import prepare_system_prompts_for_request

    composer = _composer()
    write_engine = _mock_engine(composer, "write")
    write_prompts, err_w = prepare_system_prompts_for_request(write_engine, [])
    assert err_w is None
    assert _EDIT_MARKER in write_prompts[0]
    assert write_prompts[0] == _system("write")

    read_engine = _mock_engine(composer, "read")
    read_prompts, err_r = prepare_system_prompts_for_request(read_engine, [])
    assert err_r is None
    assert _EDIT_MARKER not in read_prompts[0]

    plan_engine = _mock_engine(composer, "plan")
    plan_prompts, err_p = prepare_system_prompts_for_request(plan_engine, [])
    assert err_p is None
    assert "当前是计划模式" in plan_prompts[0]
    assert _EDIT_MARKER not in plan_prompts[0]
