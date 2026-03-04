"""多用户渠道打通集成测试。

覆盖：
- ChannelBindManager 绑定码生成/验证/过期
- Service Token 签发/解码
- AuthMiddleware service token + X-On-Behalf-Of 处理
- ExcelManusAPIClient auth header 注入
- MessageHandler /bind /bindstatus /unbind 命令
- MessageHandler _resolve_auth_user_id + on_behalf_of 注入
- SessionStore auth_user_id 字段
- UserStore channel_user_links CRUD
"""

from __future__ import annotations

import sqlite3
import tempfile
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from excelmanus.channels.base import ChannelMessage, ChannelUser
from excelmanus.channels.api_client import ExcelManusAPIClient
from excelmanus.channels.session_store import SessionStore
from excelmanus.auth.models import UserRecord


# ── Fixtures ──


@pytest.fixture
def tmp_db():
    """创建内存 SQLite 数据库并初始化 UserStore。"""
    from excelmanus.auth.store import UserStore

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    store = UserStore(MagicMock(conn=conn))
    return store


@pytest.fixture
def bind_manager(tmp_db):
    """创建 ChannelBindManager 并注入 UserStore。"""
    from excelmanus.auth.channel_bind import ChannelBindManager

    mgr = ChannelBindManager(user_store=tmp_db, ttl=10)
    return mgr


@pytest.fixture
def mock_adapter():
    """创建 MockAdapter。"""
    adapter = MagicMock()
    adapter.name = "telegram"
    adapter.send_text = AsyncMock()
    adapter.send_markdown = AsyncMock()
    adapter.show_typing = AsyncMock()
    adapter.send_progress = AsyncMock()
    adapter.send_file = AsyncMock()
    adapter.send_approval_card = AsyncMock()
    adapter.send_question_card = AsyncMock()
    adapter.update_approval_result = AsyncMock()
    adapter.update_question_result = AsyncMock()
    adapter.send_staged_card = AsyncMock()
    return adapter


# ── ChannelBindManager Tests ──


class TestChannelBindManager:
    def test_create_bind_code(self, bind_manager):
        code = bind_manager.create_bind_code("telegram", "12345", "Alice")
        assert len(code) == 6
        assert code.isdigit()

    def test_same_user_refreshes_code(self, bind_manager):
        code1 = bind_manager.create_bind_code("telegram", "12345", "Alice")
        code2 = bind_manager.create_bind_code("telegram", "12345", "Alice")
        assert code1 != code2
        # 旧码应已作废
        assert bind_manager.get_bind_info(code1) is None
        assert bind_manager.get_bind_info(code2) is not None

    def test_different_users_different_codes(self, bind_manager):
        code1 = bind_manager.create_bind_code("telegram", "111", "Alice")
        code2 = bind_manager.create_bind_code("telegram", "222", "Bob")
        assert code1 != code2

    def test_get_bind_info(self, bind_manager):
        code = bind_manager.create_bind_code("telegram", "12345", "Alice")
        info = bind_manager.get_bind_info(code)
        assert info is not None
        assert info.channel == "telegram"
        assert info.platform_id == "12345"
        assert info.platform_display_name == "Alice"

    def test_get_bind_info_invalid(self, bind_manager):
        info = bind_manager.get_bind_info("000000")
        assert info is None

    def test_confirm_bind_success(self, bind_manager, tmp_db):
        # 创建一个测试用户
        tmp_db.create_user(UserRecord(id="user-1", email="alice@test.com", password_hash="hash"))
        code = bind_manager.create_bind_code("telegram", "12345", "Alice")
        result = bind_manager.confirm_bind(code, "user-1")
        assert result["ok"] is True
        assert result["channel"] == "telegram"
        assert result["platform_id"] == "12345"

    def test_confirm_bind_invalid_code(self, bind_manager):
        result = bind_manager.confirm_bind("999999", "user-1")
        assert result["ok"] is False
        assert "无效" in result["error"]

    def test_confirm_bind_consumed(self, bind_manager, tmp_db):
        tmp_db.create_user(UserRecord(id="user-1", email="alice@test.com", password_hash="hash"))
        code = bind_manager.create_bind_code("telegram", "12345", "Alice")
        bind_manager.confirm_bind(code, "user-1")
        # 再次使用同一码应失败
        result = bind_manager.confirm_bind(code, "user-1")
        assert result["ok"] is False

    def test_confirm_bind_already_bound_same_user(self, bind_manager, tmp_db):
        tmp_db.create_user(UserRecord(id="user-1", email="alice@test.com", password_hash="hash"))
        # 先绑定
        code1 = bind_manager.create_bind_code("telegram", "12345", "Alice")
        bind_manager.confirm_bind(code1, "user-1")
        # 再次生成码并确认到同一用户
        code2 = bind_manager.create_bind_code("telegram", "12345", "Alice")
        result = bind_manager.confirm_bind(code2, "user-1")
        assert result["ok"] is True
        assert "已绑定" in result.get("message", "")

    def test_confirm_bind_already_bound_other_user(self, bind_manager, tmp_db):
        tmp_db.create_user(UserRecord(id="user-1", email="alice@test.com", password_hash="hash"))
        tmp_db.create_user(UserRecord(id="user-2", email="bob@test.com", password_hash="hash"))
        code1 = bind_manager.create_bind_code("telegram", "12345", "Alice")
        bind_manager.confirm_bind(code1, "user-1")
        code2 = bind_manager.create_bind_code("telegram", "12345", "Alice")
        result = bind_manager.confirm_bind(code2, "user-2")
        assert result["ok"] is False
        assert "其他用户" in result["error"]

    def test_check_bind_status(self, bind_manager, tmp_db):
        assert bind_manager.check_bind_status("telegram", "12345") is None
        tmp_db.create_user(UserRecord(id="user-1", email="alice@test.com", password_hash="hash"))
        code = bind_manager.create_bind_code("telegram", "12345", "Alice")
        bind_manager.confirm_bind(code, "user-1")
        assert bind_manager.check_bind_status("telegram", "12345") == "user-1"

    def test_expired_code(self):
        from excelmanus.auth.channel_bind import ChannelBindManager

        mgr = ChannelBindManager(ttl=0.01)  # 10ms TTL
        code = mgr.create_bind_code("telegram", "12345", "Alice")
        time.sleep(0.05)
        info = mgr.get_bind_info(code)
        assert info is None


# ── Service Token Tests ──


class TestServiceToken:
    def test_create_and_decode(self):
        with patch.dict("os.environ", {"EXCELMANUS_JWT_SECRET": "test-secret-key-for-jwt"}):
            from excelmanus.auth.security import create_service_token, decode_service_token

            token = create_service_token("test-bot")
            payload = decode_service_token(token)
            assert payload is not None
            assert payload["type"] == "service"
            assert payload["sub"] == "test-bot"
            assert payload["role"] == "service"

    def test_decode_non_service_returns_none(self):
        with patch.dict("os.environ", {"EXCELMANUS_JWT_SECRET": "test-secret-key-for-jwt"}):
            from excelmanus.auth.security import create_access_token, decode_service_token

            token = create_access_token({"sub": "user-1", "type": "access", "role": "user"})
            result = decode_service_token(token)
            assert result is None

    def test_decode_invalid_returns_none(self):
        from excelmanus.auth.security import decode_service_token

        result = decode_service_token("invalid.token.here")
        assert result is None


# ── AuthMiddleware Tests ──


