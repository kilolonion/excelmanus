"""Auth API router — registration, login, OAuth, profile management."""

from __future__ import annotations

import logging
import secrets
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse, RedirectResponse

from excelmanus.auth.dependencies import get_current_user, require_admin
from excelmanus.auth.models import (
    LoginRequest,
    OAuthCallbackParams,
    RefreshRequest,
    RegisterRequest,
    TokenResponse,
    UserPublic,
    UserRecord,
    UserRole,
    UserUpdateRequest,
)
from excelmanus.auth.oauth import (
    github_authorize_url,
    github_exchange_code,
    google_authorize_url,
    google_exchange_code,
)
from excelmanus.auth.security import (
    create_token_pair,
    decode_token,
    hash_password,
    verify_password,
)
from excelmanus.auth.store import UserStore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


def _get_store(request: Request) -> UserStore:
    store = getattr(request.app.state, "user_store", None)
    if store is None:
        raise HTTPException(503, "认证服务未初始化")
    return store


def _build_token_response(user: UserRecord) -> TokenResponse:
    access, refresh, expires_in = create_token_pair(user.id, user.role)
    return TokenResponse(
        access_token=access,
        refresh_token=refresh,
        expires_in=expires_in,
        user=UserPublic.from_record(user),
    )


# ── Registration & Login ──────────────────────────────────


@router.post("/register", response_model=TokenResponse, status_code=201)
async def register(body: RegisterRequest, request: Request) -> Any:
    store = _get_store(request)

    if store.email_exists(body.email):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="该邮箱已被注册",
        )

    # First user becomes admin
    role = UserRole.ADMIN if store.count_users() == 0 else UserRole.USER

    user = UserRecord(
        email=body.email,
        display_name=body.display_name or body.email.split("@")[0],
        password_hash=hash_password(body.password),
        role=role,
    )
    store.create_user(user)
    logger.info("用户注册成功: %s (role=%s)", user.email, role)
    return _build_token_response(user)


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, request: Request) -> Any:
    store = _get_store(request)
    user = store.get_by_email(body.email)

    if user is None or user.password_hash is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="邮箱或密码错误",
        )

    if not verify_password(body.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="邮箱或密码错误",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="账户已被禁用",
        )

    return _build_token_response(user)


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(body: RefreshRequest, request: Request) -> Any:
    payload = decode_token(body.refresh_token)
    if payload is None or payload.get("type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效或过期的 refresh token",
        )

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="无效的 token")

    store = _get_store(request)
    user = store.get_by_id(user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="用户不存在或已禁用")

    return _build_token_response(user)


# ── Current User ──────────────────────────────────────────


@router.get("/me", response_model=UserPublic)
async def get_me(user: UserRecord = Depends(get_current_user)) -> Any:
    return UserPublic.from_record(user)


@router.put("/me", response_model=UserPublic)
async def update_me(
    body: UserUpdateRequest,
    request: Request,
    user: UserRecord = Depends(get_current_user),
) -> Any:
    store = _get_store(request)
    updates: dict[str, Any] = {}

    if body.display_name is not None:
        updates["display_name"] = body.display_name
    if body.avatar_url is not None:
        updates["avatar_url"] = body.avatar_url
    if body.llm_api_key is not None:
        updates["llm_api_key"] = body.llm_api_key if body.llm_api_key else None
    if body.llm_base_url is not None:
        updates["llm_base_url"] = body.llm_base_url if body.llm_base_url else None
    if body.llm_model is not None:
        updates["llm_model"] = body.llm_model if body.llm_model else None

    if updates:
        store.update_user(user.id, **updates)

    updated = store.get_by_id(user.id)
    return UserPublic.from_record(updated or user)


