"""WorkBuddy/CodeBuddy 订阅提供商单元测试。

覆盖：凭证校验（workbuddy-*.json / 扁平格式、realm 不匹配拒绝）、realm 识别、请求头、
登录轮询、刷新路由（CN/Global 双 provider 实例）、动态模型目录与回退、
旧版单一 provider 数据迁移。
"""

from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from excelmanus.auth.providers.base import AuthProfileRecord, BrowserPollCapable
from excelmanus.auth.providers.workbuddy import (
    WorkBuddyProvider,
    _LoginCtx,
    realm_from_access_token,
)


def _jwt(iss: str = "https://www.codebuddy.cn", **extra) -> str:
    payload = {"iss": iss, **extra}
    h = base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b"=").decode()
    p = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    return f"{h}.{p}.sig"


def _make_record(**over) -> AuthProfileRecord:
    kw = dict(
        id="p1",
        user_id="process",
        provider="workbuddy-cn",
        profile_name="default",
        credential_type="oauth",
        access_token=_jwt(),
        refresh_token="rt_1",
        expires_at=(datetime.now(tz=timezone.utc) + timedelta(hours=1)).isoformat(),
        account_id="u-100",
        plan_type="cn",
        extra_data=json.dumps({
            "uid": "u-100",
            "enterprise_id": "ent-9",
            "domain": "codebuddy.cn",
            "nickname": "测试号",
            "realm": "cn",
        }),
        is_active=True,
        created_at="2025-01-01T00:00:00+00:00",
        updated_at="2025-01-01T00:00:00+00:00",
    )
    kw.update(over)
    return AuthProfileRecord(**kw)


class _FakeResp:
    def __init__(self, status: int = 200, payload=None, text: str = "") -> None:
        self.status_code = status
        self._payload = payload if payload is not None else {}
        self.text = text

    def json(self):
        return self._payload


class _FakeClient:
    """按 (method, url 片段) 路由到预置响应的伪 httpx.AsyncClient。"""

    def __init__(self, responses: dict | None = None) -> None:
        self.responses = responses or {}
        self.requests: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def aclose(self):
        pass

    async def request(self, method, url, headers=None, json=None):
        self.requests.append({"method": method, "url": url, "headers": headers, "json": json})
        for (m, frag), resp in self.responses.items():
            if m == method and frag in url:
                if isinstance(resp, Exception):
                    raise resp
                return resp
        return _FakeResp(404, {}, "no route")


@pytest.fixture
def provider() -> WorkBuddyProvider:
    return WorkBuddyProvider("cn")


@pytest.fixture
def global_provider() -> WorkBuddyProvider:
    return WorkBuddyProvider("global")


# ── realm / 凭证校验 ─────────────────────────────────────────


def test_realm_from_access_token_cn():
    assert realm_from_access_token(_jwt("https://www.codebuddy.cn")) == "cn"
    assert realm_from_access_token(_jwt("https://copilot.tencent.com")) == "cn"


def test_realm_from_access_token_global():
    assert realm_from_access_token(_jwt("https://www.workbuddy.ai")) == "global"


def test_realm_from_access_token_invalid():
    assert realm_from_access_token("not-a-jwt") is None


def test_get_api_credential_realm(provider, global_provider):
    cn_key, cn_url = provider.get_api_credential(_jwt("https://www.codebuddy.cn"))
    assert cn_url == "https://copilot.tencent.com/v2"
    g_key, g_url = global_provider.get_api_credential(_jwt("https://www.workbuddy.ai"))
    assert g_url == "https://www.workbuddy.ai/v2"


def test_provider_identity_split(provider, global_provider):
    assert provider.provider_name == "workbuddy-cn"
    assert provider.MODEL_NAME_PREFIX == "workbuddy-cn/"
    assert global_provider.provider_name == "workbuddy-global"
    assert global_provider.MODEL_NAME_PREFIX == "workbuddy-global/"


