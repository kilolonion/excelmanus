"""Auth API router — registration, login, OAuth, profile management."""

from __future__ import annotations

import logging
import secrets
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse, RedirectResponse

from excelmanus.auth.dependencies import get_current_user, require_admin
from excelmanus.auth.models import (
    ForgotPasswordRequest,
    LoginRequest,
    OAuthCallbackParams,
    RefreshRequest,
    RegisterPendingResponse,
    RegisterRequest,
    ResendCodeRequest,
    ResetPasswordRequest,
    TokenResponse,
    UserPublic,
    UserRecord,
    UserRole,
    UserUpdateRequest,
    VerifyEmailRequest,
)
from excelmanus.auth.oauth import (
    github_authorize_url,
    github_exchange_code,
    google_authorize_url,
    google_exchange_code,
)
from excelmanus.auth.email import is_email_configured, send_verification_email
from excelmanus.auth.security import (
    create_token_pair,
    decode_token,
    hash_password,
    verify_password,
)
from excelmanus.auth.store import UserStore
from excelmanus.auth.workspace import (
    enforce_quota,
    get_user_workspace,
    get_workspace_usage,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


def _get_store(request: Request) -> UserStore:
    store = getattr(request.app.state, "user_store", None)
    if store is None:
        raise HTTPException(503, "认证服务未初始化")
    return store


def _get_workspace_root(request: Request) -> str:
    ws = getattr(request.app.state, "workspace_root", None)
    if ws:
        return ws
    import os
    return os.environ.get("EXCELMANUS_WORKSPACE_ROOT", "./workspace")


def _build_token_response(user: UserRecord) -> TokenResponse:
    access, refresh, expires_in = create_token_pair(user.id, user.role)
    return TokenResponse(
        access_token=access,
        refresh_token=refresh,
        expires_in=expires_in,
        user=UserPublic.from_record(user),
    )


def _is_verify_required() -> bool:
    """Email verification is required when the env flag is set AND an email backend is configured."""
    import os
    flag = os.environ.get("EXCELMANUS_EMAIL_VERIFY_REQUIRED", "").strip().lower()
    return flag in ("1", "true", "yes") and is_email_configured()


def _get_rate_limiter(request: Request):
    return getattr(request.app.state, "rate_limiter", None)


# ── Registration & Login ──────────────────────────────────


@router.post("/register", status_code=201)
async def register(body: RegisterRequest, request: Request) -> Any:
    store = _get_store(request)

    existing = store.get_by_email(body.email)
    if existing is not None:
        # If already registered but inactive (pending verification), allow resend
        if not existing.is_active and _is_verify_required():
            rate_limiter = _get_rate_limiter(request)
            if rate_limiter:
                rate_limiter.check_send_code(body.email)
            store.invalidate_verifications(body.email, "register")
            _, code = store.create_verification(body.email, "register")
            await send_verification_email(body.email, code, "register")
            return RegisterPendingResponse(
                message=f"验证码已重新发送至 {body.email}",
                email=body.email,
            )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="该邮箱已被注册",
        )

    # First user becomes admin
    role = UserRole.ADMIN if store.count_users() == 0 else UserRole.USER
    verify_required = _is_verify_required()

    user = UserRecord(
        email=body.email,
        display_name=body.display_name or body.email.split("@")[0],
        password_hash=hash_password(body.password),
        role=role,
        is_active=not verify_required,
    )
    store.create_user(user)
    logger.info("用户注册: %s (role=%s, active=%s)", user.email, role, user.is_active)

    if verify_required:
        rate_limiter = _get_rate_limiter(request)
        if rate_limiter:
            rate_limiter.check_send_code(body.email)
        _, code = store.create_verification(body.email, "register")
        await send_verification_email(body.email, code, "register")
        return RegisterPendingResponse(
            message=f"验证码已发送至 {body.email}，请在 10 分钟内完成验证",
            email=body.email,
        )

    return _build_token_response(user)


@router.post("/verify-email", response_model=TokenResponse)
async def verify_email(body: VerifyEmailRequest, request: Request) -> Any:
    store = _get_store(request)
    record = store.get_valid_verification(body.email, body.code, "register")
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="验证码无效或已过期",
        )

    store.mark_verification_used(record["id"])
    user = store.get_by_email(body.email)
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")

    if not user.is_active:
        store.update_user(user.id, is_active=True)
        user = store.get_by_id(user.id) or user

    logger.info("邮箱验证成功: %s", body.email)
    return _build_token_response(user)


@router.post("/resend-code")
async def resend_code(body: ResendCodeRequest, request: Request) -> Any:
    store = _get_store(request)

    if body.purpose not in ("register", "reset_password"):
        raise HTTPException(status_code=400, detail="无效的 purpose")

    # Rate limit by email
    rate_limiter = _get_rate_limiter(request)
    if rate_limiter:
        rate_limiter.check_send_code(body.email)

    if body.purpose == "register":
        user = store.get_by_email(body.email)
        if user is None or user.is_active:
            # Don't leak whether email exists
            return {"message": "如果该邮箱待验证，验证码将重新发送"}
    else:
        user = store.get_by_email(body.email)
        if user is None:
            return {"message": "如果该邮箱已注册，验证码将重新发送"}

    store.invalidate_verifications(body.email, body.purpose)
    _, code = store.create_verification(body.email, body.purpose)
    await send_verification_email(body.email, code, body.purpose)  # type: ignore[arg-type]
    return {"message": "验证码已重新发送，请检查邮箱"}


