"""Regression cases for the static Jev audit. All evaluators are replaced locally."""

from __future__ import annotations

import json
from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

import pytest

from excelmanus.compaction import _apply_tool_result_pruning
from excelmanus.engine_core.spill import SpillStore
from excelmanus.engine_core.tool_result import ToolResult
from excelmanus.system_one.adapter import bound_state, mutation_verify_state_from_engine, ui_surface_state_from_engine
from excelmanus.system_one.budget import JevTurnBudget
from excelmanus.system_one.evidence import loop_state, rank_skills
from excelmanus.system_one.host import (
    _apply_observation_shape, maybe_record_turn_exposure, maybe_verify_mutation, should_check_delivery,
)
from excelmanus.system_one.policy import JevSettings, synthesize
from excelmanus.system_one.sheet_advice import read_suggestion
from excelmanus.system_one.trace import build_jev_trace_payload
from excelmanus.system_one.types import ChoiceAnswer, Decision, Evaluation, NoulAnswer


def settings(**changes):
    base = JevSettings(
        enabled="enforce", exposure="enforce", observation="enforce",
        mode_hint=False, ui_hint=True, model="test", api_key="vck_test", timeout_seconds=1.0,
    )
    return replace(base, **changes)


def engine(tmp_path):
    memory = NS(messages=[{"role": "user", "content": "核对销售表"}])
    return NS(
        config=NS(workspace_root=str(tmp_path)),
        _workspace_ref=NS(root=str(tmp_path), workspace_id="workspace-a"),
        _is_host_session=True, _subagent_config=None, _driver=None,
        _memory=memory, memory=memory, _active_skills=[],
        _state=NS(affected_files=[], write_operations_log=[]),
        _question_flow=NS(has_pending=lambda: False),
        has_pending_approval=lambda: False,
    )


@pytest.fixture(autouse=True)
def no_remote_evaluations():
    with patch("excelmanus.system_one.evaluate", AsyncMock(side_effect=AssertionError("remote evaluation forbidden"))):
        yield


@pytest.mark.parametrize("shape", ["truncate", "pointer"])
def test_shape_has_immutable_retrieval_and_preserves_native_value(tmp_path, shape):
    original = ToolResult(success=True, model_text="旧结果" * 5000, value={"rows": [1, 2]})
    shaped = _apply_observation_shape(
        original, shape, tool_name="observe_spreadsheet",
        arguments={"file_path": "a.xlsx"}, engine=engine(tmp_path),
    )
    assert shaped.value is original.value
    assert shaped.ui_meta is original.ui_meta
    assert len(shaped.model_text) < len(original.model_text)
    assert "read_text_file(file_path=" in shaped.model_text
    assert SpillStore(tmp_path).get(shaped.coverage["spill_locator"]) == original.model_text


@pytest.mark.asyncio
async def test_semantic_keep_survives_mechanical_pruning(tmp_path):
    e = engine(tmp_path)
    messages = []
    for index in range(6):
        messages += [
            {"role": "assistant", "tool_calls": [{
                "id": str(index), "function": {"name": "observe_spreadsheet", "arguments": json.dumps({"file_path": "a.xlsx"})},
            }]},
            {"role": "tool", "tool_call_id": str(index), "content": "data" * 3000},
        ]
    memory = NS(messages=messages)
    keep = Decision(kind="noop", reason="relevant", extras={"prune": False}, applied=True)
    with (
        patch("excelmanus.system_one.host.live_jev_settings", return_value=settings()),
        patch("excelmanus.system_one.host._eval_traced", AsyncMock(return_value=keep)),
    ):
        assert await _apply_tool_result_pruning(e, memory) == 0
    assert all(len(m["content"]) == 12000 for m in messages if m["role"] == "tool")


def test_skill_recall_reaches_end_of_catalog():
    catalog = [(f"a{i}", "unrelated") for i in range(20)] + [("z_merge", "合并多个工作簿")]
    assert rank_skills("请合并多个工作簿", catalog)[0][0] == "z_merge"
    assert rank_skills("hello", catalog) == []


def test_discovery_does_not_consume_loop_decision(tmp_path):
    e = engine(tmp_path)
    e._exposure_last_tools = ["previous_turn_tool"]
    assert loop_state(e, [NS(tool_name="introspect_capability", success=True)], iteration=1) is None
    state = loop_state(e, [NS(tool_name="observe_spreadsheet", success=True, result="当前结果")], iteration=3)
    assert state["iteration"] == 3
    assert state["observations"][0]["result_head"] == "当前结果"
    assert "previous_turn_tool" not in state["last_tools"]


@pytest.mark.parametrize("guard", [
    {"pending_items": ["待处理任务"]},
    {"has_more_results": True},
    {"observations": [{"success": True, "truncated": True}]},
])
def test_loop_cannot_wrap_with_missing_evidence(guard):
    state = {"observations": [{"success": True, "truncated": False}], **guard}
    decision = synthesize("loop.wrap", Evaluation("loop.wrap", {
        "next": ChoiceAnswer("stop", confidence=0.99),
        "done_enough": NoulAnswer(0.99), "needs_more_context": NoulAnswer(0.01),
    }), state)
    assert decision.extras["next"] == "continue"


