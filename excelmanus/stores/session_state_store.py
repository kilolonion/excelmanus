"""SessionStateStore：会话状态 checkpoint 持久化（支持 SQLite / PostgreSQL）。

职责：
- 保存会话状态快照（SessionState + TaskStore）到 session_checkpoints 表
- 加载最新 checkpoint 用于会话恢复
- 按 session_id 清理旧 checkpoint
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, overload

from excelmanus.db_adapter import ConnectionAdapter

if TYPE_CHECKING:
    from excelmanus.database import Database

logger = logging.getLogger(__name__)

# 每个会话最多保留的 checkpoint 数量
_MAX_CHECKPOINTS_PER_SESSION = 20


class SessionStateStore:
    """会话状态 checkpoint 持久化。"""

    @overload
    def __init__(self, conn: ConnectionAdapter) -> None: ...
    @overload
    def __init__(self, conn: "Database") -> None: ...

    def __init__(self, conn: Any) -> None:
        if isinstance(conn, ConnectionAdapter):
            self._conn = conn
        else:
            self._conn = conn.conn

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    def save_checkpoint(
        self,
        *,
        session_id: str,
        state_dict: dict[str, Any],
        task_list_dict: dict[str, Any],
        turn_number: int = 0,
        checkpoint_type: str = "turn",
    ) -> int | None:
        """保存一个 checkpoint，返回记录 ID（失败返回 None）。"""
        try:
            state_json = json.dumps(state_dict, ensure_ascii=False, default=str)
            task_json = json.dumps(task_list_dict, ensure_ascii=False, default=str)
            cursor = self._conn.execute(
                "INSERT INTO session_checkpoints "
                "(session_id, checkpoint_type, state_json, task_list_json, turn_number, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    session_id,
                    checkpoint_type,
                    state_json,
                    task_json,
                    turn_number,
                    self._now_iso(),
                ),
            )
            self._conn.commit()
            row_id = getattr(cursor, "lastrowid", None)

            # 清理旧 checkpoint（保留最新 N 条）
            self._cleanup_old_checkpoints(session_id)

            return row_id
        except Exception:
            logger.debug("保存 session checkpoint 失败", exc_info=True)
            return None

    def load_latest_checkpoint(
        self,
        session_id: str,
    ) -> dict[str, Any] | None:
        """加载指定会话的最新 checkpoint。

        返回 dict 包含 state_dict, task_list_dict, turn_number, created_at；
        无 checkpoint 时返回 None。
        """
        try:
            row = self._conn.execute(
                "SELECT state_json, task_list_json, turn_number, created_at "
                "FROM session_checkpoints "
                "WHERE session_id = ? "
                "ORDER BY turn_number DESC, id DESC "
                "LIMIT 1",
                (session_id,),
            ).fetchone()

            if row is None:
                return None

            return {
                "state_dict": json.loads(row["state_json"]),
                "task_list_dict": json.loads(row["task_list_json"]),
                "turn_number": row["turn_number"],
                "created_at": row["created_at"],
            }
        except Exception:
            logger.debug("加载 session checkpoint 失败", exc_info=True)
            return None

    def delete_checkpoints(self, session_id: str) -> int:
        """删除指定会话的所有 checkpoint，返回删除行数。"""
        try:
            cursor = self._conn.execute(
                "DELETE FROM session_checkpoints WHERE session_id = ?",
                (session_id,),
            )
            self._conn.commit()
            return getattr(cursor, "rowcount", 0) or 0
        except Exception:
            logger.debug("删除 session checkpoint 失败", exc_info=True)
            return 0

    def _cleanup_old_checkpoints(self, session_id: str) -> None:
        """保留最新 N 条 checkpoint，删除更早的。"""
        try:
            self._conn.execute(
                "DELETE FROM session_checkpoints "
                "WHERE session_id = ? AND id NOT IN ("
                "  SELECT id FROM session_checkpoints "
                "  WHERE session_id = ? "
                "  ORDER BY turn_number DESC, id DESC "
                f"  LIMIT {_MAX_CHECKPOINTS_PER_SESSION}"
                ")",
                (session_id, session_id),
            )
            self._conn.commit()
        except Exception:
            logger.debug("清理旧 checkpoint 失败", exc_info=True)
