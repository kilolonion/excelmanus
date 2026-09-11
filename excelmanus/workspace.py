"""统一工作区隔离层。

每个会话在 ``IsolatedWorkspace`` 内运行，封装文件系统根目录、
事务化暂存与沙盒配置。
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from excelmanus.excel_extensions import EXCEL_EXTENSIONS as _EXCEL_EXTENSIONS_BASE
from excelmanus.security.path_utils import resolve_in_workspace

if TYPE_CHECKING:
    from excelmanus.file_registry import FileRegistry

_EXCEL_EXTENSIONS = _EXCEL_EXTENSIONS_BASE | frozenset({".csv"})


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

    def undo_commit(
        self,
        original_path: str,
        undo_path: str,
        *,
        expected_version: str | None = None,
    ) -> bool:
        """撤销一次 commit。"""
        return self._registry.undo_commit(
            original_path, undo_path, expected_version=expected_version
        )

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
    """进程唯一工作区。

    持有解析后的工作区根目录与沙盒配置，
    为文件变更暂存创建每会话的 WorkspaceTransaction 实例。
    """

    def __init__(
        self,
        root_dir: str | Path,
        *,
        sandbox_config: SandboxConfig | None = None,
        transaction_enabled: bool = True,
        transaction_scope: str = "all",
    ) -> None:
        self._root_dir = Path(root_dir).expanduser().resolve()
        self._root_dir.mkdir(parents=True, exist_ok=True)
        self._sandbox_config = sandbox_config or SandboxConfig()
        self._transaction_enabled = transaction_enabled
        self._transaction_scope = transaction_scope
        self._staging_base = (self._root_dir / "outputs" / "backups").resolve()

    # -- 属性 ----------------------------------------------------------

    @property
    def root_dir(self) -> Path:
        return self._root_dir

    @property
    def sandbox_config(self) -> SandboxConfig:
        return self._sandbox_config

    @sandbox_config.setter
    def sandbox_config(self, value: SandboxConfig) -> None:
        self._sandbox_config = value

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
        sandbox_config: SandboxConfig | None = None,
        transaction_enabled: bool = True,
        transaction_scope: str = "all",
        data_root: str = "",
    ) -> "IsolatedWorkspace":
        """解析进程唯一工作区：优先 ``data_root``，否则 ``workspace_root``。"""
        root = data_root if data_root else global_workspace_root
        return IsolatedWorkspace(
            root_dir=root,
            sandbox_config=sandbox_config,
            transaction_enabled=transaction_enabled,
            transaction_scope=transaction_scope,
        )
