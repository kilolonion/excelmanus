"""Bot 端错误文本适配和上下文感知引导的测试。

覆盖：
- _rewrite_error_for_bot: Web 端语言替换为 Bot 可操作提示
- _bot_error_guidance: 根据错误类型返回精确 bot 命令建议
- _dispatch_non_text_results: error_is_reply 去重 + 引导消息
- ChunkedOutputManager.finalize: error_is_reply 标记 + 错误文本改写
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from excelmanus.channels.message_handler import MessageHandler
from excelmanus.channels.output_manager import ChunkedOutputManager


# ── Fixtures ──


@pytest.fixture
def handler():
    """创建带 mock 依赖的 MessageHandler。"""
    adapter = MagicMock()
    adapter.name = "telegram"
    adapter.send_text = AsyncMock()
    adapter.send_file = AsyncMock()
    adapter.send_approval_card = AsyncMock()
    adapter.send_question_card = AsyncMock()
    adapter.send_staged_card = AsyncMock()
    api = MagicMock()
    session_store = MagicMock()
    session_store.get.return_value = "sid-123"
    h = MessageHandler(adapter, api, session_store)
    return h


# ── TestRewriteErrorForBot ──


class TestRewriteErrorForBot:
    """_rewrite_error_for_bot: Web 端语言 → Bot 端替换。"""

    def test_rewrite_refresh_page_new_dialog(self):
        error = "会话已过期或被清理，请刷新页面开始新对话。"
        result = MessageHandler._rewrite_error_for_bot(error)
        assert "请使用 /new 开始新对话" in result
        assert "刷新页面" not in result

    def test_rewrite_refresh_page_restart(self):
        error = "会话已过期或不存在，请刷新页面重新开始。"
        result = MessageHandler._rewrite_error_for_bot(error)
        assert "请使用 /new 重新开始" in result
        assert "刷新页面" not in result

    def test_rewrite_refresh_page_retry(self):
        error = "请求的工具不可用，请刷新页面后重试。"
        result = MessageHandler._rewrite_error_for_bot(error)
        assert "请稍后重试" in result
        assert "刷新页面" not in result

    def test_rewrite_model_settings(self):
        error = "API Key 无效或已过期，请在模型设置中更新。"
        result = MessageHandler._rewrite_error_for_bot(error)
        assert "/addmodel" in result
        assert "模型设置中更新" not in result

    def test_rewrite_model_id_settings(self):
        error = "请求的模型标识无效或已下线，请在设置中确认 Model ID。"
        result = MessageHandler._rewrite_error_for_bot(error)
        assert "/model" in result
        assert "设置中确认" not in result

    def test_rewrite_login(self):
        error = "认证失败，请重新登录。"
        result = MessageHandler._rewrite_error_for_bot(error)
        assert "/bind" in result
        assert "重新登录" not in result

    def test_no_rewrite_when_no_match(self):
        error = "工具执行失败，请稍后重试。"
        result = MessageHandler._rewrite_error_for_bot(error)
        assert result == error  # 无匹配，原样返回


# ── TestBotErrorGuidance ──


class TestBotErrorGuidance:
    """_bot_error_guidance: 根据错误内容返回上下文感知引导。"""

    @pytest.fixture
    def handler(self):
        adapter = MagicMock()
        adapter.name = "telegram"
        api = MagicMock()
        session_store = MagicMock()
        return MessageHandler(adapter, api, session_store)

    def test_session_not_found(self, handler):
        guidance = handler._bot_error_guidance("会话不存在: 会话已过期或被清理")
        assert "/new" in guidance
        assert "/abort" not in guidance

    def test_session_expired(self, handler):
        guidance = handler._bot_error_guidance("会话已过期或不存在")
        assert "/new" in guidance

    def test_api_key_error(self, handler):
        guidance = handler._bot_error_guidance("模型认证失败: API Key 无效")
        assert "/model" in guidance
        assert "/addmodel" in guidance

    def test_auth_error(self, handler):
        guidance = handler._bot_error_guidance("AuthenticationError: invalid key")
        assert "/model" in guidance
        assert "/addmodel" in guidance

    def test_model_not_found(self, handler):
        guidance = handler._bot_error_guidance("模型不存在: gpt-5")
        assert "/model" in guidance

    def test_rate_limit(self, handler):
        guidance = handler._bot_error_guidance("请求频率受限，请稍后")
        assert "频繁" in guidance or "稍等" in guidance

    def test_session_busy(self, handler):
        guidance = handler._bot_error_guidance("会话正在处理中")
        assert "/abort" in guidance

    def test_permission_error(self, handler):
        guidance = handler._bot_error_guidance("权限不足，无法执行")
        assert "/bind" in guidance

    def test_default_fallback(self, handler):
        """未匹配任何模式时，返回通用引导。"""
        guidance = handler._bot_error_guidance("未知的内部错误")
        assert "/abort" in guidance
        assert "/undo" in guidance

    def test_auth_error_unbound_user_suggests_bind(self, handler):
        """未绑定用户遇到模型认证错误时，优先提示 /bind。"""
        handler._bind_manager = MagicMock()
        handler._resolve_auth_user_id = MagicMock(return_value=None)
        guidance = handler._bot_error_guidance(
            "模型认证失败: API Key 无效", platform_user_id="tg_user_1",
        )
        assert "/bind" in guidance
        assert "OAuth" in guidance
        assert "/addmodel" in guidance

    def test_auth_error_bound_user_no_bind_hint(self, handler):
        """已绑定用户遇到模型认证错误时，不提示 /bind，而是通用引导。"""
        handler._bind_manager = MagicMock()
        handler._resolve_auth_user_id = MagicMock(return_value="auth-user-123")
        guidance = handler._bot_error_guidance(
            "模型认证失败: API Key 无效", platform_user_id="tg_user_1",
        )
        assert "/model" in guidance
        assert "/addmodel" in guidance
        assert "OAuth" not in guidance

    def test_auth_error_no_bind_manager_no_bind_hint(self, handler):
        """无 bind_manager 时（认证未启用），不提示 /bind。"""
        handler._bind_manager = None
        guidance = handler._bot_error_guidance(
            "模型认证失败: API Key 无效", platform_user_id="tg_user_1",
        )
        assert "/model" in guidance
        assert "OAuth" not in guidance

    # ── 新增: 覆盖所有 error_guidance.py 错误类型 ──

    def test_quota_exceeded(self, handler):
        guidance = handler._bot_error_guidance("额度不足: 模型 API 额度已用尽")
        assert "/model" in guidance
        assert "余额" in guidance or "额度" in guidance

    def test_session_limit(self, handler):
        guidance = handler._bot_error_guidance("会话数量超限: 会话数量已达上限")
        assert "/sessions" in guidance

    def test_context_length_exceeded(self, handler):
        guidance = handler._bot_error_guidance("上下文超长: 对话历史超出模型上下文窗口限制")
        assert "/compact" in guidance
        assert "/new" in guidance

    def test_invalid_request(self, handler):
        guidance = handler._bot_error_guidance("请求格式错误: 发送给模型的请求格式有误")
        assert "/compact" in guidance

    def test_base_url_error(self, handler):
        guidance = handler._bot_error_guidance("API 路径错误: Base URL 路径不正确")
        assert "Base URL" in guidance
        assert "管理员" in guidance

    def test_content_filtered(self, handler):
        guidance = handler._bot_error_guidance("内容审查拦截: 触发了内容安全策略")
        assert "调整" in guidance or "内容" in guidance

    def test_network_error(self, handler):
        guidance = handler._bot_error_guidance("网络连接失败: 无法连接到模型服务")
        assert "网络" in guidance

    def test_timeout(self, handler):
        guidance = handler._bot_error_guidance("连接超时: 模型服务响应超时")
        assert "超时" in guidance
        assert "重试" in guidance

    def test_ssl_error(self, handler):
        guidance = handler._bot_error_guidance("SSL/TLS 错误: 安全连接失败")
        assert "SSL" in guidance or "管理员" in guidance

    def test_proxy_error(self, handler):
        guidance = handler._bot_error_guidance("代理连接失败: 通过代理连接模型服务失败")
        assert "代理" in guidance

    def test_model_overloaded(self, handler):
        guidance = handler._bot_error_guidance("模型服务过载: 暂时不可用")
        assert "重试" in guidance
        assert "/model" in guidance

    def test_provider_internal_error(self, handler):
        guidance = handler._bot_error_guidance("模型服务异常: 服务返回 500 错误")
        assert "重试" in guidance

    def test_stream_interrupted(self, handler):
        guidance = handler._bot_error_guidance("流式传输中断: 模型响应传输中断")
        assert "重试" in guidance

    def test_workspace_full(self, handler):
        guidance = handler._bot_error_guidance("工作区已满: 工作区配额超限")
        assert "清理" in guidance

    def test_disk_full(self, handler):
        guidance = handler._bot_error_guidance("磁盘空间不足: 服务器磁盘空间不足")
        assert "管理员" in guidance

    def test_encoding_error(self, handler):
        guidance = handler._bot_error_guidance("编码错误: 数据编码异常")
        assert "编码" in guidance

    def test_payload_too_large(self, handler):
        guidance = handler._bot_error_guidance("请求体过大: 请求数据超出限制")
        assert "/compact" in guidance

    def test_response_parse_error(self, handler):
        guidance = handler._bot_error_guidance("响应解析失败: 无法解析的 JSON 数据")
        assert "重试" in guidance


# ── TestDispatchNonTextResults ──


class TestDispatchNonTextResults:
    """_dispatch_non_text_results: error_is_reply 去重 + 上下文引导。"""

    @pytest.mark.asyncio
    async def test_error_is_reply_skips_duplicate(self, handler):
        """error_is_reply=True 时不发送"处理未完成"消息。"""
        result = {
            "reply": "❌ 会话不存在: 请使用 /new 开始新对话",
            "error": "会话不存在: 会话已过期或被清理",
            "error_is_reply": True,
            "file_downloads": [],
            "approval": None,
            "question": None,
            "staging_event": None,
            "tool_calls": [],
        }
        await handler._dispatch_non_text_results("chat1", "user1", result)
        texts = [call.args[1] for call in handler.adapter.send_text.call_args_list]
        # 不应有"处理未完成"
        assert not any("处理未完成" in t for t in texts)
        # 应有引导消息
        assert any("/new" in t for t in texts)

    @pytest.mark.asyncio
    async def test_midstream_error_shows_rewritten(self, handler):
        """中途出错时（有 reply 但不是 error_is_reply），显示改写后的错误。"""
        result = {
            "reply": "这是正常的部分输出...",
            "error": "会话已过期或被清理，请刷新页面开始新对话。",
            "error_is_reply": False,
            "file_downloads": [],
            "approval": None,
            "question": None,
            "staging_event": None,
            "tool_calls": [],
        }
        await handler._dispatch_non_text_results("chat1", "user1", result)
        texts = [call.args[1] for call in handler.adapter.send_text.call_args_list]
        # 应有"处理未完成"但带改写文本
        incomplete = [t for t in texts if "处理未完成" in t]
        assert len(incomplete) == 1
        assert "/new" in incomplete[0]
        assert "刷新页面" not in incomplete[0]

    @pytest.mark.asyncio
    async def test_session_error_guidance(self, handler):
        """会话不存在错误 → 引导用 /new。"""
        result = {
            "reply": "",
            "error": "会话不存在",
            "error_is_reply": True,
            "file_downloads": [],
            "approval": None,
            "question": None,
            "staging_event": None,
            "tool_calls": [],
        }
        await handler._dispatch_non_text_results("chat1", "user1", result)
        texts = [call.args[1] for call in handler.adapter.send_text.call_args_list]
        guidance_texts = [t for t in texts if "💡" in t]
        assert len(guidance_texts) == 1
        assert "/new" in guidance_texts[0]

    @pytest.mark.asyncio
    async def test_api_key_error_guidance(self, handler):
        """API Key 错误 → 引导用 /model 和 /addmodel。"""
        result = {
            "reply": "",
            "error": "模型认证失败: API Key 无效、已过期或权限不足",
            "error_is_reply": True,
            "file_downloads": [],
            "approval": None,
            "question": None,
            "staging_event": None,
            "tool_calls": [],
        }
        await handler._dispatch_non_text_results("chat1", "user1", result)
        texts = [call.args[1] for call in handler.adapter.send_text.call_args_list]
        guidance_texts = [t for t in texts if "💡" in t]
        assert len(guidance_texts) == 1
        assert "/model" in guidance_texts[0]
        assert "/addmodel" in guidance_texts[0]

    @pytest.mark.asyncio
    async def test_no_guidance_when_approval_pending(self, handler):
        """有审批请求时不发送错误引导。"""
        result = {
            "reply": "",
            "error": "some error",
            "error_is_reply": True,
            "file_downloads": [],
            "approval": {"approval_id": "a1", "approval_tool_name": "t1",
                         "risk_level": "yellow", "args_summary": {}},
            "question": None,
            "staging_event": None,
            "tool_calls": [],
        }
        await handler._dispatch_non_text_results("chat1", "user1", result)
        texts = [call.args[1] for call in handler.adapter.send_text.call_args_list]
        guidance_texts = [t for t in texts if "💡" in t]
        assert len(guidance_texts) == 0


# ── TestUnbindStateCleanup ──


class TestUnbindStateCleanup:
    """_cmd_unbind: 解绑后清理 session + pending + 区分提示。"""

    @pytest.fixture
    def unbound_handler(self):
        """创建带 bind_manager 的 handler，模拟已绑定状态。"""
        adapter = MagicMock()
        adapter.name = "telegram"
        adapter.send_text = AsyncMock()
        adapter.send_markdown = AsyncMock()
        api = MagicMock()
        session_store = MagicMock()
        bind_manager = MagicMock()
        bind_manager.check_bind_status.return_value = "auth-user-123"
        bind_manager.unbind_channel.return_value = True
        h = MessageHandler(
            adapter, api, session_store,
            bind_manager=bind_manager,
        )
        # 预设一些状态
        pk = h._pending_key("chat1", "user1")
        h._pending[pk] = MagicMock()
        h._pending_files[pk] = [MagicMock()]
        h._staged_cache[pk] = [{"original_path": "/a.xlsx"}]
        h._last_apply[pk] = [{"original_path": "/a.xlsx"}]
        return h

    def _make_unbind_msg(self):
        from excelmanus.channels.base import ChannelMessage, ChannelUser
        user = ChannelUser(user_id="user1", username="testuser")
        return ChannelMessage(
            channel="telegram",
            chat_id="chat1",
            user=user,
            text="/unbind",
            is_command=True,
            command="unbind",
        )

    @pytest.mark.asyncio
    async def test_unbind_clears_session(self, unbound_handler):
        """解绑后 session store 被清理。"""
        msg = self._make_unbind_msg()
        await unbound_handler._cmd_unbind(msg)
        unbound_handler.sessions.remove.assert_called_once_with(
            "telegram", "chat1", "user1",
        )

    @pytest.mark.asyncio
    async def test_unbind_clears_pending(self, unbound_handler):
        """解绑后 pending 交互和文件缓冲被清理。"""
        msg = self._make_unbind_msg()
        pk = unbound_handler._pending_key("chat1", "user1")
        await unbound_handler._cmd_unbind(msg)
        assert pk not in unbound_handler._pending
        assert pk not in unbound_handler._pending_files
        assert pk not in unbound_handler._staged_cache
        assert pk not in unbound_handler._last_apply

    @pytest.mark.asyncio
    async def test_unbind_message_no_require_bind(self, unbound_handler):
        """_require_bind=False 时提示匿名模式。"""
        msg = self._make_unbind_msg()
        await unbound_handler._cmd_unbind(msg)
        text = unbound_handler.adapter.send_text.call_args[0][1]
        assert "匿名" in text
        assert "独立工作区" in text
        assert "/bind" in text

    @pytest.mark.asyncio
    async def test_unbind_message_require_bind(self, unbound_handler):
        """_require_bind=True 时提示需要重新绑定。"""
        import os
        with MagicMock() as _:
            os.environ["EXCELMANUS_CHANNEL_REQUIRE_BIND"] = "true"
            try:
                msg = self._make_unbind_msg()
                await unbound_handler._cmd_unbind(msg)
                text = unbound_handler.adapter.send_text.call_args[0][1]
                assert "要求绑定" in text
                assert "/bind" in text
                assert "匿名" not in text
            finally:
                os.environ.pop("EXCELMANUS_CHANNEL_REQUIRE_BIND", None)

    @pytest.mark.asyncio
    async def test_unbind_invalidates_cache(self, unbound_handler):
        """解绑后 auth 缓存被清除。"""
        # 预填充缓存
        import time
        cache_key = f"telegram:user1"
        unbound_handler._auth_user_cache[cache_key] = ("auth-user-123", time.monotonic())
        msg = self._make_unbind_msg()
        await unbound_handler._cmd_unbind(msg)
        assert cache_key not in unbound_handler._auth_user_cache


# ── TestOutputManagerErrorRewrite ──


class TestOutputManagerErrorRewrite:
    """ChunkedOutputManager.finalize 中的错误文本改写和 error_is_reply 标记。"""

    def test_rewrite_error_for_bot(self):
        result = ChunkedOutputManager._rewrite_error_for_bot(
            "会话已过期或被清理，请刷新页面开始新对话。"
        )
        assert "/new" in result
        assert "刷新页面" not in result

    def test_rewrite_model_settings(self):
        result = ChunkedOutputManager._rewrite_error_for_bot(
            "API Key 无效或已过期，请在模型设置中更新。"
        )
        assert "/addmodel" in result
        assert "模型设置" not in result

    def test_no_rewrite_when_no_match(self):
        original = "网络连接失败，请检查网络后重试。"
        result = ChunkedOutputManager._rewrite_error_for_bot(original)
        assert result == original

    @staticmethod
    def _make_batch_adapter():
        """创建使用 BatchSendStrategy 的 mock adapter。"""
        adapter = MagicMock()
        adapter.send_text = AsyncMock()
        adapter.send_markdown = AsyncMock()
        adapter.send_markdown_return_id = AsyncMock(return_value="")
        caps = MagicMock()
        caps.supports_card_update = False
        caps.supports_edit = False
        caps.max_edits_per_minute = 0
        caps.max_message_length = 4000
        caps.preferred_format = "plain"
        caps.supports_markdown_tables = True
        caps.supports_typing = False
        caps.passive_reply_window = 0
        adapter.capabilities = caps
        return adapter

    @pytest.mark.asyncio
    async def test_finalize_error_is_reply_true(self):
        """无文本输出时，error 作为 reply 发送，error_is_reply=True。"""
        adapter = self._make_batch_adapter()
        manager = ChunkedOutputManager(adapter, "chat1")
        manager._error = "会话不存在: 请刷新页面开始新对话。"
        manager._stop_heartbeat = lambda: None
        result = await manager.finalize()
        assert result["error_is_reply"] is True
        # 验证发送的文本包含改写后的内容
        strategy_text = result["reply"]
        assert "/new" in strategy_text
        assert "刷新页面" not in strategy_text

    @pytest.mark.asyncio
    async def test_finalize_error_is_reply_false_with_text(self):
        """已有文本输出时，error_is_reply=False。"""
        adapter = self._make_batch_adapter()
        manager = ChunkedOutputManager(adapter, "chat1")
        # 模拟已有文本输出
        manager._strategy._all_text_parts.append("正常输出内容")
        manager._error = "中途出错了"
        manager._stop_heartbeat = lambda: None
        result = await manager.finalize()
        assert result["error_is_reply"] is False


# ── TestSessionAutoRecovery ──


class TestSessionAutoRecovery:
    """_stream_chat_chunked: 会话过期时自动清除 + 重试。"""

    @pytest.fixture
    def handler(self):
        adapter = MagicMock()
        adapter.name = "telegram"
        adapter.send_text = AsyncMock()
        adapter.send_markdown = AsyncMock()
        caps = MagicMock()
        caps.supports_card_update = False
        caps.supports_edit = False
        caps.max_edits_per_minute = 0
        caps.max_message_length = 4000
        caps.preferred_format = "plain"
        caps.supports_markdown_tables = True
        caps.supports_typing = False
        caps.passive_reply_window = 0
        adapter.capabilities = caps
        api = MagicMock()
        session_store = MagicMock()
        session_store.get.return_value = "old-session-id"
        h = MessageHandler(adapter, api, session_store)
        h._resolve_on_behalf_of = MagicMock(return_value="anon:tg:user1")
        return h

    @pytest.mark.asyncio
    async def test_session_expired_auto_retries(self, handler):
        """session_not_found 时自动清除旧 session 并以 session_id=None 重试。"""
        call_count = 0

        async def mock_stream_events(msg, sid, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # 第一次：返回 session_not_found
                yield "failure_guidance", {
                    "title": "会话不存在",
                    "message": "会话已过期或被清理",
                    "retryable": False,
                }
                yield "done", {}
            else:
                # 第二次：正常返回
                yield "session_init", {"session_id": "new-session-id"}
                yield "text_delta", {"content": "你好！"}
                yield "done", {}

        handler.api.stream_chat_events = mock_stream_events
        result = await handler._stream_chat_chunked(
            "chat1", "user1", "你好", "old-session-id",
        )
        assert call_count == 2
        # 旧 session 应被清除
        handler.sessions.remove.assert_called_once_with("telegram", "chat1", "user1")
        # 用户应收到自动恢复提示
        texts = [c.args[1] for c in handler.adapter.send_text.call_args_list]
        assert any("自动创建新会话" in t for t in texts)
        # 新 session_id 应被保存
        handler.sessions.set.assert_called_with(
            "telegram", "chat1", "user1", "new-session-id",
        )

    @pytest.mark.asyncio
    async def test_no_retry_when_no_stale_session(self, handler):
        """session_id=None 时不触发自动恢复（无旧 session 可清除）。"""
        async def mock_stream_events(msg, sid, **kwargs):
            yield "failure_guidance", {
                "title": "会话不存在",
                "message": "会话已过期或被清理",
                "retryable": False,
            }
            yield "done", {}

        handler.api.stream_chat_events = mock_stream_events
        result = await handler._stream_chat_chunked(
            "chat1", "user1", "你好", None,
        )
        # 不应清除 session（没有旧的）
        handler.sessions.remove.assert_not_called()
        # error 应正常返回
        assert result.get("error")

    @pytest.mark.asyncio
    async def test_non_session_error_no_retry(self, handler):
        """非 session_not_found 错误不触发自动恢复。"""
        async def mock_stream_events(msg, sid, **kwargs):
            yield "failure_guidance", {
                "title": "模型认证失败",
                "message": "API Key 无效",
                "retryable": False,
            }
            yield "done", {}

        handler.api.stream_chat_events = mock_stream_events
        result = await handler._stream_chat_chunked(
            "chat1", "user1", "你好", "old-session-id",
        )
        # 不应清除 session
        handler.sessions.remove.assert_not_called()
        # error 应正常返回
        assert "认证失败" in result.get("error", "")
