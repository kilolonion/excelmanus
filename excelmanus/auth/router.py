"""认证 API 路由 — 注册、登录、OAuth、用户资料管理。"""

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
    SetPasswordRequest,
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
    qq_authorize_url,
    qq_exchange_code,
)
from excelmanus.auth.email import is_email_configured, send_verification_email
from excelmanus.auth.security import (
    create_token_pair,
    decode_token,
    hash_password,
    verify_password,
)
from excelmanus.auth.store import UserStore
from excelmanus.workspace import IsolatedWorkspace

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


def _is_verify_required(request: Request | None = None) -> bool:
    """当管理员开关或环境变量标志已设置且邮件后端已配置时，需要邮箱验证。"""
    import os
    # 优先检查 config_kv（管理员在 UI 中设置的值）
    if request is not None:
        store = getattr(request.app.state, "config_store", None)
        if store is not None:
            raw = store.get("email_verify_required", "")
            if raw:
                return raw.lower() in ("1", "true", "yes") and is_email_configured(store)
    # 回退到环境变量
    flag = os.environ.get("EXCELMANUS_EMAIL_VERIFY_REQUIRED", "").strip().lower()
    return flag in ("1", "true", "yes") and is_email_configured()


def _get_rate_limiter(request: Request):
    return getattr(request.app.state, "rate_limiter", None)


# ── 注册与登录 ──────────────────────────────────


@router.post("/register", status_code=201)
async def register(body: RegisterRequest, request: Request) -> Any:
    store = _get_store(request)

    existing = store.get_by_email(body.email)
    if existing is not None:
        # 如果已注册但未激活（待验证），允许重新发送
        if not existing.is_active and _is_verify_required(request):
            rate_limiter = _get_rate_limiter(request)
            if rate_limiter:
                rate_limiter.check_send_code(body.email)
            store.invalidate_verifications(body.email, "register")
            _, code = store.create_verification(body.email, "register")
            await send_verification_email(body.email, code, "register", _get_config_store(request))
            return RegisterPendingResponse(
                message=f"验证码已重新发送至 {body.email}",
                email=body.email,
            )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="该邮箱已被注册",
        )

    # 第一个用户成为管理员
    role = UserRole.ADMIN if store.count_users() == 0 else UserRole.USER
    verify_required = _is_verify_required(request)

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
        await send_verification_email(body.email, code, "register", _get_config_store(request))
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

    # 按邮箱限流
    rate_limiter = _get_rate_limiter(request)
    if rate_limiter:
        rate_limiter.check_send_code(body.email)

    if body.purpose == "register":
        user = store.get_by_email(body.email)
        if user is None or user.is_active:
            # 不泄露邮箱是否存在
            return {"message": "如果该邮箱待验证，验证码将重新发送"}
    else:
        user = store.get_by_email(body.email)
        if user is None:
            return {"message": "如果该邮箱已注册，验证码将重新发送"}

    store.invalidate_verifications(body.email, body.purpose)
    _, code = store.create_verification(body.email, body.purpose)
    await send_verification_email(body.email, code, body.purpose, _get_config_store(request))  # type: ignore[arg-type]
    return {"message": "验证码已重新发送，请检查邮箱"}


@router.post("/forgot-password")
async def forgot_password(body: ForgotPasswordRequest, request: Request) -> Any:
    store = _get_store(request)

    rate_limiter = _get_rate_limiter(request)
    if rate_limiter:
        rate_limiter.check_send_code(body.email)

    user = store.get_by_email(body.email)
    if user is not None and user.is_active:
        store.invalidate_verifications(body.email, "reset_password")
        _, code = store.create_verification(body.email, "reset_password")
        await send_verification_email(body.email, code, "reset_password", _get_config_store(request))
        logger.info("密码重置验证码已发送: %s", body.email)

    # 始终返回相同消息，防止邮箱枚举
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


# ── 当前用户 ──────────────────────────────────────────


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
    """返回当前用户的工作区使用统计。"""
    ws_root = _get_workspace_root(request)
    ws = IsolatedWorkspace.resolve(ws_root, user_id=user.id, auth_enabled=True)
    return ws.get_usage().to_dict()


# ── OAuth: GitHub ─────────────────────────────────────────


@router.get("/oauth/github")
async def oauth_github_redirect(request: Request) -> Any:
    state = f"github:{secrets.token_urlsafe(32)}"
    url = github_authorize_url(state=state, config_store=_get_config_store(request))
    return JSONResponse({"authorize_url": url, "state": state})


