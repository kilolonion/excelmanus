"""Single-admin access, persisted settings and browser session boundaries."""

import pytest
from starlette.testclient import TestClient

from excelmanus.auth.access import COOKIE_NAME, LOGIN_LIMIT, LOGIN_WINDOW

PASSWORD = "correct-horse-battery-123"
HEADERS = {"X-Requested-With": "ExcelManus"}


def make_client():
    from excelmanus.api import create_app
    application = create_app()

    @application.get("/api/v1/access-test")
    def protected_read():
        return {"private": True}

    @application.post("/api/v1/access-test")
    def protected_write():
        return {"saved": True}

    return TestClient(application)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("EXCELMANUS_LOGIN_PASSWORD", PASSWORD)
    return make_client()


def sign_in(client, password=PASSWORD, username="admin"):
    return client.post("/api/v1/auth/login", json={"username": username, "password": password}, headers=HEADERS)


@pytest.mark.parametrize("path", [
    "/api/v1/sessions", "/api/v1/files/excel?path=secret.xlsx", "/api/v1/config/models",
    "/api/v1/auth/providers", "/api/v1/auth/providers/openai-codex/status",
    "/api/v1/auth/settings", "/openapi.json", "/docs", "/api/v1/health/secret",
])
def test_private_routes_require_login(client, path):
    response = client.get(path)
    assert response.status_code == 401
    assert response.headers["X-ExcelManus-Auth"] == "required"


def test_status_and_health_do_not_disclose_private_state(client):
    status = client.get("/api/v1/auth/status").json()
    assert status == {"auth_required": True, "authenticated": False, "login_method": "password", "username": None}
    for path in ["/api/v1/health", "/api/v1/health?details=1"]:
        health = client.get(path).json()
        assert health["auth_required"] is True
        assert not {"model", "onboarding", "tools", "active_sessions", "configured"} & health.keys()


def test_login_cookie_and_logout_revoke_old_cookie(client):
    assert sign_in(client, "wrong").status_code == 401
    assert sign_in(client, username="other").status_code == 401
    assert client.get("/api/v1/access-test").status_code == 401
    response = sign_in(client)
    assert response.status_code == 200
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie and "Max-Age=43200" in cookie
    assert PASSWORD not in cookie and "password" not in response.json()
    token = client.cookies.get(COOKIE_NAME)
    assert client.get("/api/v1/access-test").status_code == 200
    assert client.get("/api/v1/auth/status").json()["authenticated"] is True
    assert client.post("/api/v1/access-test").status_code == 403
    assert client.post("/api/v1/access-test", headers=HEADERS).status_code == 200
    assert client.post("/api/v1/auth/logout", headers=HEADERS).status_code == 200
    assert client.get("/api/v1/access-test", headers={"Cookie": f"{COOKIE_NAME}={token}"}).status_code == 401


def test_sessions_shared_across_workers_and_expire(client, monkeypatch):
    import excelmanus.auth.access as access
    now = access.time.time()
    monkeypatch.setattr(access.time, "time", lambda: now)
    assert sign_in(client).status_code == 200
    other = make_client()
    other.cookies.update(client.cookies)
    assert other.get("/api/v1/access-test").status_code == 200
    monkeypatch.setattr(access.time, "time", lambda: now + 43201)
    assert other.get("/api/v1/access-test").status_code == 401


def test_credential_rotation_revokes_sessions(client, monkeypatch):
    assert sign_in(client).status_code == 200
    monkeypatch.setenv("EXCELMANUS_LOGIN_PASSWORD", "a-different-password")
    assert client.get("/api/v1/access-test").status_code == 401


def test_rate_limit_is_shared_not_keyed_by_claimed_ip(client, monkeypatch):
    import excelmanus.auth.access as access
    now = access.time.time()
    monkeypatch.setattr(access.time, "time", lambda: now)
    for i in range(LOGIN_LIMIT):
        response = client.post("/api/v1/auth/login", json={"username": str(i), "password": "wrong"}, headers={**HEADERS, "X-Forwarded-For": f"1.1.1.{i}"})
        assert response.status_code == 401
    response = sign_in(make_client())
    assert response.status_code == 429
    assert int(response.headers["Retry-After"]) > 0
    monkeypatch.setattr(access.time, "time", lambda: now + LOGIN_WINDOW + 1)
    assert sign_in(client).status_code == 200


