"""认证 API 路由 — 注册、登录、OAuth、用户资料管理。"""

from __future__ import annotations

import logging
import secrets
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse, RedirectResponse

from excelmanus.auth.dependencies import get_current_user, get_current_user_optional, require_admin
from excelmanus.auth.models import (
    ChangeEmailRequest,
    ChangePasswordRequest,
    ConfirmMergeRequest,
    ForgotPasswordRequest,
    LoginRequest,
    MergeRequiredResponse,
    OAuthCallbackParams,
    OAuthLinkInfo,
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
    create_merge_token,
    create_oauth_state,
    create_token_pair,
    decode_merge_token,
    decode_token,
    hash_password,
    verify_oauth_state,
    verify_password,
)
from excelmanus.auth.store import UserStore
from excelmanus.workspace import IsolatedWorkspace, QuotaPolicy

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


def _get_data_root(request: Request) -> str:
    dr = getattr(request.app.state, "data_root", None)
    if dr:
        return dr
    import os
    return os.environ.get("EXCELMANUS_DATA_ROOT", "")


def _build_token_response(user: UserRecord, store: UserStore | None = None) -> TokenResponse:
    access, refresh, expires_in = create_token_pair(user.id, user.role)
    oauth_providers: list[str] = []
    if store is not None:
        links = store.get_oauth_links(user.id)
        oauth_providers = [link["provider"] for link in links]
    return TokenResponse(
        access_token=access,
        refresh_token=refresh,
        expires_in=expires_in,
        user=UserPublic.from_record(user, oauth_providers=oauth_providers),
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


def _parse_origin_list(raw: str) -> list[str]:
    """解析逗号分隔的 origin 列表，裸域名自动补全 https://。"""
    origins: list[str] = []
    for o in raw.split(","):
        o = o.strip()
        if not o:
            continue
        # 裸域名自动补全 https://，已有 scheme 的保持不变
        if not o.startswith("http://") and not o.startswith("https://"):
            o = f"https://{o}"
        origins.append(o.rstrip("/"))
    return origins


def _get_allowed_oauth_origins() -> list[str]:
    """获取 OAuth 回调允许的完整来源白名单。
    
    优先级：
    1. EXCELMANUS_ALLOWED_OAUTH_ORIGINS（显式 OAuth 白名单）
    2. EXCELMANUS_CORS_ALLOW_ORIGINS（自动从 CORS 配置派生，减少重复配置）
    3. 硬编码默认值
    
    支持完整 URL（如 https://kilon.top）或裸域名（自动补全 https://）。
    """
    import os
    # 1) 优先使用显式 OAuth 白名单
    raw = os.environ.get("EXCELMANUS_ALLOWED_OAUTH_ORIGINS", "").strip()
    if raw:
        return _parse_origin_list(raw)
    # 2) 自动从 CORS origins 派生（过滤掉 * 通配符和纯 IP LAN 地址）
    cors_raw = os.environ.get("EXCELMANUS_CORS_ALLOW_ORIGINS", "").strip()
    if cors_raw:
        all_origins = _parse_origin_list(cors_raw)
        # 过滤掉 localhost / 127.x / 纯 IP 地址（它们通常是开发环境，不应做 OAuth 回跳）
        import re
        _ip_pattern = re.compile(r"https?://(\d{1,3}\.){3}\d{1,3}(:\d+)?$")
        filtered = [
            o for o in all_origins
            if not _ip_pattern.match(o) and "localhost" not in o
        ]
        if filtered:
            return filtered
    # 3) 硬编码默认值
    return ["https://kilon.top", "https://excelmanus.com"]


def _default_oauth_origin() -> str:
    """获取默认的 OAuth 回跳来源（白名单第一项）。"""
    allowed = _get_allowed_oauth_origins()
    return allowed[0] if allowed else "https://kilon.top"


def _resolve_oauth_origin(origin_param: str | None, request: Request) -> str:
    """从请求中解析 OAuth 回跳来源 URL。
    
    优先级：origin 查询参数 > Referer 头 > 默认值。
    结果必须在白名单内，否则回退到默认值。
    """
    from urllib.parse import urlparse

    raw = (origin_param or "").strip()
    if not raw:
        raw = request.headers.get("referer", "").strip()
    
    if raw:
        parsed = urlparse(raw)
        if parsed.scheme and parsed.netloc:
            # 提取 scheme://host[:port]（去掉路径部分）
            candidate = f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
        elif not parsed.scheme and parsed.path:
            # 裸域名，补全 https://
            candidate = f"https://{parsed.path.split('/')[0]}"
        else:
            candidate = ""
    else:
        candidate = ""
    
    allowed = _get_allowed_oauth_origins()
    if candidate and candidate in allowed:
        return candidate
    return _default_oauth_origin()


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
    ws = IsolatedWorkspace.resolve(ws_root, user_id=user.id, auth_enabled=True, data_root=_get_data_root(request))
    return ws.get_usage().to_dict()


# ── OAuth: GitHub ─────────────────────────────────────────


@router.get("/oauth/github")
async def oauth_github_redirect(request: Request, origin: str | None = None) -> Any:
    """发起 GitHub OAuth 登录。
    
    Args:
        origin: 发起登录的来源（如 https://excelmanus.com），用于回调后重定向回原来源
    """
    origin_url = _resolve_oauth_origin(origin, request)
    state = create_oauth_state("github", origin_url)
    url = github_authorize_url(state=state, config_store=_get_config_store(request))
    return JSONResponse({"authorize_url": url, "state": state})


@router.get("/oauth/github/callback")
async def oauth_github_callback(
    code: str,
    state: str | None = None,
    request: Request = ...,  # type: ignore[assignment]
) -> Any:
    """​GitHub OAuth 回调，验证 state 并重定向到原来源。
    
    支持两种模式：
    - 浏览器重定向（默认）：返回 302 重定向到前端 /auth/callback
    - JSON API 调用（Accept: application/json）：直接返回 JSON 响应
    """
    json_mode = _wants_json(request)
    fallback = _default_oauth_origin()
    allowed = _get_allowed_oauth_origins()
    verified = verify_oauth_state(state or "", allowed) if state else None
    
    if verified is None:
        logger.warning("GitHub OAuth state 验证失败: state=%s", state)
        if json_mode:
            raise HTTPException(400, "无效的 state 参数")
        return _oauth_error_response("无效的 state 参数", origin=fallback)
    
    provider, origin = verified
    if provider != "github":
        logger.warning("GitHub OAuth state provider 不匹配: expected=github got=%s", provider)
        if json_mode:
            raise HTTPException(400, "state provider 不匹配")
        return _oauth_error_response("state provider 不匹配", origin=origin)
    
    info = await github_exchange_code(code, config_store=_get_config_store(request))
    if info is None:
        if json_mode:
            raise HTTPException(400, "GitHub 认证失败")
        return _oauth_error_response("GitHub 认证失败", origin=origin)
    
    store = _get_store(request)
    if json_mode:
        return _oauth_json_response(store, info)
    return _oauth_success_response(store, info, origin=origin)


# ── OAuth: Google ─────────────────────────────────────────


@router.get("/oauth/google")
async def oauth_google_redirect(request: Request, origin: str | None = None) -> Any:
    """发起 Google OAuth 登录。
    
    Args:
        origin: 发起登录的来源（如 https://excelmanus.com），用于回调后重定向回原来源
    """
    origin_url = _resolve_oauth_origin(origin, request)
    state = create_oauth_state("google", origin_url)
    url = google_authorize_url(state=state, config_store=_get_config_store(request))
    return JSONResponse({"authorize_url": url, "state": state})


@router.get("/oauth/google/callback")
async def oauth_google_callback(
    code: str,
    state: str | None = None,
    request: Request = ...,  # type: ignore[assignment]
) -> Any:
    """Google OAuth 回调，验证 state 并重定向到原来源。
    
    支持两种模式：
    - 浏览器重定向（默认）：返回 302 重定向到前端 /auth/callback
    - JSON API 调用（Accept: application/json）：直接返回 JSON 响应
    """
    json_mode = _wants_json(request)
    fallback = _default_oauth_origin()
    allowed = _get_allowed_oauth_origins()
    verified = verify_oauth_state(state or "", allowed) if state else None
    
    if verified is None:
        logger.warning("Google OAuth state 验证失败: state=%s", state)
        if json_mode:
            raise HTTPException(400, "无效的 state 参数")
        return _oauth_error_response("无效的 state 参数", origin=fallback)
    
    provider, origin = verified
    if provider != "google":
        logger.warning("Google OAuth state provider 不匹配: expected=google got=%s", provider)
        if json_mode:
            raise HTTPException(400, "state provider 不匹配")
        return _oauth_error_response("state provider 不匹配", origin=origin)
    
    info = await google_exchange_code(code, config_store=_get_config_store(request))
    if info is None:
        if json_mode:
            raise HTTPException(400, "Google 认证失败")
        return _oauth_error_response("Google 认证失败", origin=origin)
    
    store = _get_store(request)
    if json_mode:
        return _oauth_json_response(store, info)
    return _oauth_success_response(store, info, origin=origin)


# ── OAuth: QQ ─────────────────────────────────────────────


@router.get("/oauth/qq")
async def oauth_qq_redirect(request: Request, origin: str | None = None) -> Any:
    """发起 QQ OAuth 登录。
    
    Args:
        origin: 发起登录的来源（如 https://excelmanus.com），用于回调后重定向回原来源
    """
    origin_url = _resolve_oauth_origin(origin, request)
    state = create_oauth_state("qq", origin_url)
    url = qq_authorize_url(state=state, config_store=_get_config_store(request))
    return JSONResponse({"authorize_url": url, "state": state})


@router.get("/oauth/qq/callback")
async def oauth_qq_callback(
    code: str,
    state: str | None = None,
    request: Request = ...,  # type: ignore[assignment]
) -> Any:
    """QQ OAuth 回调，验证 state 并重定向到原来源。
    
    支持两种模式：
    - 浏览器重定向（默认）：返回 302 重定向到前端 /auth/callback
    - JSON API 调用（Accept: application/json）：直接返回 JSON 响应
    """
    json_mode = _wants_json(request)
    fallback = _default_oauth_origin()
    allowed = _get_allowed_oauth_origins()
    verified = verify_oauth_state(state or "", allowed) if state else None
    
    if verified is None:
        logger.warning("QQ OAuth state 验证失败: state=%s", state)
        if json_mode:
            raise HTTPException(400, "无效的 state 参数")
        return _oauth_error_response("无效的 state 参数", origin=fallback)
    
    provider, origin = verified
    if provider != "qq":
        logger.warning("QQ OAuth state provider 不匹配: expected=qq got=%s", provider)
        if json_mode:
            raise HTTPException(400, "state provider 不匹配")
        return _oauth_error_response("state provider 不匹配", origin=origin)
    
    info = await qq_exchange_code(code, config_store=_get_config_store(request))
    if info is None:
        if json_mode:
            raise HTTPException(400, "QQ 认证失败")
        return _oauth_error_response("QQ 认证失败", origin=origin)
    
    store = _get_store(request)
    if json_mode:
        return _oauth_json_response(store, info)
    return _oauth_success_response(store, info, origin=origin)


def _wants_json(request: Request) -> bool:
    """判断客户端是否期望 JSON（API 调用）而非浏览器重定向。"""
    accept = request.headers.get("accept", "")
    return "application/json" in accept


def _oauth_json_response(store: UserStore, info: Any) -> Any:
    """OAuth 回调 JSON 模式：前端通过 fetch() 直接调用时，返回 JSON 而非重定向。

    避免 fetch() 跟随 RedirectResponse 到不同 origin 导致 CORS "Failed to fetch" 错误。
    """
    try:
        result = _handle_oauth_user(store, info)
    except HTTPException:
        raise

    if isinstance(result, MergeRequiredResponse):
        return JSONResponse(result.model_dump())

    # TokenResponse — 直接返回 JSON
    return JSONResponse(result.model_dump())


def _oauth_error_response(message: str, origin: str = "https://kilon.top"):
    """OAuth 失败：重定向到指定来源的 /auth/callback。
    
    Args:
        message: 错误消息
        origin: 目标来源（如 https://kilon.top 或 http://localhost:3000）
    """
    from urllib.parse import quote
    return RedirectResponse(f"{origin.rstrip('/')}/auth/callback?error={quote(message)}")


def _oauth_success_response(store: UserStore, info: Any, origin: str = "https://kilon.top"):
    """将 OAuth 信息换取 JWT 令牌，重定向到指定来源的前端。

    如果检测到需要合并（同邮箱已有账号），返回 merge_required 响应。
    
    Args:
        store: 用户存储
        info: OAuth 用户信息
        origin: 目标来源（如 https://kilon.top 或 http://localhost:3000）
    """
    base = origin.rstrip("/")
    try:
        result = _handle_oauth_user(store, info)
    except HTTPException as exc:
        return _oauth_error_response(exc.detail or "认证失败", origin=origin)

    # 合并场景：返回 MergeRequiredResponse
    # 使用 hash fragment (#) 传递敏感令牌，避免浏览器历史/Referer/日志泄漏
    if isinstance(result, MergeRequiredResponse):
        from urllib.parse import urlencode
        params = urlencode({
            "merge_required": "1",
            "merge_token": result.merge_token,
            "existing_email": result.existing_email,
            "existing_display_name": result.existing_display_name,
            "existing_has_password": "1" if result.existing_has_password else "0",
            "existing_providers": ",".join(result.existing_providers),
            "new_provider": result.new_provider,
            "new_provider_display_name": result.new_provider_display_name,
        })
        return RedirectResponse(f"{base}/auth/callback#{params}")

    # 正常登录 — token 放入 hash fragment（# 后的内容不会发送给服务器）
    token_resp = result
    from urllib.parse import urlencode
    params = urlencode({
        "access_token": token_resp.access_token,
        "refresh_token": token_resp.refresh_token,
        "provider": info.provider,
    })
    return RedirectResponse(f"{base}/auth/callback#{params}")


def _handle_oauth_user(
    store: UserStore, info: Any,
) -> TokenResponse | MergeRequiredResponse:
    """根据 OAuth 信息查找或创建用户。

    返回 TokenResponse（直接登录）或 MergeRequiredResponse（需要前端确认合并）。
    """
    # 1) 检查是否已绑定此 OAuth
    user = store.get_by_oauth(info.provider, info.oauth_id)
    if user is not None:
        if not user.is_active:
            raise HTTPException(403, "账户已被禁用")
        return _build_token_response(user, store)

    # 2) 检查邮箱是否已被其他账号使用 → 需要合并确认
    existing = store.get_by_email(info.email)
    if existing is not None:
        if not existing.is_active:
            raise HTTPException(403, "该邮箱关联的账户已被禁用")
        # 获取已有绑定列表
        existing_links = store.get_oauth_links(existing.id)
        existing_providers = [link["provider"] for link in existing_links]
        # 生成短效合并令牌
        merge_token = create_merge_token(
            existing_user_id=existing.id,
            provider=info.provider,
            oauth_id=info.oauth_id,
            email=info.email,
            display_name=info.display_name or "",
            avatar_url=info.avatar_url,
        )
        logger.info(
            "OAuth 合并待确认: email=%s provider=%s existing_user=%s",
            info.email, info.provider, existing.id,
        )
        return MergeRequiredResponse(
            merge_token=merge_token,
            existing_email=existing.email,
            existing_display_name=existing.display_name,
            existing_providers=existing_providers,
            existing_has_password=bool(existing.password_hash),
            new_provider=info.provider,
            new_provider_display_name=info.display_name or "",
            new_provider_avatar_url=info.avatar_url,
        )

    # 3) 完全新用户 → 创建用户 + OAuth 绑定
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
    # 同时在 user_oauth_links 表创建绑定
    store.create_oauth_link(
        user_id=user.id,
        provider=info.provider,
        oauth_id=info.oauth_id,
        display_name=info.display_name,
        avatar_url=info.avatar_url,
    )
    logger.info("OAuth 用户注册: %s via %s", info.email, info.provider)
    return _build_token_response(user, store)


# ── 账号合并确认 ─────────────────────────────────────


@router.post("/oauth/confirm-merge", response_model=TokenResponse)
async def confirm_merge(body: ConfirmMergeRequest, request: Request) -> Any:
    """前端确认合并后调用：将 OAuth 绑定添加到已有账号。"""
    payload = decode_merge_token(body.merge_token)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="合并令牌无效或已过期，请重新登录",
        )

    store = _get_store(request)
    user_id = payload["sub"]
    provider = payload["provider"]
    oauth_id = payload["oauth_id"]
    display_name = payload.get("display_name", "")
    avatar_url = payload.get("avatar_url") or None

    user = store.get_by_id(user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=404, detail="用户不存在或已禁用")

    # 检查是否已绑定（幂等）
    existing_link = store.get_oauth_link(provider, oauth_id)
    if existing_link is None:
        store.create_oauth_link(
            user_id=user_id,
            provider=provider,
            oauth_id=oauth_id,
            display_name=display_name,
            avatar_url=avatar_url,
        )

    # 如果用户没有头像，用 OAuth 提供的
    if not user.avatar_url and avatar_url:
        store.update_user(user_id, avatar_url=avatar_url)
        user = store.get_by_id(user_id) or user

    logger.info("OAuth 合并完成: user=%s provider=%s", user.email, provider)
    return _build_token_response(user, store)