class TestAuthMiddlewareServiceToken:
    @pytest.mark.asyncio
    async def test_service_token_with_on_behalf_of(self):
        with patch.dict("os.environ", {"EXCELMANUS_JWT_SECRET": "test-secret-key-for-jwt"}):
            from excelmanus.auth.security import create_service_token
            from excelmanus.auth.middleware import AuthMiddleware

            token = create_service_token("test-bot")
            captured_scope = {}

            async def mock_app(scope, receive, send):
                captured_scope.update(scope.get("state", {}))

            middleware = AuthMiddleware(mock_app)
            mock_app_state = MagicMock()
            mock_app_state.state.auth_enabled = True

            scope = {
                "type": "http",
                "path": "/api/v1/chat/stream",
                "method": "POST",
                "headers": [
                    (b"authorization", f"Bearer {token}".encode("latin-1")),
                    (b"x-on-behalf-of", b"user-42"),
                ],
                "app": mock_app_state,
                "client": ("127.0.0.1", 12345),
            }

            await middleware(scope, AsyncMock(), AsyncMock())
            assert captured_scope.get("user_id") == "user-42"
            assert captured_scope.get("user_role") == "service"
            assert captured_scope.get("is_service_token") is True

    @pytest.mark.asyncio
    async def test_service_token_without_on_behalf_of(self):
        with patch.dict("os.environ", {"EXCELMANUS_JWT_SECRET": "test-secret-key-for-jwt"}):
            from excelmanus.auth.security import create_service_token
            from excelmanus.auth.middleware import AuthMiddleware

            token = create_service_token("test-bot")
            captured_scope = {}

            async def mock_app(scope, receive, send):
                captured_scope.update(scope.get("state", {}))

            middleware = AuthMiddleware(mock_app)
            mock_app_state = MagicMock()
            mock_app_state.state.auth_enabled = True

            scope = {
                "type": "http",
                "path": "/api/v1/chat/stream",
                "method": "POST",
                "headers": [
                    (b"authorization", f"Bearer {token}".encode("latin-1")),
                ],
                "app": mock_app_state,
                "client": ("127.0.0.1", 12345),
            }

            await middleware(scope, AsyncMock(), AsyncMock())
            assert "user_id" not in captured_scope
            assert captured_scope.get("is_service_token") is True


# ── ExcelManusAPIClient Auth Headers ──


class TestAPIClientAuthHeaders:
    def test_auth_headers_with_token(self):
        client = ExcelManusAPIClient(service_token="my-token")
        headers = client._auth_headers()
        assert headers["Authorization"] == "Bearer my-token"
        assert "X-On-Behalf-Of" not in headers

    def test_auth_headers_with_on_behalf_of(self):
        client = ExcelManusAPIClient(service_token="my-token")
        client.set_on_behalf_of("user-42")
        headers = client._auth_headers()
        assert headers["Authorization"] == "Bearer my-token"
        assert headers["X-On-Behalf-Of"] == "user-42"

    def test_auth_headers_override_on_behalf_of(self):
        client = ExcelManusAPIClient(service_token="my-token")
        client.set_on_behalf_of("user-42")
        headers = client._auth_headers(on_behalf_of="user-99")
        assert headers["X-On-Behalf-Of"] == "user-99"

    def test_auth_headers_no_token(self):
        client = ExcelManusAPIClient()
        headers = client._auth_headers()
        assert headers == {}

    def test_set_service_token_deferred(self):
        client = ExcelManusAPIClient()
        assert client._auth_headers() == {}
        client.set_service_token("late-token")
        headers = client._auth_headers()
        assert headers["Authorization"] == "Bearer late-token"


# ── MessageHandler Bind Commands ──


class TestMessageHandlerBindCommands:
    @pytest.mark.asyncio
    async def test_bind_no_manager(self, mock_adapter, tmp_path):
        from excelmanus.channels.message_handler import MessageHandler

        api = AsyncMock(spec=ExcelManusAPIClient)
        store = SessionStore(store_path=tmp_path / "s.json")
        handler = MessageHandler(
            adapter=mock_adapter, api_client=api,
            session_store=store, bind_manager=None,
        )
        msg = ChannelMessage(
            channel="telegram",
            user=ChannelUser(user_id="1"),
            chat_id="100",
            is_command=True, command="bind",
        )
        await handler.handle_message(msg)
        mock_adapter.send_text.assert_called_once()
        assert "未启用" in mock_adapter.send_text.call_args[0][1]

    @pytest.mark.asyncio
    async def test_bind_generates_code(self, mock_adapter, tmp_path, bind_manager):
        from excelmanus.channels.message_handler import MessageHandler

        api = AsyncMock(spec=ExcelManusAPIClient)
        store = SessionStore(store_path=tmp_path / "s.json")
        handler = MessageHandler(
            adapter=mock_adapter, api_client=api,
            session_store=store, bind_manager=bind_manager,
        )
        msg = ChannelMessage(
            channel="telegram",
            user=ChannelUser(user_id="1", username="alice"),
            chat_id="100",
            is_command=True, command="bind",
        )
        await handler.handle_message(msg)
        mock_adapter.send_markdown.assert_called_once()
        text = mock_adapter.send_markdown.call_args[0][1]
        assert "绑定码" in text

    @pytest.mark.asyncio
    async def test_bind_already_bound(self, mock_adapter, tmp_path, bind_manager, tmp_db):
        from excelmanus.channels.message_handler import MessageHandler

        tmp_db.create_user(UserRecord(id="user-1", email="a@test.com", password_hash="hash"))
        code = bind_manager.create_bind_code("telegram", "1", "alice")
        bind_manager.confirm_bind(code, "user-1")

        api = AsyncMock(spec=ExcelManusAPIClient)
        store = SessionStore(store_path=tmp_path / "s.json")
        handler = MessageHandler(
            adapter=mock_adapter, api_client=api,
            session_store=store, bind_manager=bind_manager,
        )
        msg = ChannelMessage(
            channel="telegram",
            user=ChannelUser(user_id="1"),
            chat_id="100",
            is_command=True, command="bind",
        )
        await handler.handle_message(msg)
        text = mock_adapter.send_text.call_args[0][1]
        assert "已绑定" in text

    @pytest.mark.asyncio
    async def test_bindstatus_unbound(self, mock_adapter, tmp_path, bind_manager):
        from excelmanus.channels.message_handler import MessageHandler

        api = AsyncMock(spec=ExcelManusAPIClient)
        store = SessionStore(store_path=tmp_path / "s.json")
        handler = MessageHandler(
            adapter=mock_adapter, api_client=api,
            session_store=store, bind_manager=bind_manager,
        )
        msg = ChannelMessage(
            channel="telegram",
            user=ChannelUser(user_id="999"),
            chat_id="100",
            is_command=True, command="bindstatus",
        )
        await handler.handle_message(msg)
        text = mock_adapter.send_text.call_args[0][1]
        assert "未绑定" in text

    @pytest.mark.asyncio
    async def test_unbind_success(self, mock_adapter, tmp_path, bind_manager, tmp_db):
        from excelmanus.channels.message_handler import MessageHandler

        tmp_db.create_user(UserRecord(id="user-1", email="a@test.com", password_hash="hash"))
        code = bind_manager.create_bind_code("telegram", "1", "alice")
        bind_manager.confirm_bind(code, "user-1")

        api = AsyncMock(spec=ExcelManusAPIClient)
        store = SessionStore(store_path=tmp_path / "s.json")
        handler = MessageHandler(
            adapter=mock_adapter, api_client=api,
            session_store=store, bind_manager=bind_manager,
        )
        msg = ChannelMessage(
            channel="telegram",
            user=ChannelUser(user_id="1"),
            chat_id="100",
            is_command=True, command="unbind",
        )
        await handler.handle_message(msg)
        text = mock_adapter.send_text.call_args[0][1]
        assert "已解绑" in text
        # 确认绑定已清除
        assert bind_manager.check_bind_status("telegram", "1") is None

    @pytest.mark.asyncio
    async def test_unbind_then_rebind_replaces_eventbridge_subscription(
        self, mock_adapter, tmp_path, bind_manager, tmp_db,
    ):
        from excelmanus.channels.event_bridge import EventBridge
        from excelmanus.channels.message_handler import MessageHandler

        tmp_db.create_user(UserRecord(id="user-1", email="u1@test.com", password_hash="hash"))
        tmp_db.create_user(UserRecord(id="user-2", email="u2@test.com", password_hash="hash"))

        bridge = EventBridge()
        api = AsyncMock(spec=ExcelManusAPIClient)
        store = SessionStore(store_path=tmp_path / "s.json")
        handler = MessageHandler(
            adapter=mock_adapter, api_client=api,
            session_store=store, bind_manager=bind_manager, event_bridge=bridge,
        )

        code1 = bind_manager.create_bind_code("telegram", "1", "alice")
        bind_manager.confirm_bind(code1, "user-1")
        msg_help = ChannelMessage(
            channel="telegram",
            user=ChannelUser(user_id="1"),
            chat_id="100",
            text="/help",
            is_command=True,
            command="help",
        )
        await handler.handle_message(msg_help)
        assert bridge.subscription_count == 1

        msg_unbind = ChannelMessage(
            channel="telegram",
            user=ChannelUser(user_id="1"),
            chat_id="100",
            is_command=True,
            command="unbind",
        )
        await handler.handle_message(msg_unbind)
        assert bridge.subscription_count == 0

        code2 = bind_manager.create_bind_code("telegram", "1", "alice")
        bind_manager.confirm_bind(code2, "user-2")
        await handler.handle_message(msg_help)
        assert bridge.subscription_count == 1
        assert await bridge.notify("user-1", "approval", {"approval_id": "a1"}) == 0
        assert await bridge.notify("user-2", "approval", {"approval_id": "a2"}) == 1

    @pytest.mark.asyncio
    async def test_unbind_not_bound(self, mock_adapter, tmp_path, bind_manager):
        from excelmanus.channels.message_handler import MessageHandler

        api = AsyncMock(spec=ExcelManusAPIClient)
        store = SessionStore(store_path=tmp_path / "s.json")
        handler = MessageHandler(
            adapter=mock_adapter, api_client=api,
            session_store=store, bind_manager=bind_manager,
        )
        msg = ChannelMessage(
            channel="telegram",
            user=ChannelUser(user_id="999"),
            chat_id="100",
            is_command=True, command="unbind",
        )
        await handler.handle_message(msg)
        text = mock_adapter.send_text.call_args[0][1]
        assert "未绑定" in text


