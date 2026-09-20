"""进程级 Codex 订阅 OAuth。不含身份登录 / 租户隔离。"""

from __future__ import annotations

import asyncio
import html
import json as _json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from excelmanus.auth.providers.credential_store import PROCESS_USER_ID

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

# ── 订阅提供商管理 ─────────────────────────────────────────────


def _get_credential_store(request: Request):
    """获取 CredentialStore 实例。"""
    store = getattr(request.app.state, "credential_store", None)
    if store is None:
        raise HTTPException(503, "凭证存储未初始化")
    return store


_CODEX_DEFAULT_MODEL = "openai-codex/gpt-6-astra"
_CODEX_DEFAULT_PROFILE_NAME = "openai-codex/gpt-6-astra"
_CODEX_DEFAULT_BASE_URL = "https://api.openai.com/v1"
# 旧版自动创建的名称/模型，用于兼容去重（防止与旧数据重复）
_CODEX_LEGACY_NAMES = {"Codex 5.3", "codex-5.3", "codex-oauth", "Codex Spark", "codex-spark", "Codex 5.2", "codex-5.2"}
_CODEX_LEGACY_MODELS = {"gpt-5.3-codex", "gpt-5.3-codex-spark", "gpt-5.2-codex", "openai-codex/gpt-5.2-codex"}


def _auto_add_codex_default_model(request: Request) -> bool:
    """Codex 连接成功后，若模型列表中还没有任何默认 Codex 条目，则自动新增一个。

    写入全局 model_profiles，随后由 _sync_subscription_sessions 同步已有会话。
    """
    from excelmanus.api_app_state import get_config_store
    config_store = get_config_store()
    if config_store is None:
        return False
    try:
        existing = config_store.list_profiles()
        # 检查是否已有同名 profile 或同 model 的 codex 条目（兼容新旧命名）
        _all_names = {_CODEX_DEFAULT_PROFILE_NAME} | _CODEX_LEGACY_NAMES
        _all_models = {_CODEX_DEFAULT_MODEL} | _CODEX_LEGACY_MODELS
        for p in existing:
            if p.get("name", "") in _all_names:
                return False
            if p.get("model", "") in _all_models:
                return False
        created = config_store.add_profile(
            name=_CODEX_DEFAULT_PROFILE_NAME,
            model=_CODEX_DEFAULT_MODEL,
            api_key="",
            base_url=_CODEX_DEFAULT_BASE_URL,
            description="GPT-6 Astra - OAuth 登录（无需 API Key）",
            protocol="openai_responses",
            thinking_mode="openai_reasoning",
            model_family="gpt",
        )
        if not created:
            return False
        # 同步到内存 config
        from excelmanus.api_app_state import _sync_config_profiles_from_db, _user_config_store
        try:
            _sync_config_profiles_from_db()
            user_cfg = _user_config_store()
            if user_cfg is not None and not user_cfg.get_active_model():
                user_cfg.set_active_model(_CODEX_DEFAULT_PROFILE_NAME)
                from excelmanus.api_app_state import apply_profile_to_config
                apply_profile_to_config(_CODEX_DEFAULT_PROFILE_NAME)
        except Exception:
            pass
        logger.info("已自动添加 Codex 默认模型: %s (%s)", _CODEX_DEFAULT_PROFILE_NAME, _CODEX_DEFAULT_MODEL)
        return True
    except Exception:
        logger.debug("自动添加 Codex 默认模型失败", exc_info=True)
        return False


async def _sync_subscription_sessions(request: Request) -> None:
    """同步全部活跃会话的订阅模型档案。"""
    from excelmanus.api_app_state import get_config, get_session_manager, _sync_config_profiles_from_db

    session_mgr = get_session_manager()
    if session_mgr is None:
        return

    from excelmanus.auth.providers.registry import list_all as _list_all_providers

    _prefixes = tuple(
        getattr(p, "MODEL_NAME_PREFIX", "")
        for p in _list_all_providers().values()
        if getattr(p, "MODEL_NAME_PREFIX", "")
    )

    try:
        _sync_config_profiles_from_db()
        config = get_config()
        if config is not None:
            # 重新读取 DB，既带入自动新增档案，也清除断开连接前注入的旧 token。
            await session_mgr.broadcast_model_profiles(config.models)
        sessions = await session_mgr.list_sessions()
        for item in sessions:
            sid = item.get("id")
            if not sid:
                continue
            engine = session_mgr.get_engine(sid)
            if engine is None:
                continue

            current_name = engine.current_model_name
            current_profile = next((p for p in engine._config.models if p.name == current_name), None)
            if current_profile is not None and (
                any(current_profile.name.startswith(pfx) for pfx in _prefixes)
                or any(current_profile.model.startswith(pfx) for pfx in _prefixes)
            ):
                engine.switch_model(current_name)
            if (
                current_name
                and any(current_name.startswith(pfx) for pfx in _prefixes)
                and all(p.name != current_name for p in engine._config.models)
            ):
                if engine._config.models:
                    engine.switch_model(engine._config.models[0].name)
    except Exception:
        logger.debug("同步订阅模型失败", exc_info=True)


