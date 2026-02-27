"""旧数据自动迁移到统一 SQLite 数据库的端到端测试。"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest

from excelmanus.database import Database, migrate_legacy_data


class TestMigrateMemoryFiles:
    """从旧 Markdown 记忆文件迁移。"""

    def test_migrate_memory_md_files(self, tmp_path: Path) -> None:
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        # 创建旧格式记忆文件
        (memory_dir / "MEMORY.md").write_text(
            "### [2026-01-15 10:30] general\n\n"
            "这是一条通用记忆\n\n---\n\n"
            "### [2026-01-15 11:00] file_pattern\n\n"
            "CSV 文件用逗号分隔\n\n---\n",
            encoding="utf-8",
        )
        (memory_dir / "file_patterns.md").write_text(
            "### [2026-01-15 11:00] file_pattern\n\n"
            "CSV 文件用逗号分隔\n\n---\n",
            encoding="utf-8",
        )

        db = Database(str(tmp_path / "test.db"))
        migrate_legacy_data(db, memory_dir=str(memory_dir))

        # 应该有 2 条去重后的记忆（MEMORY.md 和 file_patterns.md 中的 file_pattern 条目重复）
        count = db.conn.execute(
            "SELECT COUNT(*) as cnt FROM memory_entries"
        ).fetchone()["cnt"]
        assert count == 2

        # 验证内容
        rows = db.conn.execute(
            "SELECT category, content FROM memory_entries ORDER BY created_at"
        ).fetchall()
        assert rows[0]["category"] == "general"
        assert "通用记忆" in rows[0]["content"]
        assert rows[1]["category"] == "file_pattern"
        db.close()

    def test_migrate_empty_memory_dir(self, tmp_path: Path) -> None:
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        db = Database(str(tmp_path / "test.db"))
        migrate_legacy_data(db, memory_dir=str(memory_dir))
        count = db.conn.execute(
            "SELECT COUNT(*) as cnt FROM memory_entries"
        ).fetchone()["cnt"]
        assert count == 0
        db.close()

    def test_migrate_nonexistent_memory_dir(self, tmp_path: Path) -> None:
        db = Database(str(tmp_path / "test.db"))
        # 不应抛异常
        migrate_legacy_data(db, memory_dir=str(tmp_path / "nonexistent"))
        db.close()


class TestMigrateVectorFiles:
    """从旧 JSONL + npy 向量文件迁移。"""

    def test_migrate_vectors(self, tmp_path: Path) -> None:
        vectors_dir = tmp_path / "vectors"
        vectors_dir.mkdir()

        # 创建旧格式向量文件
        records = [
            {"content_hash": "abc123", "text": "hello world", "metadata": {"k": "v"}},
            {"content_hash": "def456", "text": "foo bar", "metadata": {}},
        ]
        (vectors_dir / "vectors.jsonl").write_text(
            "\n".join(json.dumps(r) for r in records),
            encoding="utf-8",
        )
        vecs = np.array([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]], dtype=np.float32)
        np.save(str(vectors_dir / "vectors.npy"), vecs)

        db = Database(str(tmp_path / "test.db"))
        migrate_legacy_data(db, vectors_dir=str(vectors_dir))

        count = db.conn.execute(
            "SELECT COUNT(*) as cnt FROM vector_records"
        ).fetchone()["cnt"]
        assert count == 2

        # 验证向量精度
        row = db.conn.execute(
            "SELECT vector, dimensions FROM vector_records WHERE content_hash = 'abc123'"
        ).fetchone()
        vec = np.frombuffer(row["vector"], dtype=np.float32)
        np.testing.assert_allclose(vec, [1.0, 2.0, 3.0])
        assert row["dimensions"] == 3
        db.close()

    def test_migrate_vectors_no_npy(self, tmp_path: Path) -> None:
        """只有 JSONL 没有 npy 时仍应迁移文本和元数据。"""
        vectors_dir = tmp_path / "vectors"
        vectors_dir.mkdir()
        records = [
            {"content_hash": "abc123", "text": "hello", "metadata": {}},
        ]
        (vectors_dir / "vectors.jsonl").write_text(
            json.dumps(records[0]),
            encoding="utf-8",
        )

        db = Database(str(tmp_path / "test.db"))
        migrate_legacy_data(db, vectors_dir=str(vectors_dir))

        count = db.conn.execute(
            "SELECT COUNT(*) as cnt FROM vector_records"
        ).fetchone()["cnt"]
        assert count == 1
        db.close()


class TestMigrateApprovals:
    """从旧 manifest.json 审批文件迁移。"""

    def test_migrate_manifest_files(self, tmp_path: Path) -> None:
        audit_dir = tmp_path / "approvals"
        appr_dir = audit_dir / "appr_001"
        appr_dir.mkdir(parents=True)

        manifest = {
            "version": 2,
            "approval": {
                "approval_id": "appr_001",
                "tool_name": "write_cells",
                "arguments": {"file": "test.xlsx"},
                "tool_scope": [],
                "created_at_utc": "2026-01-15T10:30:00+00:00",
                "applied_at_utc": "2026-01-15T10:30:05+00:00",
                "undoable": True,
            },
            "execution": {
                "status": "success",
                "result_preview": "done",
                "partial_scan": False,
            },
            "artifacts": {
                "changes": [],
                "binary_snapshots": [],
            },
        }
        (appr_dir / "manifest.json").write_text(
            json.dumps(manifest),
            encoding="utf-8",
        )

        db = Database(str(tmp_path / "test.db"))
        migrate_legacy_data(db, audit_dir=str(audit_dir))

        count = db.conn.execute(
            "SELECT COUNT(*) as cnt FROM approvals"
        ).fetchone()["cnt"]
        assert count == 1

        row = db.conn.execute(
            "SELECT * FROM approvals WHERE id = 'appr_001'"
        ).fetchone()
        assert row["tool_name"] == "write_cells"
        assert row["execution_status"] == "success"
        db.close()


class TestMigrateChatHistoryDB:
    """从旧 chat_history.db 迁移会话数据。"""

    def test_migrate_old_chat_db(self, tmp_path: Path) -> None:
        old_db_path = tmp_path / "chat_history.db"
        # 创建旧的 chat_history.db
        old_conn = sqlite3.connect(str(old_db_path))
        old_conn.executescript("""
            CREATE TABLE sessions (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                message_count INTEGER DEFAULT 0,
                status TEXT DEFAULT 'active'
            );
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL REFERENCES sessions(id),
                role TEXT NOT NULL,
                content TEXT,
                turn_number INTEGER DEFAULT 0,
                created_at TEXT NOT NULL
            );
            INSERT INTO sessions VALUES ('s1', 'Test', '2026-01-15', '2026-01-15', 1, 'active');
            INSERT INTO messages (session_id, role, content, turn_number, created_at)
                VALUES ('s1', 'user', '{"role":"user","content":"hi"}', 0, '2026-01-15');
        """)
        old_conn.commit()
        old_conn.close()

        db = Database(str(tmp_path / "excelmanus.db"))
        migrate_legacy_data(db, old_chat_db_path=str(old_db_path))

        # 会话数据应被迁移
        count = db.conn.execute(
            "SELECT COUNT(*) as cnt FROM sessions"
        ).fetchone()["cnt"]
        assert count == 1

        msg_count = db.conn.execute(
            "SELECT COUNT(*) as cnt FROM messages"
        ).fetchone()["cnt"]
        assert msg_count == 1
        db.close()


class TestMigrationIdempotent:
    """迁移应是幂等的。"""

    def test_double_migration_no_duplicates(self, tmp_path: Path) -> None:
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        (memory_dir / "MEMORY.md").write_text(
            "### [2026-01-15 10:30] general\n\nhello\n\n---\n",
            encoding="utf-8",
        )

        db = Database(str(tmp_path / "test.db"))
        migrate_legacy_data(db, memory_dir=str(memory_dir))
        migrate_legacy_data(db, memory_dir=str(memory_dir))

        count = db.conn.execute(
            "SELECT COUNT(*) as cnt FROM memory_entries"
        ).fetchone()["cnt"]
        assert count == 1
        db.close()