def test_cookie_security_and_csrf(client, monkeypatch):
    assert client.post("/api/v1/auth/login", json={"username": "admin", "password": PASSWORD}).status_code == 403
    monkeypatch.setenv("EXCELMANUS_LOGIN_COOKIE_SECURE", "true")
    assert "Secure" in sign_in(client).headers["set-cookie"]
    preflight = client.options("/api/v1/auth/login", headers={"Origin": "https://untrusted.example", "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "X-Requested-With"})
    assert preflight.status_code == 400
    assert "access-control-allow-origin" not in preflight.headers


def test_legacy_token_headers_work_but_query_tokens_do_not(monkeypatch):
    token = "existing-manage-token"
    monkeypatch.setenv("EXCELMANUS_MANAGE_TOKEN", token)
    client = make_client()
    assert client.get("/api/v1/access-test", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    assert client.post("/api/v1/access-test", headers={"X-ExcelManus-Token": token}).status_code == 200
    assert client.get(f"/api/v1/access-test?manage_token={token}").status_code == 401
    assert client.get("/api/v1/auth/status").json()["login_method"] == "token"
    assert sign_in(client, token).status_code == 200


def test_enable_disable_and_restart_without_env_credentials(monkeypatch):
    from excelmanus.data_home import get_excelmanus_home
    client = make_client()
    assert client.get("/api/v1/access-test").status_code == 200
    settings = {"enabled": True, "username": "owner", "password": PASSWORD}
    assert client.put("/api/v1/auth/settings", json={**settings, "password": ""}, headers=HEADERS).status_code == 422
    assert client.put("/api/v1/auth/settings", json=settings).status_code == 403
    assert client.put("/api/v1/auth/settings", json=settings, headers=HEADERS).status_code == 200
    assert client.get("/api/v1/access-test").status_code == 401
    monkeypatch.setenv("EXCELMANUS_DEPLOY_MODE", "server")
    restarted = make_client()
    assert sign_in(restarted, username="owner").status_code == 200
    result = restarted.get("/api/v1/auth/settings").json()
    assert result["enabled"] and result["password_configured"]
    assert "password" not in result and "password_hash" not in result
    assert PASSWORD.encode() not in (get_excelmanus_home() / "access.db").read_bytes()
    # Saving an empty password preserves it, and disables the gate immediately.
    assert restarted.put("/api/v1/auth/settings", json={**settings, "enabled": False, "password": ""}, headers=HEADERS).status_code == 200
    assert make_client().get("/api/v1/access-test").status_code == 200
    assert restarted.put("/api/v1/auth/settings", json={**settings, "password": ""}, headers=HEADERS).status_code == 200
    assert sign_in(restarted, username="owner").status_code == 200


def test_settings_update_revokes_all_sessions(client):
    assert sign_in(client).status_code == 200
    other = make_client()
    assert sign_in(other).status_code == 200
    assert client.put("/api/v1/auth/settings", json={"enabled": True, "username": "owner", "password": "replacement-password"}, headers=HEADERS).status_code == 200
    assert other.get("/api/v1/access-test").status_code == 401
    assert client.get("/api/v1/access-test").status_code == 401
    assert sign_in(client).status_code == 401
    assert sign_in(client, "replacement-password", "owner").status_code == 200


@pytest.mark.parametrize("filename", ["access.db", "access.db-journal", "access.db-wal", "access.db-shm"])
def test_access_state_cannot_be_opened_as_a_workspace_file(tmp_path, filename):
    from excelmanus.security.guard import FileAccessGuard, SecurityViolationError
    (tmp_path / filename).touch()
    with pytest.raises(SecurityViolationError, match="敏感文件"):
        FileAccessGuard(str(tmp_path)).resolve_and_validate(filename)


def test_server_requires_config_even_on_loopback(monkeypatch):
    monkeypatch.setenv("EXCELMANUS_DEPLOY_MODE", "server")
    with pytest.raises(ValueError, match="服务器模式"):
        make_client()


@pytest.mark.parametrize("name,value", [
    ("EXCELMANUS_LOGIN_PASSWORD", "short"),
    ("EXCELMANUS_MANAGE_TOKEN", "short"),
    ("EXCELMANUS_LOGIN_SESSION_HOURS", "0"),
    ("EXCELMANUS_LOGIN_COOKIE_SECURE", "invalid"),
])
def test_invalid_configuration_never_silently_disables_protection(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError):
        make_client()
