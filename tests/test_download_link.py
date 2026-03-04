"""文件下载链接功能测试：下载令牌、公开下载端点、Bot 渠道回退逻辑。"""

from __future__ import annotations

import asyncio
import time
from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from excelmanus.auth.security import (
    DOWNLOAD_TOKEN_EXPIRE_MINUTES,
    create_download_token,
    decode_download_token,
)


# ══════════════════════════════════════════════════════════════
# TestDownloadToken — 下载令牌创建 / 解码 / 过期
# ══════════════════════════════════════════════════════════════


class TestDownloadToken:
    """下载令牌基本功能测试。"""

    def test_create_and_decode(self):
        """创建令牌后能正常解码，包含正确字段。"""
        token = create_download_token("outputs/report.xlsx", user_id="user123")
        claims = decode_download_token(token)
        assert claims is not None
        assert claims["type"] == "download"
        assert claims["file_path"] == "outputs/report.xlsx"
        assert claims["sub"] == "user123"
        assert "exp" in claims

    def test_decode_wrong_type_returns_none(self):
        """非 download 类型的令牌应返回 None。"""
        from excelmanus.auth.security import create_access_token

        token = create_access_token({"sub": "user1", "role": "user"})
        assert decode_download_token(token) is None

    def test_empty_file_path_returns_none(self):
        """file_path 为空的令牌解码应返回 None。"""
        token = create_download_token("", user_id="user1")
        assert decode_download_token(token) is None

    def test_expired_token_returns_none(self):
        """过期令牌应返回 None。"""
        token = create_download_token(
            "test.xlsx",
            user_id="u1",
            expires_delta=timedelta(seconds=-1),
        )
        assert decode_download_token(token) is None

    def test_invalid_token_string(self):
        """随机字符串应返回 None。"""
        assert decode_download_token("not-a-real-token") is None
        assert decode_download_token("") is None

    def test_default_user_id_empty(self):
        """不传 user_id 时默认为空字符串。"""
        token = create_download_token("file.xlsx")
        claims = decode_download_token(token)
        assert claims is not None
        assert claims["sub"] == ""

    def test_custom_expiry(self):
        """自定义有效期应生效。"""
        token = create_download_token(
            "file.xlsx",
            expires_delta=timedelta(hours=1),
        )
        claims = decode_download_token(token)
        assert claims is not None
        # 1 小时后过期，应大于当前 + 59 分钟
        import datetime

        exp = claims["exp"]
        now = datetime.datetime.now(tz=datetime.timezone.utc).timestamp()
        assert exp > now + 3500  # at least ~58 min


# ══════════════════════════════════════════════════════════════
# TestMiddlewarePublicPrefix — 下载端点绕过 auth
# ══════════════════════════════════════════════════════════════


class TestMiddlewarePublicPrefix:
    """确保 /api/v1/files/dl/ 在 auth 中间件公开前缀列表中。"""

    def test_dl_prefix_in_public(self):
        from excelmanus.auth.middleware import _PUBLIC_PREFIXES

        assert any("/api/v1/files/dl/" in p for p in _PUBLIC_PREFIXES)


# ══════════════════════════════════════════════════════════════
# TestApiClientGenerateDownloadLink — api_client 方法
# ══════════════════════════════════════════════════════════════


class TestApiClientGenerateDownloadLink:
    """测试 api_client.generate_download_link 方法。"""

    @pytest.mark.asyncio
    async def test_returns_url_on_success(self):
        from excelmanus.channels.api_client import ExcelManusAPIClient

        client = ExcelManusAPIClient(api_url="http://localhost:8000")
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"url": "http://example.com/api/v1/files/dl/tok123"}

        with patch.object(client, "_request", new_callable=AsyncMock, return_value=mock_resp):
            url = await client.generate_download_link("test.xlsx", user_id="u1")

        assert url == "http://example.com/api/v1/files/dl/tok123"

    @pytest.mark.asyncio
    async def test_returns_none_on_failure(self):
        from excelmanus.channels.api_client import ExcelManusAPIClient

        client = ExcelManusAPIClient(api_url="http://localhost:8000")

        with patch.object(client, "_request", new_callable=AsyncMock, side_effect=Exception("fail")):
            url = await client.generate_download_link("test.xlsx")

        assert url is None


# ══════════════════════════════════════════════════════════════
# TestMessageHandlerFileDownloadFallback — 文件下载回退逻辑
# ══════════════════════════════════════════════════════════════


