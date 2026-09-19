"""Gateway 运输层：vck_ 分流、boolean.probability、providerMetadata。禁止打网。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from excelmanus.system_one.client import (
    GATEWAY_MODEL,
    GATEWAY_URL,
    SystemOneUnavailable,
    client_ready,
    detect_transport,
    resolve_jev_api_key,
    system_one,
)
from excelmanus.system_one.packs import get_pack
from excelmanus.system_one.types import ChoiceAnswer, NoulAnswer, ScoreAnswer


def _gateway_payload() -> dict:
    return {
        "answers": {
            "action": {
                "choice": "ask",
                "probabilities": {"allow": 0.47, "ask": 0.48, "deny": 0.05},
            },
            "destructive": {"probability": 0.19},
            "exfiltrating": {"probability": 0.02},
            "scope_ok": {"probability": 0.6},
            "blast_radius": {"score": 2.93},
        },
        "providerMetadata": {
            "typesafe": {"confidence": {"action": 0.22, "blast_radius": 0.93}},
        },
        "usage": {"inputTokens": 80, "outputTokens": 12},
    }


class _Resp:
    def __init__(self, status: int = 200, payload: dict | None = None) -> None:
        self.status_code = status
        self._payload = payload if payload is not None else _gateway_payload()

    def json(self) -> dict:
        return self._payload


class _Client:
    captured: dict

    def __init__(self, **kwargs) -> None:
        _Client.captured = {"timeout": kwargs.get("timeout")}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def post(self, url, headers=None, json=None):
        _Client.captured.update({"url": url, "headers": headers, "json": json})
        return _Resp()


@pytest.mark.asyncio
async def test_vck_on_typesafe_env_uses_gateway_not_chat_completions() -> None:
    key, name = resolve_jev_api_key(
        {"EXCELMANUS_TYPESAFE_API_KEY": "vck_test_not_real"}
    )
    assert name == "EXCELMANUS_TYPESAFE_API_KEY"
    assert detect_transport(key) == "gateway"
    spec = get_pack("approval.tool_call")
    with patch("httpx.AsyncClient", _Client):
        ev = await system_one(
            spec,
            {"user_text": "echo ok", "tool": {"name": "run_shell"}},
            model="jev-1.13.0",
            api_key="vck_test_not_real",
            timeout_seconds=1.5,
        )
    assert _Client.captured["url"] == GATEWAY_URL
    assert "/v1/chat/completions" not in _Client.captured["url"]
    assert _Client.captured["headers"]["ai-model-id"] == GATEWAY_MODEL
    assert "model" not in _Client.captured["json"]
    assert _Client.captured["json"]["questions"]["destructive"]["type"] == "boolean"
    assert isinstance(ev.answers["destructive"], NoulAnswer)
    assert ev.answers["destructive"].noul == pytest.approx(0.19)
    assert isinstance(ev.answers["action"], ChoiceAnswer)
    assert ev.answers["action"].choice == "ask"
    assert ev.answers["action"].confidence == pytest.approx(0.22)
    assert isinstance(ev.answers["blast_radius"], ScoreAnswer)
    assert ev.answers["blast_radius"].confidence == pytest.approx(0.93)
    assert ev.model == GATEWAY_MODEL


@pytest.mark.asyncio
async def test_gateway_http_errors_are_unavailable() -> None:
    spec = get_pack("approval.tool_call")

    class _ErrClient(_Client):
        async def post(self, url, headers=None, json=None):
            return _Resp(status=401)

    with patch("httpx.AsyncClient", _ErrClient):
        with pytest.raises(SystemOneUnavailable, match="gateway_http_401"):
            await system_one(
                spec, {"user_text": "x"}, model="jev-1.13.0",
                api_key="vck_test_not_real", timeout_seconds=1.5,
            )

    class _Unprocessable(_Client):
        async def post(self, url, headers=None, json=None):
            return _Resp(status=422)

    with patch("httpx.AsyncClient", _Unprocessable):
        with pytest.raises(SystemOneUnavailable, match="gateway_http_422"):
            await system_one(
                spec, {"user_text": "x"}, model="jev-1.13.0",
                api_key="vck_test_not_real", timeout_seconds=1.5,
            )

    class _Busy(_Client):
        async def post(self, url, headers=None, json=None):
            return _Resp(status=429)

    with patch("httpx.AsyncClient", _Busy):
        with pytest.raises(SystemOneUnavailable, match="gateway_http_429"):
            await system_one(
                spec, {"user_text": "x"}, model="jev-1.13.0",
                api_key="vck_test_not_real", timeout_seconds=1.5,
            )

    class _Overload(_Client):
        async def post(self, url, headers=None, json=None):
            return _Resp(status=529)

    with patch("httpx.AsyncClient", _Overload):
        with pytest.raises(SystemOneUnavailable, match="gateway_http_529"):
            await system_one(
                spec, {"user_text": "x"}, model="jev-1.13.0",
                api_key="vck_test_not_real", timeout_seconds=1.5,
            )


def test_client_ready_gateway_does_not_need_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("excelmanus.system_one.client._HAS_SDK", False)
    assert client_ready("vck_test_not_real") is True
    assert client_ready("ts_direct_key") is False
    assert client_ready(None) is False


def test_resolve_jev_api_key_reads_gateway_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    from excelmanus.settings_runtime import override_settings

    monkeypatch.setenv("AI_GATEWAY_API_KEY", "vck_from_process_env")
    override_settings({"EXCELMANUS_AI_GATEWAY_API_KEY": "vck_test_not_real"})
    key, name = resolve_jev_api_key()
    assert name == "EXCELMANUS_AI_GATEWAY_API_KEY"
    assert detect_transport(key) == "gateway"


def test_settings_from_reads_typesafe_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from excelmanus.settings_runtime import override_settings

    override_settings({"EXCELMANUS_TYPESAFE_API_KEY": "vck_test_not_real"})
    from excelmanus.system_one.policy import settings_from

    parsed = settings_from(None)
    assert parsed.api_key == "vck_test_not_real"
    assert detect_transport(parsed.api_key) == "gateway"
    cfg = SimpleNamespace(
        jev_enabled="off",
        jev_exposure="off",
        jev_mode_hint=False,
        jev_present_as_auto=False,
        jev_observation="off",
        jev_ui_hint=False,
        jev_model="jev-1.13.0",
        typesafe_api_key="vck_from_config",
        jev_timeout_seconds=1.5,
        jev_calibrated=False,
    )
    from_cfg = settings_from(cfg)
    assert detect_transport(from_cfg.api_key) == "gateway"
