"""Single administrator access gate, independent of model OAuth and workspaces.

Only opaque, revocable session hashes are persisted. SQLite also coordinates the
login limit across workers; no user records, registration or tenant IDs exist.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from functools import lru_cache
from typing import Iterator

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, SecretStr
from starlette.responses import JSONResponse

from excelmanus.auth.manage_token import get_manage_token, token_matches
from excelmanus.data_home import get_excelmanus_home

COOKIE_NAME = "excelmanus_session"
COOKIE_PATH = "/"
LOGIN_PASSWORD_ENV = "EXCELMANUS_LOGIN_PASSWORD"
LOGIN_USERNAME_ENV = "EXCELMANUS_LOGIN_USERNAME"
LOGIN_MIN_LENGTH = 12
LOGIN_WINDOW = 60
LOGIN_LIMIT = 10
PUBLIC_PATHS = frozenset({
    "/api/v1/health", "/api/v1/auth/status", "/api/v1/auth/login",
})
router = APIRouter(prefix="/api/v1/auth", tags=["access"])


def login_password() -> str:
    # Password spaces are significant, unlike management token whitespace.
    return os.environ.get(LOGIN_PASSWORD_ENV, "")


def login_username() -> str:
    saved = _settings()
    return saved["username"] or os.environ.get(LOGIN_USERNAME_ENV, "admin").strip()


def access_enabled() -> bool:
    # A malformed configuration must never silently disable authentication.
    saved = _settings()
    if saved["enabled"] is not None:
        return bool(saved["enabled"])
    return bool(login_password() or get_manage_token())


def access_explicitly_disabled() -> bool:
    return _settings()["enabled"] == 0


def session_seconds() -> int:
    try:
        hours = int(os.environ.get("EXCELMANUS_LOGIN_SESSION_HOURS", "12"))
    except ValueError as exc:
        raise ValueError("EXCELMANUS_LOGIN_SESSION_HOURS 必须为 1–168 的整数") from exc
    if not 1 <= hours <= 168:
        raise ValueError("EXCELMANUS_LOGIN_SESSION_HOURS 必须为 1–168 的整数")
    return hours * 3600


def validate_access_config(*, server: bool = False) -> None:
    password = login_password()
    token = get_manage_token()
    if password and (len(password) < LOGIN_MIN_LENGTH or not password.strip()):
        raise ValueError(f"{LOGIN_PASSWORD_ENV} 至少需要 {LOGIN_MIN_LENGTH} 个字符，且不能全为空格")
    if not login_username() or len(login_username()) > 128:
        raise ValueError(f"{LOGIN_USERNAME_ENV} 必须为 1–128 个字符")
    if token and len(token) < 16:
        raise ValueError("EXCELMANUS_MANAGE_TOKEN 至少需要 16 个字符")
    if access_enabled() and not (password_configured() or token):
        raise ValueError("已开启登录保护，但管理员凭据缺失")
    session_seconds()
    secure = os.environ.get("EXCELMANUS_LOGIN_COOKIE_SECURE", "auto").lower()
    if secure not in {"auto", "true", "false"}:
        raise ValueError("EXCELMANUS_LOGIN_COOKIE_SECURE 必须为 auto / true / false")
    server = server or os.environ.get("EXCELMANUS_DEPLOY_MODE", "").strip().lower() == "server"
    if server and not access_enabled() and _settings()["enabled"] is None:
        raise ValueError("服务器模式必须设置 EXCELMANUS_LOGIN_PASSWORD 或 EXCELMANUS_MANAGE_TOKEN")


@lru_cache(maxsize=4)
def _credential_epoch(username: str, password: str, token: str) -> str:
    # Slow digest avoids persisting a fast offline password verifier. Changing
    # credentials invalidates sessions on every worker after restart.
    import json
    raw = json.dumps([username, password, token]).encode()
    return hashlib.scrypt(raw, salt=b"excelmanus-access-v1", n=16384, r=8, p=1).hex()


def credential_epoch() -> str:
    saved = _settings()
    return _credential_epoch(login_username(), saved["password_hash"] or login_password(), get_manage_token() + saved["revision"])


@contextmanager
def _database() -> Iterator[sqlite3.Connection]:
    directory = get_excelmanus_home()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "access.db"
    # No raw session credentials or login password are written to this file.
    db = sqlite3.connect(path, timeout=5)
    try:
        if os.name != "nt":
            path.chmod(0o600)
        db.execute("CREATE TABLE IF NOT EXISTS sessions (digest TEXT PRIMARY KEY, epoch TEXT NOT NULL, expires REAL NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS attempts (id INTEGER PRIMARY KEY CHECK (id = 1), started REAL NOT NULL, count INTEGER NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY CHECK (id = 1), enabled INTEGER NOT NULL, username TEXT NOT NULL, password_hash TEXT NOT NULL, revision TEXT NOT NULL)")
        with db:
            yield db
    finally:
        db.close()


def _settings() -> dict:
    empty = {"enabled": None, "username": "", "password_hash": "", "revision": ""}
    if not (get_excelmanus_home() / "access.db").exists():
        return empty
    with _database() as db:
        row = db.execute("SELECT enabled, username, password_hash, revision FROM settings WHERE id = 1").fetchone()
    return dict(zip(empty, row)) if row else empty


def _hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1)
    return salt.hex() + ":" + digest.hex()


def _verify_password(password: str, encoded: str) -> bool:
    try:
        salt, _ = encoded.split(":", 1)
        return hmac.compare_digest(_hash_password(password, bytes.fromhex(salt)), encoded)
    except (ValueError, TypeError):
        return False


def password_configured() -> bool:
    return bool(_settings()["password_hash"] or login_password())


def _session_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def valid_session(request: Request) -> bool:
    token = request.cookies.get(COOKIE_NAME, "")
    if not token or len(token) > 128:
        return False
    with _database() as db:
        row = db.execute("SELECT epoch, expires FROM sessions WHERE digest = ?", (_session_digest(token),)).fetchone()
    return bool(row and row[1] > time.time() and token_matches(row[0], credential_epoch()))


def authenticated(request: Request) -> bool:
    from excelmanus.auth.manage_token import _provided_token
    return not access_enabled() or token_matches(_provided_token(request), get_manage_token()) or valid_session(request)


def require_browser_header(request: Request) -> None:
    # Non-simple header requires a successful CORS preflight. With SameSite
    # cookies this also blocks form posts and untrusted same-site origins.
    if request.headers.get("x-requested-with") != "ExcelManus":
        raise HTTPException(403, "请求来源校验失败，请刷新页面后重试")


def _secure_cookie(request: Request) -> bool:
    value = os.environ.get("EXCELMANUS_LOGIN_COOKIE_SECURE", "auto").lower()
    return value == "true" or (value == "auto" and request.url.scheme == "https")


def _status(request: Request) -> dict:
    enabled = access_enabled()
    signed_in = authenticated(request)
    return {
        "auth_required": enabled,
        "authenticated": signed_in,
        "login_method": "password" if password_configured() else "token" if enabled else "none",
        "username": login_username() if enabled and signed_in else None,
    }


@router.get("/status")
def access_status(request: Request) -> JSONResponse:
    return JSONResponse(_status(request), headers={"Cache-Control": "no-store"})


class LoginBody(BaseModel):
    username: str = Field(default="", max_length=128)
    password: SecretStr = Field(max_length=4096)


@router.post("/login")
def login(body: LoginBody, request: Request) -> JSONResponse:
    require_browser_header(request)
    if not access_enabled():
        raise HTTPException(409, "此实例未启用登录保护")
    validate_access_config()
    now = time.time()
    # One shared limit for this single administrator. X-Forwarded-For cannot
    # bypass it, and username variations don't create new buckets.
    with _database() as db:
        db.execute("BEGIN IMMEDIATE")
        attempt = db.execute("SELECT started, count FROM attempts WHERE id = 1").fetchone()
        if attempt and now - attempt[0] < LOGIN_WINDOW:
            if attempt[1] >= LOGIN_LIMIT:
                retry = max(1, int(LOGIN_WINDOW - (now - attempt[0])) + 1)
                raise HTTPException(429, "登录尝试过于频繁，请稍后再试", headers={"Retry-After": str(retry)})
            db.execute("UPDATE attempts SET count = count + 1 WHERE id = 1")
        else:
            db.execute("INSERT OR REPLACE INTO attempts VALUES (1, ?, 1)", (now,))

    epoch = credential_epoch()
    saved_hash = _settings()["password_hash"]
    password_mode = password_configured()
    provided = body.password.get_secret_value()
    correct_secret = _verify_password(provided, saved_hash) if saved_hash else token_matches(provided, login_password() or get_manage_token())
    correct_user = token_matches(body.username, login_username()) if password_mode else True
    if not (correct_secret and correct_user):
        raise HTTPException(401, "账号或密码不正确" if password_mode else "管理令牌不正确")

    token = secrets.token_urlsafe(32)
    ttl = session_seconds()
    with _database() as db:
        db.execute("BEGIN IMMEDIATE")
        if epoch != credential_epoch():
            raise HTTPException(401, "登录配置已更新，请重新登录")
        db.execute("DELETE FROM sessions WHERE expires <= ? OR epoch != ?", (now, epoch))
        # Logging in again rotates this browser's session.
        db.execute("DELETE FROM sessions WHERE digest = ?", (_session_digest(request.cookies.get(COOKIE_NAME, "")),))
        db.execute("INSERT INTO sessions VALUES (?, ?, ?)", (_session_digest(token), epoch, now + ttl))
    response = JSONResponse({"authenticated": True, "username": login_username(), "expires_in": ttl}, headers={"Cache-Control": "no-store"})
    response.set_cookie(COOKIE_NAME, token, max_age=ttl, httponly=True, secure=_secure_cookie(request), samesite="strict", path=COOKIE_PATH)
    return response


@router.post("/logout")
def logout(request: Request) -> JSONResponse:
    require_browser_header(request)
    token = request.cookies.get(COOKIE_NAME, "")
    if token:
        with _database() as db:
            db.execute("DELETE FROM sessions WHERE digest = ?", (_session_digest(token),))
    response = JSONResponse({"authenticated": False}, headers={"Cache-Control": "no-store"})
    response.delete_cookie(COOKIE_NAME, path=COOKIE_PATH, httponly=True, secure=_secure_cookie(request), samesite="strict")
    return response


@router.get("/settings")
def access_settings() -> JSONResponse:
    return JSONResponse({
        "enabled": access_enabled(),
        "username": login_username(),
        "password_configured": password_configured(),
        "manage_token_configured": bool(get_manage_token()),
        "session_hours": session_seconds() // 3600,
    }, headers={"Cache-Control": "no-store"})


class AccessSettingsBody(BaseModel):
    enabled: bool
    username: str = Field(min_length=1, max_length=128)
    password: SecretStr = Field(default=SecretStr(""), max_length=4096)


@router.put("/settings")
def save_access_settings(body: AccessSettingsBody, request: Request) -> JSONResponse:
    require_browser_header(request)
    username = body.username.strip()
    if not username:
        raise HTTPException(422, "管理员账号不能为空")
    password = body.password.get_secret_value()
    if password and (len(password) < LOGIN_MIN_LENGTH or not password.strip()):
        raise HTTPException(422, "密码至少需要 12 个字符，且不能全为空格")
    if body.enabled and not (password or password_configured() or get_manage_token()):
        raise HTTPException(422, "开启登录保护前请先设置管理员密码")
    saved = _settings()
    encoded = _hash_password(password) if password else saved["password_hash"]
    with _database() as db:
        db.execute("INSERT OR REPLACE INTO settings VALUES (1, ?, ?, ?, ?)", (int(body.enabled), username, encoded, secrets.token_hex(16)))
        db.execute("DELETE FROM sessions")
        db.execute("DELETE FROM attempts")
    # Every change revokes all browser sessions, including the current one.
    # On enable/update, the UI returns to login to verify the saved credentials.
    response = access_settings()
    response.delete_cookie(COOKIE_NAME, path=COOKIE_PATH, httponly=True, secure=_secure_cookie(request), samesite="strict")
    return response
