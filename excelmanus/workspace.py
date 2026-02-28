"""统一工作区隔离层。

每个会话在 ``IsolatedWorkspace`` 内运行，封装文件系统根目录、
事务化暂存、沙盒配置与配额。
"""

from __future__ import annotations

import logging
import os
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TYPE_CHECKING

from excelmanus.security.path_utils import resolve_in_workspace

if TYPE_CHECKING:
    from excelmanus.file_registry import FileRegistry

logger = logging.getLogger(__name__)

_EXCEL_EXTENSIONS = frozenset({".xlsx", ".xls", ".xlsm", ".xlsb", ".csv"})


# ── 配额辅助（自 auth/workspace.py 迁移） ────────


DEFAULT_MAX_SIZE_MB = 200
DEFAULT_MAX_FILES = 1000
ADMIN_DEFAULT_MAX_SIZE_MB = 1024


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if raw:
        try:
            return int(raw)
        except ValueError:
            pass
    return default


@dataclass(frozen=True)
class QuotaPolicy:
    """每工作区的存储上限。"""

    max_bytes: int
    max_files: int

    @staticmethod
    def from_env() -> QuotaPolicy:
        max_mb = _env_int("EXCELMANUS_WORKSPACE_MAX_SIZE_MB", DEFAULT_MAX_SIZE_MB)
        max_files = _env_int("EXCELMANUS_WORKSPACE_MAX_FILES", DEFAULT_MAX_FILES)
        return QuotaPolicy(max_bytes=max_mb * 1024 * 1024, max_files=max_files)

    @classmethod
    def for_user(cls, user_record: Any) -> "QuotaPolicy":
        """从用户记录读取个人配额，0 或缺失时回退全局默认。

        管理员默认 1 GB，普通用户默认 200 MB。
        """
        env = cls.from_env()
        user_mb = getattr(user_record, "max_storage_mb", 0) or 0
        user_files = getattr(user_record, "max_files", 0) or 0
        is_admin = getattr(user_record, "role", "") == "admin"
        if user_mb > 0:
            default_bytes = user_mb * 1024 * 1024
        elif is_admin:
            default_bytes = ADMIN_DEFAULT_MAX_SIZE_MB * 1024 * 1024
        else:
            default_bytes = env.max_bytes
        return cls(
            max_bytes=default_bytes,
            max_files=user_files if user_files > 0 else env.max_files,
        )

    @property
    def max_size_mb(self) -> float:
        return round(self.max_bytes / (1024 * 1024), 2)


@dataclass
class WorkspaceUsage:
    """当前工作区的存储占用。"""

    total_bytes: int
    file_count: int
    max_bytes: int
    max_files: int
    files: list[dict]

    @property
    def size_mb(self) -> float:
        return round(self.total_bytes / (1024 * 1024), 2)

    @property
    def max_size_mb(self) -> float:
        return round(self.max_bytes / (1024 * 1024), 2)

    @property
    def over_size(self) -> bool:
        return self.total_bytes > self.max_bytes

    @property
    def over_files(self) -> bool:
        return self.file_count > self.max_files

    def to_dict(self) -> dict:
        return {
            "total_bytes": self.total_bytes,
            "size_mb": self.size_mb,
            "file_count": self.file_count,
            "max_size_mb": self.max_size_mb,
            "max_files": self.max_files,
            "over_size": self.over_size,
            "over_files": self.over_files,
            "files": self.files,
        }


# 系统/配置文件前缀与名称，不计入用户配额
_SYSTEM_DIR_PREFIXES = frozenset({"outputs/backups", "outputs/approvals", "outputs/.versions", "scripts"})
_SYSTEM_FILE_NAMES = frozenset({"data.db", "data.db-shm", "data.db-wal"})


def _is_system_file(rel: Path) -> bool:
    """判断相对路径是否属于系统/配置文件，不应计入用户配额。"""
    if rel.name in _SYSTEM_FILE_NAMES:
        return True
    rel_str = str(rel)
    return any(rel_str.startswith(prefix) for prefix in _SYSTEM_DIR_PREFIXES)