# ── OAuth Links 管理（个人中心） ──────────────────────


@router.get("/me/oauth-links")
async def list_oauth_links(
    request: Request,
    user: UserRecord = Depends(get_current_user),
) -> Any:
    """列出当前用户已绑定的 OAuth 登录方式。"""
    store = _get_store(request)
    links = store.get_oauth_links(user.id)
    return {
        "links": [
            OAuthLinkInfo(
                provider=link["provider"],
                display_name=link.get("display_name"),
                avatar_url=link.get("avatar_url"),
                linked_at=link["linked_at"],
            ).model_dump()
            for link in links
        ],
        "has_password": bool(user.password_hash),
    }


@router.delete("/me/oauth-links/{provider}")
async def unlink_oauth(
    provider: str,
    request: Request,
    user: UserRecord = Depends(get_current_user),
) -> Any:
    """解绑某个 OAuth 登录方式。至少保留一种登录方式（密码或 OAuth）。"""
    store = _get_store(request)
    link_count = store.count_oauth_links(user.id)
    has_password = bool(user.password_hash)

    # 安全检查：不能解绑到零登录方式
    if not has_password and link_count <= 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="无法解绑：您需要至少保留一种登录方式。请先设置密码，再解绑此账号。",
        )

    deleted = store.delete_oauth_link(user.id, provider)
    if not deleted:
        raise HTTPException(status_code=404, detail="未找到该绑定")

    logger.info("OAuth 解绑: user=%s provider=%s", user.email, provider)
    # 返回更新后的绑定列表
    links = store.get_oauth_links(user.id)
    return {
        "status": "ok",
        "links": [
            OAuthLinkInfo(
                provider=link["provider"],
                display_name=link.get("display_name"),
                avatar_url=link.get("avatar_url"),
                linked_at=link["linked_at"],
            ).model_dump()
            for link in links
        ],
    }


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


