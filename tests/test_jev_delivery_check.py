"""The primary agent owns delivery; no auxiliary review may reopen its reply."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest

from excelmanus.agent.loop import run_tool_loop
from excelmanus.config import ExcelManusConfig
from excelmanus.engine import AgentEngine
from excelmanus.engine_types import ToolCallResult
from excelmanus.engine_core.tool_result import ToolResult
from excelmanus.events import EventType
from excelmanus.providers.stream_types import StreamDelta
from excelmanus.skillpacks import SkillMatchResult
from excelmanus.tools import ToolRegistry


def _engine() -> AgentEngine:
    return AgentEngine(ExcelManusConfig(
        api_key="test-key", base_url="https://test.example.com/v1",
        model="test-model", max_iterations=8, max_consecutive_failures=3,
        workspace_root=str(Path(__file__).resolve().parent),
        ai_gateway_api_key="vck_test", jev_enabled="enforce",
        jev_verification="enforce",
    ), ToolRegistry())


def _text_deltas(events: list) -> list[str]:
    return [ev.text_delta for ev in events if ev.event_type == EventType.TEXT_DELTA]


@pytest.mark.asyncio
@pytest.mark.parametrize("emit_events", [True, False])
@pytest.mark.parametrize("evidence", ["read_only", "complete", "sampled", "missing", "mismatch"])
async def test_delivery_is_final_without_forced_review(emit_events: bool, evidence: str) -> None:
    engine = _engine()
    initial = []
    if evidence != "read_only":
        engine._state.affected_files = ["a.xlsx"]
        initial = [ToolCallResult("apply_spreadsheet_changes", {"file_path": "a.xlsx"}, "ok", True)]
        if evidence != "missing":
            initial[0].structured = ToolResult(success=True, model_text="ok", value={
                "meta": {"write_verification": {
                    "status": "success" if evidence != "mismatch" else "failed",
                    "sampled": evidence == "sampled",
                    "mismatches": ["A1"] if evidence == "mismatch" else [],
                }},
            })
    events: list = []

    async def live_stream():
        yield StreamDelta(content_delta="已交付；")
        assert _text_deltas(events) == (["已交付；"] if emit_events else [])
        yield StreamDelta(content_delta="未验证事项已说明。")
        yield StreamDelta(finish_reason="stop", usage={"prompt_tokens": 2, "completion_tokens": 2})

    mocked_create = AsyncMock(side_effect=lambda **_kwargs: live_stream())
    engine._client.chat.completions.create = mocked_create
    # Even a legacy integration returning a review request cannot re-enter
    # the loop: the delivery hooks must no longer be called at all.
    with (
        patch("excelmanus.system_one.host.should_check_delivery", Mock(return_value=True)) as check,
        patch("excelmanus.system_one.host.maybe_verify_mutation", AsyncMock(return_value="继续检查")) as verify,
        patch("excelmanus.system_one.evaluate", AsyncMock()) as evaluate,
    ):
        result = await run_tool_loop(
            engine, SkillMatchResult(skills_used=[], route_mode="fallback", system_contexts=[]),
            on_event=events.append if emit_events else None, initial_tool_results=initial,
        )
    check.assert_not_called()
    verify.assert_not_awaited()
    evaluate.assert_not_awaited()
    mocked_create.assert_awaited_once()
    assert result.reply == "已交付；未验证事项已说明。"
    assert result.tool_calls == initial
    assert _text_deltas(events) == (["已交付；", "未验证事项已说明。"] if emit_events else [])
    assert not any(event.event_type == EventType.RETRACT_TEXT for event in events)
    assert not any(
        message.get("_prompt_kind") in {"jev_delivery_check", "jev_delivery_draft"}
        for message in engine._memory.messages
    )
