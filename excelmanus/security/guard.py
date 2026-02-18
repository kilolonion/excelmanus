"""文件访问守卫：路径规范化与越界校验。"""

from __future__ import annotations

from pathlib import Path
from unicodedata import normalize
from urllib.parse import unquote


class SecurityViolationError(Exception):
    """路径越界或路径穿越时抛出的安全异常。"""


class FileAccessGuard:
    """文件访问守卫，确保所有文件操作限制在工作目录内。"""

    def __init__(self, workspace_root: str) -> None:
        # 解析并规范化工作目录的绝对路径
        self._root: Path = Path(workspace_root).resolve()

    @property
    def workspace_root(self) -> Path:
        """返回规范化后的工作目录路径。"""
        return self._root

    def _resolve_with_existing_ancestor(self, raw: Path, user_path: str) -> Path:
        """基于最近已存在祖先进行 strict 解析，保留不存在的尾部路径。"""
        missing_parts: list[str] = []
        cursor = raw

        while not cursor.exists() and not cursor.is_symlink():
            parent = cursor.parent
            if parent == cursor:
                raise SecurityViolationError(f"路径不存在：{user_path!r}")
            missing_parts.append(cursor.name)
            cursor = parent

        try:
            resolved = cursor.resolve(strict=True)
        except FileNotFoundError as exc:
            raise SecurityViolationError(
                f"路径不存在或符号链接目标不存在：{user_path!r}"
            ) from exc

        for part in reversed(missing_parts):
            resolved = resolved / part
        return resolved

    @staticmethod
    def _contains_parent_traversal_token(user_path: str) -> bool:
        """检测路径是否包含父目录穿越片段（含 URL 编码与 Unicode 规范化场景）。"""
        normalized = user_path
        # 迭代解码，覆盖 `%252e%252e` 这类多层编码输入。
        for _ in range(3):
            decoded = unquote(normalized)
            if decoded == normalized:
                break
            normalized = decoded
        normalized = normalize("NFKC", normalized).replace("\\", "/")
        return ".." in Path(normalized).parts

    def resolve_and_validate(self, user_path: str) -> Path:
        """规范化用户路径并校验是否位于工作目录内。

        处理流程：
        1. 检查原始输入是否包含路径穿越特征（`..`）
        2. 将用户路径解析为绝对路径（相对路径基于 workspace_root）
        3. 对最近已存在祖先使用 strict 解析（避免悬空符号链接歧义）
        4. 将不存在尾部路径拼回，并校验结果是否位于 workspace_root 之下

        Args:
            user_path: 用户提供的文件路径（相对或绝对）

        Returns:
            规范化后的绝对路径

        Raises:
            SecurityViolationError: 路径越界、路径穿越或悬空符号链接时抛出
        """
        # 明确拒绝任何包含 `..` 的输入路径（即使最终解析仍在工作目录内）
        if self._contains_parent_traversal_token(user_path):
            raise SecurityViolationError(
                f"路径穿越特征被拒绝：{user_path!r} 包含 '..'"
            )

        raw = Path(user_path)

        # 相对路径基于工作目录解析
        if not raw.is_absolute():
            raw = self._root / raw

        # 使用 strict 解析已存在祖先，避免 strict=False 对悬空符号链接的歧义
        resolved = self._resolve_with_existing_ancestor(raw, user_path)

        # 校验路径是否在工作目录内
        try:
            resolved.relative_to(self._root)
        except ValueError:
            raise SecurityViolationError(
                f"路径越界：{user_path!r} 解析后位于工作目录之外"
            )

        return resolved
