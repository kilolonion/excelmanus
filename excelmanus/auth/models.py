"""User data models and Pydantic schemas for auth flows."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints
from typing import Annotated


# ── Enums ──────────────────────────────────────────────────


class UserRole(str, Enum):
    ADMIN = "admin"
    USER = "user"
    READONLY = "readonly"


class OAuthProvider(str, Enum):
    GITHUB = "github"
    GOOGLE = "google"


# ── Database record (dict-based, used before SQLAlchemy migration) ─────


class UserRecord:
    """Plain user record stored in the database."""

    __slots__ = (
        "id", "email", "display_name", "password_hash", "role",
        "oauth_provider", "oauth_id", "avatar_url",
        "llm_api_key", "llm_base_url", "llm_model",
        "daily_token_limit", "monthly_token_limit",
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
        self.is_active = is_active
        self.created_at = created_at or now
        self.updated_at = updated_at or now

    def to_dict(self) -> dict:
        return {slot: getattr(self, slot) for slot in self.__slots__}

    @classmethod
    def from_dict(cls, data: dict) -> "UserRecord":
        return cls(**{k: v for k, v in data.items() if k in cls.__slots__})


# ── Request / Response schemas ─────────────────────────────


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
    """Safe user representation exposed to the frontend."""
    id: str
    email: str
    display_name: str
    role: str
    avatar_url: str | None = None
    has_custom_llm_key: bool = False
    created_at: str

    @classmethod
    def from_record(cls, rec: UserRecord) -> "UserPublic":
        return cls(
            id=rec.id,
            email=rec.email,
            display_name=rec.display_name,
            role=rec.role,
            avatar_url=rec.avatar_url,
            has_custom_llm_key=bool(rec.llm_api_key),
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
