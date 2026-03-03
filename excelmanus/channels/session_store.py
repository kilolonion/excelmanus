"""用户-会话持久化映射：JSON 文件存储，跨重启保留。"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from excelmanus.logger import get_logger

logger = get_logger("channels.session_store")


class SessionStore:
    """将 (channel, chat_id, user_id) 映射到 ExcelManus session_id。

    持久化到 JSON 文件，支持 TTL 自动过期。
    """

    def __init__(
        self,
        store_path: str | Path | None = None,
        ttl_seconds: float = 86400 * 7,  # 默认 7 天过期
    ) -> None:
        if store_path is None:
            data_home = os.environ.get(
                "EXCELMANUS_DATA_HOME",
                os.path.expanduser("~/.excelmanus"),
            )
            store_path = Path(data_home) / "channel_sessions.json"
        self._path = Path(store_path)
        self._ttl = ttl_seconds
        self._data: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        """从磁盘加载。"""
        if self._path.exists():
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    self._data = json.load(f)
                logger.debug("加载 %d 条会话映射: %s", len(self._data), self._path)
            except Exception:
                logger.warning("会话映射文件损坏，重置: %s", self._path, exc_info=True)
                self._data = {}
        else:
            self._data = {}

    def _save(self) -> None:
        """持久化到磁盘。"""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
        except Exception:
            logger.warning("保存会话映射失败: %s", self._path, exc_info=True)

    @staticmethod
    def _key(channel: str, chat_id: str, user_id: str) -> str:
        return f"{channel}:{chat_id}:{user_id}"

    def get(self, channel: str, chat_id: str, user_id: str) -> str | None:
        """获取 session_id，过期则返回 None。"""
        key = self._key(channel, chat_id, user_id)
        entry = self._data.get(key)
        if entry is None:
            return None
        ts = entry.get("ts", 0)
        if self._ttl > 0 and (time.time() - ts) > self._ttl:
            self._data.pop(key, None)
            self._save()
            return None
        return entry.get("session_id")

    def set(self, channel: str, chat_id: str, user_id: str, session_id: str) -> None:
        """存储 session_id。"""
        key = self._key(channel, chat_id, user_id)
        existing = self._data.get(key, {})
        existing.update({"session_id": session_id, "ts": time.time()})
        self._data[key] = existing
        self._save()

    def get_auth_user_id(self, channel: str, chat_id: str, user_id: str) -> str | None:
        """获取关联的 auth user_id。"""
        key = self._key(channel, chat_id, user_id)
        entry = self._data.get(key)
        if entry is None:
            return None
        return entry.get("auth_user_id")

    def set_auth_user_id(
        self, channel: str, chat_id: str, user_id: str, auth_user_id: str,
    ) -> None:
        """存储关联的 auth user_id。"""
        key = self._key(channel, chat_id, user_id)
        entry = self._data.get(key)
        if entry is None:
            entry = {"session_id": "", "ts": time.time()}
            self._data[key] = entry
        entry["auth_user_id"] = auth_user_id
        self._save()

    def get_mode(self, channel: str, chat_id: str, user_id: str) -> str:
        """获取用户当前 chat_mode，缺失时返回 "write"。"""
        key = self._key(channel, chat_id, user_id)
        entry = self._data.get(key)
        if entry is None:
            return "write"
        return entry.get("chat_mode", "write")

    def set_mode(self, channel: str, chat_id: str, user_id: str, mode: str) -> None:
        """设置用户 chat_mode。若条目不存在则创建占位条目。"""
        key = self._key(channel, chat_id, user_id)
        entry = self._data.get(key)
        if entry is None:
            entry = {"session_id": "", "ts": time.time()}
            self._data[key] = entry
        entry["chat_mode"] = mode
        self._save()

    def remove(self, channel: str, chat_id: str, user_id: str) -> None:
        """移除映射。"""
        key = self._key(channel, chat_id, user_id)
        if self._data.pop(key, None) is not None:
            self._save()

    def backfill_auth_user_id(
        self, channel: str, platform_user_id: str, auth_user_id: str,
    ) -> int:
        """绑定成功后，回填该渠道用户所有已有条目的 auth_user_id。

        遍历所有 key 形如 ``<channel>:<chat_id>:<platform_user_id>`` 的条目，
        将 auth_user_id 写入（若尚未设置或值不同）。

        Returns:
            更新的条目数。
        """
        prefix = f"{channel}:"
        suffix = f":{platform_user_id}"
        updated = 0
        for key, entry in self._data.items():
            if key.startswith(prefix) and key.endswith(suffix):
                if entry.get("auth_user_id") != auth_user_id:
                    entry["auth_user_id"] = auth_user_id
                    updated += 1
        if updated:
            self._save()
            logger.info(
                "回填 auth_user_id: channel=%s platform=%s → %d 条",
                channel, platform_user_id, updated,
            )
        return updated

    def cleanup_expired(self) -> int:
        """清理所有过期条目。"""
        now = time.time()
        expired = [
            k for k, v in self._data.items()
            if self._ttl > 0 and (now - v.get("ts", 0)) > self._ttl
        ]
        for k in expired:
            del self._data[k]
        if expired:
            self._save()
            logger.info("清理 %d 条过期会话映射", len(expired))
        return len(expired)