def scan_workspace(workspace_dir: str) -> list[dict]:
    """遍历 workspace_dir 并返回按修改时间排序的文件元数据。

    跳过隐藏目录和系统文件（data.db, backups, approvals 等），
    仅统计用户实际创建/上传的文件。
    """
    results: list[dict] = []
    ws_path = Path(workspace_dir)
    if not ws_path.is_dir():
        return results
    for entry in ws_path.rglob("*"):
        if not entry.is_file():
            continue
        # 跳过隐藏目录（如 .avatars, .tmp）中的文件
        rel = entry.relative_to(ws_path)
        if any(part.startswith(".") for part in rel.parts[:-1]):
            continue
        # 跳过系统/配置文件
        if _is_system_file(rel):
            continue
        try:
            stat = entry.stat()
            results.append({
                "path": str(rel),
                "name": entry.name,
                "size": stat.st_size,
                "modified_at": stat.st_mtime,
            })
        except OSError:
            continue
    results.sort(key=lambda f: f["modified_at"])
    return results


def _cleanup_empty_parents(child: Path, root: Path) -> None:
    parent = child.parent
    while parent != root and parent.is_dir():
        try:
            parent.rmdir()
            parent = parent.parent
        except OSError:
            break


# ── 沙盒配置 ──────────────────────────────────


@dataclass(frozen=True)
class SandboxConfig:
    """每工作区的沙盒配置。"""

    docker_enabled: bool = False


# ── 工作区事务 ───────────────────────────────────


class WorkspaceTransaction:
    """统一的事务化文件层。

    所有暂存变更均位于 staging_dir 下，直至显式提交或回滚。
    内部统一委托 FileRegistry 做版本跟踪与物理文件操作。
    """

    def __init__(
        self,
        workspace_root: Path,
        staging_dir: Path,
        tx_id: str,
        *,
        registry: "FileRegistry",
        scope: str = "all",
    ) -> None:
        self._workspace_root = workspace_root
        self._staging_dir = staging_dir
        self._staging_dir.mkdir(parents=True, exist_ok=True)
        self._tx_id = tx_id
        self._scope = scope

        self._registry = registry

    # -- 属性 ----------------------------------------------------------

    @property
    def tx_id(self) -> str:
        return self._tx_id

    @property
    def scope(self) -> str:
        return self._scope

    @property
    def staging_dir(self) -> Path:
        return self._staging_dir

    @property
    def workspace_root(self) -> Path:
        return self._workspace_root

    @property
    def registry(self) -> "FileRegistry":
        """关联的 FileRegistry 实例（供 ApprovalManager 等共享）。"""
        return self._registry

    @property
    def tracked_originals(self) -> set[str]:
        return set(self._registry.staged_file_map().keys())

    # -- 路径辅助 --------------------------------------------------------

    def _resolve_and_validate(self, file_path: str) -> Path:
        return resolve_in_workspace(file_path, self._workspace_root)

    # -- 核心操作 -----------------------------------------------------

    def stage_for_write(self, file_path: str) -> str:
        """确保 file_path 存在对应的暂存副本。"""
        return self._registry.stage_for_write(
            file_path, ref_id=self._tx_id, scope=self._scope,
        )

    def resolve_read(self, file_path: str) -> str:
        """若存在暂存路径则返回暂存路径，否则返回原路径。"""
        resolved = self._resolve_and_validate(file_path)
        rel = self._registry._to_rel(resolved)
        staged = self._registry.get_staged_path(rel)
        return staged if staged is not None else str(resolved)

    def commit_all(self) -> list[dict[str, str]]:
        """将所有暂存文件复制回原位置。"""
        return self._registry.commit_all_staged()

    def commit_one(self, file_path: str) -> dict[str, str] | None:
        """将单个暂存文件提交回原位置。"""
        return self._registry.commit_staged(file_path)

    def rollback_one(self, file_path: str) -> bool:
        """丢弃单个暂存文件。"""
        return self._registry.discard_staged(file_path)

    def rollback_all(self) -> None:
        """丢弃所有暂存文件。"""
        self._registry.discard_all_staged()

    def cleanup_stale(self) -> int:
        """移除暂存文件已不存在的条目。"""
        return self._registry.prune_stale_staging()

    def to_relative(self, abs_path: str) -> str:
        """将绝对路径转换为相对工作区的 ./ 路径。"""
        try:
            rel = Path(abs_path).relative_to(self._workspace_root)
            return f"./{rel}"
        except ValueError:
            return abs_path

    def list_staged(self) -> list[dict[str, str]]:
        """列出当前所有暂存文件。"""
        return self._registry.list_staged()

    def staged_file_map(self) -> dict[str, str]:
        """返回 original_abs → staged_abs 的映射。"""
        return self._registry.staged_file_map()

    def undo_commit(self, original_path: str, undo_path: str) -> bool:
        """撤销一次 commit。"""
        return self._registry.undo_commit(original_path, undo_path)

    def diff_staged_summary(self, file_path: str) -> dict | None:
        """返回 staged vs original 的轻量变更摘要。"""
        return self._registry.diff_staged_summary(file_path)

    def register_cow_mappings(self, mapping: dict[str, str]) -> None:
        """将子进程级 CoW 映射合并进本事务。"""
        if not mapping:
            return
        for src_rel, dst_rel in mapping.items():
            self._registry.register_cow_mapping(src_rel, dst_rel)