def test_column_from_other_file_cannot_be_combined():
    suggestion = read_suggestion(
        {"path": "./a.xlsx", "sheet": "销售"},
        {"path": "./b.xlsx", "sheet": "工资", "column": "D", "header_row": 2},
        "column_sample",
    )
    assert suggestion["arguments"]["file_path"] == "./a.xlsx"
    assert suggestion["arguments"]["sheet"] == "销售"
    assert suggestion["purpose"] == "overview"
    assert "range" not in suggestion["arguments"]


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [{"api_key": None}, {"enabled": "off"}, {}])
async def test_delivery_review_stays_disabled_with_any_provider_settings(tmp_path, changes):
    e = engine(tmp_path)
    e._state.affected_files = ["a.xlsx"]
    e._state.write_operations_log = [{"tool": "apply_spreadsheet_changes"}]
    with patch("excelmanus.system_one.host.live_jev_settings", return_value=settings(**changes)):
        assert not should_check_delivery(e)
        advice = await maybe_verify_mutation(e, NS(tool_calls=[], truncated=False), on_event=None)
        assert advice == ""
        assert not hasattr(e, "_mutation_verification")
        assert not should_check_delivery(e)
        e._state.write_operations_log.append({"tool": "apply_spreadsheet_changes"})
        assert not should_check_delivery(e)
        assert await maybe_verify_mutation(e, NS(tool_calls=[], truncated=False)) == ""


def test_checklist_does_not_hide_requirements_past_user_text_cap(tmp_path):
    e = engine(tmp_path)
    e.memory.messages = [{"role": "user", "content": "请完成事项一。" * 100 + "最后保存并核对总额。"}]
    state = mutation_verify_state_from_engine(e, NS(tool_calls=[]))
    assert state["verification_facts"]["checklist_count"] > 5
    assert state["verification_facts"]["checklist_truncated"]


def test_ui_candidates_include_reads_and_reject_foreign_view(tmp_path):
    e = engine(tmp_path)
    e._jev_context_input = {"sheet_context": {"workspace_id": "workspace-b", "path": "foreign.xlsx"}}
    result = NS(tool_calls=[
        NS(tool_name="observe_spreadsheet", success=True, arguments={"file_path": "a.xlsx"}),
        NS(tool_name="observe_spreadsheet", success=False, arguments={"file_path": "bad.xlsx"}),
        NS(tool_name="observe_spreadsheet", success=True, arguments={"file_path": "../outside.xlsx"}),
    ])
    state = ui_surface_state_from_engine(e, result)
    assert state["candidate_files"] == ["./a.xlsx"]
    assert state["files_written"] == []


def test_ui_uses_selected_file_and_rejects_ambiguous_comparison():
    state = {"candidate_files": ["./a.xlsx", "./b.xlsx"]}
    evaluation = Evaluation("ui.surface", {
        "surface": ChoiceAnswer("side_panel", confidence=0.95),
        "wants_to_see": NoulAnswer(0.95),
        "file_pick": ChoiceAnswer("second", confidence=0.95),
    })
    assert synthesize("ui.surface", evaluation, state).extras["file_path"] == "./b.xlsx"
    comparison = replace(evaluation, answers={**evaluation.answers, "surface": ChoiceAnswer("compare", confidence=0.95)})
    assert synthesize("ui.surface", comparison, state).extras["surface"] == "stay"


def test_ui_does_not_navigate_when_user_did_not_request_viewing():
    state = {"candidate_files": ["./a.xlsx"]}
    evaluation = Evaluation("ui.surface", {
        "surface": ChoiceAnswer("side_panel", confidence=0.95),
        "wants_to_see": NoulAnswer(0.1),
    })
    decision = synthesize("ui.surface", evaluation, state)
    assert decision.reason == "user_did_not_request_surface"
    assert decision.extras["surface"] == "stay"
    assert decision.extras["suppress_heuristic"] is True


def test_path_binding_never_truncates_valid_identity():
    path = "./" + "folder/" * 20 + "a.xlsx"
    assert bound_state("ui.surface", {"candidate_files": [path]})["candidate_files"] == [path]
    assert bound_state("ui.surface", {"candidate_files": ["x" * 301]})["candidate_files"] == []


@pytest.mark.asyncio
async def test_entry_preserves_routing_budget(tmp_path):
    e = engine(tmp_path)
    budget = JevTurnBudget()
    budget.reserve()
    with patch("excelmanus.system_one.host.live_jev_settings", return_value=settings(enabled="off")):
        await maybe_record_turn_exposure(e, "继续", budget=budget)
    assert e._jev_turn_budget is budget
    assert budget.evaluations == 1


def test_security_accounting_does_not_spend_delivery_reserve():
    budget = JevTurnBudget(max_evaluations=1, max_latency_ms=50)
    budget.reserve(security=True)
    budget.record(100, security=True)
    assert budget.evaluations == 0
    assert budget.spent_latency_ms == 0
    assert budget.security_evaluations == 1
    assert budget.reserve(latency_ms=30)


def test_evaluation_eligibility_is_not_an_effect_or_outcome():
    decision = Decision(
        kind="noop", reason="next:continue", applied=True,
        evaluation=Evaluation("loop.wrap", {}, latency_ms=100),
        extras={"next": "continue"},
    )
    evaluated = build_jev_trace_payload("loop.wrap", decision, gate="enforce", transport="gateway")
    assert evaluated["eligible"] and evaluated["evaluated"]
    assert not evaluated["applied"]
    effect = replace(decision, evaluation=None, extras={"stage": "effect", "advice_delivered": True})
    consumed = build_jev_trace_payload("loop.wrap", effect, gate="enforce", transport="gateway")
    assert consumed["applied"] and consumed["advice_delivered"] and not consumed["evaluated"]
    assert consumed["latency_ms"] == 0
    outcome = replace(decision, kind="outcome", extras={"outcome": "escaped"})
    recorded = build_jev_trace_payload("recovery.next_step", outcome, gate="enforce", transport="gateway")
    assert not recorded["applied"] and not recorded["evaluated"]
