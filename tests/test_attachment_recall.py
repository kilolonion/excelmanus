"""Attachment recall must survive compaction without granting unrelated access."""

from __future__ import annotations

import base64
import json
from types import SimpleNamespace

import pytest

from excelmanus.attachments.admit import admit_image_bytes
from excelmanus.attachments.offload import attachment_ids_from_engine
from excelmanus.attachments.store import reset_attachment_store
from excelmanus.chat_history import ChatHistoryStore
from excelmanus.config import ExcelManusConfig
from excelmanus.engine_core.tool_dispatcher import ToolDispatcher
from excelmanus.memory import ConversationMemory
from excelmanus.session_log import OP_REPLACE, SessionEventLog
from excelmanus.tools.context import ToolCallContext, bind_call, current_call, reset_call
from excelmanus.tools.image_tools import init_guard, read_image


_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


@pytest.fixture
def config(tmp_path):
    reset_attachment_store()
    init_guard(str(tmp_path))
    yield ExcelManusConfig(
        api_key="test", base_url="http://localhost", model="test",
        workspace_root=str(tmp_path),
    )
    reset_attachment_store()


def _summary():
    return [
        {"role": "user", "content": "Continue from summary.", "_prompt_kind": "compaction", "_ui_hidden": True},
        {"role": "assistant", "content": "The source image was inspected.", "_prompt_kind": "compaction", "_ui_hidden": True},
    ]


def _read(engine, attachment_id, *, detail="high"):
    context = current_call()
    assert context is not None
    token = bind_call(ToolCallContext(
        binding=context.binding,
        durable_attachment_ids=attachment_ids_from_engine(engine),
    ))
    try:
        return read_image(attachment_id=attachment_id, detail=detail)
    finally:
        reset_call(token)


@pytest.mark.parametrize("logged", [False, True])
@pytest.mark.parametrize("tool_image", [False, True])
def test_recall_survives_repeated_compaction_and_resume(config, logged, tool_image):
    ref = admit_image_bytes(_PNG)
    memory = ConversationMemory(config)
    if logged:
        memory.attach_event_log(SessionEventLog("source"))
    memory.add_user_message(
        [{"type": "image", "attachment": ref.to_dict()}],
        hidden=tool_image, prompt_kind="image_observation" if tool_image else None,
    )
    engine = SimpleNamespace(_memory=memory)
    for _ in range(2):
        memory.apply_compaction_summary(_summary(), len(memory.messages))
        assert _read(engine, ref.attachment_id).success

    restored = ConversationMemory(config)
    if logged:
        restored.load_from_log(SessionEventLog(
            "source", events=[event.to_row() for event in memory.event_log.events],
        ))
    else:
        restored.inject_messages([
            json.loads(ChatHistoryStore._serialize_content(message))
            for message in memory.messages
        ])
    engine._memory = restored
    assert _read(engine, ref.attachment_id).success
    # Authorization metadata must not send all compacted images to the model.
    assert all(isinstance(message["content"], str) for message in restored.messages)
    assert all(
        not any(key.startswith("_") for key in message)
        for message in restored.get_messages()
    )


def test_old_compaction_log_restores_only_its_sources(config):
    """Old saved sessions have no explicit compacted-attachment metadata."""
    ref = admit_image_bytes(_PNG)
    memory = ConversationMemory(config)
    log = SessionEventLog("source")
    memory.attach_event_log(log)
    memory.add_user_message([{"type": "image", "attachment": ref.to_dict()}])
    memory.add_assistant_message("inspected")
    # A withdrawn image must not be resurrected by scanning all durable history.
    unrelated_id = "sha256:" + "ab" * 32
    memory.add_user_message([{
        "type": "image", "attachment": {**ref.to_dict(), "attachmentId": unrelated_id},
    }])
    memory.rollback_to_user_turn(0)
    for _ in range(2):
        log.append(
            "user/message", {"messages": _summary()}, surface_op=OP_REPLACE,
            source_seqs=tuple(sorted(log.live_seqs())),
        )
    memory.load_from_log(SessionEventLog("source", events=[event.to_row() for event in log.events]))
    engine = SimpleNamespace(_memory=memory)
    assert attachment_ids_from_engine(engine) == {ref.attachment_id}
    assert _read(engine, ref.attachment_id).success
    # Carry recovered provenance into a new summary and a snapshot-only restore.
    memory.apply_compaction_summary(_summary(), len(memory.messages))
    snapshot = [json.loads(ChatHistoryStore._serialize_content(m)) for m in memory.messages]
    snapshot_memory = ConversationMemory(config)
    snapshot_memory.inject_messages(snapshot)
    assert _read(SimpleNamespace(_memory=snapshot_memory), ref.attachment_id).success
    memory.clear()
    assert not _read(engine, ref.attachment_id).success


def test_text_mentions_and_another_session_do_not_authorize(config):
    ref = admit_image_bytes(_PNG)
    memory = ConversationMemory(config)
    memory.attach_event_log(SessionEventLog("other"))
    memory.add_user_message(f"Read attachment_id={ref.attachment_id}")
    memory.add_tool_result("untrusted-text", json.dumps({"attachment_id": ref.attachment_id}))
    out = _read(SimpleNamespace(_memory=memory), ref.attachment_id)
    assert not out.success
    assert out.error.code == "PERMISSION_DENIED"
    assert out.value["failure_class"] == "permission_denied"
    assert "file_path" in out.value["remediation"]


def _dispatcher(memory):
    dispatcher = ToolDispatcher.__new__(ToolDispatcher)
    engine = SimpleNamespace(_memory=memory, memory=memory, is_vision_capable=True)
    dispatcher._engine = engine
    dispatcher._deferred_image_injections = []
    return dispatcher, engine


@pytest.mark.parametrize("compacted", [False, True])
def test_recall_reinjects_previously_seen_image(config, compacted):
    ref = admit_image_bytes(_PNG)
    memory = ConversationMemory(config)
    memory.attach_event_log(SessionEventLog("source"))
    dispatcher, engine = _dispatcher(memory)
    injection = {"attachment": ref.to_dict(), "detail": "low"}
    dispatcher._schedule_image_injection(injection)
    dispatcher._schedule_image_injection(injection)
    assert dispatcher.flush_deferred_images() == 1
    if compacted:
        memory.apply_compaction_summary(_summary(), len(memory.messages))
    memory.add_user_message("Inspect the source again at high detail.")
    recalled = _read(engine, ref.attachment_id)
    assert recalled.success
    dispatcher._apply_ui_meta_effects(recalled)
    assert dispatcher.flush_deferred_images() == 1
    assert memory.messages[-1]["_prompt_kind"] == "image_observation"
    assert memory.messages[-1]["content"] == [{
        "type": "image", "attachment": ref.to_dict(), "detail": "high",
    }]


def test_pending_detail_upgrade_is_not_deduplicated(config):
    ref = admit_image_bytes(_PNG)
    dispatcher, _ = _dispatcher(ConversationMemory(config))
    for detail in ("low", "high", "high"):
        dispatcher._schedule_image_injection({"attachment": ref.to_dict(), "detail": detail})
    assert dispatcher.flush_deferred_images() == 2