# ── 沙盒环境 ─────────────────────────────────────────────


class SandboxEnv:
    """沙盒代码运行的执行环境。

    绑定沙盒配置、工作区挂载路径与当前事务，
    使 CoW 日志写入事务的暂存区。
    """

    def __init__(
        self,
        workspace: "IsolatedWorkspace",
        transaction: WorkspaceTransaction | None = None,
    ) -> None:
        self.workspace = workspace
        self.transaction = transaction

    @property
    def docker_enabled(self) -> bool:
        return self.workspace.sandbox_config.docker_enabled

    def get_docker_mount(self) -> str:
        return str(self.workspace.root_dir)

    def get_cow_log_path(self) -> Path:
        if self.transaction is not None:
            return self.transaction.staging_dir / f"_cow_{self.transaction.tx_id[:12]}.log"
        tmpdir = self.workspace.root_dir / ".tmp"
        tmpdir.mkdir(parents=True, exist_ok=True)
        return tmpdir / f"_cow_{secrets.token_hex(6)}.log"

    def get_staging_map_json(self) -> str:
        """导出 staging 映射为 JSON（注入子进程环境变量用）。

        格式: {"<abs_original>": "<abs_staged>", ...}
        无 transaction 或无 staging 条目时返回 "{}"。
        """
        import json
        if self.transaction is None:
            return "{}"
        file_map = self.transaction.staged_file_map()
        if not file_map:
            return "{}"
        return json.dumps(file_map, ensure_ascii=False)

    def get_tmp_dir(self) -> Path:
        tmpdir = self.workspace.root_dir / ".tmp"
        tmpdir.mkdir(parents=True, exist_ok=True)
        return tmpdir


# ── 隔离工作区 ──────────────────────────────────────