def test_matches_model(provider, global_provider):
    assert provider.matches_model("workbuddy-cn/glm-5")
    assert provider.matches_model("workbuddy-cn/auto")
    assert not provider.matches_model("glm-5")
    assert not provider.matches_model("openai-codex/gpt-6-astra")
    assert not provider.matches_model("workbuddy-cn/")
    # 前缀按 realm 隔离，不互相接管
    assert not provider.matches_model("workbuddy-global/glm-5")
    assert global_provider.matches_model("workbuddy-global/glm-5")
    assert not global_provider.matches_model("workbuddy-cn/glm-5")


def test_validate_token_data_cpa_file(provider):
    token = _jwt()
    raw = {
        "auth": {
            "accessToken": token,
            "refreshToken": "rt_cpa",
            "expiresAt": 1893456000000,  # ms epoch
            "domain": "codebuddy.cn",
        },
        "account": {"uid": "u-7", "enterpriseId": "e-7", "nickname": "nick"},
    }
    cred = provider.validate_token_data(raw)
    assert cred.access_token == token
    assert cred.refresh_token == "rt_cpa"
    assert cred.account_id == "u-7"
    assert cred.plan_type == "cn"
    extra = cred.extra_data or {}
    assert extra["enterprise_id"] == "e-7"
    assert extra["realm"] == "cn"


def test_validate_token_data_flat(global_provider):
    token = _jwt("https://www.workbuddy.ai")
    cred = global_provider.validate_token_data({
        "accessToken": token,
        "refreshToken": "rt_flat",
        "expiresIn": 7200,
        "domain": "workbuddy.ai",
    })
    assert cred.access_token == token
    assert cred.plan_type == "global"


def test_validate_token_data_realm_mismatch(provider, global_provider):
    # Global token 粘贴到 CN 卡片 → 拒绝并提示对应卡片
    with pytest.raises(ValueError, match="Global"):
        provider.validate_token_data({
            "accessToken": _jwt("https://www.workbuddy.ai"),
        })
    # CN token 粘贴到 Global 卡片 → 拒绝
    with pytest.raises(ValueError, match="国内版"):
        global_provider.validate_token_data({
            "accessToken": _jwt("https://www.codebuddy.cn"),
        })
    # realm 无法识别时按卡片 realm 接受
    cred = provider.validate_token_data({"accessToken": "opaque-token"})
    assert cred.plan_type == "cn"


def test_validate_token_data_missing(provider):
    with pytest.raises(ValueError):
        provider.validate_token_data({"foo": "bar"})


def test_get_request_headers(provider):
    record = _make_record()
    headers = provider.get_request_headers(record)
    assert headers["X-User-Id"] == "u-100"
    assert headers["X-Enterprise-Id"] == "ent-9"
    assert headers["X-Domain"] == "codebuddy.cn"
    assert headers["X-Refresh-Token"] == "rt_1"
    assert headers["X-Product"] == "SaaS"
    assert headers["Origin"] == "https://www.codebuddy.cn"
    assert headers["User-Agent"].startswith("CLI/")


def test_get_request_headers_minimal(provider):
    record = _make_record(extra_data="{}", account_id="")
    headers = provider.get_request_headers(record)
    assert headers["X-No-User-Id"] == "1"
    assert headers["X-No-Enterprise-Id"] == "1"
    assert headers["X-No-Department-Info"] == "1"


# ── 浏览器登录轮询 ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_browser_login_capability(provider):
    assert isinstance(provider, BrowserPollCapable)


@pytest.mark.asyncio
async def test_start_browser_login(provider):
    fake = _FakeClient({
        ("POST", "/v2/plugin/auth/state"): _FakeResp(200, {
            "code": 0,
            "data": {"state": "s-abc", "authUrl": "https://www.codebuddy.cn/login?s=abc"},
        }),
    })
    with patch("httpx.AsyncClient", lambda **kw: fake):
        info = await provider.start_browser_login()
    assert info["state"] == "s-abc"
    assert info["auth_url"].startswith("https://")
    assert "s-abc" in provider._logins
    assert fake.requests[0]["url"].startswith("https://copilot.tencent.com/")


