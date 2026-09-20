"""Manage token bind gate and API middleware."""

from __future__ import annotations

import os

import pytest
from starlette.testclient import TestClient

from excelmanus.auth.manage_token import (
    is_loopback_bind_host,
    require_manage_token_for_bind,
    token_matches,
)


def test_loopback_hosts() -> None:
    assert is_loopback_bind_host("127.0.0.1")
    assert is_loopback_bind_host("localhost")
    assert is_loopback_bind_host("::1")
    assert not is_loopback_bind_host("0.0.0.0")
    assert not is_loopback_bind_host("192.168.1.8")
    assert not is_loopback_bind_host("127.example.com")
    assert not is_loopback_bind_host("127.999.0.1")
    assert is_loopback_bind_host("127.1.2.3")


def test_api_entrypoint_checks_binding_before_starting(monkeypatch):
    from unittest.mock import Mock
    import excelmanus.api as api

    run = Mock()
    monkeypatch.setattr(api.uvicorn, "run", run)
    monkeypatch.setattr("sys.argv", ["excelmanus-api", "--host", "0.0.0.0", "--workers", "2"])
    with pytest.raises(SystemExit):
        api.main()
    run.assert_not_called()

    monkeypatch.setenv("EXCELMANUS_MANAGE_TOKEN", "test-token-for-remote-bind")
    api.main()
    assert run.call_args.kwargs["host"] == "0.0.0.0"
    assert run.call_args.kwargs["workers"] == 2


def test_require_token_for_non_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EXCELMANUS_MANAGE_TOKEN", raising=False)
    with pytest.raises(SystemExit):
        require_manage_token_for_bind("0.0.0.0")
    monkeypatch.setenv("EXCELMANUS_MANAGE_TOKEN", "short")
    with pytest.raises(SystemExit):
        require_manage_token_for_bind("0.0.0.0")
    monkeypatch.setenv("EXCELMANUS_MANAGE_TOKEN", "x" * 16)
    require_manage_token_for_bind("0.0.0.0")
    monkeypatch.delenv("EXCELMANUS_MANAGE_TOKEN", raising=False)
    require_manage_token_for_bind("127.0.0.1")


def test_token_matches_constant_time() -> None:
    assert token_matches("abc", "abc")
    assert not token_matches("abc", "abd")
    assert not token_matches("", "abc")


def test_middleware_requires_bearer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXCELMANUS_MANAGE_TOKEN", "manage-token-value-ok")
    from excelmanus.api import create_app
    from excelmanus.api_app_state import set_draining

    set_draining(False)
    client = TestClient(create_app())
    health = client.get("/api/v1/health")
    assert health.status_code == 200
    assert health.json()["auth_required"] is True
    denied = client.get("/api/v1/sessions")
    assert denied.status_code == 401
    ok = client.get(
        "/api/v1/sessions",
        headers={"Authorization": "Bearer manage-token-value-ok"},
    )
    assert ok.status_code != 401