# ── 修改密码（已有密码的用户） ────────────────────────


@router.post("/me/change-password")
async def change_password(
    body: ChangePasswordRequest,
    request: Request,
    user: UserRecord = Depends(get_current_user),
) -> Any:
    """已有密码的用户修改密码，需要验证旧密码。"""
    if not user.password_hash:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="当前账户尚未设置密码，请使用设置密码功能",
        )

    if not verify_password(body.old_password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="旧密码错误",
        )

    store = _get_store(request)
    new_hash = hash_password(body.new_password)
    store.update_user(user.id, password_hash=new_hash)
    logger.info("用户修改密码: %s", user.email)

    updated = store.get_by_id(user.id)
    return {"status": "ok", "user": UserPublic.from_record(updated or user).model_dump()}


# ── 修改邮箱 ────────────────────────────────────────


@router.post("/me/change-email")
async def change_email(
    body: ChangeEmailRequest,
    request: Request,
    user: UserRecord = Depends(get_current_user),
) -> Any:
    """修改邮箱，需要验证密码。"""
    if not user.password_hash:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="请先设置密码后再修改邮箱",
        )

    if not verify_password(body.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="密码错误",
        )

    store = _get_store(request)

    existing = store.get_by_email(body.new_email)
    if existing is not None and existing.id != user.id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="该邮箱已被其他用户使用",
        )

    store.update_user(user.id, email=body.new_email)
    logger.info("用户修改邮箱: %s -> %s", user.email, body.new_email)

    updated = store.get_by_id(user.id)
    return {"status": "ok", "user": UserPublic.from_record(updated or user).model_dump()}


# ── 头像上传（压缩后存储在用户工作区） ──────────────


