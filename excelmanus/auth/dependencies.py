"""FastAPI 依赖项：提取和验证当前用户。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from excelmanus.auth.models import UserRecord
from excelmanus.auth.security import decode_token

if TYPE_CHECKING:
    from excelmanus.auth.store import UserStore

_bearer_scheme = HTTPBearer(auto_error=False)


def _get_user_store(request: Request) -> "UserStore":
    store = getattr(request.app.state, "user_store", None)
    if store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="认证服务未初始化",
        )
    return store


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> UserRecord:
    """从 Authorization 请求头中提取并验证当前用户。

    将用户注入 ``request.state.user`` 供下游处理器使用。
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="缺少认证凭据",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = decode_token(credentials.credentials)
    if payload is None or payload.get("type") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效或过期的 token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id: str | None = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效的 token 载荷",
        )

    store = _get_user_store(request)
    user = store.get_by_id(user_id)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户不存在或已禁用",
        )

    request.state.user = user
    return user


async def get_current_user_from_request(request: Request) -> UserRecord:
    """从 Request 中解析 Bearer 凭据，用于手动调用场景。

    适用于未使用 FastAPI 依赖注入的场景。
    """
    credentials = await _bearer_scheme(request)
    return await get_current_user(request, credentials)


async def get_current_user_optional(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> UserRecord | None:
    """类似 get_current_user，但未认证时返回 None 而非抛出 401。"""
    if credentials is None:
        return None
    try:
        return await get_current_user(request, credentials)
    except HTTPException:
        return None


async def require_admin(
    user: UserRecord = Depends(get_current_user),
) -> UserRecord:
    """要求当前用户具有管理员角色。"""
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="需要管理员权限",
        )
    return user


def extract_user_id(request: Request) -> str | None:
    """从 request state 中提取 user_id（由 AuthMiddleware 设置）。

    认证未启用时返回 None，调用方应妥善处理。
    """
    return getattr(request.state, "user_id", None)