# ── MessageHandler identity resolution ──


class TestMessageHandlerIdentity:
    @pytest.mark.asyncio
    async def test_resolve_auth_user_id_cached(self, mock_adapter, tmp_path, bind_manager, tmp_db):
        from excelmanus.channels.message_handler import MessageHandler

        tmp_db.create_user(UserRecord(id="user-1", email="a@test.com", password_hash="hash"))
        code = bind_manager.create_bind_code("telegram", "1", "alice")
        bind_manager.confirm_bind(code, "user-1")

        api = AsyncMock(spec=ExcelManusAPIClient)
        store = SessionStore(store_path=tmp_path / "s.json")
        handler = MessageHandler(
            adapter=mock_adapter, api_client=api,
            session_store=store, bind_manager=bind_manager,
        )
        # First call: DB lookup
        uid1 = handler._resolve_auth_user_id("1")
        assert uid1 == "user-1"
        # Second call: from cache (no DB hit)
        uid2 = handler._resolve_auth_user_id("1")
        assert uid2 == "user-1"
        # After invalidation, should re-query
        handler.invalidate_auth_cache("1")
        assert "telegram:1" not in handler._auth_user_cache

    @pytest.mark.asyncio
    async def test_resolve_auth_user_id_refreshes_after_external_unbind(
        self, mock_adapter, tmp_path, bind_manager, tmp_db,
    ):
        from excelmanus.channels.message_handler import MessageHandler

        tmp_db.create_user(UserRecord(id="user-1", email="a@test.com", password_hash="hash"))
        code = bind_manager.create_bind_code("telegram", "1", "alice")
        bind_manager.confirm_bind(code, "user-1")

        api = AsyncMock(spec=ExcelManusAPIClient)
        store = SessionStore(store_path=tmp_path / "s.json")
        handler = MessageHandler(
            adapter=mock_adapter, api_client=api,
            session_store=store, bind_manager=bind_manager,
        )

        handler._AUTH_CACHE_TTL = 0  # 禁用缓存 TTL，确保每次回源
        assert handler._resolve_auth_user_id("1") == "user-1"
        assert "telegram:1" in handler._auth_user_cache

        # Simulate Web-side unbind (DB changed outside MessageHandler command path).
        tmp_db.unlink_channel_by_platform("telegram", "1")

        assert handler._resolve_auth_user_id("1") is None
        assert "telegram:1" not in handler._auth_user_cache

    @pytest.mark.asyncio
    async def test_resolve_auth_user_id_refreshes_after_external_rebind(
        self, mock_adapter, tmp_path, bind_manager, tmp_db,
    ):
        from excelmanus.channels.message_handler import MessageHandler

        tmp_db.create_user(UserRecord(id="user-1", email="a@test.com", password_hash="hash"))
        code = bind_manager.create_bind_code("telegram", "1", "alice")
        bind_manager.confirm_bind(code, "user-1")

        api = AsyncMock(spec=ExcelManusAPIClient)
        store = SessionStore(store_path=tmp_path / "s.json")
        handler = MessageHandler(
            adapter=mock_adapter, api_client=api,
            session_store=store, bind_manager=bind_manager,
        )

        handler._AUTH_CACHE_TTL = 0  # 禁用缓存 TTL，确保每次回源
        assert handler._resolve_auth_user_id("1") == "user-1"

        # Simulate Web-side rebind to another account.
        tmp_db.unlink_channel_by_platform("telegram", "1")
        tmp_db.create_user(UserRecord(id="user-2", email="b@test.com", password_hash="hash"))
        tmp_db.link_channel_user("user-2", "telegram", "1")

        assert handler._resolve_auth_user_id("1") == "user-2"
        cached = handler._auth_user_cache.get("telegram:1")
        assert cached is not None and cached[0] == "user-2"

    @pytest.mark.asyncio
    async def test_resolve_unbound_returns_none(self, mock_adapter, tmp_path, bind_manager):
        from excelmanus.channels.message_handler import MessageHandler

        api = AsyncMock(spec=ExcelManusAPIClient)
        store = SessionStore(store_path=tmp_path / "s.json")
        handler = MessageHandler(
            adapter=mock_adapter, api_client=api,
            session_store=store, bind_manager=bind_manager,
        )
        assert handler._resolve_auth_user_id("unknown") is None


# ── SessionStore auth_user_id ──


class TestSessionStoreAuthUserId:
    def test_get_set_auth_user_id(self, tmp_path):
        store = SessionStore(store_path=tmp_path / "s.json")
        assert store.get_auth_user_id("tg", "100", "1") is None
        store.set_auth_user_id("tg", "100", "1", "user-42")
        assert store.get_auth_user_id("tg", "100", "1") == "user-42"

    def test_auth_user_id_persists_with_session(self, tmp_path):
        store = SessionStore(store_path=tmp_path / "s.json")
        store.set_auth_user_id("tg", "100", "1", "user-42")
        store.set("tg", "100", "1", "sess-abc")
        # auth_user_id should survive session update
        assert store.get_auth_user_id("tg", "100", "1") == "user-42"
        assert store.get("tg", "100", "1") == "sess-abc"


# ── UserStore channel_user_links CRUD ──