class TestMessageHandlerFileDownloadFallback:
    """测试 message_handler 文件下载的 send_file → 下载链接回退。"""

    def _make_handler(self):
        """创建带 mock 的 MessageHandler。"""
        from excelmanus.channels.message_handler import MessageHandler
        from excelmanus.channels.session_store import SessionStore

        adapter = MagicMock()
        adapter.name = "test"
        adapter.send_text = AsyncMock()
        adapter.send_file = AsyncMock()
        adapter.send_approval_card = AsyncMock()
        adapter.send_question_card = AsyncMock()
        adapter.send_staged_card = AsyncMock()
        adapter.show_typing = AsyncMock()

        api = MagicMock()
        api.download_file = AsyncMock(return_value=(b"data", "test.xlsx"))
        api.generate_download_link = AsyncMock(return_value="http://example.com/api/v1/files/dl/tok")
        api.list_staged = AsyncMock(return_value={"files": []})

        sessions = SessionStore()
        handler = MessageHandler(adapter=adapter, api_client=api, session_store=sessions)
        return handler, adapter, api

    @pytest.mark.asyncio
    async def test_send_file_success_no_fallback(self):
        """send_file 成功时不发送文本回退。"""
        handler, adapter, api = self._make_handler()

        result = {
            "file_downloads": [{"file_path": "out/test.xlsx", "filename": "test.xlsx"}],
            "reply": "",
        }
        await handler._dispatch_non_text_results("chat1", "user1", result)

        adapter.send_file.assert_called_once()
        # 不应发送文本回退
        text_calls = [c for c in adapter.send_text.call_args_list if "下载链接" in str(c)]
        assert len(text_calls) == 0

    @pytest.mark.asyncio
    async def test_send_file_fails_fallback_to_link(self):
        """send_file 失败时回退到下载链接。"""
        handler, adapter, api = self._make_handler()
        adapter.send_file.side_effect = Exception("channel error")

        result = {
            "file_downloads": [{"file_path": "out/test.xlsx", "filename": "test.xlsx"}],
            "reply": "",
        }
        await handler._dispatch_non_text_results("chat1", "user1", result)

        # 应发送包含下载链接的文本
        text_calls = adapter.send_text.call_args_list
        link_msgs = [c for c in text_calls if "下载链接" in str(c)]
        assert len(link_msgs) >= 1
        assert "http://example.com" in str(link_msgs[0])

    @pytest.mark.asyncio
    async def test_send_file_fails_no_link_fallback_to_text(self):
        """send_file 和链接生成都失败时回退到纯文本提示。"""
        handler, adapter, api = self._make_handler()
        adapter.send_file.side_effect = Exception("channel error")
        api.generate_download_link.return_value = None

        result = {
            "file_downloads": [{"file_path": "out/test.xlsx", "filename": "test.xlsx"}],
            "reply": "",
        }
        await handler._dispatch_non_text_results("chat1", "user1", result)

        text_calls = adapter.send_text.call_args_list
        web_msgs = [c for c in text_calls if "Web" in str(c)]
        assert len(web_msgs) >= 1

    @pytest.mark.asyncio
    async def test_qq_adapter_triggers_fallback(self):
        """QQ adapter send_file 抛 NotImplementedError 触发下载链接回退。"""
        handler, adapter, api = self._make_handler()
        adapter.send_file.side_effect = NotImplementedError("QQ 不支持")

        result = {
            "file_downloads": [{"file_path": "out/data.csv", "filename": "data.csv"}],
            "reply": "",
        }
        await handler._dispatch_non_text_results("chat1", "user1", result)

        text_calls = adapter.send_text.call_args_list
        link_msgs = [c for c in text_calls if "下载链接" in str(c)]
        assert len(link_msgs) >= 1

    @pytest.mark.asyncio
    async def test_multiple_file_downloads(self):
        """多个文件下载各自独立处理。"""
        handler, adapter, api = self._make_handler()
        # 第一个文件成功，第二个失败
        adapter.send_file.side_effect = [None, Exception("fail")]

        result = {
            "file_downloads": [
                {"file_path": "out/a.xlsx", "filename": "a.xlsx"},
                {"file_path": "out/b.xlsx", "filename": "b.xlsx"},
            ],
            "reply": "",
        }
        await handler._dispatch_non_text_results("chat1", "user1", result)

        assert adapter.send_file.call_count == 2
        # 第二个文件应有回退文本
        text_calls = adapter.send_text.call_args_list
        link_msgs = [c for c in text_calls if "下载链接" in str(c)]
        assert len(link_msgs) >= 1

    @pytest.mark.asyncio
    async def test_empty_file_path_skipped(self):
        """空 file_path 不处理。"""
        handler, adapter, api = self._make_handler()

        result = {
            "file_downloads": [{"file_path": "", "filename": ""}],
            "reply": "",
        }
        await handler._dispatch_non_text_results("chat1", "user1", result)

        adapter.send_file.assert_not_called()
        api.generate_download_link.assert_not_called()


# ══════════════════════════════════════════════════════════════
# TestQQAdapterSendFile — QQ adapter send_file 行为
# ══════════════════════════════════════════════════════════════