class IsolatedWorkspace:
    """用户/会话文件系统隔离的核心抽象。

    持有解析后的工作区根目录、沙盒配置与配额策略，
    为文件变更暂存创建每会话的 WorkspaceTransaction 实例。
    """

    def __init__(
        self,
        root_dir: str | Path,
        *,
        owner_id: str | None = None,
        sandbox_config: SandboxConfig | None = None,
        quota: QuotaPolicy | None = None,
        transaction_enabled: bool = True,
        transaction_scope: str = "all",
    ) -> None:
        self._root_dir = Path(root_dir).expanduser().resolve()
        self._root_dir.mkdir(parents=True, exist_ok=True)
        self._owner_id = owner_id
        self._sandbox_config = sandbox_config or SandboxConfig()
        self._quota = quota or QuotaPolicy.from_env()
        self._transaction_enabled = transaction_enabled
        self._transaction_scope = transaction_scope
        self._staging_base = (self._root_dir / "outputs" / "backups").resolve()

    # -- 属性 ----------------------------------------------------------

    @property
    def root_dir(self) -> Path:
        return self._root_dir

    @property
    def owner_id(self) -> str | None:
        return self._owner_id

    @property
    def sandbox_config(self) -> SandboxConfig:
        return self._sandbox_config

    @sandbox_config.setter
    def sandbox_config(self, value: SandboxConfig) -> None:
        self._sandbox_config = value

    @property
    def quota(self) -> QuotaPolicy:
        return self._quota

    @property
    def transaction_enabled(self) -> bool:
        return self._transaction_enabled

    @transaction_enabled.setter
    def transaction_enabled(self, value: bool) -> None:
        self._transaction_enabled = value

    @property
    def transaction_scope(self) -> str:
        return self._transaction_scope

    # -- 工厂方法 -----------------------------------------------------

    def create_transaction(
        self,
        registry: "FileRegistry",
        tx_id: str | None = None,
    ) -> WorkspaceTransaction:
        """创建绑定到本工作区的新 WorkspaceTransaction。"""
        if tx_id is None:
            tx_id = secrets.token_hex(8)
        return WorkspaceTransaction(
            workspace_root=self._root_dir,
            staging_dir=self._staging_base,
            tx_id=tx_id,
            scope=self._transaction_scope,
            registry=registry,
        )

    def create_sandbox_env(
        self, transaction: WorkspaceTransaction | None = None,
    ) -> SandboxEnv:
        """创建在本工作区内执行代码用的 SandboxEnv。"""
        return SandboxEnv(workspace=self, transaction=transaction)

    # -- 配额操作（委托自 auth/workspace.py） -----------------

    def get_usage(self) -> WorkspaceUsage:
        files = scan_workspace(str(self._root_dir))
        total = sum(f["size"] for f in files)
        return WorkspaceUsage(
            total_bytes=total,
            file_count=len(files),
            max_bytes=self._quota.max_bytes,
            max_files=self._quota.max_files,
            files=files,
        )

    def enforce_quota(self) -> list[str]:
        """按时间删除最旧文件直至满足配额，返回被删除路径列表。"""
        files = scan_workspace(str(self._root_dir))
        total = sum(f["size"] for f in files)
        deleted: list[str] = []
        while files and (
            len(files) > self._quota.max_files or total > self._quota.max_bytes
        ):
            oldest = files.pop(0)
            full_path = self._root_dir / oldest["path"]
            try:
                full_path.unlink(missing_ok=True)
                total -= oldest["size"]
                deleted.append(oldest["path"])
                logger.info("Quota: deleted %s (%d bytes)", oldest["path"], oldest["size"])
                _cleanup_empty_parents(full_path, self._root_dir)
            except OSError:
                logger.warning("Quota: failed to delete %s", oldest["path"], exc_info=True)
        return deleted

    def check_upload_allowed(self, incoming_size: int) -> tuple[bool, str]:
        """预检是否允许上传 incoming_size 字节。"""
        files = scan_workspace(str(self._root_dir))
        current_size = sum(f["size"] for f in files)
        current_count = len(files)
        if current_count >= self._quota.max_files:
            return False, f"工作空间文件数已达上限 ({self._quota.max_files} 个)"
        if current_size + incoming_size > self._quota.max_bytes:
            limit_mb = round(self._quota.max_bytes / (1024 * 1024), 1)
            return False, f"工作空间存储已满 (上限 {limit_mb} MB)"
        return True, ""

    def get_upload_dir(self) -> Path:
        """返回上传目录，不存在则创建。"""
        upload_dir = self._root_dir / "uploads"
        upload_dir.mkdir(parents=True, exist_ok=True)
        return upload_dir

    # -- 工作区解析（替代 auth/workspace 辅助） ──────────

    @staticmethod
    def resolve(
        global_workspace_root: str,
        *,
        user_id: str | None = None,
        auth_enabled: bool = False,
        sandbox_config: SandboxConfig | None = None,
        transaction_enabled: bool = True,
        transaction_scope: str = "all",
    ) -> "IsolatedWorkspace":
        """解析请求对应的工作区。

        - auth_enabled 且提供 user_id  ->  每用户工作区
        - 否则                         ->  共享工作区（向后兼容）
        """
        if auth_enabled and user_id:
            root = os.path.join(global_workspace_root, "users", user_id)
        else:
            root = global_workspace_root
        return IsolatedWorkspace(
            root_dir=root,
            owner_id=user_id if (auth_enabled and user_id) else None,
            sandbox_config=sandbox_config,
            transaction_enabled=transaction_enabled,
            transaction_scope=transaction_scope,
        )