@pytest.mark.asyncio
async def test_start_browser_login_global(global_provider):
    fake = _FakeClient({
        ("POST", "/v2/plugin/auth/state"): _FakeResp(200, {
            "code": 0,
            "data": {"state": "s-g", "authUrl": "https://www.workbuddy.ai/login?s=g"},
        }),
    })
    with patch("httpx.AsyncClient", lambda **kw: fake):
        info = await global_provider.start_browser_login()
    assert info["state"] == "s-g"
    assert fake.requests[0]["url"].startswith("https://www.workbuddy.ai/")


@pytest.mark.asyncio
async def test_poll_pending_then_connected(provider):
    token = _jwt()
    fake = _FakeClient({
        ("GET", "/v2/plugin/auth/token"): _FakeResp(200, {
            "code": 0,
            "data": {
                "accessToken": token,
                "refreshToken": "rt_x",
                "expiresIn": 3600,
                "domain": "codebuddy.cn",
            },
        }),
        ("GET", "/v2/plugin/login/account"): _FakeResp(200, {
            "code": 0,
            "data": {"uid": "u-9", "enterpriseId": "e-9", "nickname": "n"},
        }),
    })
    provider._logins["s1"] = _LoginCtx(fake, 600)
    cred = await provider.poll_browser_login("s1")
    assert cred is not None
    assert cred.access_token == token
    assert cred.account_id == "u-9"
    assert cred.plan_type == "cn"
    extra = cred.extra_data or {}
    assert extra["enterprise_id"] == "e-9"
    assert "s1" not in provider._logins  # 登录成功清理 state


@pytest.mark.asyncio
async def test_poll_pending_returns_none(provider):
    fake = _FakeClient({
        ("GET", "/v2/plugin/auth/token"): _FakeResp(200, {"code": 11217, "msg": "login ing"}),
    })
    provider._logins["s2"] = _LoginCtx(fake, 600)
    assert await provider.poll_browser_login("s2") is None
    assert "s2" in provider._logins  # pending 不清理


@pytest.mark.asyncio
async def test_poll_unknown_state(provider):
    with pytest.raises(RuntimeError, match="不存在或已过期"):
        await provider.poll_browser_login("ghost")


@pytest.mark.asyncio
async def test_poll_expired_state(provider):
    provider._logins["s3"] = _LoginCtx(_FakeClient(), -1)
    with pytest.raises(RuntimeError, match="超时"):
        await provider.poll_browser_login("s3")
    assert "s3" not in provider._logins


# ── 刷新 ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_refresh_profile_cn(provider):
    fake = _FakeClient({
        ("POST", "/v2/plugin/auth/token/refresh"): _FakeResp(200, {
            "code": 0,
            "data": {"accessToken": "new_at", "refreshToken": "new_rt", "expiresIn": 7200},
        }),
    })
    record = _make_record()
    with patch("httpx.AsyncClient", lambda **kw: fake):
        refreshed = await provider.refresh_profile(record)
    assert refreshed.access_token == "new_at"
    assert refreshed.refresh_token == "new_rt"
    req = fake.requests[0]
    assert req["url"].startswith("https://copilot.tencent.com/")
    assert req["headers"]["X-Refresh-Token"] == "rt_1"
    assert req["headers"]["X-Enterprise-Id"] == "ent-9"


@pytest.mark.asyncio
async def test_refresh_profile_global(global_provider):
    fake = _FakeClient({
        ("POST", "/v2/plugin/auth/token/refresh"): _FakeResp(200, {
            "code": 0,
            "data": {"accessToken": "ga", "expiresIn": 100},
        }),
    })
    record = _make_record(
        provider="workbuddy-global",
        access_token=_jwt("https://www.workbuddy.ai"),
        extra_data=json.dumps({"realm": "global", "uid": "u-1"}),
    )
    with patch("httpx.AsyncClient", lambda **kw: fake):
        refreshed = await global_provider.refresh_profile(record)
    assert refreshed.access_token == "ga"
    assert fake.requests[0]["url"].startswith("https://www.workbuddy.ai/")


@pytest.mark.asyncio
async def test_refresh_no_token(provider):
    record = _make_record(refresh_token=None)
    with pytest.raises(RuntimeError):
        await provider.refresh_profile(record)


