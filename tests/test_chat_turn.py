"""共享直聊回合：提及解析与审批/问答提交。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from excelmanus.chat_turn import (
    resolve_mentions,
    run_engine_followup,
    submit_approval,
    submit_question_answer,
)
from excelmanus.engine_types import ChatResult


def test_resolve_mentions_without_at_returns_original() -> None:
    message, contexts = asyncio.run(resolve_mentions("请检查这个表", object()))
    assert message == "请检查这个表"
    assert contexts is None


def test_submit_question_answer_uses_registry() -> None:
    resolved: list[tuple[str, dict]] = []
    engine = SimpleNamespace(
        interaction_registry=SimpleNamespace(
            resolve=lambda qid, payload: resolved.append((qid, payload)) or True,
        ),
        _question_flow=None,
    )
    assert submit_question_answer(engine, "q1", "1") is True
    assert resolved[0][0] == "q1"
    assert resolved[0][1]["raw_input"] == "1"
    assert submit_question_answer(engine, "", "1") is False
    assert submit_question_answer(SimpleNamespace(interaction_registry=None), "q1", "1") is False


def test_submit_approval_normalizes_decision() -> None:
    resolved: list[dict] = []
    engine = SimpleNamespace(
        interaction_registry=SimpleNamespace(
            resolve=lambda aid, payload: resolved.append(payload) or True,
        ),
    )
    assert submit_approval(engine, "a1", "FULLACCESS") is True
    assert resolved[0]["decision"] == "fullaccess"
    assert submit_approval(engine, "a2", "nope") is True
    assert resolved[1]["decision"] == "accept"
    assert submit_approval(engine, "", "accept") is False


def test_run_engine_followup_passes_frontend_kwargs() -> None:
    captured: dict = {}

    async def _followup(message, **kwargs):
        captured["message"] = message
        captured.update(kwargs)
        return ChatResult(reply="ok")

    engine = SimpleNamespace(followup=_followup)
    outcome = asyncio.run(run_engine_followup(
        engine,
        "hello",
        chat_mode="read",
        images=[{"attachment_id": "att-1", "media_type": "image/png"}],
    ))
    assert outcome.result.reply == "ok"
    assert captured["message"] == "hello"
    assert captured["chat_mode"] == "read"
    assert captured["images"][0]["attachment_id"] == "att-1"


def test_run_engine_followup_reuses_preparsed_mentions() -> None:
    followup = AsyncMock(return_value=ChatResult(reply="ok"))
    engine = SimpleNamespace(followup=followup)
    mentions = [SimpleNamespace(name="sales.xlsx")]
    outcome = asyncio.run(run_engine_followup(
        engine,
        "原始@file:sales.xlsx",
        display_text="原始sales.xlsx",
        mention_contexts=mentions,  # type: ignore[arg-type]
        chat_mode="write",
    ))
    assert outcome.display_text == "原始sales.xlsx"
    followup.assert_awaited_once()
    assert followup.await_args.args[0] == "原始sales.xlsx"
    assert followup.await_args.kwargs["mention_contexts"] is mentions
