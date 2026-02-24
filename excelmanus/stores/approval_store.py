"""ApprovalStore：审批审计记录存储（支持 SQLite / PostgreSQL）。"""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from excelmanus.database import Database

logger = logging.getLogger(__name__)


class ApprovalStore:
    """审批记录 CRUD（支持 SQLite / PostgreSQL）。

    文件产物（diff/patch/binary snapshot）仍保留在文件系统，
    此处仅持久化元数据。
    """

    def __init__(self, database: "Database") -> None:
        self._conn = database.conn

    def save(self, record: dict[str, Any]) -> None:
        """保存或更新审批记录（upsert）。"""
        self._conn.execute(
            "INSERT OR REPLACE INTO approvals ("
            "  id, tool_name, arguments, tool_scope,"
            "  created_at_utc, applied_at_utc, execution_status, undoable,"
            "  result_preview, error_type, error_message, partial_scan,"
            "  audit_dir, manifest_file, patch_file,"
            "  repo_diff_before, repo_diff_after,"
            "  changes, binary_snapshots"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                record["id"],
                record.get("tool_name", ""),
                json.dumps(record.get("arguments", {}), ensure_ascii=False),
                json.dumps(record.get("tool_scope", []), ensure_ascii=False),
                record.get("created_at_utc", ""),
                record.get("applied_at_utc"),
                record.get("execution_status", "pending"),
                1 if record.get("undoable") else 0,
                record.get("result_preview"),
                record.get("error_type"),
                record.get("error_message"),
                1 if record.get("partial_scan") else 0,
                record.get("audit_dir"),
                record.get("manifest_file"),
                record.get("patch_file"),
                record.get("repo_diff_before"),
                record.get("repo_diff_after"),
                json.dumps(record.get("changes", []), ensure_ascii=False),
                json.dumps(record.get("binary_snapshots", []), ensure_ascii=False),
            ),
        )
        self._conn.commit()

    def get(self, approval_id: str) -> dict[str, Any] | None:
        """按 ID 获取审批记录。"""
        row = self._conn.execute(
            "SELECT * FROM approvals WHERE id = ?", (approval_id,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_dict(row)

    def list_approvals(
        self,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """列出审批记录，可按状态过滤。"""
        if status:
            rows = self._conn.execute(
                "SELECT * FROM approvals WHERE execution_status = ? "
                "ORDER BY created_at_utc DESC LIMIT ? OFFSET ?",
                (status, limit, offset),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM approvals "
                "ORDER BY created_at_utc DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def delete(self, approval_id: str) -> bool:
        """删除审批记录。返回是否成功。"""
        cur = self._conn.execute(
            "DELETE FROM approvals WHERE id = ?", (approval_id,)
        )
        self._conn.commit()
        return cur.rowcount > 0

    @staticmethod
    def _row_to_dict(row: object) -> dict[str, Any]:
        """将数据库行转为标准 dict。"""
        d: dict[str, Any] = {}
        d["id"] = row["id"]  # type: ignore[index]
        d["tool_name"] = row["tool_name"]  # type: ignore[index]

        for json_field in ("arguments", "tool_scope", "changes", "binary_snapshots"):
            raw = row[json_field]  # type: ignore[index]
            try:
                d[json_field] = json.loads(raw) if raw else ([] if json_field != "arguments" else {})
            except (json.JSONDecodeError, TypeError):
                d[json_field] = [] if json_field != "arguments" else {}

        d["created_at_utc"] = row["created_at_utc"]  # type: ignore[index]
        d["applied_at_utc"] = row["applied_at_utc"]  # type: ignore[index]
        d["execution_status"] = row["execution_status"]  # type: ignore[index]
        d["undoable"] = bool(row["undoable"])  # type: ignore[index]
        d["result_preview"] = row["result_preview"]  # type: ignore[index]
        d["error_type"] = row["error_type"]  # type: ignore[index]
        d["error_message"] = row["error_message"]  # type: ignore[index]
        d["partial_scan"] = bool(row["partial_scan"])  # type: ignore[index]
        d["audit_dir"] = row["audit_dir"]  # type: ignore[index]
        d["manifest_file"] = row["manifest_file"]  # type: ignore[index]
        d["patch_file"] = row["patch_file"]  # type: ignore[index]
        d["repo_diff_before"] = row["repo_diff_before"]  # type: ignore[index]
        d["repo_diff_after"] = row["repo_diff_after"]  # type: ignore[index]
        return d
