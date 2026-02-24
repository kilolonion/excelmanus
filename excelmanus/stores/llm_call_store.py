"""LLMCallStore：LLM 调用 / Token 用量追踪（支持 SQLite / PostgreSQL）。"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, overload

from excelmanus.db_adapter import ConnectionAdapter, user_filter_clause

if TYPE_CHECKING:
    from excelmanus.database import Database

logger = logging.getLogger(__name__)


class LLMCallStore:
    """LLM 调用审计日志（支持 SQLite / PostgreSQL）。"""

    @overload
    def __init__(self, conn: ConnectionAdapter, *, user_id: str | None = None) -> None: ...
    @overload
    def __init__(self, conn: "Database", *, user_id: str | None = None) -> None: ...

    def __init__(self, conn: Any, *, user_id: str | None = None) -> None:
        if isinstance(conn, ConnectionAdapter):
            self._conn = conn
        else:
            self._conn = conn.conn
        self._user_id = user_id
        self._uid_clause, self._uid_params = user_filter_clause("user_id", user_id)

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    def log(
        self,
        *,
        session_id: str | None = None,
        turn: int = 0,
        iteration: int = 0,
        model: str,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        cached_tokens: int = 0,
        total_tokens: int = 0,
        has_tool_calls: bool = False,
        thinking_chars: int = 0,
        stream: bool = False,
        latency_ms: float = 0.0,
        ttft_ms: float = 0.0,
        cache_creation_tokens: int = 0,
        cache_read_tokens: int = 0,
        error: str | None = None,
    ) -> None:
        """写入一条 LLM 调用记录。"""
        try:
            self._conn.execute(
                "INSERT INTO llm_call_log "
                "(session_id, turn, iteration, model, "
                " prompt_tokens, completion_tokens, cached_tokens, total_tokens, "
                " has_tool_calls, thinking_chars, stream, latency_ms, "
                " ttft_ms, cache_creation_tokens, cache_read_tokens, "
                " error, created_at, user_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    session_id,
                    turn,
                    iteration,
                    model,
                    prompt_tokens,
                    completion_tokens,
                    cached_tokens,
                    total_tokens if total_tokens else prompt_tokens + completion_tokens,
                    1 if has_tool_calls else 0,
                    thinking_chars,
                    1 if stream else 0,
                    round(latency_ms, 1),
                    round(ttft_ms, 1),
                    cache_creation_tokens,
                    cache_read_tokens,
                    (error or "")[:500] if error else None,
                    self._now_iso(),
                    self._user_id,
                ),
            )
            self._conn.commit()
        except Exception:
            logger.debug("写入 LLM 调用日志失败", exc_info=True)

    def query(
        self,
        *,
        session_id: str | None = None,
        model: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """查询 LLM 调用记录。"""
        conditions: list[str] = [self._uid_clause]
        params: list[Any] = list(self._uid_params)

        if session_id is not None:
            conditions.append("session_id = ?")
            params.append(session_id)
        if model is not None:
            conditions.append("model = ?")
            params.append(model)

        where = f"WHERE {' AND '.join(conditions)}"
        sql = (
            f"SELECT * FROM llm_call_log {where} "
            f"ORDER BY created_at DESC LIMIT ? OFFSET ?"
        )
        params.extend([limit, offset])
        rows = self._conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def stats(self, session_id: str | None = None) -> dict[str, Any]:
        """聚合统计：调用次数、总 token、平均延迟、按模型分组。"""
        conditions: list[str] = [self._uid_clause]
        params: list[Any] = list(self._uid_params)
        if session_id:
            conditions.append("session_id = ?")
            params.append(session_id)
        where = f"WHERE {' AND '.join(conditions)}"

        row = self._conn.execute(
            f"SELECT "
            f"  COUNT(*) as total_calls, "
            f"  COALESCE(SUM(prompt_tokens), 0) as total_prompt_tokens, "
            f"  COALESCE(SUM(completion_tokens), 0) as total_completion_tokens, "
            f"  COALESCE(SUM(cached_tokens), 0) as total_cached_tokens, "
            f"  COALESCE(SUM(total_tokens), 0) as total_tokens, "
            f"  AVG(latency_ms) as avg_latency_ms, "
            f"  SUM(CASE WHEN error IS NOT NULL THEN 1 ELSE 0 END) as error_count "
            f"FROM llm_call_log {where}",
            params,
        ).fetchone()

        total_calls = row["total_calls"] or 0

        # 按模型分组统计
        model_rows = self._conn.execute(
            f"SELECT model, COUNT(*) as calls, "
            f"  COALESCE(SUM(prompt_tokens), 0) as prompt_tok, "
            f"  COALESCE(SUM(completion_tokens), 0) as completion_tok, "
            f"  COALESCE(SUM(total_tokens), 0) as total_tok, "
            f"  AVG(latency_ms) as avg_ms "
            f"FROM llm_call_log {where} "
            f"GROUP BY model ORDER BY total_tok DESC",
            params,
        ).fetchall()

        return {
            "total_calls": total_calls,
            "total_prompt_tokens": row["total_prompt_tokens"],
            "total_completion_tokens": row["total_completion_tokens"],
            "total_cached_tokens": row["total_cached_tokens"],
            "total_tokens": row["total_tokens"],
            "avg_latency_ms": round(row["avg_latency_ms"] or 0, 1),
            "error_count": row["error_count"],
            "by_model": [
                {
                    "model": r["model"],
                    "calls": r["calls"],
                    "prompt_tokens": r["prompt_tok"],
                    "completion_tokens": r["completion_tok"],
                    "total_tokens": r["total_tok"],
                    "avg_latency_ms": round(r["avg_ms"] or 0, 1),
                }
                for r in model_rows
            ],
        }

    def session_summary(self, session_id: str) -> dict[str, Any]:
        """单会话 token 用量摘要。"""
        return self.stats(session_id=session_id)
