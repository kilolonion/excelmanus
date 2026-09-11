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
