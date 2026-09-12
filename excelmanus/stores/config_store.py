"""ConfigStore：模型配置与运行时状态存储。

拆分为 GlobalConfigStore（全局 model_profiles + 部署设置）和
UserConfigStore（进程级偏好，如 active_model）。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from excelmanus.security.cipher import TokenCipher

if TYPE_CHECKING:
    from excelmanus.database import Database
    from excelmanus.db_adapter import ConnectionAdapter

logger = logging.getLogger(__name__)

# 模块级单例，避免每次操作都重新派生密钥
_api_key_cipher = TokenCipher()


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

    @staticmethod
    def _decrypt_profile_row(row: dict[str, Any]) -> dict[str, Any]:
        """解密 profile 行中的 api_key（兼容明文迁移）。"""
        d = dict(row)
        raw = d.get("api_key")
        if raw:
            d["api_key"] = _api_key_cipher.decrypt_or_passthrough(raw)
        return d

    def list_profiles(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT name, model, api_key, base_url, description, protocol, "
            "thinking_mode, model_family, custom_extra_body, custom_extra_headers "
            "FROM model_profiles ORDER BY id ASC"
        ).fetchall()
        return [self._decrypt_profile_row(row) for row in rows]

    def get_profile(self, name: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT name, model, api_key, base_url, description, protocol, "
            "thinking_mode, model_family, custom_extra_body, custom_extra_headers "
            "FROM model_profiles WHERE name = ?",
            (name,),
        ).fetchone()
        return self._decrypt_profile_row(row) if row else None

    def add_profile(
        self,
        name: str,
        model: str,
        api_key: str = "",
        base_url: str = "",
        description: str = "",
        protocol: str = "auto",
        thinking_mode: str = "auto",
        model_family: str = "",
        custom_extra_body: str = "",
        custom_extra_headers: str = "",
    ) -> bool:
        now = _now_iso()
        try:
            enc_api_key = _api_key_cipher.encrypt(api_key) if api_key else api_key
            self._conn.execute(
                "INSERT INTO model_profiles "
                "(name, model, api_key, base_url, description, protocol, "
                "thinking_mode, model_family, custom_extra_body, custom_extra_headers, "
                "created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (name, model, enc_api_key, base_url, description, protocol,
                 thinking_mode, model_family, custom_extra_body, custom_extra_headers,
                 now, now),
            )
            self._conn.commit()
            return True
        except Exception:
            logger.warning("添加模型配置失败 (name=%s)", name, exc_info=True)
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
        thinking_mode: str | None = None,
        model_family: str | None = None,
        custom_extra_body: str | None = None,
        custom_extra_headers: str | None = None,
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
            try:
                params.append(_api_key_cipher.encrypt(api_key) if api_key else api_key)
            except Exception:
                logger.warning("加密 api_key 失败，跳过该字段更新")
                sets.pop()  # 撤销刚添加的 SET 子句
        if base_url is not None:
            sets.append("base_url = ?")
            params.append(base_url)
        if description is not None:
            sets.append("description = ?")
            params.append(description)
        if protocol is not None:
            sets.append("protocol = ?")
            params.append(protocol)
        if thinking_mode is not None:
            sets.append("thinking_mode = ?")
            params.append(thinking_mode)
        if model_family is not None:
            sets.append("model_family = ?")
            params.append(model_family)
        if custom_extra_body is not None:
            sets.append("custom_extra_body = ?")
            params.append(custom_extra_body)
        if custom_extra_headers is not None:
            sets.append("custom_extra_headers = ?")
            params.append(custom_extra_headers)
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
            thinking_mode = (item.get("thinking_mode") or "auto").strip().lower()
            model_family = (item.get("model_family") or "").strip().lower()
            custom_extra_body = (item.get("custom_extra_body") or "").strip()
            custom_extra_headers = (item.get("custom_extra_headers") or "").strip()
            if self.add_profile(
                name, model, api_key, base_url, description, protocol,
                thinking_mode=thinking_mode, model_family=model_family,
                custom_extra_body=custom_extra_body, custom_extra_headers=custom_extra_headers,
            ):
                added += 1
        return added


# ── UserConfigStore（用户级配置）──────────────────────────────


class UserConfigStore:
    """进程级配置：active_model 等偏好，写入全局 config_kv。"""

    def __init__(self, conn: "ConnectionAdapter") -> None:
        from excelmanus.db_adapter import ConnectionAdapter as _CA
        if isinstance(conn, _CA):
            self._conn = conn
        else:
            self._conn = conn.conn  # type: ignore[union-attr]

    def get(self, key: str, default: str = "") -> str:
        row = self._conn.execute(
            "SELECT value FROM config_kv WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else default

    def set(self, key: str, value: str) -> None:
        now = _now_iso()
        self._conn.execute(
            "INSERT INTO config_kv (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (key, value, now),
        )
        self._conn.commit()

    def delete_key(self, key: str) -> bool:
        cur = self._conn.execute("DELETE FROM config_kv WHERE key = ?", (key,))
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

    def get_present_as(self) -> str:
        """读取持久化的代码模式偏好（跨会话）。"""
        return "code" if self.get("present_as") == "code" else "native"

    def set_present_as(self, mode: str) -> None:
        """持久化代码模式偏好（跨会话）。"""
        self.set("present_as", "code" if mode == "code" else "native")
