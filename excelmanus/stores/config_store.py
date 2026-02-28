"""ConfigStore：模型配置与运行时状态存储（支持 SQLite / PostgreSQL）。

拆分为 GlobalConfigStore（全局 model_profiles + 部署设置）和
UserConfigStore（用户级偏好，如 active_model）。

旧 ``ConfigStore`` 保留为 ``GlobalConfigStore`` 的别名以兼容。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from excelmanus.db_adapter import user_filter_clause

if TYPE_CHECKING:
    from excelmanus.database import Database
    from excelmanus.db_adapter import ConnectionAdapter

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── GlobalConfigStore（全局配置）──────────────────────────────


class GlobalConfigStore:
    """全局配置：model_profiles（管理员管理）+ 部署级 KV。

    不包含任何用户级偏好（如 active_model），这些由 UserConfigStore 管理。
    """

    def __init__(self, database: "Database") -> None:
        self._conn = database.conn

    # ── model_profiles CRUD ──────────────────────────────

    def list_profiles(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT name, model, api_key, base_url, description, protocol "
            "FROM model_profiles ORDER BY id ASC"
        ).fetchall()
        return [dict(row) for row in rows]

    def get_profile(self, name: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT name, model, api_key, base_url, description, protocol "
            "FROM model_profiles WHERE name = ?",
            (name,),
        ).fetchone()
        return dict(row) if row else None

    def add_profile(
        self,
        name: str,
        model: str,
        api_key: str = "",
        base_url: str = "",
        description: str = "",
        protocol: str = "auto",
    ) -> bool:
        now = _now_iso()
        try:
            self._conn.execute(
                "INSERT INTO model_profiles (name, model, api_key, base_url, description, protocol, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (name, model, api_key, base_url, description, protocol, now, now),
            )
            self._conn.commit()
            return True
        except Exception:
            return False

    def update_profile(
        self,
        name: str,
        *,
        new_name: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        description: str | None = None,
        protocol: str | None = None,
    ) -> bool:
        sets: list[str] = []
        params: list[Any] = []
        if new_name is not None:
            sets.append("name = ?")
            params.append(new_name)
        if model is not None:
            sets.append("model = ?")
            params.append(model)
        if api_key is not None:
            sets.append("api_key = ?")
            params.append(api_key)
        if base_url is not None:
            sets.append("base_url = ?")
            params.append(base_url)
        if description is not None:
            sets.append("description = ?")
            params.append(description)
        if protocol is not None:
            sets.append("protocol = ?")
            params.append(protocol)
        if not sets:
            return False
        sets.append("updated_at = ?")
        params.append(_now_iso())
        params.append(name)
        cur = self._conn.execute(
            f"UPDATE model_profiles SET {', '.join(sets)} WHERE name = ?",
            params,
        )
        self._conn.commit()
        return cur.rowcount > 0

    def delete_profile(self, name: str) -> bool:
        cur = self._conn.execute(
            "DELETE FROM model_profiles WHERE name = ?", (name,)
        )
        self._conn.commit()
        return cur.rowcount > 0

    # ── 全局 config_kv（部署级设置） ──────────────────────

    def get(self, key: str, default: str = "") -> str:
        row = self._conn.execute(
            "SELECT value FROM config_kv WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else default

    def set(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT INTO config_kv (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (key, value, _now_iso()),
        )
        self._conn.commit()

    def delete_key(self, key: str) -> bool:
        cur = self._conn.execute("DELETE FROM config_kv WHERE key = ?", (key,))
        self._conn.commit()
        return cur.rowcount > 0

    def import_profiles_from_env(
        self,
        profiles_json: str,
        default_api_key: str = "",
        default_base_url: str = "",
    ) -> int:
        """从 EXCELMANUS_MODELS JSON 字符串导入 profiles（幂等）。"""
        import json

        if not profiles_json or not profiles_json.strip():
            return 0
        try:
            items = json.loads(profiles_json)
        except (json.JSONDecodeError, TypeError):
            return 0
        if not isinstance(items, list):
            return 0

        added = 0
        for item in items:
            if not isinstance(item, dict):
                continue
            name = (item.get("name") or "").strip()
            model = (item.get("model") or "").strip()
            if not name or not model:
                continue
            api_key = (item.get("api_key") or "").strip() or default_api_key
            base_url = (item.get("base_url") or "").strip() or default_base_url
            description = (item.get("description") or "").strip()
            protocol = (item.get("protocol") or "auto").strip().lower()
            if self.add_profile(name, model, api_key, base_url, description, protocol):
                added += 1
        return added


# ── UserConfigStore（用户级配置）──────────────────────────────


class UserConfigStore:
    """用户级配置：active_model 等偏好。

    每个用户的偏好存储在用户级 DB（SQLite 物理隔离）或
    全局 DB 的 user_config_kv 表（PostgreSQL 逻辑隔离）中。
    匿名模式下回退到全局 config_kv 以兼容旧行为。
    """

    def __init__(self, conn: "ConnectionAdapter", user_id: str | None = None) -> None:
        from excelmanus.db_adapter import ConnectionAdapter as _CA
        if isinstance(conn, _CA):
            self._conn = conn
        else:
            # 兼容旧调用方式（传入 Database 实例等）
            self._conn = conn.conn  # type: ignore[union-attr]
        self._user_id = user_id
        # 匿名模式使用 config_kv 表，认证模式使用 user_config_kv 表
        self._table = "config_kv" if user_id is None else "user_config_kv"
        self._uid_clause, self._uid_params = user_filter_clause("user_id", user_id)
        self._ensure_table()

    def _ensure_table(self) -> None:
        """确保 user_config_kv 表存在。"""
        try:
            if not self._conn.table_exists("user_config_kv"):
                self._conn.execute(
                    "CREATE TABLE IF NOT EXISTS user_config_kv ("
                    "  key TEXT NOT NULL,"
                    "  user_id TEXT,"
                    "  value TEXT NOT NULL,"
                    "  updated_at TEXT NOT NULL,"
                    "  UNIQUE(key, user_id)"
                    ")"
                )
                self._conn.commit()
        except Exception:
            logger.debug("user_config_kv 表创建失败", exc_info=True)

    def get(self, key: str, default: str = "") -> str:
        if self._user_id is None:
            # 匿名模式：从全局 config_kv 读取（无 user_id 列）
            row = self._conn.execute(
                "SELECT value FROM config_kv WHERE key = ?", (key,)
            ).fetchone()
        else:
            row = self._conn.execute(
                f"SELECT value FROM {self._table} WHERE key = ? AND {self._uid_clause}",
                (key, *self._uid_params),
            ).fetchone()
        return row["value"] if row else default

    def set(self, key: str, value: str) -> None:
        now = _now_iso()
        if self._user_id is None:
            self._conn.execute(
                "INSERT INTO config_kv (key, value, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                (key, value, now),
            )
        else:
            self._conn.execute(
                f"INSERT INTO {self._table} (key, user_id, value, updated_at) VALUES (?, ?, ?, ?) "
                f"ON CONFLICT(key, user_id) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                (key, self._user_id, value, now),
            )
        self._conn.commit()

    def delete_key(self, key: str) -> bool:
        if self._user_id is None:
            cur = self._conn.execute("DELETE FROM config_kv WHERE key = ?", (key,))
        else:
            cur = self._conn.execute(
                f"DELETE FROM {self._table} WHERE key = ? AND {self._uid_clause}",
                (key, *self._uid_params),
            )
        self._conn.commit()
        return cur.rowcount > 0

    def get_active_model(self) -> str | None:
        val = self.get("active_model")
        return val if val else None

    def set_active_model(self, name: str | None) -> None:
        if name:
            self.set("active_model", name)
        else:
            self.delete_key("active_model")

    def get_full_access(self) -> bool:
        """读取持久化的 full_access 开关（跨会话）。"""
        return self.get("full_access_enabled") == "true"

    def set_full_access(self, enabled: bool) -> None:
        """持久化 full_access 开关（跨会话）。"""
        self.set("full_access_enabled", "true" if enabled else "false")


# ── 向后兼容别名 ─────────────────────────────────────────


class ConfigStore(GlobalConfigStore):
    """向后兼容别名。包含用户级方法用于过渡期。

    新代码应分别使用 GlobalConfigStore + UserConfigStore。
    """

    def get_active_model(self) -> str | None:
        val = self.get("active_model")
        return val if val else None

    def set_active_model(self, name: str | None) -> None:
        if name:
            self.set("active_model", name)
        else:
            self.delete_key("active_model")
