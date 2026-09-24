from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from excelmanus.config import ExcelManusConfig
from excelmanus.engine_types import ChatResult, ToolCallResult
from excelmanus.engine_core.tool_result import ToolResult
from excelmanus.system_one.adapter import delivery_checklist, mutation_verify_state_from_engine
from excelmanus.system_one.host import maybe_verify_mutation
from excelmanus.system_one.policy import synthesize
from excelmanus.system_one.types import ChoiceAnswer, Decision, Evaluation, NoulAnswer


def _config() -> ExcelManusConfig:
    return ExcelManusConfig(
        api_key="test-key",
        base_url="https://test.example.com/v1",
        model="test-model",
        max_iterations=8,
        max_consecutive_failures=3,
        workspace_root=str(Path(__file__).resolve().parent),
        ai_gateway_api_key="vck_test",
        jev_enabled="shadow",
        jev_verification="shadow",
    )


def _result() -> ChatResult:
    return ChatResult(
        reply="done",
        tool_calls=[
            ToolCallResult(
                tool_name="apply_spreadsheet_changes",
                arguments={"file_path": "a.xlsx"},
                result="ok",
                success=True,
            ),
        ],
        truncated=False,
    )


@pytest.mark.asyncio
async def test_legacy_mutation_verify_does_not_review_written_turns() -> None:
    engine = SimpleNamespace(
        config=_config(),
        _subagent_config=None,
        _is_host_session=True,
        _current_chat_mode="write",
        _state=SimpleNamespace(
            affected_files=["a.xlsx"],
            write_operations_log=[{"tool": "apply_spreadsheet_changes", "success": True}],
        ),
        memory=SimpleNamespace(get_messages=lambda: [{"role": "user", "content": "改 A1"}]),
        _jev_turn_budget=None,
    )
    decision = Decision(
        kind="noop",
        reason="next:inspect_more",
        evaluation=Evaluation(
            pack_id="mutation.verify",
            answers={
                "satisfied": NoulAnswer(0.8),
                "scope_ok": NoulAnswer(0.9),
                "next": ChoiceAnswer("inspect_more", confidence=0.9),
            },
        ),
        extras={"satisfied": 0.8, "scope_ok": 0.9, "next": "inspect_more"},
    )
    with patch("excelmanus.system_one.evaluate", AsyncMock(return_value=decision)) as mocked:
        advice = await maybe_verify_mutation(engine, _result())
    mocked.assert_not_awaited()
    assert advice == ""
    assert not hasattr(engine, "_mutation_verification")


@pytest.mark.asyncio
async def test_mutation_verify_skips_read_only_turn() -> None:
    engine = SimpleNamespace(
        config=_config(),
        _subagent_config=None,
        _is_host_session=True,
        _state=SimpleNamespace(affected_files=[]),
    )
    with patch("excelmanus.system_one.evaluate", AsyncMock()) as mocked:
        await maybe_verify_mutation(engine, _result())
    mocked.assert_not_awaited()


def _engine(user_text: str, task_titles: list[str] | None = None) -> SimpleNamespace:
    task_store = None
    if task_titles is not None:
        from excelmanus.task_list import TaskStore

        task_store = TaskStore()
        task_store.create("计划", task_titles)
    return SimpleNamespace(
        config=_config(),
        _subagent_config=None,
        _is_host_session=True,
        _task_store=task_store,
        _state=SimpleNamespace(affected_files=["a.xlsx"], write_operations_log=[]),
        memory=SimpleNamespace(messages=[{"role": "user", "content": user_text}]),
    )


def test_delivery_checklist_from_task_store() -> None:
    engine = _engine("随便一句", task_titles=["把 A 列标红", "汇总 B 列", "保存"])
    checklist = delivery_checklist(engine)
    assert [item["id"] for item in checklist] == ["item_1", "item_2", "item_3"]
    assert checklist[0]["text"] == "把 A 列标红"


def test_delivery_checklist_splits_clauses() -> None:
    engine = _engine("把 A 列标红，再把 B 列求和，然后保存")
    checklist = delivery_checklist(engine)
    texts = [item["text"] for item in checklist]
    assert any("标红" in text for text in texts)
    assert any("求和" in text for text in texts)
    assert len(checklist) >= 2


def test_delivery_checklist_single_sentence_fallback() -> None:
    engine = _engine("改一下表头")
    checklist = delivery_checklist(engine)
    assert len(checklist) == 1
    assert checklist[0]["id"] == "item_1"
    assert checklist[0]["text"]


def test_synthesize_checklist_missing_item_forces_inspect_more() -> None:
    evaluation = Evaluation(
        pack_id="mutation.verify",
        answers={
            "satisfied": NoulAnswer(0.9),
            "scope_ok": NoulAnswer(0.9),
            "next": ChoiceAnswer("none", confidence=0.9),
            "item_1": ChoiceAnswer("evidenced", confidence=0.9),
            "item_2": ChoiceAnswer("missing", confidence=0.9),
            "item_3": ChoiceAnswer("evidenced", confidence=0.9),
        },
    )
    state = {
        "checklist": [
            {"id": "item_1", "text": "标红"},
            {"id": "item_2", "text": "求和"},
            {"id": "item_3", "text": "保存"},
        ],
        "verification_facts": {},
    }
    decision = synthesize("mutation.verify", evaluation, state)
    assert decision.extras["next"] == "inspect_more"
    assert decision.reason == "checklist_items_unevidenced"
    assert decision.extras["missing_items"] == 1
    assert [item["verdict"] for item in decision.extras["items"]] == [
        "evidenced",
        "missing",
        "evidenced",
    ]


def test_synthesize_all_evidenced_keeps_none() -> None:
    evaluation = Evaluation(
        pack_id="mutation.verify",
        answers={
            "satisfied": NoulAnswer(0.9),
            "scope_ok": NoulAnswer(0.9),
            "next": ChoiceAnswer("none", confidence=0.9),
            "item_1": ChoiceAnswer("evidenced", confidence=0.9),
        },
    )
    state = {"checklist": [{"id": "item_1", "text": "标红"}], "verification_facts": {}}
    decision = synthesize("mutation.verify", evaluation, state)
    assert decision.extras["next"] == "none"
    assert decision.extras["missing_items"] == 0


def test_style_mismatch_marks_evidence_incomplete() -> None:
    engine = _engine("标红")
    verification = {
        "status": "success",
        "verification_kind": "style",
        "total_changes": 2,
        "style_changes": [{"sheet": "S", "cell": "A1", "ok": ["font.bold"]}],
        "style_mismatches": [{"sheet": "S", "cell": "A2", "prop": "fill", "expected": "FF0000", "actual": "0000FF"}],
        "mismatch_count": 1,
    }
    result = ChatResult(
        reply="done",
        tool_calls=[
            ToolCallResult(
                tool_name="apply_spreadsheet_changes",
                arguments={"file_path": "a.xlsx"},
                result="ok",
                success=True,
                structured=ToolResult(success=True, model_text="", value={"meta": {"write_verification": verification}}),
            ),
        ],
        truncated=False,
    )
    state = mutation_verify_state_from_engine(engine, result)
    facts = state["verification_facts"]
    assert facts["style_mismatch_count"] == 1
    assert facts["has_incomplete_evidence"] is True
    evidence = state["write_evidence"][0]
    assert evidence["style_ok_count"] == 1
    assert evidence["style_mismatch_count"] == 1
