"""ConfigStore：基于 SQLite 的模型配置与运行时状态存储。"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from excelmanus.database import Database

logger = logging.getLogger(__name__)


class ConfigStore:
    """SQLite 后端的模型 profile 管理与 KV 配置存储。"""

    def __init__(self, database: "Database") -> None:
        self._conn = database.conn

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    # ── model_profiles CRUD ──────────────────────────────

    def list_profiles(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT name, model, api_key, base_url, description "
            "FROM model_profiles ORDER BY id ASC"
        ).fetchall()
        return [dict(row) for row in rows]

    def get_profile(self, name: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT name, model, api_key, base_url, description "
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
    ) -> bool:
        """新增 profile。名称已存在返回 False。"""
        now = self._now_iso()
        try:
            self._conn.execute(
                "INSERT INTO model_profiles (name, model, api_key, base_url, description, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (name, model, api_key, base_url, description, now, now),
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
    ) -> bool:
        """更新已有 profile。返回是否找到并更新。"""
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
        if not sets:
            return False
        sets.append("updated_at = ?")
        params.append(self._now_iso())
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

    # ── config_kv 通用 KV ────────────────────────────────

    def get(self, key: str, default: str = "") -> str:
        row = self._conn.execute(
            "SELECT value FROM config_kv WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else default

    def set(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT INTO config_kv (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (key, value, self._now_iso()),
        )
        self._conn.commit()

    def delete_key(self, key: str) -> bool:
        cur = self._conn.execute("DELETE FROM config_kv WHERE key = ?", (key,))
        self._conn.commit()
        return cur.rowcount > 0

    # ── 便捷方法 ─────────────────────────────────────────

    def get_active_model(self) -> str | None:
        """获取当前激活模型名称，None 表示使用 default。"""
        val = self.get("active_model")
        return val if val else None

    def set_active_model(self, name: str | None) -> None:
        if name:
            self.set("active_model", name)
        else:
            self.delete_key("active_model")

    def import_profiles_from_env(
        self,
        profiles_json: str,
        default_api_key: str = "",
        default_base_url: str = "",
    ) -> int:
        """从 EXCELMANUS_MODELS JSON 字符串导入 profiles（幂等）。返回新增数量。"""
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
            if self.add_profile(name, model, api_key, base_url, description):
                added += 1
        return added