@router.get("/oauth/github/callback")
async def oauth_github_callback(
    code: str,
    state: str | None = None,
    request: Request = ...,  # type: ignore[assignment]
) -> Any:
    want_json = _wants_json(request)
    info = await github_exchange_code(code, config_store=_get_config_store(request))
    if info is None:
        return _oauth_error_response("GitHub 认证失败", want_json)

    store = _get_store(request)
    return _oauth_success_response(store, info, want_json)


# ── OAuth: Google ─────────────────────────────────────────


@router.get("/oauth/google")
async def oauth_google_redirect(request: Request) -> Any:
    state = f"google:{secrets.token_urlsafe(32)}"
    url = google_authorize_url(state=state, config_store=_get_config_store(request))
    return JSONResponse({"authorize_url": url, "state": state})


@router.get("/oauth/google/callback")
async def oauth_google_callback(
    code: str,
    state: str | None = None,
    request: Request = ...,  # type: ignore[assignment]
) -> Any:
    want_json = _wants_json(request)
    info = await google_exchange_code(code, config_store=_get_config_store(request))
    if info is None:
        return _oauth_error_response("Google 认证失败", want_json)

    store = _get_store(request)
    return _oauth_success_response(store, info, want_json)


# ── OAuth: QQ ─────────────────────────────────────────────


@router.get("/oauth/qq")
async def oauth_qq_redirect(request: Request) -> Any:
    state = f"qq:{secrets.token_urlsafe(32)}"
    url = qq_authorize_url(state=state, config_store=_get_config_store(request))
    return JSONResponse({"authorize_url": url, "state": state})


@router.get("/oauth/qq/callback")
async def oauth_qq_callback(
    code: str,
    state: str | None = None,
    request: Request = ...,  # type: ignore[assignment]
) -> Any:
    want_json = _wants_json(request)
    info = await qq_exchange_code(code, config_store=_get_config_store(request))
    if info is None:
        return _oauth_error_response("QQ 认证失败", want_json)

    store = _get_store(request)
    return _oauth_success_response(store, info, want_json)


def _wants_json(request: Request) -> bool:
    """判断客户端是否期望 JSON（API 调用）而非浏览器重定向。"""
    accept = request.headers.get("accept", "")
    return "application/json" in accept


def _oauth_error_response(message: str, want_json: bool = False):
    """OAuth 失败：JSON 或重定向。"""
    if want_json:
        raise HTTPException(status_code=401, detail=message)
    from urllib.parse import quote
    return RedirectResponse(f"/auth/callback?error={quote(message)}")


def _oauth_success_response(store: UserStore, info: Any, want_json: bool = False):
    """将 OAuth 信息换取 JWT 令牌，返回 JSON 或重定向到前端。"""
    try:
        token_resp = _handle_oauth_user(store, info)
    except HTTPException as exc:
        return _oauth_error_response(exc.detail or "认证失败", want_json)

    if want_json:
        return JSONResponse({
            "access_token": token_resp.access_token,
            "refresh_token": token_resp.refresh_token,
            "token_type": "bearer",
            "expires_in": token_resp.expires_in,
            "user": token_resp.user.model_dump(),
        })

    from urllib.parse import urlencode
    params = urlencode({
        "access_token": token_resp.access_token,
        "refresh_token": token_resp.refresh_token,
        "provider": info.provider,
    })
    return RedirectResponse(f"/auth/callback?{params}")


def _handle_oauth_user(store: UserStore, info: Any) -> TokenResponse:
    """根据 OAuth 信息查找或创建用户，返回令牌对。"""
    # 检查用户是否已通过 OAuth 关联
    user = store.get_by_oauth(info.provider, info.oauth_id)
    if user is not None:
        if not user.is_active:
            raise HTTPException(403, "账户已被禁用")
        return _build_token_response(user)

    # 检查邮箱是否已注册（关联 OAuth）
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

    # 创建新用户
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


# ── 头像代理（绕过 GFW） ─────────────────────────────


# 允许代理的头像域名白名单
_AVATAR_ALLOWED_DOMAINS = {
    "lh3.googleusercontent.com",
    "avatars.githubusercontent.com",
    "thirdqq.qlogo.cn",
    "q.qlogo.cn",
}


