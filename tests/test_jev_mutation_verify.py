from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from excelmanus.config import ExcelManusConfig
from excelmanus.engine_types import ChatResult, ToolCallResult
from excelmanus.system_one.host import maybe_verify_mutation
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
                tool_name="edit_spreadsheet",
                arguments={"file_path": "a.xlsx"},
                result="ok",
                success=True,
            ),
        ],
        truncated=False,
    )


@pytest.mark.asyncio
async def test_mutation_verify_runs_only_for_written_turns() -> None:
    engine = SimpleNamespace(
        config=_config(),
        _subagent_config=None,
        _is_host_session=True,
        _current_chat_mode="write",
        _state=SimpleNamespace(
            affected_files=["a.xlsx"],
            write_operations_log=[{"tool": "edit_spreadsheet", "success": True}],
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
        await maybe_verify_mutation(engine, _result())
    mocked.assert_awaited_once()
    assert engine._mutation_verification["next"] == "inspect_more"


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
