"""历史会话感知功能测试。

覆盖：
- SessionSummaryStore CRUD + 去重 + user_id 隔离
- SessionSummarizer 解析逻辑
- ContextBuilder._build_session_history_notice 门控
- Engine._search_session_history 混合检索
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

# ── SessionSummaryStore 测试 ──────────────────────────────────


def _make_memory_db():
    """创建内存 SQLite 数据库并执行 session_summaries 表 DDL。"""
    from excelmanus.db_adapter import ConnectionAdapter, Backend

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    adapter = ConnectionAdapter(conn, Backend.SQLITE)

    adapter.execute("""CREATE TABLE IF NOT EXISTS session_summaries (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id      TEXT NOT NULL UNIQUE,
        user_id         TEXT,
        summary_text    TEXT NOT NULL,
        task_goal       TEXT DEFAULT '',
        files_involved  TEXT DEFAULT '[]',
        outcome         TEXT DEFAULT '',
        unfinished      TEXT DEFAULT '',
        embedding       BLOB,
        token_count     INTEGER DEFAULT 0,
        created_at      TEXT NOT NULL,
        updated_at      TEXT NOT NULL
    )""")
    adapter.execute("CREATE INDEX IF NOT EXISTS idx_ss_user ON session_summaries(user_id)")
    adapter.commit()
    return adapter


class TestSessionSummaryStore:
    def test_upsert_and_get(self):
        from excelmanus.stores.session_summary_store import SessionSummary, SessionSummaryStore
        adapter = _make_memory_db()
        store = SessionSummaryStore(adapter)

        s = SessionSummary(
            session_id="sess-1",
            summary_text="合并了两个文件",
            user_id="user-a",
            task_goal="合并月度报表",
            files_involved=["a.xlsx", "b.xlsx"],
            outcome="completed",
            token_count=50,
        )
        store.upsert(s)

        loaded = store.get_by_session("sess-1")
        assert loaded is not None
        assert loaded.task_goal == "合并月度报表"
        assert loaded.files_involved == ["a.xlsx", "b.xlsx"]
        assert loaded.outcome == "completed"
        assert loaded.token_count == 50

    def test_upsert_updates_existing(self):
        from excelmanus.stores.session_summary_store import SessionSummary, SessionSummaryStore
        adapter = _make_memory_db()
        store = SessionSummaryStore(adapter)

        s1 = SessionSummary(
            session_id="sess-1",
            summary_text="v1",
            user_id="user-a",
            task_goal="goal-v1",
            outcome="partial",
        )
        store.upsert(s1)

        s2 = SessionSummary(
            session_id="sess-1",
            summary_text="v2-updated",
            user_id="user-a",
            task_goal="goal-v2",
            outcome="completed",
        )
        store.upsert(s2)

        loaded = store.get_by_session("sess-1")
        assert loaded is not None
        assert loaded.summary_text == "v2-updated"
        assert loaded.task_goal == "goal-v2"
        assert loaded.outcome == "completed"

    def test_delete(self):
        from excelmanus.stores.session_summary_store import SessionSummary, SessionSummaryStore
        adapter = _make_memory_db()
        store = SessionSummaryStore(adapter)

        s = SessionSummary(session_id="sess-1", summary_text="text", user_id="u")
        store.upsert(s)
        assert store.get_by_session("sess-1") is not None

        assert store.delete("sess-1") is True
        assert store.get_by_session("sess-1") is None
        assert store.delete("sess-1") is False

    def test_list_recent_user_isolation(self):
        from excelmanus.stores.session_summary_store import SessionSummary, SessionSummaryStore
        adapter = _make_memory_db()
        store = SessionSummaryStore(adapter)

        store.upsert(SessionSummary(session_id="s1", summary_text="t1", user_id="alice"))
        store.upsert(SessionSummary(session_id="s2", summary_text="t2", user_id="bob"))
        store.upsert(SessionSummary(session_id="s3", summary_text="t3", user_id="alice"))

        alice_summaries = store.list_recent(user_id="alice")
        assert len(alice_summaries) == 2
        assert all(s.user_id == "alice" for s in alice_summaries)

        bob_summaries = store.list_recent(user_id="bob")
        assert len(bob_summaries) == 1

    def test_search_by_files(self):
        from excelmanus.stores.session_summary_store import SessionSummary, SessionSummaryStore
        adapter = _make_memory_db()
        store = SessionSummaryStore(adapter)

        store.upsert(SessionSummary(
            session_id="s1",
            summary_text="合并报表",
            user_id="u",
            files_involved=["/data/sales.xlsx", "/data/report.xlsx"],
        ))
        store.upsert(SessionSummary(
            session_id="s2",
            summary_text="清洗数据",
            user_id="u",
            files_involved=["/data/customers.xlsx"],
        ))

        matched = store.search_by_files(["/other/sales.xlsx"], user_id="u")
        assert len(matched) == 1
        assert matched[0].session_id == "s1"

        matched2 = store.search_by_files(["/some/path/customers.xlsx"], user_id="u")
        assert len(matched2) == 1
        assert matched2[0].session_id == "s2"

        no_match = store.search_by_files(["/none.xlsx"], user_id="u")
        assert len(no_match) == 0

    def test_count(self):
        from excelmanus.stores.session_summary_store import SessionSummary, SessionSummaryStore
        adapter = _make_memory_db()
        store = SessionSummaryStore(adapter)

        assert store.count() == 0
        store.upsert(SessionSummary(session_id="s1", summary_text="t1", user_id="u"))
        store.upsert(SessionSummary(session_id="s2", summary_text="t2", user_id="u"))
        assert store.count() == 2
        assert store.count(user_id="u") == 2
        assert store.count(user_id="other") == 0

    def test_embedding_roundtrip(self):
        from excelmanus.stores.session_summary_store import SessionSummary, SessionSummaryStore
        adapter = _make_memory_db()
        store = SessionSummaryStore(adapter)

        vec = np.array([0.1, 0.2, 0.3], dtype=np.float32)
        s = SessionSummary(
            session_id="s1",
            summary_text="text",
            embedding=vec,
        )
        store.upsert(s)

        loaded = store.get_by_session("s1")
        assert loaded is not None
        assert loaded.embedding is not None
        np.testing.assert_array_almost_equal(loaded.embedding, vec)


# ── SessionSummarizer 解析测试 ────────────────────────────────


class TestSessionSummarizer:
    def test_parse_response_valid_json(self):
        from excelmanus.session_summarizer import SessionSummarizer
        raw = json.dumps({
            "task_goal": "合并报表",
            "files_involved": ["a.xlsx", "b.xlsx"],
            "outcome": "completed",
            "unfinished": "",
            "summary": "成功合并了两个文件",
        })
        result = SessionSummarizer._parse_response(raw)
        assert result is not None
        assert result["task_goal"] == "合并报表"
        assert result["files_involved"] == ["a.xlsx", "b.xlsx"]
        assert result["outcome"] == "completed"

    def test_parse_response_markdown_wrapped(self):
        from excelmanus.session_summarizer import SessionSummarizer
        raw = '```json\n{"task_goal": "test", "summary": "ok"}\n```'
        result = SessionSummarizer._parse_response(raw)
        assert result is not None
        assert result["task_goal"] == "test"

    def test_parse_response_invalid_json(self):
        from excelmanus.session_summarizer import SessionSummarizer
        result = SessionSummarizer._parse_response("this is not json")
        assert result is None

    def test_parse_response_empty(self):
        from excelmanus.session_summarizer import SessionSummarizer
        assert SessionSummarizer._parse_response(None) is None
        assert SessionSummarizer._parse_response("") is None

    def test_parse_response_normalizes_outcome(self):
        from excelmanus.session_summarizer import SessionSummarizer
        raw = json.dumps({
            "task_goal": "t",
            "outcome": "COMPLETED",
            "summary": "done",
        })
        result = SessionSummarizer._parse_response(raw)
        assert result is not None
        assert result["outcome"] == "completed"

    def test_parse_response_unknown_outcome_defaults_partial(self):
        from excelmanus.session_summarizer import SessionSummarizer
        raw = json.dumps({
            "task_goal": "t",
            "outcome": "unknown_status",
            "summary": "done",
        })
        result = SessionSummarizer._parse_response(raw)
        assert result is not None
        assert result["outcome"] == "partial"


# ── ContextBuilder 门控测试 ───────────────────────────────────


class TestBuildSessionHistoryNotice:
    def test_returns_empty_after_turn_1(self):
        from excelmanus.engine_core.context_builder import ContextBuilder
        engine = MagicMock()
        engine._session_turn = 5
        engine._relevant_session_history = "## 历史会话参考\n..."
        cb = ContextBuilder(engine)
        assert cb._build_session_history_notice() == ""

    def test_returns_text_on_turn_0(self):
        from excelmanus.engine_core.context_builder import ContextBuilder
        engine = MagicMock()
        engine._session_turn = 0
        engine._relevant_session_history = "## 历史会话参考\nsome history"
        cb = ContextBuilder(engine)
        result = cb._build_session_history_notice()
        assert "历史会话参考" in result

    def test_returns_text_on_turn_1(self):
        from excelmanus.engine_core.context_builder import ContextBuilder
        engine = MagicMock()
        engine._session_turn = 1
        engine._relevant_session_history = "## 历史会话参考\nsome history"
        cb = ContextBuilder(engine)
        result = cb._build_session_history_notice()
        assert "历史会话参考" in result

    def test_returns_empty_when_no_history(self):
        from excelmanus.engine_core.context_builder import ContextBuilder
        engine = MagicMock()
        engine._session_turn = 0
        engine._relevant_session_history = ""
        cb = ContextBuilder(engine)
        assert cb._build_session_history_notice() == ""

    def test_returns_empty_when_whitespace_only(self):
        from excelmanus.engine_core.context_builder import ContextBuilder
        engine = MagicMock()
        engine._session_turn = 0
        engine._relevant_session_history = "   \n  "
        cb = ContextBuilder(engine)
        assert cb._build_session_history_notice() == ""