@router.get("/avatar-proxy")
async def avatar_proxy(url: str) -> Any:
    """代理外部头像 URL，解决浏览器直连被 GFW 屏蔽的问题。"""
    from urllib.parse import urlparse
    from fastapi.responses import Response
    import httpx

    if not url.startswith("https://"):
        raise HTTPException(400, "仅允许 HTTPS URL")

    domain = urlparse(url).hostname
    if domain not in _AVATAR_ALLOWED_DOMAINS:
        raise HTTPException(400, f"域名 {domain} 不在白名单中")

    from excelmanus.auth.oauth import _get_oauth_proxy
    proxy = _get_oauth_proxy()
    try:
        async with httpx.AsyncClient(timeout=10, proxy=proxy) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                raise HTTPException(502, "获取头像失败")
            return Response(
                content=resp.content,
                media_type=resp.headers.get("content-type", "image/jpeg"),
                headers={"Cache-Control": "public, max-age=86400"},
            )
    except httpx.TimeoutException:
        raise HTTPException(504, "获取头像超时")
    except httpx.RequestError as exc:
        logger.warning("Avatar proxy failed: %s", exc)
        raise HTTPException(502, "获取头像失败")


# ── 设置密码（OAuth 用户） ────────────────────────────


@router.post("/me/set-password")
async def set_password(
    body: SetPasswordRequest,
    request: Request,
    user: UserRecord = Depends(get_current_user),
) -> Any:
    """为 OAuth 注册的用户设置密码（仅限尚无密码的用户）。"""
    if user.password_hash:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="密码已存在，请使用忘记密码功能重置",
        )

    store = _get_store(request)
    new_hash = hash_password(body.new_password)
    store.update_user(user.id, password_hash=new_hash)
    logger.info("OAuth 用户设置密码: %s", user.email)

    updated = store.get_by_id(user.id)
    return {"status": "ok", "user": UserPublic.from_record(updated or user).model_dump()}


# ── 管理员：用户管理 ────────────────────────────────


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
        ws = IsolatedWorkspace.resolve(ws_root, user_id=u.id, auth_enabled=True)
        usage = ws.get_usage()
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

    allowed_fields = {"role", "is_active", "daily_token_limit", "monthly_token_limit", "display_name", "allowed_models"}
    updates = {k: v for k, v in body.items() if k in allowed_fields}
    if not updates:
        raise HTTPException(400, "无有效更新字段")

    # allowed_models: 前端传 list，存储为 JSON 字符串
    if "allowed_models" in updates:
        import json as _json
        val = updates["allowed_models"]
        if isinstance(val, list):
            updates["allowed_models"] = _json.dumps(val) if val else None
        elif val is None or val == "":
            updates["allowed_models"] = None

    store.update_user(user_id, **updates)
    updated = store.get_by_id(user_id)
    return {"status": "ok", "user": UserPublic.from_record(updated or target)}


@router.delete("/admin/users/{user_id}/workspace")
async def admin_clear_user_workspace(
    user_id: str,
    request: Request,
    _admin: UserRecord = Depends(require_admin),
) -> Any:
    """删除用户工作区中的所有文件。"""
    store = _get_store(request)
    target = store.get_by_id(user_id)
    if target is None:
        raise HTTPException(404, "用户不存在")

    ws_root = _get_workspace_root(request)
    ws = IsolatedWorkspace.resolve(ws_root, user_id=user_id, auth_enabled=True)

    import shutil
    from pathlib import Path
    ws_path = ws.root_dir
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
    """强制执行用户工作区配额。"""
    store = _get_store(request)
    target = store.get_by_id(user_id)
    if target is None:
        raise HTTPException(404, "用户不存在")

    ws_root = _get_workspace_root(request)
    ws = IsolatedWorkspace.resolve(ws_root, user_id=user_id, auth_enabled=True)
    deleted = ws.enforce_quota()
    usage = ws.get_usage()
    return {"status": "ok", "deleted": deleted, "workspace": usage.to_dict()}


# ── 管理员：登录配置 ────────────────────────────────


# 布尔开关 key → 默认值
_LOGIN_TOGGLE_KEYS: dict[str, bool] = {
    "login_github_enabled": True,
    "login_google_enabled": True,
    "login_qq_enabled": False,
    "email_verify_required": False,
}

