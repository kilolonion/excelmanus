"""ToolCallStore：基于 SQLite 的工具调用审计日志。"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from excelmanus.database import Database

logger = logging.getLogger(__name__)


class ToolCallStore:
    """SQLite 后端的工具调用审计日志。"""

    def __init__(self, database: "Database") -> None:
        self._conn = database.conn

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    def log(
        self,
        *,
        session_id: str | None = None,
        turn: int = 0,
        iteration: int = 0,
        tool_name: str,
        arguments_hash: str | None = None,
        success: bool,
        duration_ms: float = 0.0,
        result_chars: int = 0,
        error_type: str | None = None,
        error_preview: str | None = None,
    ) -> None:
        """写入一条工具调用记录。"""
        try:
            self._conn.execute(
                "INSERT INTO tool_call_log "
                "(session_id, turn, iteration, tool_name, arguments_hash, "
                " success, duration_ms, result_chars, error_type, error_preview, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    session_id,
                    turn,
                    iteration,
                    tool_name,
                    arguments_hash,
                    1 if success else 0,
                    round(duration_ms, 1),
                    result_chars,
                    error_type,
                    (error_preview or "")[:200] if error_preview else None,
                    self._now_iso(),
                ),
            )
            self._conn.commit()
        except Exception:
            logger.debug("写入工具调用日志失败", exc_info=True)

    def query(
        self,
        *,
        session_id: str | None = None,
        tool_name: str | None = None,
        success: bool | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """查询工具调用记录。"""
        conditions: list[str] = []
        params: list[Any] = []

        if session_id is not None:
            conditions.append("session_id = ?")
            params.append(session_id)
        if tool_name is not None:
            conditions.append("tool_name = ?")
            params.append(tool_name)
        if success is not None:
            conditions.append("success = ?")
            params.append(1 if success else 0)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        sql = (
            f"SELECT * FROM tool_call_log {where} "
            f"ORDER BY created_at DESC LIMIT ? OFFSET ?"
        )
        params.extend([limit, offset])
        rows = self._conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def stats(self, session_id: str | None = None) -> dict[str, Any]:
        """聚合统计：调用次数、成功率、平均耗时、top 失败工具。"""
        where = "WHERE session_id = ?" if session_id else ""
        params: list[Any] = [session_id] if session_id else []

        row = self._conn.execute(
            f"SELECT COUNT(*) as total, "
            f"SUM(success) as successes, "
            f"AVG(duration_ms) as avg_duration_ms "
            f"FROM tool_call_log {where}",
            params,
        ).fetchone()

        total = row["total"] or 0
        successes = row["successes"] or 0
        avg_ms = round(row["avg_duration_ms"] or 0, 1)

        # top 失败工具
        fail_rows = self._conn.execute(
            f"SELECT tool_name, COUNT(*) as cnt "
            f"FROM tool_call_log {where} "
            f"{'AND' if where else 'WHERE'} success = 0 "
            f"GROUP BY tool_name ORDER BY cnt DESC LIMIT 5",
            params,
        ).fetchall()

        return {
            "total_calls": total,
            "successes": successes,
            "failures": total - successes,
            "success_rate": round(successes / total, 3) if total else 0,
            "avg_duration_ms": avg_ms,
            "top_failing_tools": [
                {"tool_name": r["tool_name"], "count": r["cnt"]}
                for r in fail_rows
            ],
        }