@router.post("/me/avatar")
async def upload_avatar(
    request: Request,
    user: UserRecord = Depends(get_current_user),
) -> Any:
    """上传头像图片，自动压缩后保存在用户工作区的 .avatars 隐藏目录中。"""
    from fastapi import UploadFile
    import io

    content_type = request.headers.get("content-type", "")
    if "multipart/form-data" not in content_type:
        raise HTTPException(400, "请使用 multipart/form-data 上传")

    from starlette.formparsers import MultiPartParser
    form = await request.form()
    file = form.get("avatar")
    if file is None or not hasattr(file, "read"):
        raise HTTPException(400, "缺少 avatar 文件字段")

    data = await file.read()
    if len(data) > 10 * 1024 * 1024:
        raise HTTPException(400, "头像文件不得超过 10MB")

    # 检测文件类型
    fname = getattr(file, "filename", "avatar.jpg") or "avatar.jpg"
    ext = fname.rsplit(".", 1)[-1].lower() if "." in fname else "jpg"
    if ext not in ("jpg", "jpeg", "png", "gif", "webp"):
        raise HTTPException(400, "仅支持 jpg/png/gif/webp 格式")

    # 压缩图片
    compressed, out_ext = _compress_avatar(data, ext)

    # 存储到用户工作区的 .avatars 目录（不在常规文件列表中显示）
    ws_root = _get_workspace_root(request)
    ws = IsolatedWorkspace.resolve(ws_root, user_id=user.id, auth_enabled=True, data_root=_get_data_root(request))

    # 检查配额
    allowed, reason = ws.check_upload_allowed(len(compressed))
    if not allowed:
        raise HTTPException(413, reason)

    avatar_dir = ws.root_dir / ".avatars"
    avatar_dir.mkdir(parents=True, exist_ok=True)

    # 清理旧头像
    for old in avatar_dir.glob("avatar.*"):
        old.unlink(missing_ok=True)

    avatar_filename = f"avatar.{out_ext}"
    avatar_path = avatar_dir / avatar_filename
    avatar_path.write_bytes(compressed)

    # 生成可访问的 URL
    avatar_url = f"/api/v1/auth/me/avatar-file?t={int(__import__('time').time())}"

    store = _get_store(request)
    store.update_user(user.id, avatar_url=avatar_url)

    logger.info(
        "用户上传头像: %s (原始 %d bytes -> 压缩 %d bytes)",
        user.email, len(data), len(compressed),
    )

    updated = store.get_by_id(user.id)
    return {
        "status": "ok",
        "avatar_url": avatar_url,
        "user": UserPublic.from_record(updated or user).model_dump(),
    }


def _compress_avatar(data: bytes, ext: str, max_size: int = 256) -> tuple[bytes, str]:
    """将头像图片压缩为最大 max_size x max_size 的 JPEG/WebP。"""
    import io
    try:
        from PIL import Image
    except ImportError:
        # 如果未安装 Pillow，直接返回原始数据
        return data, ext

    img = Image.open(io.BytesIO(data))

    # 处理 EXIF 旋转
    try:
        from PIL import ImageOps
        img = ImageOps.exif_transpose(img)
    except Exception:
        pass

    # 转为 RGB（处理 RGBA、P 等模式）
    if img.mode in ("RGBA", "LA", "P"):
        background = Image.new("RGB", img.size, (255, 255, 255))
        if img.mode == "P":
            img = img.convert("RGBA")
        background.paste(img, mask=img.split()[-1] if "A" in img.mode else None)
        img = background
    elif img.mode != "RGB":
        img = img.convert("RGB")

    # 居中裁剪为正方形
    w, h = img.size
    side = min(w, h)
    left = (w - side) // 2
    top = (h - side) // 2
    img = img.crop((left, top, left + side, top + side))

    # 缩放
    if img.size[0] > max_size:
        img = img.resize((max_size, max_size), Image.LANCZOS)

    # 输出为 JPEG
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85, optimize=True)
    return buf.getvalue(), "jpg"


@router.get("/me/avatar-file")
async def get_avatar_file(
    request: Request,
    token: str | None = None,
    t: str | None = None,  # 时间戳参数，用于绕过缓存
    user: UserRecord | None = Depends(get_current_user_optional),
) -> Any:
    """返回当前用户的头像文件。

    支持 query param ``?token=xxx`` 认证，兼容 ``<img>`` 标签无法发送 Authorization header 的场景。
    
    如果 URL 包含时间戳参数 ``?t=xxx``（表示新上传的头像），则设置不缓存的响应头。
    """
    from fastapi.responses import Response

    # query param token 兜底（<img> 标签场景）
    if user is None and token:
        payload = decode_token(token)
        if payload and payload.get("type") == "access":
            user_id = payload.get("sub")
            if user_id:
                store = _get_store(request)
                user = store.get_by_id(user_id)

    if user is None or not user.is_active:
        raise HTTPException(401, "未认证")

    ws_root = _get_workspace_root(request)
    ws = IsolatedWorkspace.resolve(ws_root, user_id=user.id, auth_enabled=True, data_root=_get_data_root(request))
    avatar_dir = ws.root_dir / ".avatars"

    for ext in ("jpg", "jpeg", "png", "webp", "gif"):
        path = avatar_dir / f"avatar.{ext}"
        if path.is_file():
            media_types = {
                "jpg": "image/jpeg", "jpeg": "image/jpeg",
                "png": "image/png", "webp": "image/webp", "gif": "image/gif",
            }
            # 如果 URL 包含时间戳参数 t，说明是新上传的头像，设置不缓存
            if t is not None:
                headers = {
                    "Cache-Control": "no-cache, no-store, must-revalidate",
                    "Pragma": "no-cache",
                    "Expires": "0",
                }
            else:
                headers = {"Cache-Control": "public, max-age=86400"}
            return Response(
                content=path.read_bytes(),
                media_type=media_types.get(ext, "image/jpeg"),
                headers=headers,
            )

    raise HTTPException(404, "未找到头像文件")


@router.get("/admin/users/{user_id}/avatar-file")
async def admin_get_user_avatar(
    user_id: str,
    request: Request,
    _admin: UserRecord = Depends(require_admin),
) -> Any:
    """管理员获取指定用户的头像文件。"""
    from fastapi.responses import Response

    store = _get_store(request)
    target = store.get_by_id(user_id)
    if target is None:
        raise HTTPException(404, "用户不存在")

    ws_root = _get_workspace_root(request)
    ws = IsolatedWorkspace.resolve(ws_root, user_id=target.id, auth_enabled=True, data_root=_get_data_root(request))
    avatar_dir = ws.root_dir / ".avatars"

    for ext in ("jpg", "jpeg", "png", "webp", "gif"):
        path = avatar_dir / f"avatar.{ext}"
        if path.is_file():
            media_types = {
                "jpg": "image/jpeg", "jpeg": "image/jpeg",
                "png": "image/png", "webp": "image/webp", "gif": "image/gif",
            }
            return Response(
                content=path.read_bytes(),
                media_type=media_types.get(ext, "image/jpeg"),
                headers={"Cache-Control": "public, max-age=86400"},
            )

    raise HTTPException(404, "未找到头像文件")


# ── 管理员：用户管理 ────────────────────────────────


_ADMIN_PROVIDER_LABELS: dict[str, str] = {
    "openai-codex": "OpenAI Codex",
    "openai": "OpenAI",
    "anthropic": "Anthropic Claude",
    "gemini": "Google Gemini",
    "qwen": "阿里千问",
    "deepseek": "DeepSeek",
    "grok": "xAI Grok",
    "other": "其他",
}


def _infer_provider_from_model(model_name: str) -> str:
    """根据模型名推断提供商分组，用于管理员统计展示。"""
    normalized = (model_name or "").strip().lower()
    if not normalized:
        return "other"
    if "codex" in normalized:
        return "openai-codex"
    if normalized.startswith(("gpt-", "o1", "o3", "o4", "chatgpt", "text-embedding")):
        return "openai"
    if "claude" in normalized:
        return "anthropic"
    if "gemini" in normalized:
        return "gemini"
    if "qwen" in normalized or "qwq" in normalized:
        return "qwen"
    if "deepseek" in normalized:
        return "deepseek"
    if "grok" in normalized or "xai" in normalized:
        return "grok"
    return "other"


