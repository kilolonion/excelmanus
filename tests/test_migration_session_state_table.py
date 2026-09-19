"""v5 migration: session_checkpoints -> session_state_snapshots."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from excelmanus.database import Database
from excelmanus.stores.session_state_store import SessionStateStore


def _seed_v4_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    conn.executescript("""
        CREATE TABLE schema_version (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        INSERT INTO schema_version (version) VALUES (4);
        CREATE TABLE session_checkpoints (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id      TEXT NOT NULL,
            checkpoint_type TEXT NOT NULL DEFAULT 'turn',
            state_json      TEXT NOT NULL DEFAULT '{}',
            task_list_json  TEXT NOT NULL DEFAULT '{}',
            turn_number     INTEGER DEFAULT 0,
            created_at      TEXT NOT NULL
        );
        INSERT INTO session_checkpoints
            (session_id, checkpoint_type, state_json, task_list_json, turn_number, created_at)
        VALUES
            ('s1', 'turn', '{"a": 1}', '{"tasks": []}', 3, '2026-01-01T00:00:00+00:00');
    """)
    conn.commit()
    conn.close()


def test_v5_migrates_rows_and_drops_legacy_table(tmp_path: Path) -> None:
    db_path = tmp_path / "v4.db"
    _seed_v4_db(db_path)

    db = Database(str(db_path))
    assert db._adapter.table_exists("session_state_snapshots")
    assert not db._adapter.table_exists("session_checkpoints")

    store = SessionStateStore(db)
    loaded = store.load_latest_checkpoint("s1")
    assert loaded is not None
    assert loaded["state_dict"] == {"a": 1}
    assert loaded["task_list_dict"] == {"tasks": []}
    assert loaded["turn_number"] == 3

    store.save_session_snapshot(
        session_id="s1",
        state_dict={"b": 2},
        task_list_dict={"tasks": [1]},
        turn_number=4,
    )
    latest = store.load_latest_checkpoint("s1")
    assert latest is not None
    assert latest["state_dict"] == {"b": 2}
    assert latest["turn_number"] == 4
    db.close()


def test_fresh_db_creates_only_new_snapshot_table(tmp_path: Path) -> None:
    db = Database(str(tmp_path / "fresh.db"))
    assert db._adapter.table_exists("session_state_snapshots")
    assert not db._adapter.table_exists("session_checkpoints")
    db.close()


def test_schema_current_form_accepts_either_snapshot_table(tmp_path: Path) -> None:
    db = Database(str(tmp_path / "form.db"))
    assert db._schema_is_current_form() is True

    # 模拟旧当前形态：只有 session_checkpoints。
    db.conn.execute("DROP TABLE session_state_snapshots")
    db.conn.execute(
        """CREATE TABLE session_checkpoints (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id      TEXT NOT NULL,
            checkpoint_type TEXT NOT NULL DEFAULT 'turn',
            state_json      TEXT NOT NULL DEFAULT '{}',
            task_list_json  TEXT NOT NULL DEFAULT '{}',
            turn_number     INTEGER DEFAULT 0,
            created_at      TEXT NOT NULL
        )"""
    )
    db.conn.commit()
    assert db._schema_is_current_form() is True

    db.conn.execute("DROP TABLE session_checkpoints")
    db.conn.commit()
    assert db._schema_is_current_form() is False
    db.close()
