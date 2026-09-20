"""认证提供商基类与数据类型定义。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ValidatedCredential:
    """经过验证的凭证数据（从用户粘贴的 token 解析而来）。"""

    access_token: str
    refresh_token: str | None
    expires_at: str  # ISO 8601
    account_id: str
    plan_type: str
    credential_type: str = "oauth"
    extra_data: dict[str, Any] | None = None


@dataclass(frozen=True)
class RefreshedCredential:
    """刷新后的凭证数据。"""

    access_token: str
    refresh_token: str | None  # 可能被 provider 轮换
    expires_at: str  # ISO 8601


@dataclass(frozen=True)
class ResolvedCredential:
    """运行时解析出的 LLM 调用凭证。"""

    api_key: str
    base_url: str
    source: str  # 'oauth' | 'user_key' | 'system' | 'pool_oauth'
    provider: str | None = None
    protocol: str = "openai"
    pool_account_id: str | None = None
    pool_profile_name: str | None = None
    # 订阅上游要求的额外请求头（如 WorkBuddy 的 X-User-Id/X-Enterprise-Id）。
    extra_headers: dict[str, str] | None = None


@dataclass(frozen=True)
class AuthProfileSummary:
    """auth_profiles 表的摘要视图（不含明文 token）。"""

    id: str
    user_id: str
    provider: str
    profile_name: str
    credential_type: str
    expires_at: str | None
    account_id: str | None
    plan_type: str | None
    is_active: bool
    created_at: str
    updated_at: str


@dataclass
class AuthProfileRecord:
    """auth_profiles 表完整记录（含解密后的 token）。"""

    id: str
    user_id: str
    provider: str
    profile_name: str
    credential_type: str
    access_token: str | None
    refresh_token: str | None
    expires_at: str | None
    account_id: str | None
    plan_type: str | None
    extra_data: str | None
    is_active: bool
    created_at: str
    updated_at: str


class PKCECapable(ABC):
    """支持 PKCE OAuth 流程的 Provider 混入。"""

    @abstractmethod
    def generate_pkce(self) -> tuple[str, str]:
        """生成 code_verifier 和 code_challenge。"""

    @abstractmethod
    def build_authorize_url(
        self, redirect_uri: str, state: str, code_challenge: str,
    ) -> str:
        """构建授权 URL。"""

    @abstractmethod
    async def exchange_code(
        self, code: str, redirect_uri: str, code_verifier: str,
    ) -> ValidatedCredential:
        """用授权码交换 token。"""


class DeviceCodeCapable(ABC):
    """支持设备码登录的 Provider 混入。"""

    @abstractmethod
    async def request_user_code(self) -> dict[str, Any]:
        """向认证服务申请设备码。"""

    @abstractmethod
    async def poll_device_auth(
        self, device_auth_id: str, user_code: str,
    ) -> dict[str, Any] | None:
        """轮询设备授权；仍在等待时返回 None。"""

    @abstractmethod
    async def exchange_device_code(
        self, authorization_code: str, code_verifier: str,
    ) -> ValidatedCredential:
        """用设备授权码交换 token。"""


class BrowserPollCapable(ABC):
    """支持「浏览器授权 + 服务端轮询」登录的 Provider 混入。

    适用于无标准 OAuth 授权码/设备码、而是上游签发 login state +
    authUrl，由用户浏览器完成登录、客户端轮询取 token 的流程
    （如 Tencent CodeBuddy / WorkBuddy）。
    """

    @abstractmethod
    async def start_browser_login(self) -> dict[str, Any]:
        """发起登录，返回 {state, auth_url, expires_in?}。

        auth_url 由前端在系统浏览器中打开；state 用于后续轮询。
        """

    @abstractmethod
    async def poll_browser_login(
        self, state: str,
    ) -> ValidatedCredential | None:
        """轮询登录状态。返回 None 表示仍在等待用户完成授权。

        Raises:
            RuntimeError: state 无效/过期或上游错误。
        """


class LoopbackOAuthCapable(ABC):
    """支持「浏览器授权 + 本机回环回调」登录的 Provider 混入。

    适用于标准 OAuth 授权码流程、redirect_uri 指向 localhost 的场景
    （如 Google Antigravity：http://localhost:<port>/oauth-callback）。
    与 PKCECapable 的区别：不要求 PKCE，且回调端口/路径由 provider 决定。
    """

    #: 回环回调路径（如 "/oauth-callback"）。
    callback_path: str = "/oauth-callback"
    #: 首选回环端口；占用时可回退到随机端口（Google loopback 允许任意端口）。
    callback_port: int = 0
    #: state/登录会话有效期（秒）。
    oauth_ttl_seconds: int = 900

    @abstractmethod
    def build_authorize_url(self, state: str, redirect_uri: str) -> str:
        """构建浏览器授权 URL。"""

    @abstractmethod
    async def exchange_code(
        self, code: str, redirect_uri: str,
    ) -> ValidatedCredential:
        """用授权码交换 token 并返回验证后的凭证。"""


class AuthProvider(ABC):
    """认证提供商抽象基类。"""

    provider_name: str = ""
    # 用户私有模型档案使用 ``<MODEL_NAME_PREFIX><model_id>`` 命名，
    # 运行时据此识别「订阅管理档案」并剥离前缀得到上游模型 ID。
    MODEL_NAME_PREFIX: str = ""

    @abstractmethod
    def validate_token_data(self, raw_data: dict[str, Any]) -> ValidatedCredential:
        """验证用户粘贴的令牌数据，返回标准化凭证。

        Raises:
            ValueError: 令牌格式无效或缺少必要字段。
        """

    @abstractmethod
    async def refresh_token(self, refresh_token: str) -> RefreshedCredential:
        """刷新过期的 access token。

        Raises:
            RuntimeError: 刷新失败（网络错误、refresh token 过期等）。
        """

    async def refresh_profile(
        self, profile: AuthProfileRecord,
    ) -> RefreshedCredential:
        """用完整凭证记录刷新 token。

        默认只使用 refresh_token；需要账号上下文（如 enterpriseId）的
        provider 可重写本方法。
        """
        return await self.refresh_token(profile.refresh_token or "")

    @abstractmethod
    def get_api_credential(self, access_token: str) -> tuple[str, str]:
        """从 access token 获取 (api_key, base_url) 用于 LLM 调用。"""

    def get_request_headers(
        self, profile: AuthProfileRecord,
    ) -> dict[str, str]:
        """LLM 调用需要附带的 provider 专属请求头。默认无。"""
        return {}

    def matches_model(self, model: str) -> bool:
        """检查模型是否属于本 provider 管辖。默认返回 False。"""
        return False

    # ── 订阅管理档案（MODEL_NAME_PREFIX 前缀命名） ─────────────

    def is_managed_profile_name(self, name: str) -> bool:
        """判断名称是否为本 provider 的订阅管理档案/模型。"""
        return bool(self.MODEL_NAME_PREFIX) and name.startswith(
            self.MODEL_NAME_PREFIX
        )

    def model_from_profile_name(self, name: str) -> str | None:
        """从前缀化名称反解真实 model ID。默认剥离前缀，子类可加白名单校验。"""
        if not self.is_managed_profile_name(name):
            return None
        model_id = name[len(self.MODEL_NAME_PREFIX):]
        return model_id or None

    async def list_model_entries(
        self, record: AuthProfileRecord | None,
    ) -> list[dict[str, Any]]:
        """返回可用于建档的模型目录（{model, display_name, profile_name, ...}）。

        静态目录 provider 重写 ``list_supported_model_entries`` 即可；
        动态目录 provider 重写本方法做网络发现。
        """
        fn = getattr(self, "list_supported_model_entries", None)
        if callable(fn):
            return list(fn())
        return []

    async def subscription_profiles_on_connect(
        self,
        record: AuthProfileRecord,
        existing_profiles: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """连接成功后建议自动创建的模型档案列表（默认空，即不自动建档）。"""
        return []

    def profile_display_info(
        self, profile: AuthProfileRecord,
    ) -> dict[str, Any]:
        """status 端点返回的 provider 专属展示字段（如 email/nickname）。"""
        return {}