# ── 动态模型目录 ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_discover_models_v3(provider):
    fake = _FakeClient({
        ("GET", "/v3/config"): _FakeResp(200, {
            "code": 0,
            "data": {"agents": [
                {"name": "ide", "models": ["x"]},
                {"name": "cli", "models": ["auto", "glm-5", "kimi-k2"]},
            ]},
        }),
    })
    with patch("httpx.AsyncClient", lambda **kw: fake):
        models = await provider.discover_models(_make_record())
    ids = [m["model"] for m in models]
    assert ids == ["auto", "glm-5", "kimi-k2"]


@pytest.mark.asyncio
async def test_discover_models_legacy_fallback(provider):
    fake = _FakeClient({
        ("GET", "/v3/config"): _FakeResp(404, {}, "not found"),
        ("GET", "/console/enterprises/personal/models"): _FakeResp(200, {
            "code": 0,
            "data": {"models": [
                {"id": "glm-5", "name": "GLM-5", "contextWindow": 200000, "maxTokens": 8192},
                {"id": "old", "disabled": True},
            ]},
        }),
    })
    with patch("httpx.AsyncClient", lambda **kw: fake):
        models = await provider.discover_models(_make_record())
    assert len(models) == 1
    assert models[0]["model"] == "glm-5"
    assert models[0]["context_length"] == 200000


@pytest.mark.asyncio
async def test_subscription_profiles_on_connect(provider):
    provider.discover_models = AsyncMock(return_value=[
        {"model": "auto", "display_name": "Auto"},
        {"model": "glm-5", "display_name": "GLM-5"},
    ])
    entries = await provider.subscription_profiles_on_connect(_make_record(), [])
    assert len(entries) == 1
    assert entries[0]["name"] == "workbuddy-cn/auto"
    assert entries[0]["model"] == "workbuddy-cn/auto"
    assert entries[0]["base_url"] == "https://copilot.tencent.com/v2"
    headers = json.loads(entries[0]["custom_extra_headers"])
    assert headers["X-Product"] == "SaaS"

    # 已存在同名档案 → 不再自动建档
    existing = [{"name": "workbuddy-cn/auto", "model": "workbuddy-cn/auto"}]
    assert await provider.subscription_profiles_on_connect(_make_record(), existing) == []


@pytest.mark.asyncio
async def test_list_model_entries(provider):
    provider.discover_models = AsyncMock(return_value=[
        {"model": "glm-5", "display_name": "GLM-5"},
    ])
    entries = await provider.list_model_entries(_make_record())
    assert entries[0]["profile_name"] == "workbuddy-cn/glm-5"
    assert entries[0]["public_model_id"] == "workbuddy-cn/glm-5"


# ── WorkBuddyClient 方言适配 ─────────────────────────────────


@pytest.mark.asyncio
async def test_client_tool_choice_and_stream_fold(provider):
    from excelmanus.providers.workbuddy import WorkBuddyClient

    client = WorkBuddyClient(api_key="k", base_url="https://copilot.tencent.com/v2")

    captured: dict = {}

    class _Delta:
        role = "assistant"
        content = "你好"
        reasoning_content = "嗯"
        tool_calls = None

    class _Choice:
        index = 0
        delta = _Delta()
        finish_reason = "stop"

    class _Chunk:
        id = "c1"
        created = 1
        model = "glm-5"
        choices = [_Choice()]
        usage = None

    async def _fake_create(**kwargs):
        captured.update(kwargs)

        async def _gen():
            yield _Chunk()

        return _gen()

    client._inner = MagicMock()
    client._inner.chat.completions.create = AsyncMock(side_effect=_fake_create)

    resp = await client.chat.completions.create(
        model="glm-5",
        messages=[{"role": "user", "content": "hi"}],
        tool_choice="none",
        tools=[{"type": "function", "function": {"name": "f"}}],
        stream=False,
        stream_options={"include_usage": True},
    )
    # 上游始终收到 stream=True；stream_options 被剔除；none → tools 一并移除
    assert captured["stream"] is True
    assert "stream_options" not in captured
    assert "tool_choice" not in captured
    assert "tools" not in captured
    # 本地聚合为 ChatCompletion
    assert resp.choices[0].message.content == "你好"
    assert getattr(resp.choices[0].message, "reasoning_content", None) == "嗯"