# 字符串凭据 key → 对应环境变量名
_LOGIN_CREDENTIAL_KEYS: dict[str, str] = {
    "github_client_id":     "EXCELMANUS_GITHUB_CLIENT_ID",
    "github_client_secret": "EXCELMANUS_GITHUB_CLIENT_SECRET",
    "github_redirect_uri":  "EXCELMANUS_GITHUB_REDIRECT_URI",
    "google_client_id":     "EXCELMANUS_GOOGLE_CLIENT_ID",
    "google_client_secret": "EXCELMANUS_GOOGLE_CLIENT_SECRET",
    "google_redirect_uri":  "EXCELMANUS_GOOGLE_REDIRECT_URI",
    "qq_client_id":         "EXCELMANUS_QQ_CLIENT_ID",
    "qq_client_secret":     "EXCELMANUS_QQ_CLIENT_SECRET",
    "qq_redirect_uri":      "EXCELMANUS_QQ_REDIRECT_URI",
    "email_resend_api_key": "EXCELMANUS_RESEND_API_KEY",
    "email_smtp_host":      "EXCELMANUS_SMTP_HOST",
    "email_smtp_port":      "EXCELMANUS_SMTP_PORT",
    "email_smtp_user":      "EXCELMANUS_SMTP_USER",
    "email_smtp_password":  "EXCELMANUS_SMTP_PASSWORD",
    "email_from":           "EXCELMANUS_EMAIL_FROM",
}

# 需要脱敏的 key（GET 时只返回 ****xxxx 格式）
_SECRET_KEYS = {"github_client_secret", "google_client_secret", "qq_client_secret", "email_resend_api_key", "email_smtp_password"}


def _mask_secret(value: str) -> str:
    """将敏感值脱敏：保留最后 4 位，其余用 * 替代。"""
    if not value or len(value) <= 4:
        return "*" * len(value) if value else ""
    return "*" * (len(value) - 4) + value[-4:]


def _get_config_store(request: Request):
    return getattr(request.app.state, "config_store", None)


def get_login_config(request: Request) -> dict[str, Any]:
    """读取登录配置（开关 + 凭据），config_kv 优先，否则回退环境变量。"""
    import os
    store = _get_config_store(request)
    result: dict[str, Any] = {}

    # 布尔开关
    for key, default_val in _LOGIN_TOGGLE_KEYS.items():
        if store is not None:
            raw = store.get(key, "")
            if raw:
                result[key] = raw.lower() in ("1", "true", "yes")
                continue
        if key == "email_verify_required":
            env = os.environ.get("EXCELMANUS_EMAIL_VERIFY_REQUIRED", "").strip().lower()
            result[key] = env in ("1", "true", "yes") and is_email_configured(store)
        else:
            result[key] = default_val

    # 字符串凭据
    for key, env_name in _LOGIN_CREDENTIAL_KEYS.items():
        val = ""
        if store is not None:
            val = store.get(key, "")
        if not val:
            val = os.environ.get(env_name, "").strip()
        # GET 时脱敏
        result[key] = _mask_secret(val) if key in _SECRET_KEYS else val

    return result


def _get_credential_raw(request: Request, key: str) -> str:
    """读取凭据原始值（不脱敏），供内部逻辑使用。"""
    import os
    store = _get_config_store(request)
    if store is not None:
        val = store.get(key, "")
        if val:
            return val
    env_name = _LOGIN_CREDENTIAL_KEYS.get(key, "")
    return os.environ.get(env_name, "").strip() if env_name else ""


@router.get("/admin/login-config")
async def admin_get_login_config(
    request: Request,
    _admin: UserRecord = Depends(require_admin),
) -> Any:
    """获取登录方式配置（GitHub/Google/邮箱验证 + 凭据）。"""
    return get_login_config(request)


@router.put("/admin/login-config")
async def admin_update_login_config(
    request: Request,
    _admin: UserRecord = Depends(require_admin),
) -> Any:
    """更新登录方式配置。"""
    store = _get_config_store(request)
    if store is None:
        raise HTTPException(503, "配置存储未初始化")

    body = await request.json()
    updated: dict[str, Any] = {}

    # 布尔开关
    for key in _LOGIN_TOGGLE_KEYS:
        if key in body:
            val = bool(body[key])
            store.set(key, "true" if val else "false")
            updated[key] = val

    # 字符串凭据（跳过脱敏占位值，即全是 * 的值）
    for key in _LOGIN_CREDENTIAL_KEYS:
        if key in body:
            val = str(body[key]).strip()
            # 如果前端回传的是脱敏值，跳过不写入
            if val and not all(c == "*" for c in val):
                store.set(key, val)
                updated[key] = _mask_secret(val) if key in _SECRET_KEYS else val

    if not updated:
        raise HTTPException(400, "无有效更新字段")

    logger.info("管理员更新登录配置: %s", {k: v for k, v in updated.items() if k not in _SECRET_KEYS})
    return {"status": "ok", **get_login_config(request)}