class TestUserStoreChannelLinks:
    def test_link_and_lookup(self, tmp_db):
        tmp_db.create_user(UserRecord(id="user-1", email="a@test.com", password_hash="hash"))
        link_id = tmp_db.link_channel_user("user-1", "telegram", "tg-123", "Alice")
        assert link_id

        user = tmp_db.get_user_by_channel("telegram", "tg-123")
        assert user is not None
        assert user.id == "user-1"

    def test_get_channel_link(self, tmp_db):
        tmp_db.create_user(UserRecord(id="user-1", email="a@test.com", password_hash="hash"))
        tmp_db.link_channel_user("user-1", "telegram", "tg-123", "Alice")
        link = tmp_db.get_channel_link("telegram", "tg-123")
        assert link is not None
        assert link["user_id"] == "user-1"
        assert link["channel"] == "telegram"
        assert link["display_name"] == "Alice"

    def test_get_channel_links_for_user(self, tmp_db):
        tmp_db.create_user(UserRecord(id="user-1", email="a@test.com", password_hash="hash"))
        tmp_db.link_channel_user("user-1", "telegram", "tg-123")
        tmp_db.link_channel_user("user-1", "qq", "qq-456")
        links = tmp_db.get_channel_links_for_user("user-1")
        assert len(links) == 2
        channels = {l["channel"] for l in links}
        assert channels == {"telegram", "qq"}

    def test_unlink_channel_user(self, tmp_db):
        tmp_db.create_user(UserRecord(id="user-1", email="a@test.com", password_hash="hash"))
        tmp_db.link_channel_user("user-1", "telegram", "tg-123")
        assert tmp_db.unlink_channel_user("user-1", "telegram") is True
        assert tmp_db.get_user_by_channel("telegram", "tg-123") is None

    def test_unlink_channel_by_platform(self, tmp_db):
        tmp_db.create_user(UserRecord(id="user-1", email="a@test.com", password_hash="hash"))
        tmp_db.link_channel_user("user-1", "telegram", "tg-123")
        assert tmp_db.unlink_channel_by_platform("telegram", "tg-123") is True
        assert tmp_db.get_channel_link("telegram", "tg-123") is None

    def test_unlink_nonexistent(self, tmp_db):
        assert tmp_db.unlink_channel_user("user-1", "telegram") is False
        assert tmp_db.unlink_channel_by_platform("telegram", "none") is False

    def test_unique_constraint(self, tmp_db):
        tmp_db.create_user(UserRecord(id="user-1", email="a@test.com", password_hash="hash"))
        tmp_db.link_channel_user("user-1", "telegram", "tg-123")
        with pytest.raises(Exception):
            tmp_db.link_channel_user("user-1", "telegram", "tg-123")

    def test_lookup_nonexistent(self, tmp_db):
        assert tmp_db.get_user_by_channel("telegram", "no-such") is None
        assert tmp_db.get_channel_link("telegram", "no-such") is None


# ── REQUIRE_BIND Tests ──


class TestRequireBind:
    """EXCELMANUS_CHANNEL_REQUIRE_BIND 环境变量测试。"""

    @pytest.fixture
    def handler_require_bind(self, mock_adapter, bind_manager):
        from excelmanus.channels.message_handler import MessageHandler

        api = MagicMock()
        store = SessionStore(store_path=Path(tempfile.mkdtemp()) / "s.json")
        # 使用 config_store 设置 require_bind（_require_bind 现在是 property，动态读取）
        fake_config = MagicMock()
        fake_config.get = MagicMock(return_value="true")
        handler = MessageHandler(
            adapter=mock_adapter,
            api_client=api,
            session_store=store,
            bind_manager=bind_manager,
            config_store=fake_config,
        )
        return handler

    @pytest.mark.asyncio
    async def test_unbound_user_blocked(self, handler_require_bind, mock_adapter):
        """未绑定用户发普通消息被拦截。"""
        msg = ChannelMessage(
            channel="telegram",
            chat_id="c1",
            user=ChannelUser(user_id="999", username="stranger"),
            text="hello",
        )
        await handler_require_bind.handle_message(msg)
        mock_adapter.send_text.assert_called_once()
        assert "绑定" in mock_adapter.send_text.call_args[0][1]

    @pytest.mark.asyncio
    async def test_bind_command_allowed_unbound(self, handler_require_bind, mock_adapter):
        """未绑定用户可执行 /bind 命令。"""
        msg = ChannelMessage(
            channel="telegram",
            chat_id="c1",
            user=ChannelUser(user_id="999", username="stranger"),
            text="/bind",
            is_command=True,
            command="bind",
        )
        await handler_require_bind.handle_message(msg)
        # /bind 应生成绑定码（通过 send_markdown），不应显示"需要绑定"
        calls = mock_adapter.send_markdown.call_args_list
        assert any("绑定码" in str(c) for c in calls)

    @pytest.mark.asyncio
    async def test_help_command_allowed_unbound(self, handler_require_bind, mock_adapter):
        """未绑定用户可执行 /help 命令。"""
        msg = ChannelMessage(
            channel="telegram",
            chat_id="c1",
            user=ChannelUser(user_id="999", username="stranger"),
            text="/help",
            is_command=True,
            command="help",
        )
        await handler_require_bind.handle_message(msg)
        calls = mock_adapter.send_text.call_args_list
        # 应显示帮助，不应显示绑定拦截
        assert any("命令" in str(c) for c in calls)


# ── SessionStore Backfill Tests ──


class TestSessionStoreBackfill:
    """SessionStore.backfill_auth_user_id 测试。"""

    def test_backfill_updates_matching_entries(self):
        store = SessionStore(
            store_path=Path(tempfile.mkdtemp()) / "s.json",
        )
        # 创建一些条目
        store.set("telegram", "chat1", "tg-100", "sess-1")
        store.set("telegram", "chat2", "tg-100", "sess-2")
        store.set("telegram", "chat1", "tg-200", "sess-3")  # 不同用户

        updated = store.backfill_auth_user_id("telegram", "tg-100", "auth-user-abc")

        assert updated == 2
        assert store.get_auth_user_id("telegram", "chat1", "tg-100") == "auth-user-abc"
        assert store.get_auth_user_id("telegram", "chat2", "tg-100") == "auth-user-abc"
        # 不同用户不受影响
        assert store.get_auth_user_id("telegram", "chat1", "tg-200") is None

    def test_backfill_idempotent(self):
        store = SessionStore(
            store_path=Path(tempfile.mkdtemp()) / "s.json",
        )
        store.set("telegram", "chat1", "tg-100", "sess-1")

        store.backfill_auth_user_id("telegram", "tg-100", "auth-user-1")
        updated = store.backfill_auth_user_id("telegram", "tg-100", "auth-user-1")
        assert updated == 0  # 已设置相同值，无需更新

    def test_backfill_no_matches(self):
        store = SessionStore(
            store_path=Path(tempfile.mkdtemp()) / "s.json",
        )
        updated = store.backfill_auth_user_id("telegram", "no-such", "auth-user-1")
        assert updated == 0


# ── Anonymous Workspace Isolation Tests ──


class TestAnonymousWorkspaceIsolation:
    """channel_anon: 前缀的工作区路径解析测试。"""

    def test_anon_user_gets_isolated_workspace(self):
        from excelmanus.workspace import IsolatedWorkspace

        ws = IsolatedWorkspace.resolve(
            "/workspace",
            user_id="channel_anon:telegram:12345",
            auth_enabled=True,
        )
        root = str(ws.root_dir)
        assert "channel_anonymous" in root
        assert "telegram" in root
        assert "12345" in root

    def test_normal_user_unaffected(self):
        from excelmanus.workspace import IsolatedWorkspace

        ws = IsolatedWorkspace.resolve(
            "/workspace",
            user_id="normal-uuid-user-id",
            auth_enabled=True,
        )
        root = str(ws.root_dir)
        assert "users" in root
        assert "normal-uuid-user-id" in root
        assert "channel_anonymous" not in root

    def test_anon_with_data_root(self):
        from excelmanus.workspace import IsolatedWorkspace

        ws = IsolatedWorkspace.resolve(
            "/workspace",
            user_id="channel_anon:qq:67890",
            auth_enabled=True,
            data_root="/data",
        )
        root = str(ws.root_dir)
        assert "channel_anonymous" in root
        assert "qq" in root


