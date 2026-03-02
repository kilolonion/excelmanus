"""OpenAI Codex (ChatGPT Plus/Pro 订阅) 认证提供商。"""

from __future__ import annotations

import base64
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

import httpx

from excelmanus.auth.providers.base import (
    AuthProvider,
    RefreshedCredential,
    ValidatedCredential,
)

logger = logging.getLogger(__name__)

_CODEX_MODEL_PATTERN = re.compile(r"(codex|gpt-5)", re.IGNORECASE)


def _parse_jwt_claims(token: str) -> dict[str, Any] | None:
    """解析 JWT payload（不验证签名）。"""
    parts = token.split(".")
    if len(parts) != 3:
        return None
    try:
        payload = parts[1]
        padding = "=" * ((4 - len(payload) % 4) % 4)
        decoded = base64.urlsafe_b64decode(payload + padding)
        return json.loads(decoded)
    except Exception:
        return None


def _extract_account_info(claims: dict[str, Any] | None) -> tuple[str, str]:
    """从 JWT claims 提取 account_id 和 plan_type。"""
    if not claims:
        return ("", "")
    auth = claims.get("https://api.openai.com/auth", {})
    account_id = auth.get("chatgpt_account_id", "")
    plan_type = auth.get("chatgpt_plan_type", "")
    return (account_id, plan_type)


