"""Google Antigravity（Cloud Code Assist）订阅提供商与客户端单元测试。

覆盖：OAuth URL 构造、授权码交换（含 project 发现与 onboard 回退）、
token 刷新、粘贴凭证校验、请求头注入、registry 路由、
v1internal 信封构造、schema 清洗、SSE 解包。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import pytest

import excelmanus.auth.providers.antigravity as antigravity_mod
from excelmanus.auth.providers.antigravity import AntigravityProvider
from excelmanus.auth.providers.base import AuthProfileRecord, LoopbackOAuthCapable
from excelmanus.providers.antigravity import (
    AntigravityClient,
    PROJECT_ID_HEADER,
    build_antigravity_envelope,
    clean_schema_for_antigravity,
)


@pytest.fixture(autouse=True)
def _inject_test_oauth_client(monkeypatch):
    """测试注入占位凭据，不依赖真实 OAuth 客户端配置。"""
    monkeypatch.setattr(antigravity_mod, "_CLIENT_ID", "test-client-id")
    monkeypatch.setattr(antigravity_mod, "_CLIENT_SECRET", "test-client-secret")


def _make_record(**over) -> AuthProfileRecord:
    kw = dict(
        id="p1",
        user_id="process",
        provider="antigravity",
        profile_name="default",
        credential_type="oauth",
        access_token="ya29.access-token",
        refresh_token="rt-1",
        expires_at=(datetime.now(tz=timezone.utc) + timedelta(hours=1)).isoformat(),
        account_id="user@example.com",
        plan_type="",
        extra_data=json.dumps({
            "email": "user@example.com",
            "project_id": "proj-abc-123",
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


class _FakeOAuthClient:
    """按 (method, url 片段) 路由的伪 httpx.AsyncClient（OAuth 层）。"""

    def __init__(self, responses: dict | None = None) -> None:
        self.responses = responses or {}
        self.requests: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, data=None, json=None, headers=None, **kw):
        self.requests.append({
            "method": "POST", "url": url, "data": data,
            "json": json, "headers": headers,
        })
        for (m, frag), resp in self.responses.items():
            if m == "POST" and frag in url:
                if isinstance(resp, Exception):
                    raise resp
                return resp
        return _FakeResp(404, {}, "no route")

    async def get(self, url, headers=None, **kw):
        self.requests.append({"method": "GET", "url": url, "headers": headers})
        for (m, frag), resp in self.responses.items():
            if m == "GET" and frag in url:
                if isinstance(resp, Exception):
                    raise resp
                return resp
        return _FakeResp(404, {}, "no route")


@pytest.fixture
def provider() -> AntigravityProvider:
    return AntigravityProvider()


def _ok_exchange_responses(**over) -> dict:
    responses = {
        ("POST", "oauth2.googleapis.com/token"): _FakeResp(200, {
            "access_token": "ya29.new-token",
            "refresh_token": "rt-new",
            "expires_in": 3600,
        }),
        ("GET", "oauth2/v2/userinfo"): _FakeResp(200, {
            "email": "user@example.com",
        }),
        ("POST", "loadCodeAssist"): _FakeResp(200, {
            "cloudaicompanionProject": "proj-abc-123",
        }),
    }
    responses.update(over)
    return responses


# ── Loopback OAuth ───────────────────────────────────────────


def test_provider_is_loopback_capable(provider):
    assert isinstance(provider, LoopbackOAuthCapable)
    assert provider.callback_path == "/oauth-callback"
    assert provider.callback_port == 51121


def test_build_authorize_url(provider):
    url = provider.build_authorize_url(
        "state-xyz", "http://localhost:51121/oauth-callback",
    )
    parsed = urlparse(url)
    assert parsed.scheme == "https"
    assert parsed.netloc == "accounts.google.com"
    qs = parse_qs(parsed.query)
    assert qs["response_type"] == ["code"]
    assert qs["state"] == ["state-xyz"]
    assert qs["redirect_uri"] == ["http://localhost:51121/oauth-callback"]
    assert qs["access_type"] == ["offline"]
    assert qs["prompt"] == ["consent"]
    assert "client_id" in qs
    scope = qs["scope"][0]
    assert "cloud-platform" in scope
    assert "userinfo.email" in scope
    assert "cclog" in scope


@pytest.mark.asyncio
async def test_exchange_code_full_flow(provider):
    fake = _FakeOAuthClient(_ok_exchange_responses())
    with patch(
        "excelmanus.auth.providers.antigravity._http_client",
        lambda: fake,
    ):
        cred = await provider.exchange_code(
            code="authcode-1",
            redirect_uri="http://localhost:51121/oauth-callback",
        )
    assert cred.access_token == "ya29.new-token"
    assert cred.refresh_token == "rt-new"
    assert cred.account_id == "user@example.com"
    assert cred.extra_data["project_id"] == "proj-abc-123"
    assert cred.extra_data["email"] == "user@example.com"
    # token 交换为表单 POST
    token_req = fake.requests[0]
    assert token_req["method"] == "POST"
    assert token_req["data"]["grant_type"] == "authorization_code"
    assert token_req["data"]["code"] == "authcode-1"
    assert token_req["data"]["redirect_uri"].endswith("/oauth-callback")


@pytest.mark.asyncio
async def test_exchange_code_onboard_fallback(provider):
    """loadCodeAssist 无 project → onboardUser 轮询直至 done。"""
    responses = _ok_exchange_responses()
    responses[("POST", "loadCodeAssist")] = _FakeResp(200, {
        "allowedTiers": [{"id": "free-tier", "isDefault": True}],
    })
    responses[("POST", "onboardUser")] = _FakeResp(200, {
        "done": True,
        "response": {"cloudaicompanionProject": "proj-onboarded"},
    })
    fake = _FakeOAuthClient(responses)
    with patch(
        "excelmanus.auth.providers.antigravity._http_client", lambda: fake,
    ), patch(
        "excelmanus.auth.providers.antigravity.asyncio.sleep",
        _no_sleep,
    ):
        cred = await provider.exchange_code(
            code="c", redirect_uri="http://localhost:51121/oauth-callback",
        )
    assert cred.extra_data["project_id"] == "proj-onboarded"
    onboard_req = next(r for r in fake.requests if "onboardUser" in r["url"])
    assert onboard_req["json"]["tier_id"] == "free-tier"
    assert onboard_req["json"]["metadata"]["ide_type"] == "ANTIGRAVITY"


async def _no_sleep(_seconds):
    return None


@pytest.mark.asyncio
async def test_exchange_code_token_failure(provider):
    fake = _FakeOAuthClient({
        ("POST", "oauth2.googleapis.com/token"): _FakeResp(
            400, {"error": "invalid_grant"}, "invalid_grant",
        ),
    })
    with patch(
        "excelmanus.auth.providers.antigravity._http_client", lambda: fake,
    ), pytest.raises(RuntimeError, match="授权码交换失败"):
        await provider.exchange_code(
            code="bad", redirect_uri="http://localhost:51121/oauth-callback",
        )


@pytest.mark.asyncio
async def test_refresh_token(provider):
    fake = _FakeOAuthClient({
        ("POST", "oauth2.googleapis.com/token"): _FakeResp(200, {
            "access_token": "ya29.refreshed",
            "expires_in": 3600,
        }),
    })
    with patch(
        "excelmanus.auth.providers.antigravity._http_client", lambda: fake,
    ):
        refreshed = await provider.refresh_token("rt-1")
    assert refreshed.access_token == "ya29.refreshed"
    # 响应未携带新 refresh_token 时保留旧的
    assert refreshed.refresh_token == "rt-1"
    req = fake.requests[0]
    assert req["data"]["grant_type"] == "refresh_token"
    assert req["data"]["refresh_token"] == "rt-1"


@pytest.mark.asyncio
async def test_refresh_token_missing(provider):
    with pytest.raises(RuntimeError, match="refresh token"):
        await provider.refresh_token("")


# ── 凭证校验 / 请求头 / registry ──────────────────────────────


def test_validate_token_data_cpa_format(provider):
    cred = provider.validate_token_data({
        "access_token": "ya29.pasted",
        "refresh_token": "rt-pasted",
        "project_id": "proj-pasted",
        "email": "pasted@example.com",
        "expired": "2030-01-01T00:00:00Z",
    })
    assert cred.access_token == "ya29.pasted"
    assert cred.refresh_token == "rt-pasted"
    assert cred.account_id == "pasted@example.com"
    assert cred.extra_data["project_id"] == "proj-pasted"
    assert cred.expires_at.startswith("2030-01-01")


def test_validate_token_data_missing_token(provider):
    with pytest.raises(ValueError, match="access_token"):
        provider.validate_token_data({"refresh_token": "x"})


def test_get_api_credential(provider):
    key, base = provider.get_api_credential("ya29.x")
    assert key == "ya29.x"
    assert base == "https://daily-cloudcode-pa.googleapis.com"


def test_get_request_headers_injects_project(provider):
    headers = provider.get_request_headers(_make_record())
    assert headers["User-Agent"].startswith("antigravity/")
    assert headers[PROJECT_ID_HEADER] == "proj-abc-123"


def test_get_request_headers_without_project(provider):
    record = _make_record(extra_data=json.dumps({"email": "a@b.c"}))
    headers = provider.get_request_headers(record)
    assert "User-Agent" in headers
    assert PROJECT_ID_HEADER not in headers


def test_matches_model_only_via_prefix(provider):
    assert provider.matches_model("claude-sonnet-4-6") is False
    assert provider.matches_model("antigravity/claude-sonnet-4-6") is False


def test_registry_routing():
    from excelmanus.auth.providers.registry import (
        get_provider, managed_provider_for, match_provider,
        strip_managed_prefix,
    )
    prov = managed_provider_for("antigravity/claude-sonnet-4-6")
    assert prov is not None and prov.provider_name == "antigravity"
    assert match_provider("antigravity/gemini-3-flash") == "antigravity"
    assert strip_managed_prefix("antigravity/gemini-3-flash") == "gemini-3-flash"
    assert get_provider("antigravity") is not None
    # 不误伤其它 provider 前缀
    assert managed_provider_for("workbuddy-cn/x").provider_name == "workbuddy-cn"


def test_list_supported_model_entries(provider):
    entries = provider.list_supported_model_entries()
    assert entries
    for e in entries:
        assert e["profile_name"].startswith("antigravity/")
        assert e["public_model_id"] == e["profile_name"]
    ids = [e["model"] for e in entries]
    assert "claude-sonnet-4-6" in ids


@pytest.mark.asyncio
async def test_subscription_profiles_on_connect(provider):
    record = _make_record()
    suggested = await provider.subscription_profiles_on_connect(record, [])
    assert len(suggested) == 1
    assert suggested[0]["name"] == "antigravity/claude-sonnet-4-6"
    assert suggested[0]["protocol"] == "antigravity"
    # 已有同档案则不再建议
    dup = await provider.subscription_profiles_on_connect(
        record, [{"name": "antigravity/claude-sonnet-4-6"}],
    )
    assert dup == []


def test_profile_display_info(provider):
    info = provider.profile_display_info(_make_record())
    assert info["email"] == "user@example.com"


# ── v1internal 信封 ───────────────────────────────────────────


def test_envelope_basic():
    inner = {
        "contents": [{"role": "user", "parts": [{"text": "你好"}]}],
        "safetySettings": [{"category": "x"}],
    }
    env = build_antigravity_envelope(
        inner, model="gemini-3-flash", project_id="proj-1",
    )
    assert env["model"] == "gemini-3-flash"
    assert env["project"] == "proj-1"
    assert env["userAgent"] == "antigravity"
    assert env["requestType"] == "agent"
    assert env["requestId"].startswith("agent-")
    req = env["request"]
    assert "safetySettings" not in req
    assert req["sessionId"].startswith("-")
    assert req["contents"][0]["parts"][0]["text"] == "你好"


def test_envelope_stable_session_id():
    inner = {
        "contents": [{"role": "user", "parts": [{"text": "same"}]}],
    }
    a = build_antigravity_envelope(inner, model="gemini-3-flash", project_id="p")
    b = build_antigravity_envelope(
        dict(inner), model="gemini-3-flash", project_id="p",
    )
    assert a["request"]["sessionId"] == b["request"]["sessionId"]


def test_envelope_claude_validated():
    inner = {
        "contents": [{"role": "user", "parts": [{"text": "hi"}]}],
        "tools": [{"functionDeclarations": [{
            "name": "noop",
            "parameters": {"type": "object", "properties": {}},
        }]}],
        "generationConfig": {"maxOutputTokens": 8192},
    }
    env = build_antigravity_envelope(
        inner, model="claude-sonnet-4-6", project_id="p",
    )
    req = env["request"]
    assert req["toolConfig"]["functionCallingConfig"]["mode"] == "VALIDATED"
    # claude 保留 maxOutputTokens
    assert req["generationConfig"]["maxOutputTokens"] == 8192
    # VALIDATED 需要至少一个 required → 补占位 reason
    decl = req["tools"][0]["functionDeclarations"][0]
    assert decl["parameters"]["required"] == ["reason"]
    assert "reason" in decl["parameters"]["properties"]


def test_envelope_non_claude_drops_max_output_tokens():
    inner = {
        "contents": [{"role": "user", "parts": [{"text": "hi"}]}],
        "generationConfig": {"maxOutputTokens": 8192, "temperature": 0.5},
    }
    env = build_antigravity_envelope(
        inner, model="gemini-3-flash", project_id="p",
    )
    gen = env["request"]["generationConfig"]
    assert "maxOutputTokens" not in gen
    assert gen["temperature"] == 0.5


def test_envelope_image_model():
    inner = {"contents": [{"role": "user", "parts": [{"text": "draw"}]}]}
    env = build_antigravity_envelope(
        inner, model="gemini-3.1-flash-image", project_id="p",
    )
    assert env["requestType"] == "image_gen"
    assert env["requestId"].startswith("image_gen/")
    assert "sessionId" not in env["request"]


def test_envelope_empty_project_omitted():
    env = build_antigravity_envelope(
        {"contents": []}, model="gemini-3-flash", project_id="",
    )
    assert "project" not in env


# ── schema 清洗 ───────────────────────────────────────────────


def test_schema_clean_drops_enum_with_hint():
    out = clean_schema_for_antigravity({
        "type": "object",
        "properties": {
            "mode": {"type": "string", "enum": ["a", "b"], "default": "a"},
        },
    })
    prop = out["properties"]["mode"]
    assert "enum" not in prop
    assert "default" not in prop
    assert "Allowed: a, b" in prop["description"]
    assert "default: a" in prop["description"]


def test_schema_clean_inline_ref():
    out = clean_schema_for_antigravity({
        "type": "object",
        "properties": {
            "item": {"$ref": "#/definitions/Item"},
        },
        "definitions": {
            "Item": {"type": "string", "description": "条目"},
        },
    })
    prop = out["properties"]["item"]
    assert "$ref" not in prop
    assert prop["type"] == "string"
    assert prop["description"] == "条目"
    assert "definitions" not in out


def test_schema_clean_flattens_anyof():
    out = clean_schema_for_antigravity({
        "type": "object",
        "properties": {
            "v": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        },
    })
    prop = out["properties"]["v"]
    assert prop["type"] == "string"
    assert prop["nullable"] is True
    assert "anyOf" not in prop


def test_schema_clean_array_items_added():
    out = clean_schema_for_antigravity({
        "type": "object",
        "properties": {"tags": {"type": "array"}},
    })
    assert out["properties"]["tags"]["items"] == {"type": "string"}


def test_schema_clean_validated_placeholder():
    out = clean_schema_for_antigravity(
        {"type": "object", "properties": {}}, validated=True,
    )
    assert out["required"] == ["reason"]
    assert out["properties"]["reason"]["type"] == "string"


def test_schema_clean_validated_underscore_placeholder():
    """有 properties 但无 required → 补 `_` boolean 占位。"""
    out = clean_schema_for_antigravity(
        {
            "type": "object",
            "properties": {"x": {"type": "string"}},
        },
        validated=True,
    )
    assert out["required"] == ["_"]
    assert out["properties"]["_"]["type"] == "boolean"


def test_schema_clean_required_intersection():
    out = clean_schema_for_antigravity({
        "type": "object",
        "properties": {"a": {"type": "string"}},
        "required": ["a", "ghost"],
    })
    assert out["required"] == ["a"]


def test_schema_clean_does_not_touch_property_data():
    """properties 内的键名（如 title/default）是数据键，不应被删。"""
    out = clean_schema_for_antigravity({
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "default": {"type": "number"},
        },
    })
    assert "title" in out["properties"]
    assert "default" in out["properties"]


def test_schema_clean_response_mode_preserves():
    out = clean_schema_for_antigravity(
        {
            "type": "object",
            "properties": {"ok": {"type": "boolean", "enum": [True, False]}},
            "additionalProperties": False,
        },
        response_mode=True,
    )
    assert out["additionalProperties"] is False
    # 响应 schema 的布尔枚举被丢弃，string 枚举保留
    assert "enum" not in out["properties"]["ok"]


# ── 客户端 ────────────────────────────────────────────────────


class _FakeStreamResp:
    def __init__(self, lines: list[str], status: int = 200) -> None:
        self.status_code = status
        self._lines = lines

    async def aiter_lines(self):
        for line in self._lines:
            yield line

    async def aread(self):
        return b""


class _FakeStreamCtx:
    def __init__(self, resp: _FakeStreamResp) -> None:
        self._resp = resp

    async def __aenter__(self):
        return self._resp

    async def __aexit__(self, *a):
        return False


class _FakeHttp:
    def __init__(self, post_resp=None, stream_resp=None) -> None:
        self.post_resp = post_resp
        self.stream_resp = stream_resp
        self.posts: list[dict] = []
        self.streams: list[dict] = []

    async def post(self, url, json=None, headers=None, params=None):
        self.posts.append({
            "url": url, "json": json, "headers": headers, "params": params,
        })
        return self.post_resp

    def stream(self, method, url, json=None, headers=None, params=None):
        self.streams.append({
            "method": method, "url": url, "json": json,
            "headers": headers, "params": params,
        })
        return _FakeStreamCtx(self.stream_resp)


def _client(http: _FakeHttp) -> AntigravityClient:
    c = AntigravityClient(
        api_key="ya29.tok",
        base_url="https://daily-cloudcode-pa.googleapis.com",
        default_headers={
            "User-Agent": "antigravity/hub/2.9.1 darwin/arm64",
            PROJECT_ID_HEADER: "proj-xyz",
        },
    )
    c._http = http
    return c


@pytest.mark.asyncio
async def test_client_generate_envelope_and_unwrap():
    http = _FakeHttp(post_resp=_FakeResp(200, {
        "response": {
            "candidates": [{
                "content": {"role": "model", "parts": [{"text": "你好"}]},
                "finishReason": "STOP",
            }],
            "usageMetadata": {
                "promptTokenCount": 10,
                "candidatesTokenCount": 5,
                "totalTokenCount": 15,
            },
        },
    }))
    client = _client(http)
    resp = await client.chat.completions.create(
        model="claude-sonnet-4-6",
        messages=[{"role": "user", "content": "hi"}],
    )
    req = http.posts[0]
    assert req["url"].endswith("/v1internal:generateContent")
    assert req["headers"]["Authorization"] == "Bearer ya29.tok"
    assert req["headers"]["User-Agent"].startswith("antigravity/")
    # project 头被消费进信封，不上送
    assert PROJECT_ID_HEADER not in req["headers"]
    body = req["json"]
    assert body["project"] == "proj-xyz"
    assert body["model"] == "claude-sonnet-4-6"
    assert body["requestType"] == "agent"
    assert body["request"]["contents"][0]["parts"][0]["text"] == "hi"
    # 响应解包 → OpenAI 结构
    assert resp.choices[0].message.content == "你好"
    assert resp.choices[0].finish_reason == "stop"
    assert resp.usage.total_tokens == 15


@pytest.mark.asyncio
async def test_client_generate_extra_headers_override_project():
    http = _FakeHttp(post_resp=_FakeResp(200, {"response": {"candidates": []}}))
    client = _client(http)
    await client.chat.completions.create(
        model="gemini-3-flash",
        messages=[{"role": "user", "content": "hi"}],
        extra_headers={PROJECT_ID_HEADER: "proj-override"},
    )
    assert http.posts[0]["json"]["project"] == "proj-override"


@pytest.mark.asyncio
async def test_client_generate_error():
    http = _FakeHttp(post_resp=_FakeResp(500, {}, "boom"))
    client = _client(http)
    with pytest.raises(RuntimeError, match="HTTP 500"):
        await client.chat.completions.create(
            model="gemini-3-flash",
            messages=[{"role": "user", "content": "hi"}],
        )


@pytest.mark.asyncio
async def test_client_stream_sse_unwrap():
    lines = [
        'data: {"response": {"candidates": [{"content": {"parts": [{"text": "你"}]}}]}}',
        'data: {"response": {"candidates": [{"content": {"parts": [{"text": "好"}]}}]}}',
        'data: {"response": {"candidates": [{"finishReason": "STOP"}], "usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 2, "totalTokenCount": 5}}}',
    ]
    http = _FakeHttp(stream_resp=_FakeStreamResp(lines))
    client = _client(http)
    stream = await client.chat.completions.create(
        model="gemini-3-flash",
        messages=[{"role": "user", "content": "hi"}],
        stream=True,
    )
    deltas = [d async for d in stream]
    req = http.streams[0]
    assert "streamGenerateContent" in req["url"]
    assert req["params"] == {"alt": "sse"}
    text = "".join(d.content_delta or "" for d in deltas)
    assert text == "你好"
    last = deltas[-1]
    assert last.finish_reason == "stop"
    assert last.usage.total_tokens == 5


@pytest.mark.asyncio
async def test_client_stream_tool_call():
    lines = [
        'data: {"response": {"candidates": [{"content": {"parts": [{"functionCall": {"name": "read_cell", "args": {"cell": "A1"}}}]}, "finishReason": "STOP"}]}}',
    ]
    http = _FakeHttp(stream_resp=_FakeStreamResp(lines))
    client = _client(http)
    stream = await client.chat.completions.create(
        model="claude-sonnet-4-6",
        messages=[{"role": "user", "content": "读 A1"}],
        stream=True,
    )
    deltas = [d async for d in stream]
    tc = [t for d in deltas for t in (d.tool_calls_delta or [])]
    assert len(tc) == 1
    assert tc[0]["name"] == "read_cell"
    assert json.loads(tc[0]["arguments"]) == {"cell": "A1"}
    # claude 模型：VALIDATED mode 已注入信封
    req_body = http.streams[0]["json"]["request"]
    assert req_body["toolConfig"]["functionCallingConfig"]["mode"] == "VALIDATED"


def test_create_client_dispatch():
    from excelmanus.providers import create_client
    client = create_client(
        api_key="ya29.x",
        base_url="https://daily-cloudcode-pa.googleapis.com",
        protocol="antigravity",
    )
    assert isinstance(client, AntigravityClient)
    # base_url 不应被补 /v1
    assert client._base_url == "https://daily-cloudcode-pa.googleapis.com"


def test_compile_provider_body_antigravity():
    from excelmanus.providers.request_body import compile_provider_body
    body = compile_provider_body("antigravity", {
        "model": "claude-sonnet-4-6",
        "messages": [{"role": "user", "content": "hi"}],
    })
    assert "contents" in body
    assert body["contents"][0]["parts"][0]["text"] == "hi"