# ── _resolve_on_behalf_of Tests ──


class TestResolveOnBehalfOf:
    """MessageHandler._resolve_on_behalf_of 测试。"""

    @pytest.fixture
    def handler(self, mock_adapter, bind_manager):
        from excelmanus.channels.message_handler import MessageHandler

        api = MagicMock()
        store = SessionStore(store_path=Path(tempfile.mkdtemp()) / "s.json")
        handler = MessageHandler(
            adapter=mock_adapter,
            api_client=api,
            session_store=store,
            bind_manager=bind_manager,
        )
        return handler

    def test_bound_user_returns_auth_uid(self, handler, tmp_db):
        """已绑定用户返回真实 auth user_id。"""
        tmp_db.create_user(UserRecord(id="auth-001", email="a@t.com", password_hash="h"))
        tmp_db.link_channel_user("auth-001", "telegram", "tg-111")

        result = handler._resolve_on_behalf_of("tg-111")
        assert result == "auth-001"

    def test_unbound_user_returns_anon_id(self, handler):
        """未绑定用户返回合成匿名 ID。"""
        result = handler._resolve_on_behalf_of("tg-999")
        assert result is not None
        assert result.startswith("channel_anon:")
        assert "telegram" in result
        assert "tg-999" in result

    def test_backfill_triggered_on_first_resolve(self, handler, tmp_db):
        """首次发现绑定时触发 SessionStore 回填。"""
        # 预先创建 session 条目
        handler.sessions.set("telegram", "chat-A", "tg-222", "sess-x")

        # 绑定用户
        tmp_db.create_user(UserRecord(id="auth-002", email="b@t.com", password_hash="h"))
        tmp_db.link_channel_user("auth-002", "telegram", "tg-222")

        # 触发解析
        handler._resolve_auth_user_id("tg-222")

        # 验证回填
        assert handler.sessions.get_auth_user_id("telegram", "chat-A", "tg-222") == "auth-002"


# ── B1+B2: 跨渠道 session 可见性与切换验证 ──


class TestCrossChannelSession:
    """验证 Bot 和 Web 使用同一 auth_user_id 时 session 互通。"""

    @pytest.fixture
    def handler_with_bind(self, mock_adapter, bind_manager, tmp_db):
        """创建已绑定用户的 MessageHandler。"""
        from excelmanus.channels.message_handler import MessageHandler

        api = MagicMock()
        api.list_sessions = AsyncMock(return_value=[])
        api.stream_chat_events = AsyncMock()
        store = SessionStore(store_path=Path(tempfile.mkdtemp()) / "s.json")
        handler = MessageHandler(
            adapter=mock_adapter,
            api_client=api,
            session_store=store,
            bind_manager=bind_manager,
        )
        # 创建用户并绑定
        tmp_db.create_user(UserRecord(id="auth-web-1", email="web@t.com", password_hash="h"))
        tmp_db.link_channel_user("auth-web-1", "telegram", "tg-bot-1")
        return handler, api, store, mock_adapter

    @pytest.mark.asyncio
    async def test_bot_sees_web_sessions(self, handler_with_bind):
        """Bot /sessions 通过 on_behalf_of 能看到 Web 创建的 session。"""
        handler, api, store, adapter = handler_with_bind
        # 模拟后端返回 Web 创建的 session
        api.list_sessions = AsyncMock(return_value=[
            {"session_id": "web-sess-1", "title": "Web Session", "message_count": 5},
        ])
        msg = ChannelMessage(
            channel="telegram", user=ChannelUser(user_id="tg-bot-1"),
            chat_id="chat-1", text="/sessions", is_command=True, command="sessions",
        )
        await handler.handle_message(msg)
        # list_sessions 应使用真实 auth user_id 作为 on_behalf_of
        api.list_sessions.assert_called_once_with(on_behalf_of="auth-web-1")
        # 应显示 session 列表
        sent = [call[0][1] for call in adapter.send_text.call_args_list]
        assert any("Web Session" in s for s in sent)

    @pytest.mark.asyncio
    async def test_bot_switches_to_web_session(self, handler_with_bind):
        """Bot /sessions <n> 切换到 Web 创建的 session，SessionStore 正确更新。"""
        handler, api, store, adapter = handler_with_bind
        api.list_sessions = AsyncMock(return_value=[
            {"session_id": "web-sess-1", "title": "Web Session", "message_count": 5},
            {"session_id": "bot-sess-2", "title": "Bot Session", "message_count": 3},
        ])
        msg = ChannelMessage(
            channel="telegram", user=ChannelUser(user_id="tg-bot-1"),
            chat_id="chat-1", text="/sessions 1",
            is_command=True, command="sessions", command_args=["1"],
        )
        await handler.handle_message(msg)
        # SessionStore 应记录切换后的 session_id
        stored_sid = store.get("telegram", "chat-1", "tg-bot-1")
        assert stored_sid == "web-sess-1"
        # 应有切换成功提示
        sent = [call[0][1] for call in adapter.send_text.call_args_list]
        assert any("切换" in s for s in sent)

    @pytest.mark.asyncio
    async def test_chat_after_cross_channel_switch(self, handler_with_bind):
        """切换到 Web session 后发消息，stream_chat_events 携带正确的 session_id 和 on_behalf_of。"""
        handler, api, store, adapter = handler_with_bind
        # 预设切换后的 session
        store.set("telegram", "chat-1", "tg-bot-1", "web-sess-1")

        # 记录 stream_chat_events 被调用时的参数
        call_kwargs: dict = {}

        async def mock_stream(message, session_id=None, *, chat_mode="write",
                              images=None, on_behalf_of=None, channel=None, **kw):
            call_kwargs.update({
                "message": message, "session_id": session_id,
                "on_behalf_of": on_behalf_of, "channel": channel,
            })
            yield ("session_init", {"session_id": session_id or "web-sess-1"})
            yield ("finish", {})

        api.stream_chat_events = mock_stream

        msg = ChannelMessage(
            channel="telegram", user=ChannelUser(user_id="tg-bot-1"),
            chat_id="chat-1", text="继续分析",
        )
        await handler.handle_message(msg)
        # 验证 stream_chat_events 被调用时携带了正确的参数
        assert call_kwargs.get("session_id") == "web-sess-1"
        assert call_kwargs.get("on_behalf_of") == "auth-web-1"


# ── C1: Service Token IP 限制测试 ──


