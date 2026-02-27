"""用户数据模型和认证流程的 Pydantic schema。"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints
from typing import Annotated


# ── 枚举 ──────────────────────────────────────────────────


class UserRole(str, Enum):
    ADMIN = "admin"
    USER = "user"
    READONLY = "readonly"


class OAuthProvider(str, Enum):
    GITHUB = "github"
    GOOGLE = "google"
    QQ = "qq"


# ── 数据库记录（基于 dict，SQLAlchemy 迁移前使用） ─────


class UserRecord:
    """存储在数据库中的用户记录。"""

    __slots__ = (
        "id", "email", "display_name", "password_hash", "role",
        "oauth_provider", "oauth_id", "avatar_url",
        "llm_api_key", "llm_base_url", "llm_model",
        "daily_token_limit", "monthly_token_limit",
        "allowed_models",
        "is_active", "created_at", "updated_at",
    )

    def __init__(
        self,
        *,
        id: str | None = None,
        email: str,
        display_name: str = "",
        password_hash: str | None = None,
        role: str = UserRole.USER,
        oauth_provider: str | None = None,
        oauth_id: str | None = None,
        avatar_url: str | None = None,
        llm_api_key: str | None = None,
        llm_base_url: str | None = None,
        llm_model: str | None = None,
        daily_token_limit: int = 0,
        monthly_token_limit: int = 0,
        allowed_models: str | None = None,
        is_active: bool = True,
        created_at: str | None = None,
        updated_at: str | None = None,
    ) -> None:
        now = datetime.now(tz=timezone.utc).isoformat()
        self.id = id or str(uuid.uuid4())
        self.email = email
        self.display_name = display_name or email.split("@")[0]
        self.password_hash = password_hash
        self.role = role
        self.oauth_provider = oauth_provider
        self.oauth_id = oauth_id
        self.avatar_url = avatar_url
        self.llm_api_key = llm_api_key
        self.llm_base_url = llm_base_url
        self.llm_model = llm_model
        self.daily_token_limit = daily_token_limit
        self.monthly_token_limit = monthly_token_limit
        self.allowed_models = allowed_models
        self.is_active = is_active
        self.created_at = created_at or now
        self.updated_at = updated_at or now

    def to_dict(self) -> dict:
        return {slot: getattr(self, slot) for slot in self.__slots__}

    @classmethod
    def from_dict(cls, data: dict) -> "UserRecord":
        return cls(**{k: v for k, v in data.items() if k in cls.__slots__})


# ── 请求 / 响应 schema ─────────────────────────────


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=255)]
    password: Annotated[str, StringConstraints(min_length=8, max_length=128)]
    display_name: Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)] = ""


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    password: Annotated[str, StringConstraints(min_length=1)]


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user: "UserPublic"


class RefreshRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    refresh_token: str


class UserPublic(BaseModel):
    """暴露给前端的安全用户表示。"""
    id: str
    email: str
    display_name: str
    role: str
    avatar_url: str | None = None
    has_custom_llm_key: bool = False
    has_password: bool = True
    allowed_models: list[str] = []
    created_at: str

    @classmethod
    def from_record(cls, rec: UserRecord) -> "UserPublic":
        import json as _json
        raw = getattr(rec, "allowed_models", None)
        if isinstance(raw, list):
            am = raw
        elif isinstance(raw, str) and raw:
            try:
                am = _json.loads(raw)
                if not isinstance(am, list):
                    am = []
            except Exception:
                am = []
        else:
            am = []
        return cls(
            id=rec.id,
            email=rec.email,
            display_name=rec.display_name,
            role=rec.role,
            avatar_url=rec.avatar_url,
            has_custom_llm_key=bool(rec.llm_api_key),
            has_password=bool(rec.password_hash),
            allowed_models=am,
            created_at=rec.created_at,
        )


class UserUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: Optional[Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)]] = None
    avatar_url: Optional[str] = None
    llm_api_key: Optional[str] = None
    llm_base_url: Optional[str] = None
    llm_model: Optional[str] = None


class OAuthCallbackParams(BaseModel):
    code: str
    state: str | None = None


# ── 邮箱验证 schema ─────────────────────────────


class RegisterPendingResponse(BaseModel):
    """注册后需要邮箱验证时返回的响应。"""
    requires_verification: bool = True
    message: str
    email: str


class VerifyEmailRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3)]
    code: Annotated[str, StringConstraints(strip_whitespace=True, min_length=6, max_length=6)]


class ResendCodeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3)]
    purpose: Annotated[str, StringConstraints(strip_whitespace=True)] = "register"


class ForgotPasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3)]


class ResetPasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3)]
    code: Annotated[str, StringConstraints(strip_whitespace=True, min_length=6, max_length=6)]
    new_password: Annotated[str, StringConstraints(min_length=8, max_length=128)]


class SetPasswordRequest(BaseModel):
    """OAuth 用户首次设置密码。"""
    model_config = ConfigDict(extra="forbid")
    new_password: Annotated[str, StringConstraints(min_length=8, max_length=128)]
