"""RulesStore：基于 SQLite 的会话级规则存储。"""
from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import TYPE_CHECKING

from excelmanus.rules import Rule

if TYPE_CHECKING:
    from excelmanus.database import Database

logger = logging.getLogger(__name__)


class RulesStore:
    """SQLite 后端的会话级规则 CRUD。"""

    def __init__(self, database: "Database") -> None:
        self._conn = database.conn

    def list_rules(self, session_id: str) -> list[Rule]:
        rows = self._conn.execute(
            "SELECT rule_id, content, enabled, created_at "
            "FROM session_rules WHERE session_id = ? ORDER BY created_at ASC",
            (session_id,),
        ).fetchall()
        return [
            Rule(
                id=row["rule_id"],
                content=row["content"],
                enabled=bool(row["enabled"]),
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def save_rule(self, session_id: str, rule: Rule) -> None:
        self._conn.execute(
            "INSERT INTO session_rules (session_id, rule_id, content, enabled, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (session_id, rule.id, rule.content, int(rule.enabled), rule.created_at),
        )
        self._conn.commit()

    def update_rule(
        self,
        session_id: str,
        rule_id: str,
        *,
        content: str | None = None,
        enabled: bool | None = None,
    ) -> Rule | None:
        updates: list[str] = []
        params: list[object] = []
        if content is not None:
            updates.append("content = ?")
            params.append(content.strip())
        if enabled is not None:
            updates.append("enabled = ?")
            params.append(int(enabled))
        if not updates:
            return None
        params.extend([session_id, rule_id])
        self._conn.execute(
            f"UPDATE session_rules SET {', '.join(updates)} "
            "WHERE session_id = ? AND rule_id = ?",
            tuple(params),
        )
        self._conn.commit()
        rows = self._conn.execute(
            "SELECT rule_id, content, enabled, created_at "
            "FROM session_rules WHERE session_id = ? AND rule_id = ?",
            (session_id, rule_id),
        ).fetchall()
        if not rows:
            return None
        row = rows[0]
        return Rule(
            id=row["rule_id"],
            content=row["content"],
            enabled=bool(row["enabled"]),
            created_at=row["created_at"],
        )

    def delete_rule(self, session_id: str, rule_id: str) -> bool:
        cursor = self._conn.execute(
            "DELETE FROM session_rules WHERE session_id = ? AND rule_id = ?",
            (session_id, rule_id),
        )
        self._conn.commit()
        return cursor.rowcount > 0