class TestServiceTokenIPRestriction:
    """C1: EXCELMANUS_SERVICE_TOKEN_ALLOWED_IPS 白名单检查。"""

    def test_check_ip_loopback_allowed(self):
        """127.0.0.1 在默认白名单内。"""
        from excelmanus.auth.middleware import _check_ip_allowed, _DEFAULT_ALLOWED_IPS
        assert _check_ip_allowed("127.0.0.1", _DEFAULT_ALLOWED_IPS) is True

    def test_check_ip_ipv6_loopback_allowed(self):
        """::1 在默认白名单内。"""
        from excelmanus.auth.middleware import _check_ip_allowed, _DEFAULT_ALLOWED_IPS
        assert _check_ip_allowed("::1", _DEFAULT_ALLOWED_IPS) is True

    def test_check_ip_docker_private_range(self):
        """Docker 内网 IP 在默认白名单内。"""
        from excelmanus.auth.middleware import _check_ip_allowed, _DEFAULT_ALLOWED_IPS
        assert _check_ip_allowed("172.17.0.2", _DEFAULT_ALLOWED_IPS) is True
        assert _check_ip_allowed("10.0.0.5", _DEFAULT_ALLOWED_IPS) is True
        assert _check_ip_allowed("192.168.1.100", _DEFAULT_ALLOWED_IPS) is True

    def test_check_ip_public_rejected(self):
        """公网 IP 不在默认白名单内。"""
        from excelmanus.auth.middleware import _check_ip_allowed, _DEFAULT_ALLOWED_IPS
        assert _check_ip_allowed("8.8.8.8", _DEFAULT_ALLOWED_IPS) is False

    def test_check_ip_empty_whitelist_allows_all(self):
        """空白名单 → 跳过检查，全部放行。"""
        from excelmanus.auth.middleware import _check_ip_allowed
        assert _check_ip_allowed("8.8.8.8", "") is True
        assert _check_ip_allowed("1.2.3.4", "   ") is True

    def test_check_ip_custom_cidr(self):
        """自定义 CIDR 段匹配。"""
        from excelmanus.auth.middleware import _check_ip_allowed
        assert _check_ip_allowed("203.0.113.50", "203.0.113.0/24") is True
        assert _check_ip_allowed("203.0.114.1", "203.0.113.0/24") is False

    def test_check_ip_invalid_host(self):
        """无效客户端 IP → 拒绝。"""
        from excelmanus.auth.middleware import _check_ip_allowed, _DEFAULT_ALLOWED_IPS
        assert _check_ip_allowed("", _DEFAULT_ALLOWED_IPS) is False
        assert _check_ip_allowed("not-an-ip", _DEFAULT_ALLOWED_IPS) is False

    @pytest.mark.asyncio
    async def test_middleware_rejects_non_whitelisted_ip(self):
        """Middleware 对非白名单 IP 的 service token 请求返回 403。"""
        with patch.dict("os.environ", {
            "EXCELMANUS_JWT_SECRET": "test-secret-key-for-jwt",
            "EXCELMANUS_SERVICE_TOKEN_ALLOWED_IPS": "10.0.0.0/8",
        }):
            from excelmanus.auth.security import create_service_token
            from excelmanus.auth.middleware import AuthMiddleware

            token = create_service_token("test-bot")
            responses = []

            async def capture_send(msg):
                responses.append(msg)

            async def mock_app(scope, receive, send):
                pass  # should not be reached

            middleware = AuthMiddleware(mock_app)
            mock_app_state = MagicMock()
            mock_app_state.state.auth_enabled = True

            scope = {
                "type": "http",
                "path": "/api/v1/chat/stream",
                "method": "POST",
                "headers": [
                    (b"authorization", f"Bearer {token}".encode("latin-1")),
                ],
                "app": mock_app_state,
                "client": ("8.8.8.8", 54321),
            }

            await middleware(scope, AsyncMock(), capture_send)
            assert responses[0]["status"] == 403

    @pytest.mark.asyncio
    async def test_middleware_allows_whitelisted_ip(self):
        """Middleware 对白名单 IP 的 service token 请求正常放行。"""
        with patch.dict("os.environ", {
            "EXCELMANUS_JWT_SECRET": "test-secret-key-for-jwt",
            "EXCELMANUS_SERVICE_TOKEN_ALLOWED_IPS": "127.0.0.1,::1",
        }):
            from excelmanus.auth.security import create_service_token
            from excelmanus.auth.middleware import AuthMiddleware

            token = create_service_token("test-bot")
            captured_scope = {}

            async def mock_app(scope, receive, send):
                captured_scope.update(scope.get("state", {}))

            middleware = AuthMiddleware(mock_app)
            mock_app_state = MagicMock()
            mock_app_state.state.auth_enabled = True

            scope = {
                "type": "http",
                "path": "/api/v1/chat/stream",
                "method": "POST",
                "headers": [
                    (b"authorization", f"Bearer {token}".encode("latin-1")),
                ],
                "app": mock_app_state,
                "client": ("127.0.0.1", 12345),
            }

            await middleware(scope, AsyncMock(), AsyncMock())
            assert captured_scope.get("is_service_token") is True


# ── C3: X-On-Behalf-Of 一致性校验测试 ──


class TestOnBehalfOfValidation:
    """C3: Middleware 验证 X-On-Behalf-Of 引用的用户存在且活跃。"""

    @pytest.fixture(autouse=True)
    def clear_obo_cache(self):
        """每个测试前清空 OBO 缓存。"""
        from excelmanus.auth.middleware import _obo_cache
        _obo_cache._cache.clear()
        yield
        _obo_cache._cache.clear()

    @pytest.mark.asyncio
    async def test_valid_user_allowed(self, tmp_db):
        """OBO 引用的活跃用户 → 放行。"""
        with patch.dict("os.environ", {"EXCELMANUS_JWT_SECRET": "test-secret-key-for-jwt"}):
            from excelmanus.auth.security import create_service_token
            from excelmanus.auth.middleware import AuthMiddleware

            tmp_db.create_user(UserRecord(id="u-valid", email="v@t.com", password_hash="h"))
            token = create_service_token("test-bot")
            captured_scope = {}

            async def mock_app(scope, receive, send):
                captured_scope.update(scope.get("state", {}))

            middleware = AuthMiddleware(mock_app)
            mock_app_state = MagicMock()
            mock_app_state.state.auth_enabled = True
            mock_app_state.state.user_store = tmp_db

            scope = {
                "type": "http",
                "path": "/api/v1/sessions",
                "method": "GET",
                "headers": [
                    (b"authorization", f"Bearer {token}".encode("latin-1")),
                    (b"x-on-behalf-of", b"u-valid"),
                ],
                "app": mock_app_state,
                "client": ("127.0.0.1", 12345),
            }

            await middleware(scope, AsyncMock(), AsyncMock())
            assert captured_scope.get("user_id") == "u-valid"

    @pytest.mark.asyncio
    async def test_nonexistent_user_rejected(self, tmp_db):
        """OBO 引用的不存在用户 → 403。"""
        with patch.dict("os.environ", {"EXCELMANUS_JWT_SECRET": "test-secret-key-for-jwt"}):
            from excelmanus.auth.security import create_service_token
            from excelmanus.auth.middleware import AuthMiddleware

            token = create_service_token("test-bot")
            responses = []

            async def capture_send(msg):
                responses.append(msg)

            async def mock_app(scope, receive, send):
                pass

            middleware = AuthMiddleware(mock_app)
            mock_app_state = MagicMock()
            mock_app_state.state.auth_enabled = True
            mock_app_state.state.user_store = tmp_db  # 真实 store，无此用户

            scope = {
                "type": "http",
                "path": "/api/v1/sessions",
                "method": "GET",
                "headers": [
                    (b"authorization", f"Bearer {token}".encode("latin-1")),
                    (b"x-on-behalf-of", b"nonexistent-user"),
                ],
                "app": mock_app_state,
                "client": ("127.0.0.1", 12345),
            }

            await middleware(scope, AsyncMock(), capture_send)
            assert responses[0]["status"] == 403

    @pytest.mark.asyncio
    async def test_inactive_user_rejected(self, tmp_db):
        """OBO 引用的已禁用用户 → 403。"""
        with patch.dict("os.environ", {"EXCELMANUS_JWT_SECRET": "test-secret-key-for-jwt"}):
            from excelmanus.auth.security import create_service_token
            from excelmanus.auth.middleware import AuthMiddleware

            tmp_db.create_user(UserRecord(
                id="u-disabled", email="dis@t.com", password_hash="h", is_active=False,
            ))
            token = create_service_token("test-bot")
            responses = []

            async def capture_send(msg):
                responses.append(msg)

            async def mock_app(scope, receive, send):
                pass

            middleware = AuthMiddleware(mock_app)
            mock_app_state = MagicMock()
            mock_app_state.state.auth_enabled = True
            mock_app_state.state.user_store = tmp_db

            scope = {
                "type": "http",
                "path": "/api/v1/sessions",
                "method": "GET",
                "headers": [
                    (b"authorization", f"Bearer {token}".encode("latin-1")),
                    (b"x-on-behalf-of", b"u-disabled"),
                ],
                "app": mock_app_state,
                "client": ("127.0.0.1", 12345),
            }

            await middleware(scope, AsyncMock(), capture_send)
            assert responses[0]["status"] == 403

    @pytest.mark.asyncio
    async def test_channel_anon_prefix_always_allowed(self):
        """channel_anon: 前缀的 OBO 值直接放行，不查 DB。"""
        with patch.dict("os.environ", {"EXCELMANUS_JWT_SECRET": "test-secret-key-for-jwt"}):
            from excelmanus.auth.security import create_service_token
            from excelmanus.auth.middleware import AuthMiddleware

            token = create_service_token("test-bot")
            captured_scope = {}

            async def mock_app(scope, receive, send):
                captured_scope.update(scope.get("state", {}))

            middleware = AuthMiddleware(mock_app)
            mock_app_state = MagicMock()
            mock_app_state.state.auth_enabled = True
            # 不设置 user_store — 如果查 DB 会异常
            mock_app_state.state.user_store = None

            scope = {
                "type": "http",
                "path": "/api/v1/sessions",
                "method": "GET",
                "headers": [
                    (b"authorization", f"Bearer {token}".encode("latin-1")),
                    (b"x-on-behalf-of", b"channel_anon:telegram:12345"),
                ],
                "app": mock_app_state,
                "client": ("127.0.0.1", 12345),
            }

            await middleware(scope, AsyncMock(), AsyncMock())
            assert captured_scope.get("user_id") == "channel_anon:telegram:12345"