@pytest.mark.asyncio
async def test_client_global_injects_system(provider):
    from excelmanus.providers.workbuddy import WorkBuddyClient

    client = WorkBuddyClient(api_key="k", base_url="https://www.workbuddy.ai/v2")

    captured: dict = {}

    async def _fake_create(**kwargs):
        captured.update(kwargs)

        async def _gen():
            yield MagicMock()

        return _gen()

    client._inner = MagicMock()
    client._inner.chat.completions.create = AsyncMock(side_effect=_fake_create)

    await client.chat.completions.create(
        model="auto",
        messages=[{"role": "user", "content": "hi"}],
        stream=True,
    )
    assert captured["messages"][0]["role"] == "system"


# ── 旧版单一 provider 迁移 ──────────────────────────────────


class _FakeCredStore:
    def __init__(self, record=None) -> None:
        self.record = record
        self.upserts: list[dict] = []
        self.deletes: list[tuple] = []

    def get_active_profile(self, provider, user_id=None):
        return self.record if provider == "workbuddy" else None

    def upsert_profile(self, user_id, provider, profile_name, credential):
        self.upserts.append({
            "user_id": user_id, "provider": provider,
            "profile_name": profile_name, "credential": credential,
        })

    def delete_profile(self, user_id, provider, profile_name="default"):
        self.deletes.append((user_id, provider, profile_name))
        return True


class _FakeConfigStore:
    def __init__(self, profiles) -> None:
        self._profiles = profiles
        self.updates: list[dict] = []

    def list_profiles(self):
        return list(self._profiles)

    def update_profile(self, name, **kw):
        self.updates.append({"name": name, **kw})
        return True


def test_migrate_legacy_global_credential_and_profiles():
    from excelmanus.auth.providers.workbuddy import migrate_legacy_workbuddy

    legacy = _make_record(
        provider="workbuddy",
        access_token=_jwt("https://www.workbuddy.ai"),
        extra_data=json.dumps({"realm": "global", "uid": "u-g"}),
    )
    cred_store = _FakeCredStore(legacy)
    cfg_store = _FakeConfigStore([
        {"name": "workbuddy/glm-5", "model": "workbuddy/glm-5"},
        {"name": "openai/gpt-6", "model": "gpt-6"},
    ])
    migrate_legacy_workbuddy(cred_store, cfg_store)

    assert cred_store.upserts[0]["provider"] == "workbuddy-global"
    assert cred_store.deletes == [(legacy.user_id, "workbuddy", "default")]
    assert cfg_store.updates == [{
        "name": "workbuddy/glm-5",
        "new_name": "workbuddy-global/glm-5",
        "model": "workbuddy-global/glm-5",
        "base_url": "https://www.workbuddy.ai/v2",
    }]


def test_migrate_legacy_defaults_cn_when_no_credential():
    from excelmanus.auth.providers.workbuddy import migrate_legacy_workbuddy

    cred_store = _FakeCredStore(None)
    cfg_store = _FakeConfigStore([{"name": "workbuddy/auto", "model": "workbuddy/auto"}])
    migrate_legacy_workbuddy(cred_store, cfg_store)

    assert cred_store.upserts == []
    assert cfg_store.updates[0]["new_name"] == "workbuddy-cn/auto"
    assert cfg_store.updates[0]["base_url"] == "https://copilot.tencent.com/v2"


def test_migrate_legacy_idempotent_when_clean():
    from excelmanus.auth.providers.workbuddy import migrate_legacy_workbuddy

    cred_store = _FakeCredStore(None)
    cfg_store = _FakeConfigStore([{"name": "workbuddy-cn/auto", "model": "workbuddy-cn/auto"}])
    migrate_legacy_workbuddy(cred_store, cfg_store)
    assert cred_store.upserts == [] and cfg_store.updates == []