class OpenAICodexProvider(AuthProvider):
    """OpenAI Codex OAuth 提供商。"""

    provider_name = "openai-codex"
    AUTH_ENDPOINT = "https://auth.openai.com/oauth/authorize"
    TOKEN_ENDPOINT = "https://auth.openai.com/oauth/token"
    CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
    SCOPE = "openid profile email offline_access"
    BASE_URL = "https://chatgpt.com/backend-api/codex"
    PROTOCOL = "openai_responses"
    REFRESH_MARGIN_SECONDS = 300
    MODEL_NAME_PREFIX = "openai-codex/"
    # 连接成功后自动暴露给当前用户的 Codex 可用模型（仅用户私有，不写入全局 model_profiles）。
    # model: 真实模型 ID；display_name: 前端展示友好别名。
    _SUPPORTED_MODELS: tuple[tuple[str, str], ...] = (
        ("gpt-5.3-codex-spark", "Codex Spark"),
        ("gpt-5.3-codex", "Codex 5.3"),
        ("gpt-5.2-codex", "Codex 5.2"),
        ("gpt-5.1-codex", "Codex 5.1"),
        ("gpt-5.1-codex-mini", "Codex Mini"),
        ("gpt-5.1-codex-max", "Codex Max"),
        ("codex-mini-latest", "Codex Mini Latest"),
        ("gpt-5.2", "GPT-5.2 (Codex)"),
        ("gpt-5.1", "GPT-5.1 (Codex)"),
    )

    # ── Device Code Flow (RFC 8628) ───────────────────────────
    DEVICE_USERCODE_URL = "https://auth.openai.com/api/accounts/deviceauth/usercode"
    DEVICE_TOKEN_URL = "https://auth.openai.com/api/accounts/deviceauth/token"
    DEVICE_CALLBACK_URI = "https://auth.openai.com/deviceauth/callback"
    DEVICE_VERIFY_URL = "https://auth.openai.com/codex/device"

    @classmethod
    async def request_user_code(cls) -> dict[str, Any]:
        """向 OpenAI 请求设备码，返回 {device_auth_id, user_code, interval, verification_url}。"""
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                cls.DEVICE_USERCODE_URL,
                json={"client_id": cls.CLIENT_ID, "scope": cls.SCOPE},
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
            )
            if resp.status_code == 404:
                raise RuntimeError(
                    "Device Code 登录未启用。请在 ChatGPT 设置 → 安全 中开启 "
                    "\"Enable device code authentication for Codex\"。"
                )
            if resp.status_code != 200:
                body = resp.text[:500]
                logger.warning("Device usercode 请求失败: %d %s", resp.status_code, body)
                raise RuntimeError(f"请求设备码失败 (HTTP {resp.status_code})")
            data = resp.json()
        user_code = data.get("user_code") or data.get("usercode") or ""
        return {
            "device_auth_id": data["device_auth_id"],
            "user_code": user_code,
            "interval": int(data.get("interval", 5)),
            "verification_url": cls.DEVICE_VERIFY_URL,
        }

    @classmethod
    async def poll_device_auth(cls, device_auth_id: str, user_code: str) -> dict[str, Any] | None:
        """单次轮询设备授权状态。

        返回 {authorization_code, code_verifier} 或 None（仍在等待）。
        Raises RuntimeError 表示永久失败。
        """
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                cls.DEVICE_TOKEN_URL,
                json={
                    "device_auth_id": device_auth_id,
                    "user_code": user_code,
                },
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
            )
        if resp.status_code in (403, 404):
            return None  # 授权仍在等待中
        if resp.status_code != 200:
            body = resp.text[:500]
            raise RuntimeError(f"设备授权轮询失败 (HTTP {resp.status_code}): {body}")
        data = resp.json()
        return {
            "authorization_code": data["authorization_code"],
            "code_verifier": data.get("code_verifier", ""),
        }

    async def exchange_device_code(
        self, authorization_code: str, code_verifier: str,
    ) -> ValidatedCredential:
        """用设备授权码交换 token（redirect_uri 固定为 OpenAI deviceauth callback）。"""
        return await self.exchange_code(
            code=authorization_code,
            redirect_uri=self.DEVICE_CALLBACK_URI,
            code_verifier=code_verifier,
        )

    # ── PKCE + OAuth Browser Flow (localhost only) ────────────

    @staticmethod
    def generate_pkce() -> tuple[str, str]:
        """生成 PKCE code_verifier 和 code_challenge (S256)。"""
        import hashlib
        import os
        verifier_bytes = os.urandom(32)
        code_verifier = base64.urlsafe_b64encode(verifier_bytes).rstrip(b"=").decode()
        digest = hashlib.sha256(code_verifier.encode()).digest()
        code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
        return code_verifier, code_challenge

    @classmethod
    def build_authorize_url(
        cls, redirect_uri: str, state: str, code_challenge: str,
    ) -> str:
        """构造 OpenAI OAuth 授权 URL（仅适用于 localhost redirect_uri）。"""
        from urllib.parse import urlencode
        params = {
            "response_type": "code",
            "client_id": cls.CLIENT_ID,
            "redirect_uri": redirect_uri,
            "scope": cls.SCOPE,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "state": state,
            "id_token_add_organizations": "true",
            "codex_cli_simplified_flow": "true",
            "originator": "codex_cli_rs",
        }
        return f"{cls.AUTH_ENDPOINT}?{urlencode(params)}"

    async def exchange_code(
        self, code: str, redirect_uri: str, code_verifier: str,
    ) -> ValidatedCredential:
        """用授权码交换 token 并返回验证后的凭证。"""
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                resp = await client.post(
                    self.TOKEN_ENDPOINT,
                    data={
                        "grant_type": "authorization_code",
                        "code": code,
                        "redirect_uri": redirect_uri,
                        "client_id": self.CLIENT_ID,
                        "code_verifier": code_verifier,
                    },
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
            except httpx.HTTPError as e:
                raise RuntimeError(f"Token 交换网络错误: {e}") from e

            if resp.status_code != 200:
                body = resp.text[:500]
                logger.warning("Codex token 交换失败: %d %s", resp.status_code, body)
                raise RuntimeError(f"Token 交换失败 (HTTP {resp.status_code})")

            data = resp.json()

        access_token = data.get("access_token", "")
        refresh_token = data.get("refresh_token")
        id_token = data.get("id_token", "")
        # 诊断日志：记录 token 响应中的 scope 字段
        _resp_scope = data.get("scope", "")
        logger.info("Codex token exchange response: scope=%r, token_type=%r, has_refresh=%s",
                     _resp_scope, data.get("token_type", ""), bool(refresh_token))

        if not access_token:
            raise RuntimeError("Token 交换响应中缺少 access_token")

        claims = _parse_jwt_claims(access_token)
        # 诊断日志：记录 JWT claims 中的 scope
        if claims:
            _jwt_scope = claims.get("scope", claims.get("scp", ""))
            logger.info("Codex JWT claims: scope=%r, aud=%r, iss=%r",
                         _jwt_scope, claims.get("aud", ""), claims.get("iss", ""))
        account_id, plan_type = _extract_account_info(claims)
        # 也尝试从 id_token 提取（某些情况下 access_token 中没有）
        if not account_id and id_token:
            id_claims = _parse_jwt_claims(id_token)
            account_id, plan_type = _extract_account_info(id_claims)

        exp = claims.get("exp") if claims else None
        if exp:
            expires_at = datetime.fromtimestamp(exp, tz=timezone.utc).isoformat()
        else:
            from datetime import timedelta
            expires_at = (datetime.now(tz=timezone.utc) + timedelta(hours=1)).isoformat()

        return ValidatedCredential(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=expires_at,
            account_id=account_id,
            plan_type=plan_type,
            credential_type="oauth",
            extra_data={"claims": claims} if claims else None,
        )

    def validate_token_data(self, raw_data: dict[str, Any]) -> ValidatedCredential:
        """验证粘贴的 token 数据。支持 Codex CLI / OpenClaw / 简化 三种格式。"""
        access_token = (
            raw_data.get("token")
            or raw_data.get("access")
            or raw_data.get("access_token")
            or ""
        )
        refresh_token = raw_data.get("refresh_token") or raw_data.get("refresh")

        if not access_token:
            raise ValueError(
                "缺少 access token。请粘贴完整的 auth.json 内容，"
                "需包含 'token'、'access' 或 'access_token' 字段。"
            )

        expires_at = self._parse_expires(raw_data)
        claims = _parse_jwt_claims(access_token)
        account_id, plan_type = _extract_account_info(claims)

        return ValidatedCredential(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=expires_at,
            account_id=account_id,
            plan_type=plan_type,
            credential_type="oauth",
            extra_data={"claims": claims} if claims else None,
        )

    async def refresh_token(self, refresh_token: str) -> RefreshedCredential:
        """通过 OpenAI token endpoint 刷新 access token。"""
        if not refresh_token:
            raise RuntimeError("无 refresh token，无法刷新。请重新运行 codex login。")

        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                resp = await client.post(
                    self.TOKEN_ENDPOINT,
                    data={
                        "grant_type": "refresh_token",
                        "refresh_token": refresh_token,
                        "client_id": self.CLIENT_ID,
                    },
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
            except httpx.HTTPError as e:
                raise RuntimeError(f"刷新请求网络错误: {e}") from e

            if resp.status_code != 200:
                body = resp.text[:500]
                logger.warning("Codex token 刷新失败: %d %s", resp.status_code, body)
                raise RuntimeError(
                    f"Token 刷新失败 (HTTP {resp.status_code})。"
                    "请重新运行 codex login 获取新令牌。"
                )

            data = resp.json()

        new_access = data.get("access_token", "")
        new_refresh = data.get("refresh_token") or refresh_token
        if not new_access:
            raise RuntimeError("刷新响应中缺少 access_token")

        claims = _parse_jwt_claims(new_access)
        exp = claims.get("exp") if claims else None
        if exp:
            expires_at = datetime.fromtimestamp(exp, tz=timezone.utc).isoformat()
        else:
            expires_at = datetime.now(tz=timezone.utc).isoformat()

        return RefreshedCredential(
            access_token=new_access,
            refresh_token=new_refresh,
            expires_at=expires_at,
        )

    def get_api_credential(self, access_token: str) -> tuple[str, str]:
        """返回 (api_key, base_url)。Codex OAuth token 直接作为 Bearer token。"""
        return (access_token, self.BASE_URL)

    @classmethod
    def list_supported_models(cls) -> tuple[str, ...]:
        """返回默认支持的 Codex 模型列表。"""
        return tuple(model_id for model_id, _display_name in cls._SUPPORTED_MODELS)

    @classmethod
    def list_supported_model_entries(cls) -> list[dict[str, str]]:
        """返回模型目录（含友好别名与前缀化公开 ID）。"""
        entries: list[dict[str, str]] = []
        for model_id, display_name in cls._SUPPORTED_MODELS:
            public_id = cls.profile_name_for_model(model_id)
            entries.append({
                "model": model_id,
                "display_name": display_name,
                "profile_name": public_id,
                "public_model_id": public_id,
            })
        return entries

    @classmethod
    def profile_name_for_model(cls, model_id: str) -> str:
        """将模型 ID 转换为用户私有 profile 名称。"""
        return f"{cls.MODEL_NAME_PREFIX}{model_id}"

    @classmethod
    def is_codex_profile_name(cls, profile_name: str) -> bool:
        """判断 profile 名称是否为 Codex 用户私有模型。"""
        return profile_name.startswith(cls.MODEL_NAME_PREFIX)

    @classmethod
    def model_from_profile_name(cls, profile_name: str) -> str | None:
        """从前缀化 profile 名称反解真实 model ID。"""
        if not cls.is_codex_profile_name(profile_name):
            return None
        model_id = profile_name[len(cls.MODEL_NAME_PREFIX):]
        if not model_id:
            return None
        if model_id not in cls.list_supported_models():
            return None
        return model_id

    def matches_model(self, model: str) -> bool:
        """检查模型是否属于 OpenAI Codex 订阅范畴。"""
        return bool(_CODEX_MODEL_PATTERN.search(model))

    @staticmethod
    def _parse_expires(raw_data: dict[str, Any]) -> str:
        """从多种格式解析过期时间为 ISO 8601 字符串。"""
        # 格式 1: ISO 字符串
        if ea := raw_data.get("expires_at"):
            if isinstance(ea, str):
                return ea
            if isinstance(ea, (int, float)):
                return datetime.fromtimestamp(ea, tz=timezone.utc).isoformat()

        # 格式 2: Unix ms (OpenClaw)
        if exp := raw_data.get("expires"):
            if isinstance(exp, (int, float)):
                ts = exp / 1000 if exp > 1e12 else exp
                return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()

        # 格式 3: 从 JWT 提取
        access = (
            raw_data.get("token")
            or raw_data.get("access")
            or raw_data.get("access_token")
            or ""
        )
        if access:
            claims = _parse_jwt_claims(access)
            if claims and "exp" in claims:
                return datetime.fromtimestamp(
                    claims["exp"], tz=timezone.utc
                ).isoformat()

        # 回退：1 小时后
        from datetime import timedelta
        return (
            datetime.now(tz=timezone.utc) + timedelta(hours=1)
        ).isoformat()