def _empty_admin_llm_usage() -> dict[str, Any]:
    """管理员视角的空 LLM 用量结构。"""
    return {
        "total_calls": 0,
        "total_prompt_tokens": 0,
        "total_completion_tokens": 0,
        "total_tokens": 0,
        "providers": [],
    }


def _build_admin_llm_usage_index(store: UserStore, user_ids: list[str]) -> dict[str, dict[str, Any]]:
    """按用户聚合 llm_call_log：provider -> model 级别统计。"""
    dedup_user_ids = [uid for uid in dict.fromkeys(user_ids) if uid]
    if not dedup_user_ids:
        return {}

    usage_index: dict[str, dict[str, Any]] = {
        uid: {
            **_empty_admin_llm_usage(),
            "_providers": {},
        }
        for uid in dedup_user_ids
    }

    placeholders = ", ".join("?" for _ in dedup_user_ids)
    sql = (
        "SELECT user_id, model, "
        "COUNT(*) as calls, "
        "COALESCE(SUM(prompt_tokens), 0) as prompt_tokens, "
        "COALESCE(SUM(completion_tokens), 0) as completion_tokens, "
        "COALESCE(SUM(total_tokens), 0) as total_tokens, "
        "MAX(created_at) as last_used_at "
        f"FROM llm_call_log WHERE user_id IN ({placeholders}) "
        "GROUP BY user_id, model "
        "ORDER BY user_id ASC, total_tokens DESC"
    )

    try:
        rows = store._conn.execute(sql, tuple(dedup_user_ids)).fetchall()  # type: ignore[attr-defined]
    except Exception:
        logger.debug("聚合管理员 LLM 用量失败", exc_info=True)
        return {uid: _empty_admin_llm_usage() for uid in dedup_user_ids}

    for row in rows:
        user_id = str(row["user_id"] or "")
        model = str(row["model"] or "")
        if not user_id or not model:
            continue
        user_usage = usage_index.get(user_id)
        if user_usage is None:
            continue

        calls = int(row["calls"] or 0)
        prompt_tokens = int(row["prompt_tokens"] or 0)
        completion_tokens = int(row["completion_tokens"] or 0)
        total_tokens = int(row["total_tokens"] or 0)
        last_used_at = str(row["last_used_at"] or "")

        provider_key = _infer_provider_from_model(model)
        provider_map: dict[str, dict[str, Any]] = user_usage["_providers"]
        provider_usage = provider_map.setdefault(
            provider_key,
            {
                "provider": provider_key,
                "display_name": _ADMIN_PROVIDER_LABELS.get(provider_key, provider_key),
                "calls": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "models": [],
            },
        )

        provider_usage["calls"] += calls
        provider_usage["prompt_tokens"] += prompt_tokens
        provider_usage["completion_tokens"] += completion_tokens
        provider_usage["total_tokens"] += total_tokens
        provider_usage["models"].append({
            "model": model,
            "display_name": model,
            "calls": calls,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "last_used_at": last_used_at,
        })

        user_usage["total_calls"] += calls
        user_usage["total_prompt_tokens"] += prompt_tokens
        user_usage["total_completion_tokens"] += completion_tokens
        user_usage["total_tokens"] += total_tokens

    for uid, user_usage in usage_index.items():
        provider_map = user_usage.pop("_providers", {})
        providers = list(provider_map.values())
        for provider_usage in providers:
            provider_usage["models"].sort(
                key=lambda item: (item.get("total_tokens", 0), item.get("calls", 0)),
                reverse=True,
            )
        providers.sort(
            key=lambda item: (item.get("total_tokens", 0), item.get("calls", 0)),
            reverse=True,
        )
        user_usage["providers"] = providers
        usage_index[uid] = user_usage

    return usage_index


@router.get("/admin/users")
async def admin_list_users(
    request: Request,
    _admin: UserRecord = Depends(require_admin),
) -> Any:
    store = _get_store(request)
    users = store.list_users(include_inactive=True)
    ws_root = _get_workspace_root(request)
    llm_usage_index = _build_admin_llm_usage_index(store, [u.id for u in users])

    result = []
    for u in users:
        pub = UserPublic.from_record(u)
        user_quota = QuotaPolicy.for_user(u)
        ws = IsolatedWorkspace.resolve(ws_root, user_id=u.id, auth_enabled=True, data_root=_get_data_root(request))
        ws._quota = user_quota
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
            "max_storage_mb": getattr(u, "max_storage_mb", 0) or 0,
            "max_files": getattr(u, "max_files", 0) or 0,
            "workspace": usage.to_dict(),
            "llm_usage": llm_usage_index.get(u.id, _empty_admin_llm_usage()),
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

    allowed_fields = {"role", "is_active", "daily_token_limit", "monthly_token_limit", "display_name", "allowed_models", "max_storage_mb", "max_files"}
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
    ws = IsolatedWorkspace.resolve(ws_root, user_id=user_id, auth_enabled=True, data_root=_get_data_root(request))

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
    user_quota = QuotaPolicy.for_user(target)
    ws = IsolatedWorkspace.resolve(ws_root, user_id=user_id, auth_enabled=True, data_root=_get_data_root(request))
    ws._quota = user_quota
    deleted = ws.enforce_quota()
    usage = ws.get_usage()
    return {"status": "ok", "deleted": deleted, "workspace": usage.to_dict()}


@router.delete("/admin/users/{user_id}")
async def admin_delete_user(
    user_id: str,
    request: Request,
    _admin: UserRecord = Depends(require_admin),
) -> Any:
    """彻底删除用户：清空工作空间、删除所有会话、删除用户记录。"""
    if user_id == _admin.id:
        raise HTTPException(400, "不能删除自己的账户")

    store = _get_store(request)
    target = store.get_by_id(user_id)
    if target is None:
        raise HTTPException(404, "用户不存在")

    # 1) 清空工作空间
    ws_root = _get_workspace_root(request)
    ws = IsolatedWorkspace.resolve(ws_root, user_id=user_id, auth_enabled=True, data_root=_get_data_root(request))
    import shutil
    ws_path = ws.root_dir
    deleted_files = 0
    if ws_path.is_dir():
        for item in list(ws_path.rglob("*")):
            if item.is_file():
                deleted_files += 1
        shutil.rmtree(ws_path, ignore_errors=True)

    # 2) 删除所有会话（内存 + SQLite）
    deleted_sessions = 0
    session_mgr = getattr(request.app.state, "session_manager", None)
    if session_mgr is not None:
        try:
            sessions = await session_mgr.list_sessions(include_archived=True, user_id=user_id)
            for s in sessions:
                try:
                    await session_mgr.delete(s["id"], user_id=user_id)
                    deleted_sessions += 1
                except Exception:
                    pass
        except Exception:
            logger.warning("删除用户 %s 会话失败", user_id, exc_info=True)

    # 3) 删除用户记录（含 token 用量）
    store.delete_user(user_id)

    logger.info(
        "Admin deleted user %s: %d files, %d sessions removed",
        user_id, deleted_files, deleted_sessions,
    )
    return {
        "status": "ok",
        "deleted_files": deleted_files,
        "deleted_sessions": deleted_sessions,
    }


