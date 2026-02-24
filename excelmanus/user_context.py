"""用户上下文：贯穿整个请求生命周期的不可变身份载体。

所有数据操作（DB、文件系统、审批）均通过 UserContext 获取用户归属，
杜绝 user_id 缺失或传错导致的跨用户数据泄露。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

_ANONYMOUS_SENTINEL = "__anonymous__"


@dataclass(frozen=True)
class UserContext:
    """不可变的用户身份上下文。

    - 多用户模式：user_id 为真实 UUID，workspace_root 指向 per-user 目录
    - 单用户/CLI 模式：user_id 为哨兵值，workspace_root 指向全局目录
    """

    user_id: str
    role: str = "user"
    workspace_root: Path = Path(".")

    @property
    def is_anonymous(self) -> bool:
        return self.user_id == _ANONYMOUS_SENTINEL

    @staticmethod
    def create(
        user_id: str,
        *,
        role: str = "user",
        global_workspace_root: str,
    ) -> UserContext:
        """为已认证用户创建上下文，workspace 自动隔离到 per-user 目录。"""
        root = Path(global_workspace_root) / "users" / user_id
        root.mkdir(parents=True, exist_ok=True)
        return UserContext(
            user_id=user_id,
            role=role,
            workspace_root=root.resolve(),
        )

    @staticmethod
    def anonymous(global_workspace_root: str) -> UserContext:
        """单用户/CLI 模式的匿名上下文，兼容旧行为。"""
        root = Path(global_workspace_root)
        root.mkdir(parents=True, exist_ok=True)
        return UserContext(
            user_id=_ANONYMOUS_SENTINEL,
            role="admin",
            workspace_root=root.resolve(),
        )

    @property
    def db_user_id(self) -> str | None:
        """数据库查询用的 user_id：匿名时返回 None 以兼容旧数据。"""
        return None if self.is_anonymous else self.user_id
