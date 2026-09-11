"""Process-unique workspace root and sandbox tmpdir.

Overlay staging / CoW / WorkspaceTransaction are gone.
History lives in ``RevisionStore``; writes go through ``publish_bytes``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SandboxConfig:
    """Per-workspace sandbox config. Docker code sandbox is deleted."""


class SandboxEnv:
    """Local subprocess fence: tmpdir inside the workspace tree."""

    def __init__(self, workspace: "IsolatedWorkspace") -> None:
        self.workspace = workspace

    def get_tmp_dir(self) -> Path:
        tmpdir = self.workspace.root_dir / ".tmp"
        tmpdir.mkdir(parents=True, exist_ok=True)
        return tmpdir


class IsolatedWorkspace:
    """Process-unique workspace root."""

    def __init__(
        self,
        root_dir: str | Path,
        *,
        sandbox_config: SandboxConfig | None = None,
        transaction_enabled: bool = False,
        transaction_scope: str = "all",
    ) -> None:
        self._root_dir = Path(root_dir).expanduser().resolve()
        self._root_dir.mkdir(parents=True, exist_ok=True)
        self._sandbox_config = sandbox_config or SandboxConfig()
        _ = (transaction_enabled, transaction_scope)

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
        return False

    @transaction_enabled.setter
    def transaction_enabled(self, value: bool) -> None:
        return

    def create_sandbox_env(self, transaction: object | None = None) -> SandboxEnv:
        _ = transaction
        return SandboxEnv(workspace=self)

    def get_upload_dir(self) -> Path:
        upload_dir = self._root_dir / "uploads"
        upload_dir.mkdir(parents=True, exist_ok=True)
        return upload_dir

    @staticmethod
    def resolve(
        global_workspace_root: str,
        *,
        sandbox_config: SandboxConfig | None = None,
        transaction_enabled: bool = False,
        transaction_scope: str = "all",
        data_root: str = "",
    ) -> "IsolatedWorkspace":
        root = data_root if data_root else global_workspace_root
        return IsolatedWorkspace(
            root_dir=root,
            sandbox_config=sandbox_config,
        )