@router.get("/admin/users/{user_id}/sessions")
async def admin_list_user_sessions(
    user_id: str,
    request: Request,
    _admin: UserRecord = Depends(require_admin),
) -> Any:
    """列出用户的所有会话（仅元数据，不含消息内容）。"""
    store = _get_store(request)
    target = store.get_by_id(user_id)
    if target is None:
        raise HTTPException(404, "用户不存在")

    session_mgr = getattr(request.app.state, "session_manager", None)
    if session_mgr is None:
        return {"sessions": [], "total": 0}

    sessions = await session_mgr.list_sessions(include_archived=True, user_id=user_id)
    # 仅返回元数据：id, title, message_count, status, updated_at
    safe_sessions = [
        {
            "id": s.get("id"),
            "title": s.get("title", ""),
            "message_count": s.get("message_count", 0),
            "status": s.get("status", "active"),
            "updated_at": s.get("updated_at", ""),
        }
        for s in sessions
    ]
    return {"sessions": safe_sessions, "total": len(safe_sessions)}


@router.delete("/admin/users/{user_id}/sessions")
async def admin_delete_user_sessions(
    user_id: str,
    request: Request,
    _admin: UserRecord = Depends(require_admin),
) -> Any:
    """删除用户的所有会话。"""
    store = _get_store(request)
    target = store.get_by_id(user_id)
    if target is None:
        raise HTTPException(404, "用户不存在")

    session_mgr = getattr(request.app.state, "session_manager", None)
    if session_mgr is None:
        return {"status": "ok", "deleted_sessions": 0}

    deleted = 0
    sessions = await session_mgr.list_sessions(include_archived=True, user_id=user_id)
    for s in sessions:
        try:
            await session_mgr.delete(s["id"], user_id=user_id)
            deleted += 1
        except Exception:
            logger.warning("删除会话 %s 失败", s.get("id"), exc_info=True)

    logger.info("Admin deleted %d sessions for user %s", deleted, user_id)
    return {"status": "ok", "deleted_sessions": deleted}


@router.delete("/admin/users/{user_id}/sessions/{session_id}")
async def admin_delete_user_session(
    user_id: str,
    session_id: str,
    request: Request,
    _admin: UserRecord = Depends(require_admin),
) -> Any:
    """删除用户的单个会话。"""
    store = _get_store(request)
    target = store.get_by_id(user_id)
    if target is None:
        raise HTTPException(404, "用户不存在")

    session_mgr = getattr(request.app.state, "session_manager", None)
    if session_mgr is None:
        raise HTTPException(503, "会话管理器未初始化")

    result = await session_mgr.delete(session_id, user_id=user_id)
    if not result:
        raise HTTPException(404, "会话不存在或不属于该用户")

    return {"status": "ok"}


# ── 管理员：登录配置 ────────────────────────────────