def _mask_token(token: str | None) -> str:
    """脱敏 token：保留前4后4位。"""
    if not token or len(token) <= 12:
        return "****" if token else ""
    return f"{token[:4]}{'*' * (len(token) - 8)}{token[-4:]}"


def _codex_profile_email(profile: Any) -> str:
    """从已存凭证中取出可展示邮箱，不回传完整 claims。"""
    raw = getattr(profile, "extra_data", None)
    data: Any = None
    if isinstance(raw, str) and raw:
        try:
            data = _json.loads(raw)
        except Exception:
            data = None
    elif isinstance(raw, dict):
        data = raw
    if isinstance(data, dict):
        email = data.get("email")
        if isinstance(email, str) and email.strip():
            return email.strip()
    access = getattr(profile, "access_token", None)
    if access:
        from excelmanus.auth.providers.openai_codex import _extract_email, _parse_jwt_claims
        return _extract_email(_parse_jwt_claims(access))
    return ""


@router.get("/providers")
async def list_providers(
    request: Request,
) -> Any:
    """列出当前用户已连接的订阅提供商。"""
    store = _get_credential_store(request)
    profiles = store.list_profiles()
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
# - 适用于前后端分离、多 worker 场景

import json as _json
import time as _time

_CODEX_DEVICE_TTL = 900  # 15 分钟过期（与 OpenAI 设备码有效期对齐）


def _get_oauth_fernet():
    """获取 OAuth state 加解密用的 Fernet 实例。"""
    from excelmanus.security.cipher import derive_fernet_key
    key = derive_fernet_key()
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
        "device_auth_id": device_info["device_auth_id"],
        "user_code": device_info["user_code"],
        "ts": _time.time(),
    })

    payload = {
        "user_code": device_info["user_code"],
        "verification_url": device_info["verification_url"],
        "interval": device_info["interval"],
        "state": state,
    }
    if device_info.get("expires_in"):
        payload["expires_in"] = device_info["expires_in"]
    return payload


@router.post("/providers/openai-codex/device-code/poll")
async def codex_device_code_poll(
    request: Request,
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
        user_id=PROCESS_USER_ID,
        provider="openai-codex",
        profile_name="default",
        credential=credential,
    )
    _auto_add_codex_default_model(request)
    await _sync_subscription_sessions(request)

    logger.info(
        "通过 Device Code 流程连接 Codex (account=%s, plan=%s)",
        credential.account_id, credential.plan_type,
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
        user_id=PROCESS_USER_ID,
        provider="openai-codex",
        profile_name="default",
        credential=credential,
    )
    _auto_add_codex_default_model(request)
    await _sync_subscription_sessions(request)

    logger.info(
        "已连接 OpenAI Codex (account=%s, plan=%s)",
        credential.account_id, credential.plan_type,
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
) -> Any:
    """断开 OpenAI Codex 订阅连接。"""
    store = _get_credential_store(request)
    deleted = store.delete_profile(PROCESS_USER_ID, "openai-codex", "default")
    if not deleted:
        raise HTTPException(404, "未找到 OpenAI Codex 连接")
    await _sync_subscription_sessions(request)
    logger.info("已断开 OpenAI Codex")
    return {"status": "disconnected", "provider": "openai-codex"}


@router.get("/providers/openai-codex/status")
async def openai_codex_status(
    request: Request,
) -> Any:
    """查询 OpenAI Codex 连接状态。"""
    store = _get_credential_store(request)
    profile = store.get_active_profile("openai-codex")
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

    email = _codex_profile_email(profile)
    return {
        "status": "expired" if is_expired else "connected",
        "provider": "openai-codex",
        "account_id": profile.account_id,
        "plan_type": profile.plan_type,
        "expires_at": profile.expires_at,
        "is_active": profile.is_active,
        "access_token_preview": _mask_token(profile.access_token),
        "has_refresh_token": bool(profile.refresh_token),
        **({"email": email} if email else {}),
    }


