"""API 服务端点测试：覆盖 Property 12-15、18、20。

使用 httpx.AsyncClient + ASGITransport 测试 FastAPI 端点，
通过 mock AgentEngine.chat() 避免真实 LLM 调用。
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from excelmanus.config import ExcelManusConfig
from excelmanus.session import SessionManager
from excelmanus.skills import SkillRegistry

import excelmanus.api as api_module
from excelmanus.api import app


# ── 辅助函数 ──────────────────────────────────────────────


def _test_config(**overrides) -> ExcelManusConfig:
    """创建测试用配置。"""
    defaults = dict(
        api_key="test-key",
        base_url="https://test.example.com/v1",
        model="test-model",
        session_ttl_seconds=60,
        max_sessions=5,
    )
    defaults.update(overrides)
    return ExcelManusConfig(**defaults)


def _make_transport():
    """创建 ASGITransport，关闭 raise_app_exceptions 以测试异常处理器。"""
    return ASGITransport(app=app, raise_app_exceptions=False)


@contextmanager
def _setup_api_globals(config=None):
    """上下文管理器：注入 API 全局状态，退出时清理。"""
    if config is None:
        config = _test_config()
    registry = SkillRegistry()
    manager = SessionManager(
        max_sessions=config.max_sessions,
        ttl_seconds=config.session_ttl_seconds,
        config=config,
        registry=registry,
    )

    old_config = api_module._config
    old_registry = api_module._registry
    old_manager = api_module._session_manager

    api_module._config = config
    api_module._registry = registry
    api_module._session_manager = manager

    try:
        yield {"config": config, "registry": registry, "manager": manager}
    finally:
        api_module._config = old_config
        api_module._registry = old_registry
        api_module._session_manager = old_manager


# ── Fixtures ──────────────────────────────────────────────


@pytest.fixture
def setup_api_state():
    """初始化 API 全局状态，绕过 lifespan 直接注入测试依赖。"""
    with _setup_api_globals() as state:
        yield state


@pytest.fixture
def client(setup_api_state):
    """创建 httpx AsyncClient（不触发 lifespan）。"""
    transport = _make_transport()
    return AsyncClient(transport=transport, base_url="http://test")


# ── 单元测试：Property 12 - API Chat 响应格式 ────────────


class TestProperty12ChatResponseFormat:
    """Property 12：任意合法 chat 请求应返回 200，且响应包含非空 session_id/reply。

    **Validates: Requirements 5.2**
    """

    @pytest.mark.asyncio
    async def test_chat_returns_200_with_session_id_and_reply(
        self, client: AsyncClient
    ) -> None:
        """基本 chat 请求返回 200 和正确结构。"""
        with patch(
            "excelmanus.engine.AgentEngine.chat",
            new_callable=AsyncMock,
            return_value="测试回复",
        ):
            resp = await client.post(
                "/api/v1/chat", json={"message": "你好"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert "session_id" in data
        assert "reply" in data
        assert len(data["session_id"]) > 0
        assert data["reply"] == "测试回复"

    @pytest.mark.asyncio
    async def test_chat_with_explicit_session_id(
        self, client: AsyncClient
    ) -> None:
        """带 session_id 的 chat 请求也返回 200。"""
        with patch(
            "excelmanus.engine.AgentEngine.chat",
            new_callable=AsyncMock,
            return_value="回复内容",
        ):
            resp = await client.post(
                "/api/v1/chat",
                json={"message": "测试", "session_id": "my-session"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["session_id"] == "my-session"
        assert data["reply"] == "回复内容"

    @pytest.mark.asyncio
    async def test_empty_reply_is_normalized(self, client: AsyncClient) -> None:
        """引擎返回空白回复时，API 应返回非空占位文本。"""
        with patch(
            "excelmanus.engine.AgentEngine.chat",
            new_callable=AsyncMock,
            return_value="   ",
        ):
            resp = await client.post(
                "/api/v1/chat", json={"message": "测试"},
            )
        assert resp.status_code == 200
        assert resp.json()["reply"] == "未生成有效回复，请重试。"


class TestRequestValidation:
    """请求参数校验测试。"""

    @pytest.mark.asyncio
    async def test_empty_message_returns_422(self, client: AsyncClient) -> None:
        resp = await client.post("/api/v1/chat", json={"message": ""})
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_blank_message_returns_422(self, client: AsyncClient) -> None:
        resp = await client.post("/api/v1/chat", json={"message": "   "})
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_blank_session_id_returns_422(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/chat",
            json={"message": "你好", "session_id": "   "},
        )
        assert resp.status_code == 422


# ── 单元测试：Property 13 - API 会话复用 ─────────────────


class TestProperty13SessionReuse:
    """Property 13：同一 session_id 的连续请求应复用同一上下文。

    **Validates: Requirements 5.3**
    """

    @pytest.mark.asyncio
    async def test_same_session_id_reuses_engine(
        self, client: AsyncClient, setup_api_state: dict
    ) -> None:
        """同一 session_id 的两次请求复用同一 AgentEngine 实例。"""
        with patch(
            "excelmanus.engine.AgentEngine.chat",
            new_callable=AsyncMock, return_value="第一次回复",
        ):
            resp1 = await client.post(
                "/api/v1/chat", json={"message": "第一条"},
            )
        sid = resp1.json()["session_id"]

        with patch(
            "excelmanus.engine.AgentEngine.chat",
            new_callable=AsyncMock, return_value="第二次回复",
        ):
            resp2 = await client.post(
                "/api/v1/chat",
                json={"message": "第二条", "session_id": sid},
            )
        assert resp2.status_code == 200
        assert resp2.json()["session_id"] == sid
        manager: SessionManager = setup_api_state["manager"]
        assert manager.active_count == 1

    @pytest.mark.asyncio
    async def test_same_session_concurrent_request_returns_409(
        self, client: AsyncClient
    ) -> None:
        """同一 session_id 并发请求时，第二个请求应返回 409。"""
        gate = asyncio.Event()

        async def slow_reply(_: str) -> str:
            await gate.wait()
            return "慢速回复"

        with patch(
            "excelmanus.engine.AgentEngine.chat",
            new_callable=AsyncMock,
            side_effect=slow_reply,
        ):
            first = asyncio.create_task(
                client.post(
                    "/api/v1/chat",
                    json={"message": "第一条", "session_id": "busy-sid"},
                )
            )
            await asyncio.sleep(0.02)
            second = await client.post(
                "/api/v1/chat",
                json={"message": "第二条", "session_id": "busy-sid"},
            )
            gate.set()
            first_resp = await first

        assert first_resp.status_code == 200
        assert second.status_code == 409
        assert "error" in second.json()


# ── 单元测试：Property 14 - API 会话删除 ─────────────────


class TestProperty14SessionDeletion:
    """Property 14：删除会话后，同 ID 后续请求必须创建新会话。

    **Validates: Requirements 5.4**
    """

    @pytest.mark.asyncio
    async def test_delete_then_recreate(
        self, client: AsyncClient, setup_api_state: dict
    ) -> None:
        """删除会话后，同 ID 的请求创建新会话（新 engine 实例）。"""
        manager: SessionManager = setup_api_state["manager"]

        with patch(
            "excelmanus.engine.AgentEngine.chat",
            new_callable=AsyncMock, return_value="初始回复",
        ):
            resp1 = await client.post(
                "/api/v1/chat", json={"message": "创建会话"},
            )
        sid = resp1.json()["session_id"]
        _, engine_before = await manager.get_or_create(sid)

        del_resp = await client.delete(f"/api/v1/sessions/{sid}")
        assert del_resp.status_code == 200
        assert manager.active_count == 0

        with patch(
            "excelmanus.engine.AgentEngine.chat",
            new_callable=AsyncMock, return_value="新会话回复",
        ):
            resp2 = await client.post(
                "/api/v1/chat",
                json={"message": "新请求", "session_id": sid},
            )
        assert resp2.status_code == 200
        assert resp2.json()["session_id"] == sid
        _, engine_after = await manager.get_or_create(sid)
        assert engine_before is not engine_after

    @pytest.mark.asyncio
    async def test_delete_nonexistent_returns_404(
        self, client: AsyncClient
    ) -> None:
        """删除不存在的会话返回 404。"""
        resp = await client.delete("/api/v1/sessions/nonexistent-id")
        assert resp.status_code == 404


# ── 单元测试：Property 15 - API 异常不泄露 ───────────────


class TestProperty15ErrorNoLeak:
    """Property 15：500 响应必须包含 error_id 且不得泄露 traceback 或内部路径。

    **Validates: Requirements 5.6**
    """

    @pytest.mark.asyncio
    async def test_500_contains_error_id_no_traceback(
        self, client: AsyncClient
    ) -> None:
        """引擎抛出未预期异常时，返回 500 + error_id，无堆栈泄露。"""
        with patch(
            "excelmanus.engine.AgentEngine.chat",
            new_callable=AsyncMock,
            side_effect=RuntimeError("内部数据库连接失败 /home/user/secret"),
        ):
            resp = await client.post(
                "/api/v1/chat", json={"message": "触发异常"},
            )
        assert resp.status_code == 500
        data = resp.json()
        assert "error_id" in data
        assert len(data["error_id"]) > 0
        body_str = resp.text
        assert "Traceback" not in body_str
        assert "/home/user/secret" not in body_str
        assert "数据库连接" not in body_str

    @pytest.mark.asyncio
    async def test_session_limit_returns_429(
        self, client: AsyncClient, setup_api_state: dict
    ) -> None:
        """会话数达到上限时返回 429。"""
        for i in range(5):
            with patch(
                "excelmanus.engine.AgentEngine.chat",
                new_callable=AsyncMock, return_value=f"回复{i}",
            ):
                await client.post(
                    "/api/v1/chat", json={"message": f"消息{i}"},
                )

        with patch(
            "excelmanus.engine.AgentEngine.chat",
            new_callable=AsyncMock, return_value="不应到达",
        ):
            resp = await client.post(
                "/api/v1/chat", json={"message": "超限"},
            )
        assert resp.status_code == 429
        assert "error" in resp.json()


# ── 单元测试：Health 端点 ────────────────────────────────


class TestHealthEndpoint:
    """GET /api/v1/health 端点测试。"""

    @pytest.mark.asyncio
    async def test_health_returns_status_and_version(
        self, client: AsyncClient
    ) -> None:
        """健康检查返回 status、version 和 skills。"""
        resp = await client.get("/api/v1/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "version" in data
        assert "skills" in data
        assert isinstance(data["skills"], list)


class TestCleanupIntervalStrategy:
    """TTL 清理间隔策略测试。"""

    def test_cleanup_interval_scales_with_ttl(self) -> None:
        assert api_module._cleanup_interval_from_ttl(1) == 1
        assert api_module._cleanup_interval_from_ttl(2) == 1
        assert api_module._cleanup_interval_from_ttl(10) == 5
        assert api_module._cleanup_interval_from_ttl(120) == 60


# ── 单元测试：Property 18 - 会话 TTL 清理（API 层） ──────


class TestProperty18TTLCleanupAPI:
    """Property 18：超过 session_ttl_seconds 的空闲会话必须被清理。

    **Validates: Requirements 5.8, 5.10, 6.7**
    """

    @pytest.mark.asyncio
    async def test_expired_session_cleaned_via_manager(
        self, client: AsyncClient, setup_api_state: dict
    ) -> None:
        """通过 API 创建的会话在 TTL 过期后被清理。"""
        manager: SessionManager = setup_api_state["manager"]
        with patch(
            "excelmanus.engine.AgentEngine.chat",
            new_callable=AsyncMock, return_value="回复",
        ):
            resp = await client.post(
                "/api/v1/chat", json={"message": "创建"},
            )
        sid = resp.json()["session_id"]
        assert manager.active_count == 1

        manager._sessions[sid].last_access = 0.0
        removed = await manager.cleanup_expired(now=61.0)
        assert removed == 1
        assert manager.active_count == 0


# ── 单元测试：Property 20 - 异步不阻塞（API 层） ────────


class TestProperty20AsyncNonBlockingAPI:
    """Property 20：并发请求场景下，阻塞工具执行不得阻塞主事件循环。

    **Validates: Requirements 1.10, 5.7**
    """

    @pytest.mark.asyncio
    async def test_concurrent_chat_requests_do_not_block(
        self, client: AsyncClient
    ) -> None:
        """多个并发 chat 请求应出现重叠执行，不互相串行阻塞。"""
        delay_per_request = 0.08
        active_calls = 0
        max_active = 0

        async def delayed_reply(msg: str) -> str:
            nonlocal active_calls, max_active
            active_calls += 1
            if active_calls > max_active:
                max_active = active_calls
            await asyncio.sleep(delay_per_request)
            active_calls -= 1
            return f"回复: {msg}"

        mock = AsyncMock(side_effect=delayed_reply)
        with patch("excelmanus.engine.AgentEngine.chat", mock):
            tasks = [
                client.post("/api/v1/chat", json={"message": f"并发{i}"})
                for i in range(3)
            ]
            responses = await asyncio.gather(*tasks)

        for resp in responses:
            assert resp.status_code == 200
            assert resp.json()["reply"].startswith("回复:")
        assert mock.call_count == 3
        assert max_active >= 2



# ── 属性测试（Property-Based Tests）────────────────────────

from hypothesis import given, settings, strategies as st

# 生成合法的消息文本（去除空白后仍非空）
message_st = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P")),
    min_size=1,
    max_size=200,
)

# 生成合法的 session_id
session_id_st = st.from_regex(r"[a-z0-9\-]{4,36}", fullmatch=True)


# ---------------------------------------------------------------------------
# Property 12：API Chat 响应格式（属性测试）
# **Validates: Requirements 5.2**
# ---------------------------------------------------------------------------


class TestPBTProperty12ChatResponseFormat:
    """Property 12：任意合法 chat 请求应返回 200，且响应包含非空 session_id/reply。

    **Validates: Requirements 5.2**
    """

    @settings(max_examples=100, deadline=None)
    @given(message=message_st)
    @pytest.mark.asyncio
    async def test_any_valid_message_returns_200_with_fields(
        self, message: str
    ) -> None:
        """任意合法消息都应返回 200 + 非空 session_id + 非空 reply。"""
        with _setup_api_globals():
            transport = _make_transport()
            async with AsyncClient(transport=transport, base_url="http://test") as c:
                with patch(
                    "excelmanus.engine.AgentEngine.chat",
                    new_callable=AsyncMock,
                    return_value=f"回复: {message}",
                ):
                    resp = await c.post(
                        "/api/v1/chat", json={"message": message},
                    )

            # 不变量 1：状态码 200
            assert resp.status_code == 200
            data = resp.json()
            # 不变量 2：session_id 非空
            assert isinstance(data["session_id"], str)
            assert len(data["session_id"]) > 0
            # 不变量 3：reply 非空
            assert isinstance(data["reply"], str)
            assert len(data["reply"]) > 0


# ---------------------------------------------------------------------------
# Property 13：API 会话复用（属性测试）
# **Validates: Requirements 5.3**
# ---------------------------------------------------------------------------


class TestPBTProperty13SessionReuse:
    """Property 13：同一 session_id 的连续请求应复用同一上下文。

    **Validates: Requirements 5.3**
    """

    @settings(max_examples=100, deadline=None)
    @given(
        n_requests=st.integers(min_value=2, max_value=5),
        messages=st.lists(message_st, min_size=5, max_size=5),
    )
    @pytest.mark.asyncio
    async def test_same_session_reused_across_requests(
        self, n_requests: int, messages: list[str]
    ) -> None:
        """同一 session_id 的 N 次请求始终复用同一会话。"""
        with _setup_api_globals() as state:
            manager: SessionManager = state["manager"]
            transport = _make_transport()
            async with AsyncClient(transport=transport, base_url="http://test") as c:
                # 第一次请求：创建会话
                with patch(
                    "excelmanus.engine.AgentEngine.chat",
                    new_callable=AsyncMock, return_value="首次回复",
                ):
                    resp1 = await c.post(
                        "/api/v1/chat", json={"message": messages[0]},
                    )
                sid = resp1.json()["session_id"]

                # 后续请求：复用同一 session_id
                for i in range(1, n_requests):
                    with patch(
                        "excelmanus.engine.AgentEngine.chat",
                        new_callable=AsyncMock, return_value=f"回复{i}",
                    ):
                        resp = await c.post(
                            "/api/v1/chat",
                            json={"message": messages[i], "session_id": sid},
                        )
                    # 不变量 1：每次都返回相同的 session_id
                    assert resp.json()["session_id"] == sid

            # 不变量 2：SessionManager 中只有一个会话
            assert manager.active_count == 1


# ---------------------------------------------------------------------------
# Property 14：API 会话删除（属性测试）
# **Validates: Requirements 5.4**
# ---------------------------------------------------------------------------


class TestPBTProperty14SessionDeletion:
    """Property 14：删除会话后，同 ID 后续请求必须创建新会话。

    **Validates: Requirements 5.4**
    """

    @settings(max_examples=100, deadline=None)
    @given(session_id=session_id_st)
    @pytest.mark.asyncio
    async def test_delete_then_new_session_created(
        self, session_id: str
    ) -> None:
        """删除会话后，同 ID 的请求创建全新会话。"""
        with _setup_api_globals() as state:
            manager: SessionManager = state["manager"]
            transport = _make_transport()
            async with AsyncClient(transport=transport, base_url="http://test") as c:
                # 创建会话
                with patch(
                    "excelmanus.engine.AgentEngine.chat",
                    new_callable=AsyncMock, return_value="初始",
                ):
                    resp1 = await c.post(
                        "/api/v1/chat",
                        json={"message": "创建", "session_id": session_id},
                    )
                assert resp1.status_code == 200

                # 记录原始 engine 的 id
                _, engine_before = await manager.get_or_create(session_id)
                engine_before_id = id(engine_before)

                # 删除会话
                del_resp = await c.delete(f"/api/v1/sessions/{session_id}")
                assert del_resp.status_code == 200
                # 不变量 1：删除后会话数为 0
                assert manager.active_count == 0

                # 用同一 ID 再次请求
                with patch(
                    "excelmanus.engine.AgentEngine.chat",
                    new_callable=AsyncMock, return_value="新会话",
                ):
                    resp2 = await c.post(
                        "/api/v1/chat",
                        json={"message": "重建", "session_id": session_id},
                    )
                # 不变量 2：返回相同的 session_id
                assert resp2.json()["session_id"] == session_id
                # 不变量 3：新 engine 实例与旧的不同
                _, engine_after = await manager.get_or_create(session_id)
                assert id(engine_after) != engine_before_id


# ---------------------------------------------------------------------------
# Property 15：API 异常不泄露（属性测试）
# **Validates: Requirements 5.6**
# ---------------------------------------------------------------------------

# 生成可能包含敏感信息的错误消息
sensitive_error_st = st.one_of(
    st.from_regex(r"/[a-z]+/[a-z]+/[a-z]+\.[a-z]+", fullmatch=True),
    st.text(
        alphabet=st.characters(whitelist_categories=("L", "N")),
        min_size=6,
        max_size=100,
    ),
)


class TestPBTProperty15ErrorNoLeak:
    """Property 15：500 响应必须包含 error_id 且不得泄露 traceback 或内部路径。

    **Validates: Requirements 5.6**
    """

    @settings(max_examples=100, deadline=None)
    @given(error_msg=sensitive_error_st)
    @pytest.mark.asyncio
    async def test_500_never_leaks_internal_details(
        self, error_msg: str
    ) -> None:
        """任意异常消息都不应在 500 响应中泄露。"""
        with _setup_api_globals():
            transport = _make_transport()
            async with AsyncClient(transport=transport, base_url="http://test") as c:
                with patch(
                    "excelmanus.engine.AgentEngine.chat",
                    new_callable=AsyncMock,
                    side_effect=RuntimeError(error_msg),
                ):
                    resp = await c.post(
                        "/api/v1/chat", json={"message": "触发异常"},
                    )

            # 不变量 1：状态码 500
            assert resp.status_code == 500
            data = resp.json()
            # 不变量 2：包含 error_id
            assert "error_id" in data
            assert isinstance(data["error_id"], str)
            assert len(data["error_id"]) > 0
            # 不变量 3：响应体不包含原始错误消息
            body_str = resp.text
            assert error_msg not in body_str
            # 不变量 4：不包含 Traceback 关键字
            assert "Traceback" not in body_str
            assert "File \"" not in body_str


# ---------------------------------------------------------------------------
# Property 18：会话 TTL 清理（API 层属性测试）
# **Validates: Requirements 5.8, 5.10, 6.7**
# ---------------------------------------------------------------------------


class TestPBTProperty18TTLCleanupAPI:
    """Property 18：超过 session_ttl_seconds 的空闲会话必须被清理。

    **Validates: Requirements 5.8, 5.10, 6.7**
    """

    @settings(max_examples=100, deadline=None)
    @given(
        ttl=st.integers(min_value=1, max_value=3600),
        idle_extra=st.integers(min_value=1, max_value=3600),
        n_sessions=st.integers(min_value=1, max_value=5),
    )
    @pytest.mark.asyncio
    async def test_api_sessions_cleaned_after_ttl(
        self, ttl: int, idle_extra: int, n_sessions: int
    ) -> None:
        """通过 SessionManager 创建的会话在 TTL 过期后必须被清理。"""
        config = _test_config(session_ttl_seconds=ttl, max_sessions=1000)
        registry = SkillRegistry()
        manager = SessionManager(
            max_sessions=1000,
            ttl_seconds=ttl,
            config=config,
            registry=registry,
        )

        for _ in range(n_sessions):
            await manager.get_or_create(None)

        base_time = 10000.0
        for entry in manager._sessions.values():
            entry.last_access = base_time

        now = base_time + ttl + idle_extra
        removed = await manager.cleanup_expired(now=now)

        # 不变量：所有会话都被清理
        assert removed == n_sessions
        assert manager.active_count == 0


# ---------------------------------------------------------------------------
# Property 20：异步不阻塞（API 层属性测试）
# **Validates: Requirements 1.10, 5.7**
# ---------------------------------------------------------------------------


class TestPBTProperty20AsyncNonBlockingAPI:
    """Property 20：并发请求场景下，阻塞工具执行不得阻塞主事件循环。

    **Validates: Requirements 1.10, 5.7**
    """

    @settings(max_examples=100, deadline=None)
    @given(n_concurrent=st.integers(min_value=2, max_value=4))
    @pytest.mark.asyncio
    async def test_concurrent_requests_complete_without_blocking(
        self, n_concurrent: int
    ) -> None:
        """N 个并发请求应出现重叠执行（max_active >= 2）。"""
        delay_per_request = 0.1
        active_calls = 0
        max_active = 0

        async def delayed_reply(msg: str) -> str:
            nonlocal active_calls, max_active
            active_calls += 1
            if active_calls > max_active:
                max_active = active_calls
            await asyncio.sleep(delay_per_request)
            active_calls -= 1
            return f"回复: {msg}"

        with _setup_api_globals():
            mock = AsyncMock(side_effect=delayed_reply)
            transport = _make_transport()
            async with AsyncClient(transport=transport, base_url="http://test") as c:
                with patch("excelmanus.engine.AgentEngine.chat", mock):
                    tasks = [
                        c.post(
                            "/api/v1/chat",
                            json={"message": f"并发请求{i}"},
                        )
                        for i in range(n_concurrent)
                    ]
                    responses = await asyncio.gather(*tasks)

            # 不变量 1：所有请求都成功
            for resp in responses:
                assert resp.status_code == 200
                assert resp.json()["reply"].startswith("回复:")

            # 不变量 2：mock 被调用了 n_concurrent 次
            assert mock.call_count == n_concurrent
            # 不变量 3：至少有 2 个请求发生重叠执行
            assert max_active >= 2
