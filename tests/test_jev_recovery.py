from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from excelmanus.config import ExcelManusConfig
from excelmanus.engine_types import ToolCallResult
from excelmanus.system_one.host import emit_recovery_outcome, maybe_suggest_recovery
from excelmanus.system_one.types import Decision


def _config(**overrides: object) -> ExcelManusConfig:
    values: dict[str, object] = {
        "api_key": "test-key",
        "base_url": "https://test.example.com/v1",
        "model": "test-model",
        "max_iterations": 8,
        "max_consecutive_failures": 3,
        "workspace_root": str(Path(__file__).resolve().parent),
        "ai_gateway_api_key": "vck_test",
        "jev_enabled": "enforce",
        "jev_recovery": "enforce",
    }
    values.update(overrides)
    return ExcelManusConfig(**values)


@pytest.mark.asyncio
async def test_recovery_is_not_called_for_success_or_twice() -> None:
    engine = SimpleNamespace(
        config=_config(),
        _subagent_config=None,
        _is_host_session=True,
        _recovery_hint=None,
        memory=SimpleNamespace(get_messages=lambda: [{"role": "user", "content": "继续"}]),
        _last_iteration_count=2,
    )
    success = [ToolCallResult("read", {}, "ok", True)]
    with patch(
        "excelmanus.system_one.evaluate",
        AsyncMock(return_value=Decision.noop("next:none", next="none")),
    ) as mocked:
        await maybe_suggest_recovery(engine, success)
        mocked.assert_not_awaited()
        # 单次失败不触发评估：需要连续 ≥2 次失败（或熔断）才评估
        single = [ToolCallResult("inspect_spreadsheet", {}, "bad", False, error="bad")]
        await maybe_suggest_recovery(engine, single)
        mocked.assert_not_awaited()
        assert engine._recovery_hint is None
        # 只读工具的失败不会命中 commit_unknown 确定性分支，才会走到 evaluate
        failures = [
            ToolCallResult("inspect_spreadsheet", {}, "bad", False, error="bad"),
            ToolCallResult("inspect_spreadsheet", {}, "bad", False, error="bad"),
        ]
        await maybe_suggest_recovery(engine, failures)
        await maybe_suggest_recovery(engine, failures)
    mocked.assert_awaited_once()
    assert engine._recovery_hint["next"] == "none"
    assert engine._recovery_hint["source"] == "jev"
    assert engine._recovery_hint["breaker_triggered"] is False


def _engine_with_hint(hint: dict) -> SimpleNamespace:
    return SimpleNamespace(
        config=_config(),
        _subagent_config=None,
        _is_host_session=True,
        _driver=None,
        _recovery_hint=hint,
    )


def _hint(**overrides: object) -> dict:
    base: dict[str, object] = {
        "next": "inspect_more",
        "applied": True,
        "reason": "next:inspect_more",
        "source": "jev",
        "breaker_triggered": False,
        "delivered": True,
        "following_success": True,
        "same_failure_repeated": False,
    }
    base.update(overrides)
    return base


@pytest.mark.parametrize(
    "hint,outcome",
    [
        (_hint(), "escaped"),
        (_hint(same_failure_repeated=True), "repeated"),
        (_hint(following_success=None), "not_continued"),
        (_hint(delivered=False), "not_delivered"),
        (
            _hint(
                next="stop",
                reason="deterministic_stop",
                breaker_triggered=True,
                delivered=False,
                source="deterministic",
            ),
            "stopped",
        ),
    ],
)
def test_emit_recovery_outcome(hint: dict, outcome: str) -> None:
    engine = _engine_with_hint(hint)
    with (
        patch("excelmanus.system_one.host.emit_jev_trace") as emit,
        patch("excelmanus.system_one.host.record_jev_decision") as record,
    ):
        emit_recovery_outcome(engine)
    record.assert_called_once()
    emit.assert_called_once()
    decision = record.call_args.kwargs["decision"]
    assert decision.kind == "outcome"
    assert decision.extras["outcome"] == outcome
    assert decision.extras["source"] == hint["source"]
    assert record.call_args.kwargs["pack_id"] == "recovery.next_step"


def test_emit_recovery_outcome_skips_child_and_missing_hint() -> None:
    engine = _engine_with_hint(_hint())
    engine._is_host_session = False
    with (
        patch("excelmanus.system_one.host.emit_jev_trace") as emit,
        patch("excelmanus.system_one.host.record_jev_decision") as record,
    ):
        emit_recovery_outcome(engine)
    record.assert_not_called()
    emit.assert_not_called()

    empty = _engine_with_hint(None)
    with (
        patch("excelmanus.system_one.host.emit_jev_trace") as emit,
        patch("excelmanus.system_one.host.record_jev_decision") as record,
    ):
        emit_recovery_outcome(empty)
    record.assert_not_called()
    emit.assert_not_called()


@pytest.mark.asyncio
async def test_recovery_hint_source_deterministic_for_breaker() -> None:
    engine = SimpleNamespace(
        config=_config(),
        _subagent_config=None,
        _is_host_session=True,
        _recovery_hint=None,
        memory=SimpleNamespace(get_messages=lambda: [{"role": "user", "content": "继续"}]),
        _last_iteration_count=2,
    )
    failures = [
        ToolCallResult("inspect_spreadsheet", {}, "bad", False, error="bad"),
        ToolCallResult("inspect_spreadsheet", {}, "bad", False, error="bad"),
    ]
    with patch(
        "excelmanus.system_one.evaluate",
        AsyncMock(return_value=Decision.noop("next:none", next="none")),
    ) as mocked:
        advice = await maybe_suggest_recovery(engine, failures, breaker_triggered=True)
    mocked.assert_not_awaited()
    assert advice
    assert engine._recovery_hint["source"] == "deterministic"
    assert engine._recovery_hint["breaker_triggered"] is True
    assert engine._recovery_hint["reason"] == "deterministic_stop"