@router.post("/providers/openai-codex/refresh")
async def refresh_openai_codex(
    request: Request,
) -> Any:
    """手动刷新 OpenAI Codex token。"""
    store = _get_credential_store(request)
    profile = store.get_active_profile("openai-codex")
    if not profile:
        raise HTTPException(404, "未找到 OpenAI Codex 连接")
    if not profile.refresh_token:
        raise HTTPException(400, "无 refresh token，请重新登录 ChatGPT 订阅")

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
    await _sync_subscription_sessions(request)
    logger.info("OpenAI Codex token 已刷新")
    return {
        "status": "refreshed",
        "provider": "openai-codex",
        "expires_at": refreshed.expires_at,
    }


# ── Codex OAuth PKCE Browser Flow ──────────────────────────────
# 双路径设计：
# - Path A (本地访问): popup → OpenAI auth → 固定 loopback:1455 → postMessage
# - Path B (远程访问): popup → OpenAI auth → localhost:1455 (失败) → 用户粘贴 URL
# OpenAI 的公开 Codex client 只接受固定的 localhost:1455/auth/callback。
#
# 注意：state 参数必须短（≤128 字符），OpenAI auth 端点对长 state 会报 unknown_error。
# 因此 PKCE 数据存储在 DB 中（多 worker 安全），state 仅为短随机 token（与 Codex CLI 一致）。

_CODEX_OAUTH_TTL = 900  # state token 有效期 15 分钟
_CODEX_OAUTH_FALLBACK_PORT = 1455  # 与 Codex CLI 默认回调端口保持一致
_codex_callback_server: asyncio.AbstractServer | None = None
_codex_callback_timeout_task: asyncio.Task[None] | None = None
_codex_callback_state = ""


