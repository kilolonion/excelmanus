"""Backup 沙盒模式：文件操作重定向到工作副本。"""

from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path


_EXCEL_EXTENSIONS = frozenset({".xlsx", ".xls", ".xlsm", ".xlsb", ".csv"})


class BackupManager:
    """管理文件备份映射，将读写操作重定向到 outputs/backups/ 下的副本。

    支持跨会话共享 ``shared_path_map``：当多个 session 的 BackupManager
    使用同一个字典实例时，对某文件的首次备份会被后续 session 复用，
    避免同一文件被反复备份。
    """

    def __init__(
        self,
        workspace_root: str,
        backup_dir: str = "outputs/backups",
        scope: str = "all",
        shared_path_map: dict[str, str] | None = None,
    ) -> None:
        self._workspace_root = Path(workspace_root).expanduser().resolve()
        self._backup_dir = (self._workspace_root / backup_dir).resolve()
        self._backup_dir.mkdir(parents=True, exist_ok=True)
        self._scope = scope  # "all" | "excel_only"
        # 原始绝对路径 → 备份绝对路径（可跨 session 共享）
        self._path_map: dict[str, str] = shared_path_map if shared_path_map is not None else {}
        self._timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")

    @property
    def scope(self) -> str:
        return self._scope

    @property
    def backup_dir(self) -> Path:
        return self._backup_dir

    def _is_in_scope(self, path: Path) -> bool:
        """判断文件是否在备份范围内。"""
        if self._scope == "all":
            return True
        if self._scope == "excel_only":
            return path.suffix.lower() in _EXCEL_EXTENSIONS
        return True

    def _resolve_and_validate(self, file_path: str) -> Path:
        """解析并校验路径在工作区内。"""
        raw = Path(file_path).expanduser()
        path = raw if raw.is_absolute() else self._workspace_root / raw
        resolved = path.resolve(strict=False)
        try:
            resolved.relative_to(self._workspace_root)
        except ValueError:
            raise ValueError(
                f"文件路径在工作区外，无法备份：{file_path}"
            )
        return resolved

    def _make_backup_name(self, original: Path) -> str:
        """生成备份文件名：<stem>_<timestamp><suffix>。"""
        stem = original.stem
        suffix = original.suffix
        return f"{stem}_{self._timestamp}{suffix}"

    def ensure_backup(self, file_path: str) -> str:
        """确保文件有备份副本。首次触及时复制，后续返回缓存路径。

        Returns:
            备份文件的绝对路径。如果文件不在 scope 内或原文件不存在，返回原路径。
        """
        resolved = self._resolve_and_validate(file_path)
        key = str(resolved)

        # 已缓存
        if key in self._path_map:
            return self._path_map[key]

        # 不在 scope 内，返回原路径
        if not self._is_in_scope(resolved):
            return str(resolved)

        # 原文件不存在时，不创建虚假映射，直接返回原路径
        if not (resolved.exists() and resolved.is_file()):
            return str(resolved)

        backup_name = self._make_backup_name(resolved)
        backup_path = self._backup_dir / backup_name

        backup_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(resolved), str(backup_path))

        self._path_map[key] = str(backup_path)
        return str(backup_path)

    def resolve_path(self, file_path: str) -> str:
        """查询路径是否有备份重定向。有则返回备份路径，否则返回原路径。"""
        resolved = self._resolve_and_validate(file_path)
        return self._path_map.get(str(resolved), str(resolved))

    def apply_all(self) -> list[dict[str, str]]:
        """将所有备份副本覆盖回原始位置。

        Returns:
            已应用的文件列表 [{"original": ..., "backup": ...}]
        """
        applied: list[dict[str, str]] = []
        for original_str, backup_str in self._path_map.items():
            backup_path = Path(backup_str)
            original_path = Path(original_str)
            if backup_path.exists():
                original_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(backup_path), str(original_path))
                applied.append({
                    "original": original_str,
                    "backup": backup_str,
                })
        return applied

    def apply_one(self, file_path: str) -> dict[str, str] | None:
        """将单个文件的备份副本覆盖回原始位置并移除映射。

        Returns:
            {"original": ..., "backup": ...} 或 None（如果文件不在备份映射中）
        """
        resolved = self._resolve_and_validate(file_path)
        key = str(resolved)
        backup_str = self._path_map.get(key)
        if backup_str is None:
            return None
        backup_path = Path(backup_str)
        original_path = Path(key)
        if backup_path.exists():
            original_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(backup_path), str(original_path))
        del self._path_map[key]
        return {"original": key, "backup": backup_str}

    def discard_one(self, file_path: str) -> bool:
        """移除单个文件的备份映射（不删除备份文件）。

        Returns:
            True 如果成功移除，False 如果文件不在映射中。
        """
        resolved = self._resolve_and_validate(file_path)
        key = str(resolved)
        if key not in self._path_map:
            return False
        del self._path_map[key]
        return True

    def discard_all(self) -> None:
        """清除所有备份映射（不删除备份文件，保留供手动查看）。"""
        self._path_map.clear()

    def list_backups(self) -> list[dict[str, str]]:
        """列出当前管理的备份文件。"""
        result: list[dict[str, str]] = []
        for original, backup in self._path_map.items():
            result.append({
                "original": original,
                "backup": backup,
                "exists": str(Path(backup).exists()),
            })
        return result