@router.post("/forgot-password")
async def forgot_password(body: ForgotPasswordRequest, request: Request) -> Any:
    store = _get_store(request)

    rate_limiter = _get_rate_limiter(request)
    if rate_limiter:
        rate_limiter.check_send_code(body.email)

    user = store.get_by_email(body.email)
    if user is not None and user.is_active and user.password_hash is not None:
        store.invalidate_verifications(body.email, "reset_password")
        _, code = store.create_verification(body.email, "reset_password")
        await send_verification_email(body.email, code, "reset_password")
        logger.info("密码重置验证码已发送: %s", body.email)

    # Always return same message to prevent email enumeration
    return {"message": "如果该邮箱已注册，验证码将在几秒内发送到您的邮箱"}


@router.post("/reset-password")
async def reset_password(body: ResetPasswordRequest, request: Request) -> Any:
    store = _get_store(request)
    record = store.get_valid_verification(body.email, body.code, "reset_password")
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="验证码无效或已过期",
        )

    user = store.get_by_email(body.email)
    if user is None or not user.is_active:
        raise HTTPException(status_code=404, detail="用户不存在或已禁用")

    store.mark_verification_used(record["id"])
    new_hash = hash_password(body.new_password)
    store.update_user(user.id, password_hash=new_hash)
    logger.info("密码重置成功: %s", body.email)
    return {"message": "密码重置成功，请使用新密码登录"}


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


@router.get("/me/workspace")
async def get_my_workspace_usage(
    request: Request,
    user: UserRecord = Depends(get_current_user),
) -> Any:
    """Return workspace usage stats for the current user."""
    ws_root = _get_workspace_root(request)
    ws_dir = get_user_workspace(ws_root, user.id)
    usage = get_workspace_usage(ws_dir)
    return usage.to_dict()


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
        return _oauth_error_redirect("GitHub 认证失败")

    store = _get_store(request)
    return _oauth_success_redirect(store, info)


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
        return _oauth_error_redirect("Google 认证失败")

    store = _get_store(request)
    return _oauth_success_redirect(store, info)


def _oauth_error_redirect(message: str) -> RedirectResponse:
    """Redirect browser to frontend callback page with error."""
    from urllib.parse import quote
    return RedirectResponse(f"/auth/callback?error={quote(message)}")


def _oauth_success_redirect(store: UserStore, info: Any) -> RedirectResponse:
    """Exchange OAuth info for JWT tokens, then redirect to frontend."""
    try:
        token_resp = _handle_oauth_user(store, info)
    except HTTPException as exc:
        return _oauth_error_redirect(exc.detail or "认证失败")

    from urllib.parse import urlencode
    params = urlencode({
        "access_token": token_resp.access_token,
        "refresh_token": token_resp.refresh_token,
        "provider": info.provider,
    })
    return RedirectResponse(f"/auth/callback?{params}")


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
    ws_root = _get_workspace_root(request)

    result = []
    for u in users:
        pub = UserPublic.from_record(u)
        ws_dir = get_user_workspace(ws_root, u.id)
        usage = get_workspace_usage(ws_dir)
        daily = store.get_daily_usage(u.id)
        monthly = store.get_monthly_usage(u.id)
        result.append({
            **pub.model_dump(),
            "is_active": u.is_active,
            "daily_token_limit": u.daily_token_limit,
            "monthly_token_limit": u.monthly_token_limit,
            "daily_tokens_used": daily,
            "monthly_tokens_used": monthly,
            "workspace": usage.to_dict(),
        })

    return {"users": result, "total": len(result)}


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


@router.delete("/admin/users/{user_id}/workspace")
async def admin_clear_user_workspace(
    user_id: str,
    request: Request,
    _admin: UserRecord = Depends(require_admin),
) -> Any:
    """Delete all files in a user's workspace."""
    store = _get_store(request)
    target = store.get_by_id(user_id)
    if target is None:
        raise HTTPException(404, "用户不存在")

    ws_root = _get_workspace_root(request)
    ws_dir = get_user_workspace(ws_root, user_id)

    import shutil
    from pathlib import Path
    ws_path = Path(ws_dir)
    deleted_count = 0
    if ws_path.is_dir():
        for item in list(ws_path.rglob("*")):
            if item.is_file():
                item.unlink(missing_ok=True)
                deleted_count += 1
        for item in sorted(ws_path.rglob("*"), reverse=True):
            if item.is_dir() and item != ws_path:
                try:
                    item.rmdir()
                except OSError:
                    pass

    logger.info("Admin cleared workspace for user %s: %d files deleted", user_id, deleted_count)
    return {"status": "ok", "deleted_files": deleted_count}


@router.post("/admin/users/{user_id}/enforce-quota")
async def admin_enforce_user_quota(
    user_id: str,
    request: Request,
    _admin: UserRecord = Depends(require_admin),
) -> Any:
    """Force enforce quota on a user's workspace."""
    store = _get_store(request)
    target = store.get_by_id(user_id)
    if target is None:
        raise HTTPException(404, "用户不存在")

    ws_root = _get_workspace_root(request)
    ws_dir = get_user_workspace(ws_root, user_id)
    deleted = enforce_quota(ws_dir)
    usage = get_workspace_usage(ws_dir)
    return {"status": "ok", "deleted": deleted, "workspace": usage.to_dict()}
