"""伪造前端：上传文案、附件合成、审批/问答与对话导出。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from excelmanus.engine_types import ChatResult
from excelmanus.events import EventType, ToolCallEvent
from excelmanus.fake_frontend import FakeFrontend, format_upload_notice, is_image_path
from excelmanus.chat_turn import ChatTurnOutcome


def test_format_upload_notice_matches_web() -> None:
    assert format_upload_notice("file", "./uploads/a.csv") == "[已上传文件: ./uploads/a.csv]"
    assert format_upload_notice("image", "./uploads/pic.png") == "[已上传图片: ./uploads/pic.png]"


def test_is_image_path() -> None:
    assert is_image_path("shot.PNG")
    assert not is_image_path("sales.xlsx")


def test_upload_file_writes_workspace_uploads(tmp_path: Path) -> None:
    src = tmp_path / "hello.txt"
    src.write_text("abc", encoding="utf-8")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    engine = SimpleNamespace(
        workspace=SimpleNamespace(root_dir=workspace),
        _file_registry=None,
    )
    frontend = FakeFrontend(manager=SimpleNamespace(), session_id="s1")
    attached = frontend.upload_file(engine, src)
    assert attached.kind == "file"
    assert attached.path.startswith("./uploads/")
    dest = workspace / attached.path.removeprefix("./")
    assert dest.is_file()
    assert dest.read_text(encoding="utf-8") == "abc"
    export = frontend.export_conversation()
    assert export["attachments"][0]["filename"] == "hello.txt"


def test_compose_message_prefixes_notices_and_rewrites_source() -> None:
    frontend = FakeFrontend(manager=SimpleNamespace(), session_id="s1")
    uploaded = [
        SimpleNamespace(
            kind="file",
            path="./uploads/abc_sales.xlsx",
            source="D:/data/sales.xlsx",
            attachment_id="",
            media_type="",
            filename="sales.xlsx",
        ),
        SimpleNamespace(
            kind="image",
            path="./uploads/def_shot.png",
            source="D:/data/shot.png",
            attachment_id="att-1",
            media_type="image/png",
            filename="shot.png",
        ),
    ]
    text, images = frontend._compose_message(
        "请检查 D:/data/sales.xlsx",
        uploaded,  # type: ignore[arg-type]
    )
    assert text.startswith("[已上传文件: ./uploads/abc_sales.xlsx]")
    assert "[已上传图片: ./uploads/def_shot.png]" in text
    assert "./uploads/abc_sales.xlsx" in text
    assert "D:/data/sales.xlsx" not in text
    assert images == [{
        "attachment_id": "att-1",
        "media_type": "image/png",
        "name": "shot.png",
    }]


def test_export_conversation_structure() -> None:
    history = SimpleNamespace(load_messages=lambda sid: [{"role": "user", "content": "hi"}])
    engine = SimpleNamespace(memory=SimpleNamespace(get_messages=lambda: [{"role": "assistant", "content": "ok"}]))
    frontend = FakeFrontend(
        manager=SimpleNamespace(chat_history=history),
        session_id="sess-1",
    )
    frontend.engine = engine
    frontend.transcript.append({"role": "user", "content": "hi", "typed": "hi"})
    export = frontend.export_conversation()
    assert export["session_id"] == "sess-1"
    assert export["pipeline"] == "fake_frontend"
    assert export["transcript"][0]["typed"] == "hi"
    assert export["session_messages"][0]["content"] == "hi"
    assert export["engine_messages"][0]["content"] == "ok"
    assert export["auto_reply_count"] == 0
    assert export["auto_approve_count"] == 0
    assert export["auto_approve"] == "fullaccess"
    assert export["full_access_enabled"] is False


def test_send_records_transcript_and_calls_followup() -> None:
    engine = object()
    manager = SimpleNamespace(
        acquire_for_chat=AsyncMock(return_value=("sess-1", engine)),
        release_for_chat=AsyncMock(),
        flush_messages_sync=lambda sid: None,
        chat_history=None,
    )
    outcome = ChatTurnOutcome(
        result=ChatResult(reply="处理好了"),
        display_text="hello",
        mention_contexts=None,
    )

    async def _go() -> tuple[ChatResult, list[dict]]:
        frontend = FakeFrontend(manager=manager, session_id="sess-1")
        with patch(
            "excelmanus.fake_frontend.run_engine_followup",
            new=AsyncMock(return_value=outcome),
        ) as followup:
            result = await frontend.send("hello")
            followup.assert_awaited_once()
            assert followup.await_args.kwargs["chat_mode"] == "write"
        return result, frontend.transcript

    result, transcript = asyncio.run(_go())
    assert result.reply == "处理好了"
    assert transcript[0]["role"] == "user"
    assert transcript[0]["typed"] == "hello"
    assert transcript[1]["role"] == "assistant"
    assert transcript[1]["content"] == "处理好了"
    manager.release_for_chat.assert_awaited()


def test_send_enables_full_access_by_default() -> None:
    engine = SimpleNamespace(_full_access_enabled=False)
    manager = SimpleNamespace(
        acquire_for_chat=AsyncMock(return_value=("sess-1", engine)),
        release_for_chat=AsyncMock(),
        flush_messages_sync=lambda sid: None,
        chat_history=None,
    )
    outcome = ChatTurnOutcome(
        result=ChatResult(reply="ok"),
        display_text="hello",
        mention_contexts=None,
    )

    async def _go() -> FakeFrontend:
        frontend = FakeFrontend(manager=manager, session_id="sess-1")
        with patch(
            "excelmanus.fake_frontend.run_engine_followup",
            new=AsyncMock(return_value=outcome),
        ):
            await frontend.send("hello")
        return frontend

    frontend = asyncio.run(_go())
    assert engine._full_access_enabled is True
    assert frontend.export_conversation()["full_access_enabled"] is True


def test_send_does_not_enable_full_access_when_reject() -> None:
    engine = SimpleNamespace(_full_access_enabled=False)
    manager = SimpleNamespace(
        acquire_for_chat=AsyncMock(return_value=("sess-1", engine)),
        release_for_chat=AsyncMock(),
        flush_messages_sync=lambda sid: None,
        chat_history=None,
    )
    outcome = ChatTurnOutcome(
        result=ChatResult(reply="ok"),
        display_text="hello",
        mention_contexts=None,
    )

    async def _go() -> None:
        frontend = FakeFrontend(
            manager=manager, session_id="sess-1", auto_approve="reject",
        )
        with patch(
            "excelmanus.fake_frontend.run_engine_followup",
            new=AsyncMock(return_value=outcome),
        ):
            await frontend.send("hello")

    asyncio.run(_go())
    assert engine._full_access_enabled is False


def test_dispatch_default_approval_is_fullaccess() -> None:
    approvals: list[tuple[str, str]] = []

    async def _go() -> FakeFrontend:
        frontend = FakeFrontend(
            manager=SimpleNamespace(flush_messages_sync=lambda sid: None),
            session_id="s1",
        )
        frontend.engine = object()
        with patch(
            "excelmanus.fake_frontend.submit_approval",
            side_effect=lambda engine, aid, decision: approvals.append((aid, decision)) or True,
        ):
            frontend._dispatch(ToolCallEvent(
                event_type=EventType.PENDING_APPROVAL,
                approval_id="a1",
                approval_tool_name="write_excel",
            ))
            await frontend.drain()
        return frontend

    frontend = asyncio.run(_go())
    assert approvals == [("a1", "fullaccess")]
    assert frontend.auto_approve == "fullaccess"


def test_dispatch_auto_answers_and_approves() -> None:
    answers: list[tuple[str, str]] = []
    approvals: list[tuple[str, str]] = []

    async def _go() -> FakeFrontend:
        frontend = FakeFrontend(
            manager=SimpleNamespace(flush_messages_sync=lambda sid: None),
            session_id="s1",
            auto_replies=["选项1"],
            auto_approve="accept",
        )
        frontend.engine = object()
        with (
            patch(
                "excelmanus.fake_frontend.submit_question_answer",
                side_effect=lambda engine, qid, answer: answers.append((qid, answer)) or True,
            ),
            patch(
                "excelmanus.fake_frontend.submit_approval",
                side_effect=lambda engine, aid, decision: approvals.append((aid, decision)) or True,
            ),
        ):
            frontend._dispatch(ToolCallEvent(
                event_type=EventType.USER_QUESTION,
                question_id="q1",
                question_header="选一个",
            ))
            frontend._dispatch(ToolCallEvent(
                event_type=EventType.PENDING_APPROVAL,
                approval_id="a1",
                approval_tool_name="write_excel",
            ))
            await frontend.drain()
        return frontend

    frontend = asyncio.run(_go())
    assert answers == [("q1", "选项1")]
    assert approvals == [("a1", "accept")]
    assert frontend.auto_reply_count == 1
    assert frontend.auto_approve_count == 1


def test_empty_auto_replies_cancels_ask_user() -> None:
    cancelled: list[str] = []
    answers: list[tuple[str, str]] = []

    class _Registry:
        def cancel(self, qid: str) -> bool:
            cancelled.append(qid)
            return True

    async def _go() -> FakeFrontend:
        frontend = FakeFrontend(
            manager=SimpleNamespace(flush_messages_sync=lambda sid: None),
            session_id="s1",
            auto_replies=[],
        )
        frontend.engine = SimpleNamespace(interaction_registry=_Registry())
        with patch(
            "excelmanus.fake_frontend.submit_question_answer",
            side_effect=lambda engine, qid, answer: answers.append((qid, answer)) or True,
        ):
            frontend._dispatch(ToolCallEvent(
                event_type=EventType.USER_QUESTION,
                question_id="q-empty",
                question_header="下一步",
            ))
            await frontend.drain()
        return frontend

    frontend = asyncio.run(_go())
    assert cancelled == ["q-empty"]
    assert answers == []
    assert frontend.auto_reply_count == 0