@router.get("/me/usage")
async def get_my_usage(
    request: Request,
    user: UserRecord = Depends(get_current_user),
) -> Any:
    store = _get_store(request)
    daily = store.get_daily_usage(user.id)
    monthly = store.get_monthly_usage(user.id)
    return {
        "user_id": user.id,
        "daily_tokens": daily,
        "monthly_tokens": monthly,
        "daily_limit": user.daily_token_limit,
        "monthly_limit": user.monthly_token_limit,
        "daily_remaining": max(0, user.daily_token_limit - daily) if user.daily_token_limit > 0 else -1,
        "monthly_remaining": max(0, user.monthly_token_limit - monthly) if user.monthly_token_limit > 0 else -1,
    }


# ── OAuth: GitHub ─────────────────────────────────────────


@router.get("/oauth/github")
async def oauth_github_redirect() -> Any:
    state = secrets.token_urlsafe(32)
    url = github_authorize_url(state=state)
    return JSONResponse({"authorize_url": url, "state": state})


@router.get("/oauth/github/callback")
async def oauth_github_callback(
    code: str,
    state: str | None = None,
    request: Request = ...,  # type: ignore[assignment]
) -> Any:
    info = await github_exchange_code(code)
    if info is None:
        raise HTTPException(400, "GitHub 认证失败")

    store = _get_store(request)
    return _handle_oauth_user(store, info)


# ── OAuth: Google ─────────────────────────────────────────


@router.get("/oauth/google")
async def oauth_google_redirect() -> Any:
    state = secrets.token_urlsafe(32)
    url = google_authorize_url(state=state)
    return JSONResponse({"authorize_url": url, "state": state})


@router.get("/oauth/google/callback")
async def oauth_google_callback(
    code: str,
    state: str | None = None,
    request: Request = ...,  # type: ignore[assignment]
) -> Any:
    info = await google_exchange_code(code)
    if info is None:
        raise HTTPException(400, "Google 认证失败")

    store = _get_store(request)
    return _handle_oauth_user(store, info)


def _handle_oauth_user(store: UserStore, info: Any) -> TokenResponse:
    """Find or create user from OAuth info, return token pair."""
    # Check if user already linked by OAuth
    user = store.get_by_oauth(info.provider, info.oauth_id)
    if user is not None:
        if not user.is_active:
            raise HTTPException(403, "账户已被禁用")
        return _build_token_response(user)

    # Check if email already registered (link OAuth)
    user = store.get_by_email(info.email)
    if user is not None:
        store.update_user(
            user.id,
            oauth_provider=info.provider,
            oauth_id=info.oauth_id,
            avatar_url=info.avatar_url or user.avatar_url,
        )
        updated = store.get_by_id(user.id)
        return _build_token_response(updated or user)

    # Create new user
    role = UserRole.ADMIN if store.count_users() == 0 else UserRole.USER
    user = UserRecord(
        email=info.email,
        display_name=info.display_name or info.email.split("@")[0],
        role=role,
        oauth_provider=info.provider,
        oauth_id=info.oauth_id,
        avatar_url=info.avatar_url,
    )
    store.create_user(user)
    logger.info("OAuth 用户注册: %s via %s", info.email, info.provider)
    return _build_token_response(user)


# ── Admin: User Management ────────────────────────────────


@router.get("/admin/users")
async def admin_list_users(
    request: Request,
    _admin: UserRecord = Depends(require_admin),
) -> Any:
    store = _get_store(request)
    users = store.list_users(include_inactive=True)
    return {
        "users": [UserPublic.from_record(u) for u in users],
        "total": len(users),
    }


@router.patch("/admin/users/{user_id}")
async def admin_update_user(
    user_id: str,
    request: Request,
    _admin: UserRecord = Depends(require_admin),
) -> Any:
    body = await request.json()
    store = _get_store(request)
    target = store.get_by_id(user_id)
    if target is None:
        raise HTTPException(404, "用户不存在")

    allowed_fields = {"role", "is_active", "daily_token_limit", "monthly_token_limit", "display_name"}
    updates = {k: v for k, v in body.items() if k in allowed_fields}
    if not updates:
        raise HTTPException(400, "无有效更新字段")

    store.update_user(user_id, **updates)
    updated = store.get_by_id(user_id)
    return {"status": "ok", "user": UserPublic.from_record(updated or target)}
