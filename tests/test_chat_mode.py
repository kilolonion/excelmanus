"""chat_mode 唯一权限事实：apply_chat_mode、plan 粘滞消失、hybrid 禁止、MODE_CHANGED。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from excelmanus.api_sse import sse_event_to_sse
from excelmanus.config import ExcelManusConfig
from excelmanus.engine import AgentEngine
from excelmanus.events import EventType, ToolCallEvent
from excelmanus.plan_mode import apply_chat_mode, handle_plan_command, migrate_legacy_plan_flag
from excelmanus.security.policy import is_plan_active, writes_denied
from excelmanus.tools.registry import ToolRegistry


def _engine_stub(**kwargs: object) -> SimpleNamespace:
    base: dict[str, object] = {
        "_current_chat_mode": "write",
        "_plan_active": False,
        "_pending_plan_exit": "draft",
        "_tools_cache": ["cached"],
        "_registry": None,
    }
    base.update(kwargs)
    return SimpleNamespace(**base)


def _make_engine() -> AgentEngine:
    cfg = ExcelManusConfig(
        api_key="test-key",
        base_url="https://test.example.com/v1",
        model="test-model",
        max_iterations=20,
        max_consecutive_failures=3,
        workspace_root=str(Path(__file__).resolve().parent),
    )
    return AgentEngine(config=cfg, registry=ToolRegistry())


def _text_response(content: str):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=None))],
    )


def test_permission_denied_remediation_mentions_edit_mode() -> None:
    from excelmanus.engine_core.error_payload import PERMISSION_DENIED, remediation_for

    text = remediation_for(PERMISSION_DENIED)
    assert "编辑模式" in text


def test_is_plan_active_reads_only_chat_mode() -> None:
    assert is_plan_active(_engine_stub(_current_chat_mode="plan"))
    assert not is_plan_active(_engine_stub(_current_chat_mode="write", _plan_active=True))
    assert not is_plan_active(_engine_stub(_current_chat_mode="read", _plan_active=True))


def test_legacy_plan_flag_promotes_only_on_explicit_migrate() -> None:
    e = _engine_stub(_current_chat_mode="write", _plan_active=True)
    assert not is_plan_active(e)
    migrate_legacy_plan_flag(e)
    assert e._current_chat_mode == "plan"
    assert e._plan_active is False
    assert is_plan_active(e)


def test_apply_chat_mode_emits_chat_mode_value() -> None:
    events: list[ToolCallEvent] = []
    e = _engine_stub(_current_chat_mode="write", _pending_plan_exit=None)
    apply_chat_mode(e, "plan", source="slash", on_event=events.append)
    assert e._current_chat_mode == "plan"
    assert e._plan_active is False
    assert is_plan_active(e)
    assert len(events) == 1
    assert events[0].event_type == EventType.MODE_CHANGED
    assert events[0].mode_name == "chat_mode"
    assert events[0].mode_value == "plan"
    sse = sse_event_to_sse(events[0])
    assert sse is not None
    assert "mode_changed" in sse
    assert '"mode_name": "chat_mode"' in sse
    assert '"value": "plan"' in sse
    assert '"mode_name": "plan"' not in sse


def test_plan_to_read_exits_plan_and_clears_pending() -> None:
    e = _engine_stub(_current_chat_mode="plan", _pending_plan_exit="keep-me")
    apply_chat_mode(e, "read", source="request")
    assert e._current_chat_mode == "read"
    assert e._pending_plan_exit is None
    assert not is_plan_active(e)
    assert writes_denied(e)


def test_request_write_does_not_stick_on_legacy_plan_flag() -> None:
    e = _engine_stub(_current_chat_mode="plan", _plan_active=True, _pending_plan_exit="x")
    apply_chat_mode(e, "write", source="request")
    assert e._current_chat_mode == "write"
    assert not is_plan_active(e)
    assert e._pending_plan_exit is None


def test_slash_plan_off_emits_write() -> None:
    events: list[ToolCallEvent] = []
    e = _engine_stub(_current_chat_mode="plan")
    handle_plan_command(e, "off", on_event=events.append)
    assert e._current_chat_mode == "write"
    assert events[0].mode_name == "chat_mode"
    assert events[0].mode_value == "write"


def test_plan_exit_answer_emits_mode_changed() -> None:
    from excelmanus.engine_core.interaction_handler import InteractionHandler

    events: list[ToolCallEvent] = []
    engine = _make_engine()
    apply_chat_mode(engine, "plan", source="slash")
    engine._pending_plan_exit = "# plan"
    handler = InteractionHandler(engine)
    parsed = SimpleNamespace(
        selected_options=[{"label": "批准并退出"}],
        question_id="q1",
    )
    result = handler.handle_plan_exit_answer(parsed=parsed, on_event=events.append)
    assert "退出计划模式" in result.reply
    assert engine._current_chat_mode == "write"
    assert not is_plan_active(engine)
    assert events and events[0].mode_name == "chat_mode"
    assert events[0].mode_value == "write"


@pytest.mark.asyncio
async def test_claimed_followup_request_body_is_authoritative() -> None:
    engine = _make_engine()
    engine._client.chat.completions.create = AsyncMock(return_value=_text_response("ok"))
    await engine.followup("/plan on")
    assert is_plan_active(engine)
    await engine.followup("下一步", chat_mode="write")
    assert engine._current_chat_mode == "write"
    assert not is_plan_active(engine)


@pytest.mark.asyncio
async def test_followup_plan_chat_mode_stays_in_plan() -> None:
    engine = _make_engine()
    engine._client.chat.completions.create = AsyncMock(return_value=_text_response("ok"))
    await engine.followup("/plan on")
    await engine.followup("继续分析", chat_mode="plan")
    assert is_plan_active(engine)
    assert engine._current_chat_mode == "plan"


@pytest.mark.asyncio
async def test_followup_read_from_plan_is_not_hybrid() -> None:
    engine = _make_engine()
    engine._client.chat.completions.create = AsyncMock(return_value=_text_response("ok"))
    await engine.followup("/plan on")
    engine._pending_plan_exit = "pending"
    await engine.followup("只看看", chat_mode="read")
    assert engine._current_chat_mode == "read"
    assert not is_plan_active(engine)
    assert engine._pending_plan_exit is None
    assert writes_denied(engine)