# 布尔开关 key → 默认值
_LOGIN_TOGGLE_KEYS: dict[str, bool] = {
    "login_github_enabled": True,
    "login_google_enabled": True,
    "login_qq_enabled": False,
    "email_verify_required": False,
    "require_agreement": True,
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


# ── 订阅提供商管理 ─────────────────────────────────────────────


def _get_credential_store(request: Request):
    """获取 CredentialStore 实例。"""
    store = getattr(request.app.state, "credential_store", None)
    if store is None:
        raise HTTPException(503, "凭证存储未初始化")
    return store


_CODEX_DEFAULT_MODEL = "openai-codex/gpt-5.3-codex"
_CODEX_DEFAULT_PROFILE_NAME = "openai-codex/gpt-5.3-codex"
_CODEX_DEFAULT_BASE_URL = "https://api.openai.com/v1"
# 旧版自动创建的名称/模型，用于兼容去重（防止与旧数据重复）
_CODEX_LEGACY_NAMES = {"Codex 5.3", "codex-5.3", "codex-oauth"}
_CODEX_LEGACY_MODELS = {"gpt-5.3-codex"}


def _auto_add_codex_default_model(request: Request) -> None:
    """Codex 连接成功后，若模型列表中还没有任何 gpt-5.3-codex 条目，则自动新增一个。

    仅写入全局 model_profiles（config_store），不影响用户私有 Codex 动态模型列表。
    """
    config_store = getattr(request.app.state, "config_store", None)
    if config_store is None:
        return
    try:
        existing = config_store.list_profiles()
        # 检查是否已有同名 profile 或同 model 的 codex 条目（兼容新旧命名）
        _all_names = {_CODEX_DEFAULT_PROFILE_NAME} | _CODEX_LEGACY_NAMES
        _all_models = {_CODEX_DEFAULT_MODEL} | _CODEX_LEGACY_MODELS
        for p in existing:
            if p.get("name", "") in _all_names:
                return
            if p.get("model", "") in _all_models:
                return
        config_store.add_profile(
            name=_CODEX_DEFAULT_PROFILE_NAME,
            model=_CODEX_DEFAULT_MODEL,
            api_key="",
            base_url=_CODEX_DEFAULT_BASE_URL,
            description="Codex 5.3 — OAuth 登录（无需 API Key）",
            protocol="openai",
            thinking_mode="openai_reasoning",
            model_family="gpt",
        )
        # 同步到内存 config
        from excelmanus.api import _sync_config_profiles_from_db
        try:
            _sync_config_profiles_from_db()
        except Exception:
            pass
        logger.info("已自动添加 Codex 默认模型: %s (%s)", _CODEX_DEFAULT_PROFILE_NAME, _CODEX_DEFAULT_MODEL)
    except Exception:
        logger.debug("自动添加 Codex 默认模型失败", exc_info=True)


async def _sync_user_subscription_sessions(request: Request, user_id: str) -> None:
    """同步指定用户所有活跃会话的订阅模型档案。"""
    session_mgr = getattr(request.app.state, "session_manager", None)
    if session_mgr is None:
        return

    from excelmanus.auth.providers.openai_codex import OpenAICodexProvider

    try:
        sessions = await session_mgr.list_sessions(user_id=user_id)
        for item in sessions:
            sid = item.get("id")
            if not sid:
                continue
            engine = session_mgr.get_engine(sid, user_id=user_id)
            if engine is None:
                continue

            session_mgr.sync_user_subscription_profiles(engine, user_id)

            current_name = engine.current_model_name
            if (
                current_name
                and OpenAICodexProvider.is_codex_profile_name(current_name)
                and all(p.name != current_name for p in engine._config.models)
            ):
                engine.switch_model("default")
    except Exception:
        logger.debug("同步用户订阅模型失败", exc_info=True)


def _mask_token(token: str | None) -> str:
    """脱敏 token：保留前4后4位。"""
    if not token or len(token) <= 12:
        return "****" if token else ""
    return f"{token[:4]}{'*' * (len(token) - 8)}{token[-4:]}"


@router.get("/providers")
async def list_providers(
    request: Request,
    user: UserRecord = Depends(get_current_user),
) -> Any:
    """列出当前用户已连接的订阅提供商。"""
    store = _get_credential_store(request)
    profiles = store.list_profiles(user.id)
    return {
        "providers": [
            {
                "provider": p.provider,
                "profile_name": p.profile_name,
                "credential_type": p.credential_type,
                "account_id": p.account_id,
                "plan_type": p.plan_type,
                "expires_at": p.expires_at,
                "is_active": p.is_active,
                "created_at": p.created_at,
                "updated_at": p.updated_at,
            }
            for p in profiles
        ]
    }


# ── Codex Device Code Flow ──────────────────────────────────
# 生产级设计：
# - Device Code Flow (RFC 8628) 替代 Authorization Code popup flow
# - 该客户端 ID 仅允许 localhost redirect_uri，不支持服务端回调
# - 加密 state token（Fernet）保护轮询阶段的 device_auth_id
# - 适用于前后端分离、多 worker、多用户场景

import json as _json
import time as _time

_CODEX_DEVICE_TTL = 900  # 15 分钟过期（与 OpenAI 设备码有效期对齐）


def _get_oauth_fernet():
    """获取 OAuth state 加解密用的 Fernet 实例。"""
    from excelmanus.auth.providers.credential_store import _derive_fernet_key
    key = _derive_fernet_key()
    if not key:
        return None
    from cryptography.fernet import Fernet
    return Fernet(key)


def _seal_oauth_state(payload: dict) -> str:
    """将 OAuth 流程数据加密为 URL-safe state token。"""
    f = _get_oauth_fernet()
    if f is None:
        raise RuntimeError("加密密钥不可用，无法发起 OAuth 流程")
    raw = _json.dumps(payload, ensure_ascii=False).encode()
    return f.encrypt(raw).decode()


def _unseal_oauth_state(token: str) -> dict | None:
    """解密 state token，过期或篡改返回 None。"""
    f = _get_oauth_fernet()
    if f is None:
        return None
    try:
        from cryptography.fernet import InvalidToken
        raw = f.decrypt(token.encode(), ttl=_CODEX_DEVICE_TTL)
        return _json.loads(raw)
    except (InvalidToken, Exception):
        return None


@router.post("/providers/openai-codex/device-code/start")
async def codex_device_code_start(
    request: Request,
    user: UserRecord = Depends(get_current_user),
) -> Any:
    """发起 Device Code 登录流程，返回用户码和验证链接。

    前端展示 user_code 和 verification_url 给用户，然后轮询 poll 端点。
    """
    from excelmanus.auth.providers.openai_codex import OpenAICodexProvider

    try:
        device_info = await OpenAICodexProvider.request_user_code()
    except RuntimeError as e:
        raise HTTPException(502, str(e))

    # 加密 device_auth_id + user_code + user_id 到 state token
    state = _seal_oauth_state({
        "user_id": user.id,
        "device_auth_id": device_info["device_auth_id"],
        "user_code": device_info["user_code"],
        "ts": _time.time(),
    })

    return {
        "user_code": device_info["user_code"],
        "verification_url": device_info["verification_url"],
        "interval": device_info["interval"],
        "state": state,
    }


@router.post("/providers/openai-codex/device-code/poll")
async def codex_device_code_poll(
    request: Request,
    user: UserRecord = Depends(get_current_user),
) -> Any:
    """轮询 Device Code 授权状态。

    Body: {"state": "..."}
    返回:
      - {"status": "pending"} 用户尚未完成授权
      - {"status": "connected", ...} 授权成功
      - 4xx/5xx 错误
    """
    body = await request.json()
    state = body.get("state", "")
    if not state:
        raise HTTPException(400, "缺少 state 参数")

    pending = _unseal_oauth_state(state)
    if not pending:
        raise HTTPException(400, "state 无效或已过期，请重新发起登录")

    # 验证 state 中的 user_id 与当前用户匹配
    if pending.get("user_id") != user.id:
        raise HTTPException(403, "state 与当前用户不匹配")

    from excelmanus.auth.providers.openai_codex import OpenAICodexProvider

    try:
        result = await OpenAICodexProvider.poll_device_auth(
            device_auth_id=pending["device_auth_id"],
            user_code=pending["user_code"],
        )
    except RuntimeError as e:
        raise HTTPException(502, str(e))

    if result is None:
        return {"status": "pending"}

    # 授权成功，交换 token
    provider = OpenAICodexProvider()
    try:
        credential = await provider.exchange_device_code(
            authorization_code=result["authorization_code"],
            code_verifier=result["code_verifier"],
        )
    except RuntimeError as e:
        raise HTTPException(502, f"Token 交换失败: {e}")

    store = _get_credential_store(request)
    store.upsert_profile(
        user_id=user.id,
        provider="openai-codex",
        profile_name="default",
        credential=credential,
    )
    _auto_add_codex_default_model(request)
    await _sync_user_subscription_sessions(request, user.id)

    logger.info(
        "用户 %s 通过 Device Code 流程连接 Codex (account=%s, plan=%s)",
        user.id, credential.account_id, credential.plan_type,
    )

    return {
        "status": "connected",
        "provider": "openai-codex",
        "account_id": credential.account_id,
        "plan_type": credential.plan_type,
        "expires_at": credential.expires_at,
    }


@router.post("/providers/openai-codex")
async def connect_openai_codex(
    request: Request,
    user: UserRecord = Depends(get_current_user),
) -> Any:
    """粘贴 token 接入 OpenAI Codex 订阅。"""
    body = await request.json()
    token_data = body.get("token_data")
    if not token_data or not isinstance(token_data, dict):
        raise HTTPException(400, "请提供 token_data 字段（粘贴 ~/.codex/auth.json 的内容）")

    from excelmanus.auth.providers.openai_codex import OpenAICodexProvider
    provider = OpenAICodexProvider()

    try:
        credential = provider.validate_token_data(token_data)
    except ValueError as e:
        raise HTTPException(400, str(e))

    store = _get_credential_store(request)
    summary = store.upsert_profile(
        user_id=user.id,
        provider="openai-codex",
        profile_name="default",
        credential=credential,
    )
    _auto_add_codex_default_model(request)
    await _sync_user_subscription_sessions(request, user.id)

    logger.info(
        "用户 %s 已连接 OpenAI Codex (account=%s, plan=%s)",
        user.id, credential.account_id, credential.plan_type,
    )
    return {
        "status": "connected",
        "provider": "openai-codex",
        "account_id": credential.account_id,
        "plan_type": credential.plan_type,
        "expires_at": credential.expires_at,
        "created_at": summary.created_at,
    }


@router.delete("/providers/openai-codex")
async def disconnect_openai_codex(
    request: Request,
    user: UserRecord = Depends(get_current_user),
) -> Any:
    """断开 OpenAI Codex 订阅连接。"""
    store = _get_credential_store(request)
    deleted = store.delete_profile(user.id, "openai-codex", "default")
    if not deleted:
        raise HTTPException(404, "未找到 OpenAI Codex 连接")
    await _sync_user_subscription_sessions(request, user.id)
    logger.info("用户 %s 已断开 OpenAI Codex", user.id)
    return {"status": "disconnected", "provider": "openai-codex"}


@router.get("/providers/openai-codex/status")
async def openai_codex_status(
    request: Request,
    user: UserRecord = Depends(get_current_user),
) -> Any:
    """查询 OpenAI Codex 连接状态。"""
    store = _get_credential_store(request)
    profile = store.get_active_profile(user.id, "openai-codex")
    if not profile:
        return {"status": "disconnected", "provider": "openai-codex"}

    from datetime import datetime as _dt, timezone as _tz
    is_expired = False
    if profile.expires_at:
        try:
            exp = _dt.fromisoformat(profile.expires_at)
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=_tz.utc)
            is_expired = exp < _dt.now(tz=_tz.utc)
        except (ValueError, TypeError):
            pass

    return {
        "status": "expired" if is_expired else "connected",
        "provider": "openai-codex",
        "account_id": profile.account_id,
        "plan_type": profile.plan_type,
        "expires_at": profile.expires_at,
        "is_active": profile.is_active,
        "access_token_preview": _mask_token(profile.access_token),
        "has_refresh_token": bool(profile.refresh_token),
    }