# ── Channel Unlink Confirmation Tests ──


class TestUnlinkChannelConfirmation:
    """DELETE /channel/links/{channel} 二次确认（密码校验）测试。"""

    @pytest.fixture
    def store_with_link(self, tmp_db):
        """创建有绑定的用户。"""
        from excelmanus.auth.security import hash_password

        hashed = hash_password("correct-password")
        tmp_db.create_user(UserRecord(id="u1", email="a@t.com", password_hash=hashed))
        tmp_db.link_channel_user("u1", "telegram", "tg-100")
        return tmp_db

    def _make_request(self, store):
        req = MagicMock()
        req.app.state.user_store = store
        # _get_store reads from request.app.state
        return req

    @pytest.mark.asyncio
    async def test_password_user_correct_password(self, store_with_link):
        """有密码用户提供正确密码 → 解绑成功。"""
        from excelmanus.auth.router import unlink_channel
        from excelmanus.auth.models import UnlinkChannelRequest

        user = store_with_link.get_by_id("u1")
        body = UnlinkChannelRequest(password="correct-password")
        req = self._make_request(store_with_link)

        result = await unlink_channel(channel="telegram", request=req, user=user, body=body)
        assert result["status"] == "unlinked"
        assert result["channel"] == "telegram"
        # 确认绑定已清除
        assert store_with_link.get_user_by_channel("telegram", "tg-100") is None

    @pytest.mark.asyncio
    async def test_password_user_wrong_password(self, store_with_link):
        """有密码用户提供错误密码 → 400。"""
        from fastapi import HTTPException
        from excelmanus.auth.router import unlink_channel
        from excelmanus.auth.models import UnlinkChannelRequest

        user = store_with_link.get_by_id("u1")
        body = UnlinkChannelRequest(password="wrong-password")
        req = self._make_request(store_with_link)

        with pytest.raises(HTTPException) as exc_info:
            await unlink_channel(channel="telegram", request=req, user=user, body=body)
        assert exc_info.value.status_code == 400
        assert "密码错误" in exc_info.value.detail
        # 绑定应仍存在
        assert store_with_link.get_user_by_channel("telegram", "tg-100") is not None

    @pytest.mark.asyncio
    async def test_password_user_no_password(self, store_with_link):
        """有密码用户不提供密码 → 400。"""
        from fastapi import HTTPException
        from excelmanus.auth.router import unlink_channel

        user = store_with_link.get_by_id("u1")
        req = self._make_request(store_with_link)

        with pytest.raises(HTTPException) as exc_info:
            await unlink_channel(channel="telegram", request=req, user=user, body=None)
        assert exc_info.value.status_code == 400
        assert "密码" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_oauth_user_no_password_required(self, tmp_db):
        """纯 OAuth 用户（无密码）不需要提供密码即可解绑。"""
        from excelmanus.auth.router import unlink_channel

        tmp_db.create_user(UserRecord(id="u2", email="b@t.com", password_hash=None))
        tmp_db.link_channel_user("u2", "telegram", "tg-200")

        user = tmp_db.get_by_id("u2")
        req = self._make_request(tmp_db)

        result = await unlink_channel(channel="telegram", request=req, user=user, body=None)
        assert result["status"] == "unlinked"
        assert tmp_db.get_user_by_channel("telegram", "tg-200") is None


# ── Service Token Renewal / Rotation Tests ──


class TestServiceTokenRenewal:
    """C4: Service Token 自动续签与轮换测试。"""

    def test_auto_renew_when_expiring_soon(self, tmp_path):
        """剩余不足 30 天的 token 应被自动续签。"""
        with patch.dict("os.environ", {"EXCELMANUS_JWT_SECRET": "test-secret-key-for-jwt"}):
            from excelmanus.auth.security import (
                create_service_token, get_or_create_service_token,
                decode_service_token, SERVICE_TOKEN_RENEW_DAYS,
            )
            from datetime import timedelta

            # 创建一个快要过期的 token（剩余 10 天）
            short_token = create_service_token(
                expires_delta=timedelta(days=10),
            )
            token_file = tmp_path / ".service_token"
            token_file.write_text(short_token, encoding="utf-8")

            with patch("pathlib.Path.home", return_value=tmp_path):
                # 确保目录结构匹配
                data_dir = tmp_path / ".excelmanus" / "data"
                data_dir.mkdir(parents=True, exist_ok=True)
                real_file = data_dir / ".service_token"
                real_file.write_text(short_token, encoding="utf-8")

                with patch("excelmanus.auth.security._restrict_file_permissions", create=True):
                    with patch("excelmanus.security.cipher._restrict_file_permissions"):
                        result = get_or_create_service_token()

            # 应该返回一个新 token（不是旧的快过期的那个）
            assert result != short_token
            payload = decode_service_token(result)
            assert payload is not None

    def test_no_renew_when_plenty_remaining(self, tmp_path):
        """剩余时间充足的 token 不应被续签。"""
        with patch.dict("os.environ", {"EXCELMANUS_JWT_SECRET": "test-secret-key-for-jwt"}):
            from excelmanus.auth.security import (
                create_service_token, get_or_create_service_token,
                decode_service_token,
            )
            from datetime import timedelta

            # 创建一个剩余 200 天的 token
            long_token = create_service_token(
                expires_delta=timedelta(days=200),
            )
            data_dir = tmp_path / ".excelmanus" / "data"
            data_dir.mkdir(parents=True, exist_ok=True)
            real_file = data_dir / ".service_token"
            real_file.write_text(long_token, encoding="utf-8")

            with patch("pathlib.Path.home", return_value=tmp_path):
                result = get_or_create_service_token()

            # 应该返回原 token
            assert result == long_token

    def test_rotate_creates_new_token(self, tmp_path):
        """rotate_service_token 应生成新 token 并持久化到文件。"""
        with patch.dict("os.environ", {"EXCELMANUS_JWT_SECRET": "test-secret-key-for-jwt"}):
            from excelmanus.auth.security import (
                rotate_service_token, decode_service_token,
            )

            data_dir = tmp_path / ".excelmanus" / "data"
            data_dir.mkdir(parents=True, exist_ok=True)
            real_file = data_dir / ".service_token"
            real_file.write_text("old-invalid-token", encoding="utf-8")

            with patch("pathlib.Path.home", return_value=tmp_path):
                with patch("excelmanus.security.cipher._restrict_file_permissions"):
                    new_token = rotate_service_token()

            payload = decode_service_token(new_token)
            assert payload is not None
            assert payload["type"] == "service"
            # 持久化文件应已更新为新 token
            assert real_file.read_text(encoding="utf-8") == new_token
            assert real_file.read_text(encoding="utf-8") != "old-invalid-token"

    def test_rotate_env_token_returns_same(self):
        """环境变量指定的 token 无法轮换，应返回原值。"""
        with patch.dict("os.environ", {
            "EXCELMANUS_JWT_SECRET": "test-secret-key-for-jwt",
            "EXCELMANUS_SERVICE_TOKEN": "env-fixed-token",
        }):
            from excelmanus.auth.security import rotate_service_token

            result = rotate_service_token()
            assert result == "env-fixed-token"


