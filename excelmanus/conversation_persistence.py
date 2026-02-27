"""ConversationPersistence：ConversationMemory ↔ ChatHistoryStore 增量同步服务。

将散落在 SessionManager 中的消息持久化逻辑集中管理，SessionManager 不再
直接穿透 AgentEngine 内部属性来追踪同步位置。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from excelmanus.chat_history import ChatHistoryStore
    from excelmanus.engine import AgentEngine

logger = logging.getLogger(__name__)


class ConversationPersistence:
    """ConversationMemory ↔ ChatHistoryStore 增量同步服务。

    职责：
    - 通过 engine 的 snapshot index 追踪已持久化位置
    - 增量写入新消息到 ChatHistoryStore
    - 处理回退 / 清除时的同步重置
    """

    def __init__(self, chat_history: "ChatHistoryStore") -> None:
        self._chat_history = chat_history

    @property
    def store(self) -> "ChatHistoryStore":
        """底层 ChatHistoryStore 实例。"""
        return self._chat_history

    def sync_new_messages(
        self,
        session_id: str,
        engine: "AgentEngine",
        *,
        user_id: str | None = None,
    ) -> None:
        """将 engine 中新增的消息增量持久化到 ChatHistoryStore。

        注意：此方法直接读取 engine 可变状态，调用方需确保并发安全。
        对于需要并发安全的场景，请使用 sync_from_snapshot()。
        """
        messages = engine.raw_messages
        snapshot_idx = engine.message_snapshot_index
        new_msgs = messages[snapshot_idx:]
        if not new_msgs:
            return

        turn = engine.session_turn

        exists = self._chat_history.session_exists(session_id, user_id=user_id)
        if not exists:
            title = ""
            for msg in messages:
                if isinstance(msg, dict) and msg.get("role") == "user":
                    content = msg.get("content", "")
                    if isinstance(content, str):
                        title = content[:80]
                    break
            self._chat_history.create_session(session_id, title, user_id=user_id)

        self._chat_history.save_turn_messages(session_id, new_msgs, turn_number=turn)
        engine.set_message_snapshot_index(len(messages))

        # F9: 同步更新 SQLite 中的会话标题（从第一条用户消息派生）
        self._sync_title(session_id, messages)

    def _sync_title(self, session_id: str, messages: list) -> None:
        """从消息列表中派生标题并更新 SQLite（仅当当前标题为空时）。"""
        try:
            title = ""
            for msg in messages:
                if isinstance(msg, dict) and msg.get("role") == "user":
                    content = msg.get("content", "")
                    if isinstance(content, str) and content.strip():
                        title = content.strip()[:80]
                        break
            if title:
                self._chat_history.update_session(session_id, title=title)
        except Exception:
            logger.debug("会话 %s 标题同步失败", session_id, exc_info=True)

    def sync_from_snapshot(
        self,
        session_id: str,
        snapshot: Any,
    ) -> None:
        """基于快照增量持久化消息（并发安全）。

        snapshot 应包含 messages, snapshot_index, turn, user_id 属性。
        此方法不读取 engine 可变状态，适用于锁外调用。
        """
        new_msgs = snapshot.messages[snapshot.snapshot_index:]
        if not new_msgs:
            return

        user_id = snapshot.user_id
        exists = self._chat_history.session_exists(session_id, user_id=user_id)
        if not exists:
            title = ""
            for msg in snapshot.messages:
                if isinstance(msg, dict) and msg.get("role") == "user":
                    content = msg.get("content", "")
                    if isinstance(content, str):
                        title = content[:80]
                    break
            self._chat_history.create_session(session_id, title, user_id=user_id)

        self._chat_history.save_turn_messages(
            session_id, new_msgs, turn_number=snapshot.turn
        )

        # F9: 同步更新 SQLite 中的会话标题
        self._sync_title(session_id, snapshot.messages)

    def reset_after_rollback(
        self,
        session_id: str,
        engine: "AgentEngine",
    ) -> None:
        """回退后清空持久化消息并重置快照索引。"""
        try:
            if self._chat_history.session_exists(session_id):
                self._chat_history.clear_messages(session_id)
            engine.set_message_snapshot_index(0)
        except Exception:
            logger.warning(
                "会话 %s 回退后清理持久化消息失败", session_id, exc_info=True
            )

    def clear(
        self,
        session_id: str,
        engine: "AgentEngine | None" = None,
    ) -> None:
        """清除会话持久化消息，并重置引擎快照索引。"""
        try:
            self._chat_history.clear_messages(session_id)
        except Exception:
            logger.warning("会话 %s 持久化消息清除失败", session_id, exc_info=True)
        if engine is not None:
            engine.set_message_snapshot_index(0)
