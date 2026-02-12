"""文件访问守卫：路径规范化与越界校验。"""

from __future__ import annotations

from pathlib import Path


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

    def resolve_and_validate(self, user_path: str) -> Path:
        """规范化用户路径并校验是否位于工作目录内。

        处理流程：
        1. 检查原始输入是否包含路径穿越特征（`..`）
        2. 将用户路径解析为绝对路径（相对路径基于 workspace_root）
        3. 使用 Path.resolve() 规范化（消除符号链接等）
        4. 校验规范化后的路径是否位于 workspace_root 之下
        5. 若路径为符号链接，校验链接目标是否也在 workspace_root 内

        Args:
            user_path: 用户提供的文件路径（相对或绝对）

        Returns:
            规范化后的绝对路径

        Raises:
            SecurityViolationError: 路径越界或符号链接越界时抛出
        """
        # 明确拒绝任何包含 `..` 的输入路径（即使最终解析仍在工作目录内）
        normalized_for_check = user_path.replace("\\", "/")
        if ".." in Path(normalized_for_check).parts:
            raise SecurityViolationError(
                f"路径穿越特征被拒绝：{user_path!r} 包含 '..'"
            )

        raw = Path(user_path)

        # 相对路径基于工作目录解析
        if not raw.is_absolute():
            raw = self._root / raw

        # 规范化路径（消除 .., 符号链接等）
        resolved = raw.resolve()

        # 校验路径是否在工作目录内
        try:
            resolved.relative_to(self._root)
        except ValueError:
            raise SecurityViolationError(
                f"路径越界：{user_path!r} 解析后位于工作目录之外"
            )

        # 额外校验：若原始路径是符号链接，确认链接目标也在工作目录内
        if raw.is_symlink():
            link_target = raw.resolve(strict=False)
            try:
                link_target.relative_to(self._root)
            except ValueError:
                raise SecurityViolationError(
                    f"符号链接越界：{user_path!r} 的链接目标位于工作目录之外"
                )

        return resolved
