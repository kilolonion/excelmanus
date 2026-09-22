"""Tencent CodeBuddy / WorkBuddy 订阅认证提供商。

登录、刷新、对话与模型目录的接口约定如下：

- 登录：``POST /v2/plugin/auth/state?platform=CLI`` 签发 ``{state, authUrl}``，
  用户在浏览器完成登录后，``GET /v2/plugin/auth/token?state=`` 返回 token。
  整个过程要求同一个带 cookie jar 的 HTTP 客户端（cookie 亲和）。
- 刷新：``POST {realm}/v2/plugin/auth/token/refresh``，
  头 ``X-Refresh-Token``（可选 ``X-Enterprise-Id``）。
- 对话：``POST {realm}/v2/chat/completions``，OpenAI Chat Completions 兼容，
  附带 ``X-User-Id`` / ``X-Enterprise-Id`` / ``X-Domain`` 等头。
- 模型目录：``GET {realm}/v3/config``（取 ``data.agents[name="cli"].models``），
  404/405 时回退 ``GET /console/enterprises/personal/models``。
- Realm 按 provider 实例固定（``workbuddy-cn`` / ``workbuddy-global``），
  粘贴导入时用 token JWT ``iss`` 校验 realm 是否匹配
  （codebuddy.cn / copilot.tencent.com 为 CN，workbuddy.ai 为 Global）。
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

import httpx

from excelmanus.auth.providers.base import (
    AuthProfileRecord,
    AuthProvider,
    BrowserPollCapable,
    RefreshedCredential,
    ValidatedCredential,
)
from excelmanus.auth.providers.openai_codex import _parse_jwt_claims

logger = logging.getLogger(__name__)

UPSTREAM_BASE_CN = "https://copilot.tencent.com"
UPSTREAM_BASE_GLOBAL = "https://www.workbuddy.ai"
ORIGIN_CN = "https://www.codebuddy.cn"
ORIGIN_GLOBAL = "https://www.workbuddy.ai"
CLIENT_UA = "CLI/2.63.2 CodeBuddy/2.63.2"

_AUTH_STATE_PATH = "/v2/plugin/auth/state?platform=CLI"
_LOGIN_ACCOUNT_PATH = "/v2/plugin/login/account?state="
_AUTH_TOKEN_PATH = "/v2/plugin/auth/token?state="
_TOKEN_REFRESH_PATH = "/v2/plugin/auth/token/refresh"
_V3_CONFIG_PATH = "/v3/config"
_LEGACY_MODELS_PATH = "/console/enterprises/personal/models"

LOGIN_TTL_SECONDS = 600

_CN_ISS_HOSTS = {"codebuddy.cn", "www.codebuddy.cn", "copilot.tencent.com"}
_GLOBAL_ISS_HOSTS = {"workbuddy.ai", "www.workbuddy.ai"}

_GLOBAL_SYSTEM_MESSAGE = {"role": "system", "content": "You are a helpful assistant."}


def _oauth_proxy() -> str | None:
    for key in (
        "EXCELMANUS_OAUTH_PROXY",
        "HTTPS_PROXY",
        "https_proxy",
        "ALL_PROXY",
        "all_proxy",
    ):
        value = os.environ.get(key, "").strip()
        if value:
            return value
    return None


def realm_from_access_token(access_token: str) -> str | None:
    """从 access token JWT iss 识别 realm：'cn' | 'global' | None。"""
    claims = _parse_jwt_claims(access_token)
    if not claims:
        return None
    iss = str(claims.get("iss") or "")
    try:
        host = urlparse(iss).hostname
    except Exception:
        return None
    if not host:
        return None
    host = host.lower()
    if host in _CN_ISS_HOSTS:
        return "cn"
    if host in _GLOBAL_ISS_HOSTS:
        return "global"
    return None


def _realm_base(realm: str) -> str:
    return UPSTREAM_BASE_GLOBAL if realm == "global" else UPSTREAM_BASE_CN


def _realm_origin(realm: str) -> str:
    return ORIGIN_GLOBAL if realm == "global" else ORIGIN_CN


def _common_headers(realm: str) -> dict[str, str]:
    origin = _realm_origin(realm)
    return {
        "Content-Type": "application/json",
        "Accept": "application/json, text/plain, */*",
        "X-Requested-With": "XMLHttpRequest",
        "Origin": origin,
        "Referer": origin + "/",
        "User-Agent": CLIENT_UA,
    }


class _UpstreamHTTPError(RuntimeError):
    """上游返回非 2xx HTTP 状态。"""

    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"HTTP {status}: {body[:300]}")
        self.status = status


def _unwrap_data(env: dict[str, Any]) -> dict[str, Any]:
    """解析 {code,msg,data} 业务信封；code != 0 抛 RuntimeError。"""
    code = env.get("code")
    if code not in (0, "0", None):
        msg = str(env.get("msg") or "")
        raise RuntimeError(f"上游业务错误 code={code}: {msg[:200]}")
    data = env.get("data")
    return data if isinstance(data, dict) else {}


async def _request_envelope(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    body: Any = None,
) -> tuple[dict[str, Any], int]:
    """发送请求并返回 (信封, http_status)。传输层/HTTP 错误抛 RuntimeError。"""
    kwargs: dict[str, Any] = {"headers": headers or _common_headers("cn")}
    if body is not None:
        kwargs["json"] = body
    try:
        resp = await client.request(method, url, **kwargs)
    except httpx.HTTPError as e:
        raise RuntimeError(f"网络错误: {e}") from e
    if resp.status_code >= 300:
        raise _UpstreamHTTPError(resp.status_code, resp.text)
    try:
        env = resp.json()
    except ValueError as e:
        raise RuntimeError("上游响应不是有效 JSON") from e
    if not isinstance(env, dict):
        raise RuntimeError("上游响应格式异常")
    return env, resp.status_code


def _parse_extra(record: AuthProfileRecord) -> dict[str, Any]:
    raw = getattr(record, "extra_data", None)
    if isinstance(raw, str) and raw:
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else {}
        except (ValueError, TypeError):
            return {}
    if isinstance(raw, dict):
        return raw
    return {}


def _iso_from_seconds(seconds: Any) -> str:
    try:
        secs = float(seconds)
    except (TypeError, ValueError):
        secs = 3600.0
    return (datetime.now(tz=timezone.utc) + timedelta(seconds=secs)).isoformat()


def _iso_from_epoch(epoch: Any) -> str:
    try:
        ts = float(epoch)
        if ts > 1e12:
            ts /= 1000.0
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return _iso_from_seconds(3600)


class _LoginCtx:
    """一次进行中登录的 cookie 亲和客户端与过期时间。"""

    __slots__ = ("client", "expires_at")

    def __init__(self, client: httpx.AsyncClient, ttl: float) -> None:
        self.client = client
        self.expires_at = time.monotonic() + ttl


class WorkBuddyProvider(AuthProvider, BrowserPollCapable):
    """Tencent CodeBuddy / WorkBuddy OAuth 提供商。

    按 realm 拆分为两个 provider 实例（``workbuddy-cn`` / ``workbuddy-global``），
    各自持有独立凭证槽位与 ``workbuddy-<realm>/`` 模型前缀，CN 与 Global
    订阅可同时连接、互不覆盖。
    """

    PROTOCOL = "openai"
    REFRESH_MARGIN_SECONDS = 300
    REALMS = ("cn", "global")

    def __init__(self, realm: str = "cn") -> None:
        if realm not in self.REALMS:
            raise ValueError(f"未知 WorkBuddy realm: {realm}")
        self._realm = realm
        self.provider_name = f"workbuddy-{realm}"
        self.MODEL_NAME_PREFIX = f"workbuddy-{realm}/"
        # state -> _LoginCtx；cookie jar 亲和要求同实例客户端复用
        self._logins: dict[str, _LoginCtx] = {}

    @property
    def realm(self) -> str:
        return self._realm

    @property
    def _base(self) -> str:
        return _realm_base(self._realm)

    # ── 浏览器登录（state + authUrl + 轮询） ────────────────────

    async def start_browser_login(self) -> dict[str, Any]:
        client = httpx.AsyncClient(timeout=30.0, proxy=_oauth_proxy())
        env, _status = await _request_envelope(
            client, "POST", self._base + _AUTH_STATE_PATH,
            headers=_common_headers(self._realm), body={},
        )
        data = _unwrap_data(env)
        state = str(data.get("state") or "")
        auth_url = str(data.get("authUrl") or data.get("auth_url") or "")
        if not state or not auth_url:
            await client.aclose()
            raise RuntimeError("auth/state 响应缺少 state 或 authUrl")
        self._prune_logins()
        self._logins[state] = _LoginCtx(client, LOGIN_TTL_SECONDS)
        return {
            "state": state,
            "auth_url": auth_url,
            "expires_in": LOGIN_TTL_SECONDS,
        }

    async def poll_browser_login(self, state: str) -> ValidatedCredential | None:
        ctx = self._logins.get(state)
        if ctx is None:
            raise RuntimeError("登录会话不存在或已过期，请重新发起登录")
        if time.monotonic() >= ctx.expires_at:
            await self._discard_login(state)
            raise RuntimeError("登录已超时，请重新发起登录")

        # 授权完成前上游返回非零业务码（如 11217 "login ing"）→ 继续等待。
        env, _status = await _request_envelope(
            ctx.client, "GET", self._base + _AUTH_TOKEN_PATH + state,
            headers=_common_headers(self._realm),
        )
        if env.get("code") not in (0, "0"):
            return None
        tok = _unwrap_data(env)
        access_token = str(tok.get("accessToken") or tok.get("access_token") or "")
        if not access_token:
            return None

        # 拿到 bearer 后才能调 login/account（网关此前 401）。
        account: dict[str, Any] = {}
        try:
            acct_headers = dict(_common_headers(self._realm))
            acct_headers["Authorization"] = f"Bearer {access_token}"
            acct_env, _ = await _request_envelope(
                ctx.client, "GET", self._base + _LOGIN_ACCOUNT_PATH + state,
                headers=acct_headers,
            )
            account = _unwrap_data(acct_env)
        except RuntimeError:
            logger.debug("workbuddy login/account 获取失败，仅保存 token", exc_info=True)

        await self._discard_login(state)

        domain = str(tok.get("domain") or "")
        uid = str(account.get("uid") or account.get("userId") or "")
        enterprise_id = str(
            account.get("enterpriseId") or account.get("enterprise_id") or ""
        )
        nickname = str(account.get("nickname") or account.get("name") or "")
        realm = self._realm

        return ValidatedCredential(
            access_token=access_token,
            refresh_token=str(tok.get("refreshToken") or tok.get("refresh_token") or "") or None,
            expires_at=_iso_from_seconds(tok.get("expiresIn") or tok.get("expires_in") or 3600),
            account_id=uid,
            plan_type=realm,
            credential_type="oauth",
            extra_data={
                "uid": uid,
                "enterprise_id": enterprise_id,
                "domain": domain,
                "nickname": nickname,
                "realm": realm,
            },
        )

    def _prune_logins(self) -> None:
        now = time.monotonic()
        for key in [k for k, v in self._logins.items() if now >= v.expires_at]:
            self._logins.pop(key, None)

    async def _discard_login(self, state: str) -> None:
        ctx = self._logins.pop(state, None)
        if ctx is not None:
            try:
                await ctx.client.aclose()
            except Exception:
                pass

    # ── token 校验 / 刷新 / 凭证 ──────────────────────────────

    def validate_token_data(self, raw_data: dict[str, Any]) -> ValidatedCredential:
        """验证粘贴的凭证。兼容 workbuddy-*.json 与扁平格式。"""
        if not isinstance(raw_data, dict):
            raise ValueError("凭证格式无效，请粘贴 JSON 对象")

        auth = raw_data.get("auth") if isinstance(raw_data.get("auth"), dict) else raw_data
        account = (
            raw_data.get("account") if isinstance(raw_data.get("account"), dict) else {}
        )

        access_token = str(
            auth.get("accessToken") or auth.get("access_token") or auth.get("access") or ""
        ).strip()
        if not access_token:
            raise ValueError(
                "缺少 access token。请粘贴 workbuddy-*.json 凭证内容，"
                "需包含 auth.accessToken 字段。"
            )
        refresh_token = str(
            auth.get("refreshToken") or auth.get("refresh_token") or auth.get("refresh") or ""
        ).strip() or None

        expires_raw = auth.get("expiresAt") or auth.get("expires_at")
        if isinstance(expires_raw, str):
            expires_at = expires_raw
        elif expires_raw:
            expires_at = _iso_from_epoch(expires_raw)
        else:
            claims = _parse_jwt_claims(access_token)
            exp = claims.get("exp") if claims else None
            expires_at = (
                datetime.fromtimestamp(exp, tz=timezone.utc).isoformat()
                if exp else _iso_from_seconds(3600)
            )

        domain = str(auth.get("domain") or raw_data.get("domain") or "")
        uid = str(account.get("uid") or raw_data.get("uid") or "")
        enterprise_id = str(
            account.get("enterpriseId")
            or account.get("enterprise_id")
            or raw_data.get("enterprise_id")
            or ""
        )
        nickname = str(account.get("nickname") or raw_data.get("nickname") or "")
        detected = realm_from_access_token(access_token) or (
            "global" if "workbuddy.ai" in domain.lower() else None
        )
        if detected and detected != self._realm:
            other = "WorkBuddy 国内版" if detected == "cn" else "WorkBuddy Global"
            raise ValueError(f"该凭证属于 {other} 账号，请粘贴到对应的订阅卡片")
        realm = self._realm

        return ValidatedCredential(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=expires_at,
            account_id=uid,
            plan_type=realm,
            credential_type="oauth",
            extra_data={
                "uid": uid,
                "enterprise_id": enterprise_id,
                "domain": domain,
                "nickname": nickname,
                "realm": realm,
            },
        )

    async def refresh_token(self, refresh_token: str) -> RefreshedCredential:
        """无账号上下文的刷新。优先使用 refresh_profile。"""
        if not refresh_token:
            raise RuntimeError("无 refresh token，无法刷新。请重新登录 WorkBuddy。")
        return await self._do_refresh(self._base, refresh_token, "")

    async def refresh_profile(self, profile: AuthProfileRecord) -> RefreshedCredential:
        if not profile.refresh_token:
            raise RuntimeError("无 refresh token，无法刷新。请重新登录 WorkBuddy。")
        extra = _parse_extra(profile)
        return await self._do_refresh(
            self._base,
            profile.refresh_token,
            str(extra.get("enterprise_id") or ""),
        )

    async def _do_refresh(
        self, base: str, refresh_token: str, enterprise_id: str,
    ) -> RefreshedCredential:
        headers = _common_headers("global" if "workbuddy.ai" in base else "cn")
        headers["X-Refresh-Token"] = refresh_token
        if enterprise_id:
            headers["X-Enterprise-Id"] = enterprise_id
        headers["X-Auth-Refresh-Source"] = "excelmanus"

        async with httpx.AsyncClient(timeout=30.0, proxy=_oauth_proxy()) as client:
            env, _status = await _request_envelope(
                client, "POST", base + _TOKEN_REFRESH_PATH, headers=headers,
            )
        tok = _unwrap_data(env)
        access_token = str(tok.get("accessToken") or "")
        if not access_token:
            raise RuntimeError("刷新响应中缺少 accessToken")
        new_refresh = str(tok.get("refreshToken") or "") or refresh_token
        return RefreshedCredential(
            access_token=access_token,
            refresh_token=new_refresh,
            expires_at=_iso_from_seconds(tok.get("expiresIn") or 3600),
        )

    def get_api_credential(self, access_token: str) -> tuple[str, str]:
        return (access_token, self._base + "/v2")

    def get_request_headers(self, profile: AuthProfileRecord) -> dict[str, str]:
        extra = _parse_extra(profile)
        headers = _common_headers(self._realm)
        uid = str(extra.get("uid") or getattr(profile, "account_id", None) or "")
        enterprise_id = str(extra.get("enterprise_id") or "")
        domain = str(extra.get("domain") or "")
        if uid:
            headers["X-User-Id"] = uid
        else:
            headers["X-No-User-Id"] = "1"
        if enterprise_id:
            headers["X-Enterprise-Id"] = enterprise_id
        else:
            headers["X-No-Enterprise-Id"] = "1"
        refresh_tok = getattr(profile, "refresh_token", None)
        if refresh_tok:
            headers["X-Refresh-Token"] = refresh_tok
        if domain:
            headers["X-Domain"] = domain
        else:
            headers["X-No-Department-Info"] = "1"
        headers["X-Product"] = "SaaS"
        return headers

    # ── 模型匹配与档案命名 ─────────────────────────────────────

    def matches_model(self, model: str) -> bool:
        return self.is_managed_profile_name(model) and bool(
            model[len(self.MODEL_NAME_PREFIX):].strip()
        )

    def profile_name_for_model(self, model_id: str) -> str:
        return f"{self.MODEL_NAME_PREFIX}{model_id}"

    # ── 动态模型目录 ───────────────────────────────────────────

    async def discover_models(
        self, record: AuthProfileRecord,
    ) -> list[dict[str, Any]]:
        """拉取账号权益模型目录。失败抛 RuntimeError。"""
        base = self._base
        headers = self.get_request_headers(record)
        headers["Authorization"] = f"Bearer {record.access_token or ''}"
        headers["Accept"] = "application/json"

        async with httpx.AsyncClient(timeout=15.0, proxy=_oauth_proxy()) as client:
            try:
                env, _status = await _request_envelope(
                    client, "GET", base + _V3_CONFIG_PATH, headers=headers,
                )
            except _UpstreamHTTPError as e:
                # 仅 404/405 回退旧接口，其余失败不再尝试
                if e.status in (404, 405):
                    return await self._discover_legacy(client, base, headers)
                raise
            data = _unwrap_data(env)

        agents = data.get("agents")
        if not isinstance(agents, list):
            raise RuntimeError("v3/config 响应缺少 agents")
        cli_models: list[Any] | None = None
        for agent in agents:
            if isinstance(agent, dict) and agent.get("name") == "cli":
                cli_models = agent.get("models")
                break
        if not isinstance(cli_models, list) or not cli_models:
            raise RuntimeError("v3/config 响应缺少 cli agent 模型列表")
        return [
            {"model": str(m).strip(), "display_name": str(m).strip()}
            for m in cli_models
            if str(m).strip()
        ]

    async def _discover_legacy(
        self,
        client: httpx.AsyncClient,
        base: str,
        headers: dict[str, str],
    ) -> list[dict[str, Any]]:
        env, status = await _request_envelope(
            client, "GET", base + _LEGACY_MODELS_PATH, headers=headers,
        )
        if status != 200:
            raise RuntimeError(f"legacy models HTTP {status}")
        data = _unwrap_data(env)
        models = data.get("models")
        if not isinstance(models, list) or not models:
            raise RuntimeError("legacy models 响应为空")
        out: list[dict[str, Any]] = []
        for m in models:
            if not isinstance(m, dict) or m.get("disabled"):
                continue
            mid = str(m.get("id") or "").strip()
            if not mid:
                continue
            entry: dict[str, Any] = {
                "model": mid,
                "display_name": str(m.get("name") or mid),
            }
            if m.get("contextWindow"):
                entry["context_length"] = m["contextWindow"]
            if m.get("maxTokens"):
                entry["max_completion_tokens"] = m["maxTokens"]
            out.append(entry)
        if not out:
            raise RuntimeError("legacy models 无可用模型")
        return out

    async def list_model_entries(
        self, record: AuthProfileRecord | None,
    ) -> list[dict[str, Any]]:
        if record is None:
            return []
        models = await self.discover_models(record)
        return [
            {
                "model": m["model"],
                "display_name": m.get("display_name") or m["model"],
                "profile_name": self.profile_name_for_model(m["model"]),
                "public_model_id": self.profile_name_for_model(m["model"]),
            }
            for m in models
        ]

    async def subscription_profiles_on_connect(
        self,
        record: AuthProfileRecord,
        existing_profiles: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """连接成功后自动建档一个默认模型（优先 auto，其次目录第一项）。"""
        try:
            models = await self.discover_models(record)
        except RuntimeError:
            logger.warning("WorkBuddy 模型目录发现失败，使用 auto 兜底", exc_info=True)
            models = [{"model": "auto", "display_name": "Auto"}]

        existing_names = {p.get("name", "") for p in existing_profiles}
        existing_models = {p.get("model", "") for p in existing_profiles}

        default_id = "auto" if any(m["model"] == "auto" for m in models) else (
            models[0]["model"] if models else "auto"
        )
        display = next(
            (m.get("display_name") for m in models if m["model"] == default_id),
            None,
        ) or default_id
        profile_name = self.profile_name_for_model(default_id)
        if profile_name in existing_names or profile_name in existing_models:
            return []

        headers_json = json.dumps(self.get_request_headers(record), ensure_ascii=False)
        return [{
            "name": profile_name,
            "model": profile_name,
            "api_key": "",
            "base_url": self._base + "/v2",
            "description": f"{display} — WorkBuddy 订阅登录（无需 API Key）",
            "protocol": self.PROTOCOL,
            "thinking_mode": "auto",
            "model_family": "",
            "custom_extra_headers": headers_json,
        }]

    def profile_display_info(self, profile: AuthProfileRecord) -> dict[str, Any]:
        extra = _parse_extra(profile)
        out: dict[str, Any] = {}
        nickname = str(extra.get("nickname") or "").strip()
        if nickname:
            out["nickname"] = nickname
        out["realm"] = self._realm
        uid = str(extra.get("uid") or profile.account_id or "")
        if uid:
            out["uid"] = uid
        return out


# ── 旧版单一 provider 数据迁移 ─────────────────────────────

_LEGACY_PROVIDER = "workbuddy"
_LEGACY_PREFIX = "workbuddy/"


def migrate_legacy_workbuddy(
    credential_store: Any,
    config_store: Any | None = None,
) -> None:
    """把旧版单一 ``workbuddy`` provider 的凭证与模型档案迁移到 realm 拆分。

    - auth_profiles 中 provider="workbuddy" 的记录按其 realm 搬到
      ``workbuddy-cn`` / ``workbuddy-global``（profile_name 不变），原记录删除；
    - model_profiles 中 ``workbuddy/<model>`` 档案改名为
      ``workbuddy-<realm>/<model>`` 并更新 base_url（幂等，可重复调用）。
    """
    realm = "cn"
    try:
        legacy = credential_store.get_active_profile(_LEGACY_PROVIDER)
    except Exception:
        logger.debug("读取旧版 workbuddy 凭证失败", exc_info=True)
        legacy = None
    if legacy is not None:
        extra = _parse_extra(legacy)
        detected = str(extra.get("realm") or "") or realm_from_access_token(
            legacy.access_token or ""
        )
        if detected in WorkBuddyProvider.REALMS:
            realm = detected
        try:
            credential_store.upsert_profile(
                user_id=legacy.user_id,
                provider=f"workbuddy-{realm}",
                profile_name=legacy.profile_name or "default",
                credential=ValidatedCredential(
                    access_token=legacy.access_token,
                    refresh_token=legacy.refresh_token,
                    expires_at=legacy.expires_at,
                    account_id=legacy.account_id,
                    plan_type=legacy.plan_type or realm,
                    credential_type=legacy.credential_type or "oauth",
                    extra_data={**extra, "realm": realm},
                ),
            )
            credential_store.delete_profile(
                legacy.user_id, _LEGACY_PROVIDER, legacy.profile_name
            )
            logger.info("已迁移旧版 workbuddy 凭证到 workbuddy-%s", realm)
        except Exception:
            logger.warning("迁移旧版 workbuddy 凭证失败", exc_info=True)

    if config_store is None:
        return
    new_prefix = f"workbuddy-{realm}/"
    try:
        for p in config_store.list_profiles():
            name = str(p.get("name") or "")
            model = str(p.get("model") or "")
            if not (name.startswith(_LEGACY_PREFIX) or model.startswith(_LEGACY_PREFIX)):
                continue
            model_id = (model if model.startswith(_LEGACY_PREFIX) else name)[
                len(_LEGACY_PREFIX):
            ]
            if not model_id:
                continue
            config_store.update_profile(
                name,
                new_name=f"{new_prefix}{model_id}",
                model=f"{new_prefix}{model_id}",
                base_url=_realm_base(realm) + "/v2",
            )
            logger.info("已迁移模型档案 %s -> %s%s", name, new_prefix, model_id)
    except Exception:
        logger.warning("迁移旧版 workbuddy 模型档案失败", exc_info=True)