# ── EventBridge Tests ──


class TestEventBridge:
    """B3: EventBridge 实时事件推送测试。"""

    @pytest.mark.asyncio
    async def test_subscribe_and_notify(self):
        """订阅后 notify 应触发回调。"""
        from excelmanus.channels.event_bridge import EventBridge

        bridge = EventBridge()
        received = []

        async def cb(event_type, data):
            received.append((event_type, data))

        bridge.subscribe("user-1", "telegram", "chat-100", cb)
        assert bridge.subscription_count == 1

        count = await bridge.notify("user-1", "approval", {"approval_id": "a1"})
        assert count == 1
        assert len(received) == 1
        assert received[0] == ("approval", {"approval_id": "a1"})

    @pytest.mark.asyncio
    async def test_notify_no_subscriber(self):
        """无订阅者时 notify 应返回 0。"""
        from excelmanus.channels.event_bridge import EventBridge

        bridge = EventBridge()
        count = await bridge.notify("user-999", "approval", {})
        assert count == 0

    @pytest.mark.asyncio
    async def test_unsubscribe(self):
        """取消订阅后不再收到通知。"""
        from excelmanus.channels.event_bridge import EventBridge

        bridge = EventBridge()
        received = []

        async def cb(event_type, data):
            received.append(event_type)

        bridge.subscribe("user-1", "telegram", "chat-100", cb)
        bridge.unsubscribe("user-1", "telegram")
        assert bridge.subscription_count == 0

        count = await bridge.notify("user-1", "approval", {})
        assert count == 0
        assert len(received) == 0

    @pytest.mark.asyncio
    async def test_callback_error_does_not_break(self):
        """回调异常不应影响其他回调。"""
        from excelmanus.channels.event_bridge import EventBridge

        bridge = EventBridge()
        received = []

        async def bad_cb(event_type, data):
            raise RuntimeError("boom")

        async def good_cb(event_type, data):
            received.append(event_type)

        bridge.subscribe("user-1", "telegram", "chat-1", bad_cb)
        bridge.subscribe("user-1", "telegram", "chat-2", good_cb)

        count = await bridge.notify("user-1", "question", {"id": "q1"})
        assert count == 1  # good_cb succeeded
        assert received == ["question"]

    @pytest.mark.asyncio
    async def test_duplicate_subscribe_replaces(self):
        """同一 (user, channel, chat_id) 重复订阅应替换旧回调。"""
        from excelmanus.channels.event_bridge import EventBridge

        bridge = EventBridge()
        calls_old = []
        calls_new = []

        async def old_cb(et, d):
            calls_old.append(et)

        async def new_cb(et, d):
            calls_new.append(et)

        bridge.subscribe("user-1", "tg", "c1", old_cb)
        bridge.subscribe("user-1", "tg", "c1", new_cb)
        assert bridge.subscription_count == 1

        await bridge.notify("user-1", "test", {})
        assert len(calls_old) == 0
        assert len(calls_new) == 1


# ── get_current_user + Service Token 回归测试 ──
# 修复: /api/v1/auth/me/* 在 middleware PUBLIC_PREFIXES 中被跳过，
# 而 get_current_user 只接受 type=access 的 token，导致 bot 渠道
# 用服务令牌调用 /me/usage 时返回 401，/quota 命令永远显示"未绑定"。


class TestGetCurrentUserServiceToken:
    """验证 get_current_user 依赖对服务令牌 + X-On-Behalf-Of 的支持。"""

    @pytest.mark.asyncio
    async def test_service_token_with_valid_obo_resolves_user(self):
        """服务令牌 + 有效 X-On-Behalf-Of 应返回目标用户。"""
        with patch.dict("os.environ", {"EXCELMANUS_JWT_SECRET": "test-secret-key-for-jwt"}):
            from excelmanus.auth.security import create_service_token
            from excelmanus.auth.dependencies import get_current_user
            from fastapi.security import HTTPAuthorizationCredentials

            token = create_service_token("test-bot")
            credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

            target_user = UserRecord(id="user-42", email="alice@test.com", password_hash="hash")
            mock_store = MagicMock()
            mock_store.get_by_id.return_value = target_user

            request = MagicMock()
            request.headers.get.return_value = "user-42"
            request.app.state.user_store = mock_store
            request.state = MagicMock()

            user = await get_current_user(request, credentials)
            assert user.id == "user-42"
            mock_store.get_by_id.assert_called_once_with("user-42")

    @pytest.mark.asyncio
    async def test_service_token_without_obo_raises_401(self):
        """服务令牌但无 X-On-Behalf-Of 应返回 401。"""
        with patch.dict("os.environ", {"EXCELMANUS_JWT_SECRET": "test-secret-key-for-jwt"}):
            from excelmanus.auth.security import create_service_token
            from excelmanus.auth.dependencies import get_current_user
            from fastapi import HTTPException
            from fastapi.security import HTTPAuthorizationCredentials

            token = create_service_token("test-bot")
            credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

            request = MagicMock()
            request.headers.get.return_value = ""
            request.app.state.user_store = MagicMock()

            with pytest.raises(HTTPException) as exc_info:
                await get_current_user(request, credentials)
            assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_service_token_with_anon_obo_raises_401(self):
        """服务令牌 + channel_anon: 前缀的匿名 ID 应返回 401。"""
        with patch.dict("os.environ", {"EXCELMANUS_JWT_SECRET": "test-secret-key-for-jwt"}):
            from excelmanus.auth.security import create_service_token
            from excelmanus.auth.dependencies import get_current_user
            from fastapi import HTTPException
            from fastapi.security import HTTPAuthorizationCredentials

            token = create_service_token("test-bot")
            credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

            request = MagicMock()
            request.headers.get.return_value = "channel_anon:telegram:12345"
            request.app.state.user_store = MagicMock()

            with pytest.raises(HTTPException) as exc_info:
                await get_current_user(request, credentials)
            assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_access_token_still_works(self):
        """普通 access token 应继续正常工作。"""
        with patch.dict("os.environ", {"EXCELMANUS_JWT_SECRET": "test-secret-key-for-jwt"}):
            from excelmanus.auth.security import create_access_token
            from excelmanus.auth.dependencies import get_current_user
            from fastapi.security import HTTPAuthorizationCredentials

            token = create_access_token({"sub": "user-1", "type": "access", "role": "user"})
            credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

            target_user = UserRecord(id="user-1", email="alice@test.com", password_hash="hash")
            mock_store = MagicMock()
            mock_store.get_by_id.return_value = target_user

            request = MagicMock()
            request.app.state.user_store = mock_store
            request.state = MagicMock()

            user = await get_current_user(request, credentials)
            assert user.id == "user-1"