@router.post("/providers/openai-codex/refresh")
async def refresh_openai_codex(
    request: Request,
    user: UserRecord = Depends(get_current_user),
) -> Any:
    """手动刷新 OpenAI Codex token。"""
    store = _get_credential_store(request)
    profile = store.get_active_profile(user.id, "openai-codex")
    if not profile:
        raise HTTPException(404, "未找到 OpenAI Codex 连接")
    if not profile.refresh_token:
        raise HTTPException(400, "无 refresh token，请重新运行 codex login 后粘贴新令牌")

    from excelmanus.auth.providers.openai_codex import OpenAICodexProvider
    provider = OpenAICodexProvider()

    try:
        refreshed = await provider.refresh_token(profile.refresh_token)
    except RuntimeError as e:
        store.deactivate_profile(profile.id)
        raise HTTPException(502, str(e))

    store.update_tokens(
        profile.id,
        refreshed.access_token,
        refreshed.refresh_token,
        refreshed.expires_at,
    )
    await _sync_user_subscription_sessions(request, user.id)
    logger.info("用户 %s 的 OpenAI Codex token 已刷新", user.id)
    return {
        "status": "refreshed",
        "provider": "openai-codex",
        "expires_at": refreshed.expires_at,
    }


# ── Codex OAuth PKCE Browser Flow ──────────────────────────────
# 双路径设计：
# - Path A (本地访问): popup → OpenAI auth → redirect 回前端回调页 → postMessage
# - Path B (远程访问): popup → OpenAI auth → redirect 到 localhost (失败) → 用户粘贴 URL
# 两种路径共享同一后端端点，区别仅在前端行为。
#
# 注意：state 参数必须短（≤128 字符），OpenAI auth 端点对长 state 会报 unknown_error。
# 因此 PKCE 数据存储在 DB 中（多 worker 安全），state 仅为短随机 token（与 Codex CLI 一致）。

_CODEX_OAUTH_TTL = 900  # state token 有效期 15 分钟
_CODEX_OAUTH_FALLBACK_PORT = 1455  # 与 Codex CLI 默认回调端口保持一致

def _generate_oauth_state() -> str:
    """生成与 Codex CLI 相同格式的短随机 state（32 字节 → 43 字符 base64url）。"""
    import os
    import base64
    return base64.urlsafe_b64encode(os.urandom(32)).rstrip(b"=").decode()


@router.post("/providers/openai-codex/oauth/start")
async def codex_oauth_start(
    request: Request,
    user: UserRecord = Depends(get_current_user),
) -> Any:
    """发起 Codex OAuth PKCE 浏览器流程。

    Body (可选): {"redirect_uri": "http://localhost:3000/auth/callback"}
    - 如果前端在 localhost 上运行，传入实际回调 URL（Path A）
    - 如果不传，使用 fallback localhost:1455（Path B，用户需粘贴 URL）

    返回: {"authorize_url": "...", "state": "...", "redirect_uri": "...", "mode": "popup"|"paste"}
    """
    from excelmanus.auth.providers.openai_codex import OpenAICodexProvider

    body = {}
    try:
        body = await request.json()
    except Exception:
        pass

    # 确定 redirect_uri
    client_redirect = (body.get("redirect_uri") or "").strip()
    if client_redirect:
        # 安全校验：仅允许 localhost / 127.0.0.1
        from urllib.parse import urlparse
        parsed = urlparse(client_redirect)
        if parsed.hostname not in ("localhost", "127.0.0.1"):
            raise HTTPException(400, "redirect_uri 仅允许 localhost 或 127.0.0.1")
        redirect_uri = client_redirect
        mode = "popup"
    else:
        redirect_uri = f"http://localhost:{_CODEX_OAUTH_FALLBACK_PORT}/auth/callback"
        mode = "paste"

    # 生成 PKCE
    code_verifier, code_challenge = OpenAICodexProvider.generate_pkce()

    # 生成短随机 state（与 Codex CLI 格式一致，43 字符）
    state = _generate_oauth_state()

    # 存储 PKCE 数据到 DB（多 worker 安全，重启不丢失）
    cred_store = _get_credential_store(request)
    cred_store.save_oauth_state(state, {
        "user_id": user.id,
        "code_verifier": code_verifier,
        "redirect_uri": redirect_uri,
    }, ttl=_CODEX_OAUTH_TTL)

    # 构造授权 URL
    authorize_url = OpenAICodexProvider.build_authorize_url(
        redirect_uri=redirect_uri,
        state=state,
        code_challenge=code_challenge,
    )

    return {
        "authorize_url": authorize_url,
        "state": state,
        "redirect_uri": redirect_uri,
        "mode": mode,
    }


@router.post("/providers/openai-codex/oauth/exchange")
async def codex_oauth_exchange(
    request: Request,
    user: UserRecord = Depends(get_current_user),
) -> Any:
    """用授权码交换 Codex token。

    Body: {"code": "...", "state": "..."}
    - code: OpenAI 返回的授权码
    - state: /oauth/start 返回的 state token
    """
    body = await request.json()
    code = (body.get("code") or "").strip()
    state = (body.get("state") or "").strip()

    if not code:
        raise HTTPException(400, "缺少 code 参数")
    if not state:
        raise HTTPException(400, "缺少 state 参数")

    # 从 DB 查找并消费 pending 数据（原子 pop，多 worker 安全）
    cred_store = _get_credential_store(request)
    pending = cred_store.pop_oauth_state(state, ttl=_CODEX_OAUTH_TTL)
    if not pending:
        raise HTTPException(400, "state 无效或已过期，请重新发起授权")
    if pending.get("user_id") != user.id:
        raise HTTPException(403, "state 与当前用户不匹配")

    code_verifier = pending.get("code_verifier", "")
    redirect_uri = pending.get("redirect_uri", "")
    if not code_verifier or not redirect_uri:
        raise HTTPException(400, "state 数据不完整")

    from excelmanus.auth.providers.openai_codex import OpenAICodexProvider
    provider = OpenAICodexProvider()

    try:
        credential = await provider.exchange_code(
            code=code,
            redirect_uri=redirect_uri,
            code_verifier=code_verifier,
        )
    except RuntimeError as e:
        raise HTTPException(502, f"Token 交换失败: {e}")

    store = _get_credential_store(request)
    store.upsert_profile(
        user_id=user.id,
        provider="openai-codex",
        profile_name="default",
        credential=credential,
    )
    _auto_add_codex_default_model(request)
    await _sync_user_subscription_sessions(request, user.id)

    logger.info(
        "用户 %s 通过 OAuth PKCE 流程连接 Codex (account=%s, plan=%s)",
        user.id, credential.account_id, credential.plan_type,
    )

    return {
        "status": "connected",
        "provider": "openai-codex",
        "account_id": credential.account_id,
        "plan_type": credential.plan_type,
        "expires_at": credential.expires_at,
    }
