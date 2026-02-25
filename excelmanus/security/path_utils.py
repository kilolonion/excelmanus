"""路径校验公共工具：工作区范围内路径解析与转换。

收敛 FileAccessGuard、WorkspaceTransaction、FileVersionManager 中
重复的「路径在工作区内」校验逻辑为单一入口。

注意：sandbox_hook 在子进程内运行，无法直接引用本模块，
其独立的 os.path 实现保留，但注释指向此公共版本。
"""
from __future__ import annotations

from pathlib import Path


def resolve_in_workspace(file_path: str, workspace_root: Path) -> Path:
    """解析并校验路径在工作区内。

    Args:
        file_path: 用户提供的文件路径（相对或绝对）
        workspace_root: 工作区根目录（已 resolve 的绝对路径）

    Returns:
        规范化后的绝对路径

    Raises:
        ValueError: 路径在工作区外
    """
    raw = Path(file_path).expanduser()
    path = raw if raw.is_absolute() else workspace_root / raw
    resolved = path.resolve(strict=False)
    try:
        resolved.relative_to(workspace_root)
    except ValueError:
        raise ValueError(f"文件路径在工作区外：{file_path}")
    return resolved


def to_workspace_relative(abs_path: Path, workspace_root: Path) -> str:
    """绝对路径 → 工作区相对路径字符串。

    Args:
        abs_path: 绝对路径
        workspace_root: 工作区根目录

    Returns:
        相对路径字符串（如 ``data/report.xlsx``）
    """
    return str(abs_path.relative_to(workspace_root))