def _codex_callback_page(*, code: str, state: str, error: str) -> str:
    """生成一次性 loopback 回调页，把结果传回发起登录的前端窗口。"""
    payload = {
        "type": "codex-oauth-callback",
        **({"error": error} if error else {"code": code, "state": state}),
    }
    payload_json = _json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c")
    success = not error
    title = "授权成功" if success else "授权失败"
    message = "正在完成连接，此窗口将自动关闭。" if success else error
    color = "#16a34a" if success else "#dc2626"
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title><style>body{{font-family:-apple-system,BlinkMacSystemFont,sans-serif;display:grid;place-items:center;min-height:100vh;margin:0;background:#f8fafc;color:#111827}}main{{max-width:420px;padding:32px;text-align:center}}h1{{color:{color};font-size:24px}}p{{color:#6b7280;line-height:1.6}}</style></head>
<body><main><h1>{title}</h1><p>{html.escape(message)}</p></main>
<script>const payload={payload_json};if(window.opener){{window.opener.postMessage(payload,"*");}}setTimeout(()=>window.close(),1500);</script>
</body></html>"""


async def _close_codex_callback_listener() -> None:
    global _codex_callback_server, _codex_callback_timeout_task, _codex_callback_state
    server = _codex_callback_server
    _codex_callback_server = None
    _codex_callback_state = ""
    if server is not None:
        server.close()
        await server.wait_closed()
    timeout_task = _codex_callback_timeout_task
    _codex_callback_timeout_task = None
    current = asyncio.current_task()
    if timeout_task is not None and timeout_task is not current:
        timeout_task.cancel()


async def _expire_codex_callback_listener(expected_state: str) -> None:
    await asyncio.sleep(_CODEX_OAUTH_TTL)
    if _codex_callback_state == expected_state:
        await _close_codex_callback_listener()


async def _handle_codex_loopback_callback(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    expected_state: str,
) -> None:
    from urllib.parse import parse_qs, urlsplit

    status = 400
    code = ""
    state = ""
    error = "回调请求无效"
    matched_attempt = False
    try:
        request_head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=5)
        request_lines = request_head.split(b"\r\n")
        request_line = request_lines[0].decode("ascii", "replace")
        host_header = next(
            (
                line.split(b":", 1)[1].strip().decode("ascii", "replace")
                for line in request_lines[1:]
                if line.lower().startswith(b"host:")
            ),
            "",
        )
        method, target, _version = request_line.split(" ", 2)
        parsed = urlsplit(target)
        params = parse_qs(parsed.query)
        state = (params.get("state") or [""])[0]
        if host_header not in ("localhost:1455", "127.0.0.1:1455"):
            status, error = 400, "OAuth 回调 Host 无效"
        elif method != "GET" or parsed.path != "/auth/callback":
            status, error = 404, "未找到 OAuth 回调地址"
        elif state != expected_state:
            status, error = 400, "OAuth state 不匹配，请重新发起登录"
        else:
            matched_attempt = True
            oauth_error = (params.get("error_description") or params.get("error") or [""])[0]
            code = (params.get("code") or [""])[0]
            if oauth_error:
                status, error = 400, oauth_error
            elif not code:
                status, error = 400, "OpenAI 回调缺少授权码"
            else:
                status, error = 200, ""
    except Exception:
        logger.debug("Codex loopback 回调解析失败", exc_info=True)

    body = _codex_callback_page(code=code, state=state, error=error).encode("utf-8")
    reason = "OK" if status == 200 else "Bad Request" if status == 400 else "Not Found"
    headers = (
        f"HTTP/1.1 {status} {reason}\r\n"
        "Content-Type: text/html; charset=utf-8\r\n"
        "Cache-Control: no-store\r\n"
        "X-Content-Type-Options: nosniff\r\n"
        "Content-Security-Policy: default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Connection: close\r\n\r\n"
    ).encode("ascii")
    writer.write(headers + body)
    try:
        await writer.drain()
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except (ConnectionError, OSError):
            pass
    if matched_attempt:
        await _close_codex_callback_listener()


async def _start_codex_callback_listener(state: str) -> None:
    """在官方固定回调端口启动一个与 state 绑定的一次性 listener。"""
    global _codex_callback_server, _codex_callback_timeout_task, _codex_callback_state
    await _close_codex_callback_listener()
    server = await asyncio.start_server(
        lambda reader, writer: _handle_codex_loopback_callback(
            reader, writer, state,
        ),
        host="127.0.0.1",
        port=_CODEX_OAUTH_FALLBACK_PORT,
    )
    _codex_callback_server = server
    _codex_callback_state = state
    _codex_callback_timeout_task = asyncio.create_task(
        _expire_codex_callback_listener(state)
    )

def _generate_oauth_state() -> str:
    """生成与 Codex CLI 相同格式的短随机 state（32 字节 → 43 字符 base64url）。"""
    import os
    import base64
    return base64.urlsafe_b64encode(os.urandom(32)).rstrip(b"=").decode()


@router.post("/providers/openai-codex/oauth/start")
async def codex_oauth_start(
    request: Request,
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

    # 前端回调 URL 仅用于判断是否为同机浏览器；向 OpenAI 注册的 redirect_uri
    # 必须始终是官方 Codex client 的固定 loopback 地址。
    client_redirect = (body.get("redirect_uri") or "").strip()
    if client_redirect:
        # 安全校验：仅允许 localhost / 127.0.0.1
        from urllib.parse import urlparse
        parsed = urlparse(client_redirect)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in ("localhost", "127.0.0.1")
            or parsed.username
            or parsed.password
            or parsed.path != OpenAICodexProvider.CALLBACK_PATH
            or parsed.query
            or parsed.fragment
        ):
            raise HTTPException(
                400,
                "redirect_uri 必须是 http://localhost[:port]/auth/callback "
                "或 http://127.0.0.1[:port]/auth/callback",
            )
        mode = "popup"
    else:
        mode = "paste"
    redirect_uri = OpenAICodexProvider.BROWSER_REDIRECT_URI

    # 生成 PKCE
    code_verifier, code_challenge = OpenAICodexProvider.generate_pkce()

    # 生成短随机 state（与 Codex CLI 格式一致，43 字符）
    state = _generate_oauth_state()

    # 存储 PKCE 数据到 DB（多 worker 安全，重启不丢失）
    cred_store = _get_credential_store(request)
    cred_store.save_oauth_state(state, {
        "code_verifier": code_verifier,
        "redirect_uri": redirect_uri,
    }, ttl=_CODEX_OAUTH_TTL)

    if mode == "popup":
        try:
            await _start_codex_callback_listener(state)
        except OSError as exc:
            cred_store.pop_oauth_state(state, ttl=_CODEX_OAUTH_TTL)
            raise HTTPException(
                409,
                "本机 1455 端口被占用，无法接收 OpenAI 回调。"
                "请关闭正在运行的 codex login，或改用设备码登录。",
            ) from exc

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
        user_id=PROCESS_USER_ID,
        provider="openai-codex",
        profile_name="default",
        credential=credential,
    )
    _auto_add_codex_default_model(request)
    await _sync_subscription_sessions(request)

    logger.info(
        "通过 OAuth PKCE 流程连接 Codex (account=%s, plan=%s)",
        credential.account_id, credential.plan_type,
    )

    return {
        "status": "connected",
        "provider": "openai-codex",
        "account_id": credential.account_id,
        "plan_type": credential.plan_type,
        "expires_at": credential.expires_at,
    }