class TestQQAdapterSendFile:
    """QQ adapter send_file 应抛异常。"""

    def test_raises_not_implemented(self):
        from excelmanus.channels.qq.adapter import QQBotAdapter

        adapter = QQBotAdapter.__new__(QQBotAdapter)
        with pytest.raises(NotImplementedError):
            asyncio.get_event_loop().run_until_complete(
                adapter.send_file("chat1", b"data", "file.xlsx")
            )


# ══════════════════════════════════════════════════════════════
# TestDownloadLinkPublicUrlResolution — public_url 优先级测试
# ══════════════════════════════════════════════════════════════


class TestDownloadLinkPublicUrlResolution:
    """测试 create_download_link 端点的 public_url 解析优先级：env > config_kv > Host 推断。"""

    def _make_request(self, host: str = "localhost:8000", scheme: str = "http"):
        """创建 mock Request 对象。"""
        req = MagicMock()
        req.json = AsyncMock(return_value={"file_path": "out/test.xlsx", "user_id": "u1"})
        req.headers = {"host": host, "x-forwarded-proto": scheme}
        url = MagicMock()
        url.scheme = scheme
        req.url = url
        return req

    @pytest.mark.asyncio
    async def test_env_public_url_takes_priority(self):
        """EXCELMANUS_PUBLIC_URL 环境变量优先于 config_kv。"""
        import excelmanus.api as api_mod

        mock_config = MagicMock()
        mock_config.public_url = "https://env.example.com"
        mock_config.workspace_root = "."
        mock_config.data_root = ""

        mock_store = MagicMock()
        mock_store.get.return_value = "https://db.example.com"

        with (
            patch.object(api_mod, "_config", mock_config),
            patch.object(api_mod, "_config_store", mock_store),
            patch.object(api_mod, "_resolve_excel_path", return_value="/tmp/test.xlsx"),
        ):
            req = self._make_request()
            resp = await api_mod.create_download_link(req)
            body = resp.body.decode()
            import json
            data = json.loads(body)
            assert data["url"].startswith("https://env.example.com/")
            # config_kv 不应被查询（env 已生效）
            mock_store.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_config_kv_public_url_used_when_env_empty(self):
        """环境变量为空时回退到 config_kv 中的 channel_public_url。"""
        import excelmanus.api as api_mod

        mock_config = MagicMock()
        mock_config.public_url = ""  # env 未设置
        mock_config.workspace_root = "."
        mock_config.data_root = ""

        mock_store = MagicMock()
        mock_store.get.return_value = "https://db.example.com"

        with (
            patch.object(api_mod, "_config", mock_config),
            patch.object(api_mod, "_config_store", mock_store),
            patch.object(api_mod, "_resolve_excel_path", return_value="/tmp/test.xlsx"),
        ):
            req = self._make_request()
            resp = await api_mod.create_download_link(req)
            body = resp.body.decode()
            import json
            data = json.loads(body)
            assert data["url"].startswith("https://db.example.com/")
            mock_store.get.assert_called_once_with("channel_public_url", "")

    @pytest.mark.asyncio
    async def test_host_fallback_when_both_empty(self):
        """环境变量和 config_kv 都为空时回退到请求 Host。"""
        import excelmanus.api as api_mod

        mock_config = MagicMock()
        mock_config.public_url = ""
        mock_config.workspace_root = "."
        mock_config.data_root = ""

        mock_store = MagicMock()
        mock_store.get.return_value = ""  # config_kv 也为空

        with (
            patch.object(api_mod, "_config", mock_config),
            patch.object(api_mod, "_config_store", mock_store),
            patch.object(api_mod, "_resolve_excel_path", return_value="/tmp/test.xlsx"),
        ):
            req = self._make_request(host="myserver.com:8000", scheme="https")
            resp = await api_mod.create_download_link(req)
            body = resp.body.decode()
            import json
            data = json.loads(body)
            assert data["url"].startswith("https://myserver.com:8000/")

    @pytest.mark.asyncio
    async def test_config_kv_trailing_slash_stripped(self):
        """config_kv 中的 public_url 尾部斜杠应被清理。"""
        import excelmanus.api as api_mod

        mock_config = MagicMock()
        mock_config.public_url = ""
        mock_config.workspace_root = "."
        mock_config.data_root = ""

        mock_store = MagicMock()
        mock_store.get.return_value = "https://db.example.com/"  # 带尾部斜杠

        with (
            patch.object(api_mod, "_config", mock_config),
            patch.object(api_mod, "_config_store", mock_store),
            patch.object(api_mod, "_resolve_excel_path", return_value="/tmp/test.xlsx"),
        ):
            req = self._make_request()
            resp = await api_mod.create_download_link(req)
            body = resp.body.decode()
            import json
            data = json.loads(body)
            # URL 不应包含双斜杠
            assert "/api/v1/files/dl/" in data["url"]
            assert "//api" not in data["url"]
