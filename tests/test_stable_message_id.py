"""Tests for stable message_id injection in ConversationMemory.

P2-B: Every add_* method should inject a uuid4().hex message_id,
eliminating the need for volatile: fallback IDs.
"""

from __future__ import annotations

import re
from unittest.mock import MagicMock

from excelmanus.config import ExcelManusConfig
from excelmanus.memory import ConversationMemory


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_UUID_HEX_RE = re.compile(r"^[0-9a-f]{32}$")


def _make_memory() -> ConversationMemory:
    config = MagicMock(spec=ExcelManusConfig)
    config.max_context_tokens = 100_000
    config.image_keep_rounds = 3
    config.image_max_active = 2
    config.image_token_budget = 6000
    return ConversationMemory(config)


def _last_msg(mem: ConversationMemory) -> dict:
    return mem.messages[-1]


# ---------------------------------------------------------------------------
# Tests: every add_* injects a message_id
# ---------------------------------------------------------------------------


class TestAddUserMessage:
    def test_plain_text_has_message_id(self):
        mem = _make_memory()
        mem.add_user_message("hello")
        mid = _last_msg(mem).get("message_id")
        assert mid is not None
        assert _UUID_HEX_RE.match(mid)

    def test_multimodal_with_image_has_message_id(self):
        mem = _make_memory()
        content = [
            {"type": "text", "text": "describe"},
            {
                "type": "image_url",
                "image_url": {
                    "url": "data:image/png;base64,iVBOR",
                    "detail": "auto",
                },
            },
        ]
        mem.add_user_message(content)
        mid = _last_msg(mem).get("message_id")
        assert mid is not None
        assert _UUID_HEX_RE.match(mid)


class TestAddImageMessage:
    def test_has_message_id(self):
        mem = _make_memory()
        mem.add_image_message("iVBOR", "image/png", "auto")
        mid = _last_msg(mem).get("message_id")
        assert mid is not None
        assert _UUID_HEX_RE.match(mid)


class TestAddAssistantMessage:
    def test_has_message_id(self):
        mem = _make_memory()
        mem.add_assistant_message("sure, here you go")
        mid = _last_msg(mem).get("message_id")
        assert mid is not None
        assert _UUID_HEX_RE.match(mid)


class TestAddToolCall:
    def test_has_message_id(self):
        mem = _make_memory()
        mem.add_tool_call("tc-1", "read_excel", '{"path": "a.xlsx"}')
        mid = _last_msg(mem).get("message_id")
        assert mid is not None
        assert _UUID_HEX_RE.match(mid)


class TestAddAssistantToolMessage:
    def test_injects_message_id_when_missing(self):
        mem = _make_memory()
        mem.add_assistant_tool_message({
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "tc-2", "function": {"name": "f", "arguments": "{}"}}],
        })
        mid = _last_msg(mem).get("message_id")
        assert mid is not None
        assert _UUID_HEX_RE.match(mid)

    def test_preserves_existing_message_id(self):
        mem = _make_memory()
        mem.add_assistant_tool_message({
            "role": "assistant",
            "content": None,
            "message_id": "custom-id-from-provider",
            "tool_calls": [{"id": "tc-3", "function": {"name": "g", "arguments": "{}"}}],
        })
        assert _last_msg(mem)["message_id"] == "custom-id-from-provider"


class TestAddToolResult:
    def test_has_message_id(self):
        mem = _make_memory()
        mem.add_tool_result("tc-1", '{"status": "ok"}')
        mid = _last_msg(mem).get("message_id")
        assert mid is not None
        assert _UUID_HEX_RE.match(mid)


# ---------------------------------------------------------------------------
# Cross-cutting: uniqueness
# ---------------------------------------------------------------------------


class TestMessageIdUniqueness:
    def test_all_ids_unique_across_add_methods(self):
        mem = _make_memory()
        mem.add_user_message("q1")
        mem.add_assistant_message("a1")
        mem.add_tool_call("tc-1", "read_excel", "{}")
        mem.add_tool_result("tc-1", "ok")
        mem.add_user_message("q2")
        mem.add_assistant_tool_message({
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "tc-2", "function": {"name": "f", "arguments": "{}"}}],
        })
        mem.add_image_message("iVBOR", "image/png")

        ids = [m["message_id"] for m in mem.messages]
        assert len(ids) == 7
        assert len(set(ids)) == 7, "message_ids must be unique"

    def test_no_volatile_prefix(self):
        mem = _make_memory()
        mem.add_user_message("hi")
        mem.add_assistant_message("hello")
        for m in mem.messages:
            mid = m.get("message_id", "")
            assert not mid.startswith("volatile:"), f"unexpected volatile id: {mid}"


# ---------------------------------------------------------------------------
# Persistence round-trip
# ---------------------------------------------------------------------------


class TestPersistenceRoundTrip:
    """message_id survives save_turn_messages → load_messages."""

    def test_roundtrip_preserves_message_id(self, tmp_path):
        from excelmanus.chat_history import ChatHistoryStore
        from excelmanus.db_adapter import ConnectionAdapter

        import sqlite3

        db_path = str(tmp_path / "test.db")
        raw_conn = sqlite3.connect(db_path)
        raw_conn.row_factory = sqlite3.Row
        raw_conn.execute(
            "CREATE TABLE IF NOT EXISTS sessions "
            "(id TEXT PRIMARY KEY, title TEXT, created_at TEXT, updated_at TEXT, "
            "user_id TEXT, title_source TEXT DEFAULT 'fallback', "
            "status TEXT DEFAULT 'active', message_count INTEGER DEFAULT 0)"
        )
        raw_conn.execute(
            "CREATE TABLE IF NOT EXISTS messages "
            "(id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT, "
            "content TEXT, turn_number INTEGER DEFAULT 0, created_at TEXT)"
        )
        raw_conn.commit()

        conn = ConnectionAdapter(raw_conn, "sqlite")
        store = ChatHistoryStore(conn)
        store.create_session("s1", "test session")

        mem = _make_memory()
        mem.add_user_message("hello")
        mem.add_assistant_message("world")

        original_ids = [m["message_id"] for m in mem.messages]
        assert len(original_ids) == 2

        store.save_turn_messages("s1", mem.messages)
        loaded = store.load_messages("s1")

        assert len(loaded) == 2
        for i, msg in enumerate(loaded):
            assert msg.get("message_id") == original_ids[i], (
                f"message_id lost after round-trip at index {i}"
            )

        raw_conn.close()
