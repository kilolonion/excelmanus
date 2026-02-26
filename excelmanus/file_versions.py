"""FileVersionManager：统一文件版本管理层。

收敛 WorkspaceTransaction staging、ApprovalManager snapshot、CoW 注册表
三套独立的文件保护机制，提供单一版本链 + 角色标签的统一抽象。

每个 AgentEngine 持有一个会话级实例（非全局单例）。
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from excelmanus.security.path_utils import resolve_in_workspace, to_workspace_relative

logger = logging.getLogger(__name__)

VersionReason = Literal["staging", "audit", "cow", "restore", "manual", "turn"]

_EXCEL_EXTENSIONS = frozenset({".xlsx", ".xls", ".xlsm", ".xlsb", ".csv"})


@dataclass
class TurnCheckpoint:
    """一轮工具调用结束后的文件快照集合。"""

    turn_number: int
    created_at: float
    version_ids: list[str]       # 该轮创建的 FileVersion ID
    files_modified: list[str]    # 被修改的文件相对路径
    tool_names: list[str]        # 该轮调用的工具名


@dataclass(frozen=True)
class FileVersion:
    """单个文件的一个版本快照。"""

    version_id: str
    file_path: str  # 工作区相对路径（规范化）
    snapshot_path: str  # 快照文件的绝对路径（tombstone 时为空字符串）
    reason: VersionReason
    ref_id: str  # 关联 ID（tx_id / approval_id / cow_source）
    created_at: float
    original_existed: bool
    content_hash: str  # SHA-256 hex，tombstone 时为空字符串
    invalidated: bool = False  # commit 后标记为不可恢复


@dataclass
class _StagingEntry:
    """活跃的 staging 条目：跟踪原始路径 → staged 副本路径的映射。"""

    original_abs: str
    staged_abs: str
    rel_path: str


class FileVersionManager:
    """统一文件版本管理器。

    维护 workspace 内所有文件的版本链：
    file_path → [v0(original), v1(staging), v2(audit), ...]

    所有快照物理存储在 ``versions_dir`` 下。
    """

    def __init__(
        self,
        workspace_root: Path,
        versions_dir: Path | None = None,
    ) -> None:
        self._workspace_root = workspace_root.resolve()
        self._versions_dir = (
            versions_dir
            if versions_dir is not None
            else self._workspace_root / "outputs" / ".versions"
        )
        self._versions_dir.mkdir(parents=True, exist_ok=True)

        # file_path (rel) → list[FileVersion]，时间正序
        self._chains: dict[str, list[FileVersion]] = {}

        # ref_id → list[FileVersion]
        self._ref_index: dict[str, list[FileVersion]] = {}

        # 活跃 staging 映射：rel_path → _StagingEntry
        self._staging: dict[str, _StagingEntry] = {}

        # 轮次 checkpoint 列表（时间正序）
        self._turn_checkpoints: list[TurnCheckpoint] = []
        self._max_turn_checkpoints: int = 30

        # W3: 从磁盘恢复 staging 映射（进程重启后不丢失）
        self._load_staging()

    # ── Properties ──────────────────────────────────────────────

    @property
    def workspace_root(self) -> Path:
        return self._workspace_root

    @property
    def versions_dir(self) -> Path:
        return self._versions_dir

    @property
    def _staging_json_path(self) -> Path:
        """staging 映射持久化文件路径。"""
        return self._versions_dir / "_staging.json"

    def _save_staging(self) -> None:
        """W3: 将 staging 映射持久化到 JSON 文件。"""
        data = [
            {
                "rel_path": e.rel_path,
                "original_abs": e.original_abs,
                "staged_abs": e.staged_abs,
            }
            for e in self._staging.values()
        ]
        try:
            tmp = self._staging_json_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self._staging_json_path)
        except Exception:
            logger.debug("staging 映射持久化失败", exc_info=True)

    def _load_staging(self) -> None:
        """W3: 从 JSON 文件恢复 staging 映射，跳过物理文件已不存在的条目。"""
        jp = self._staging_json_path
        if not jp.exists():
            return
        try:
            data = json.loads(jp.read_text(encoding="utf-8"))
            for item in data:
                staged = Path(item["staged_abs"])
                if not staged.exists():
                    continue  # 跳过孤儿条目
                self._staging[item["rel_path"]] = _StagingEntry(
                    original_abs=item["original_abs"],
                    staged_abs=item["staged_abs"],
                    rel_path=item["rel_path"],
                )
            logger.debug("从磁盘恢复 %d 条 staging 映射", len(self._staging))
        except Exception:
            logger.debug("staging 映射加载失败", exc_info=True)

    # ── 路径工具 ────────────────────────────────────────────────

    def _resolve(self, file_path: str) -> Path:
        """解析为绝对路径并校验在工作区内。"""
        return resolve_in_workspace(file_path, self._workspace_root)

    def _to_rel(self, abs_path: Path) -> str:
        """绝对路径 → 工作区相对路径字符串。"""
        return to_workspace_relative(abs_path, self._workspace_root)

    def _version_store_dir(self, version_id: str) -> Path:
        """版本快照的物理存储目录。"""
        return self._versions_dir / version_id[:2] / version_id

    @staticmethod
    def _file_hash(path: Path) -> str:
        """计算文件 SHA-256 hex。"""
        h = hashlib.sha256()
        with open(path, "rb") as f:
            while True:
                chunk = f.read(1 << 16)  # 64KB
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()

    @staticmethod
    def _new_version_id() -> str:
        return secrets.token_hex(6)  # 12 chars

    # ── 版本创建 ────────────────────────────────────────────────

    def checkpoint(
        self,
        file_path: str,
        *,
        reason: VersionReason,
        ref_id: str = "",
    ) -> FileVersion | None:
        """为文件创建版本快照。

        文件不存在时记录 tombstone。
        返回 None 如果文件内容与最新版本相同（去重）。
        """
        resolved = self._resolve(file_path)
        rel = self._to_rel(resolved)
        existed = resolved.exists() and resolved.is_file()

        if not existed:
            # 墓碑记录（文件不存在时的占位版本）
            ver = FileVersion(
                version_id=self._new_version_id(),
                file_path=rel,
                snapshot_path="",
                reason=reason,
                ref_id=ref_id,
                created_at=time.time(),
                original_existed=False,
                content_hash="",
            )
            self._append_version(ver)
            return ver

        content_hash = self._file_hash(resolved)

        # 去重：与最新版本比较
        chain = self._chains.get(rel, [])
        if chain and chain[-1].content_hash == content_hash:
            return None

        vid = self._new_version_id()
        store_dir = self._version_store_dir(vid)
        store_dir.mkdir(parents=True, exist_ok=True)
        snapshot_path = store_dir / resolved.name
        shutil.copy2(str(resolved), str(snapshot_path))

        ver = FileVersion(
            version_id=vid,
            file_path=rel,
            snapshot_path=str(snapshot_path),
            reason=reason,
            ref_id=ref_id,
            created_at=time.time(),
            original_existed=True,
            content_hash=content_hash,
        )
        self._append_version(ver)
        return ver

    def checkpoint_many(
        self,
        file_paths: list[str],
        *,
        reason: VersionReason,
        ref_id: str = "",
    ) -> list[FileVersion]:
        """批量创建版本快照。"""
        results: list[FileVersion] = []
        for fp in file_paths:
            try:
                ver = self.checkpoint(fp, reason=reason, ref_id=ref_id)
                if ver is not None:
                    results.append(ver)
            except Exception:
                logger.warning("checkpoint 失败: %s", fp, exc_info=True)
        return results

    def _append_version(self, ver: FileVersion) -> None:
        """将版本追加到内部索引。"""
        self._chains.setdefault(ver.file_path, []).append(ver)
        if ver.ref_id:
            self._ref_index.setdefault(ver.ref_id, []).append(ver)

    # ── 版本查询 ────────────────────────────────────────────────

    def get_original(self, file_path: str) -> FileVersion | None:
        """获取文件的最早版本（真正的原始状态）。"""
        resolved = self._resolve(file_path)
        rel = self._to_rel(resolved)
        chain = self._chains.get(rel, [])
        return chain[0] if chain else None

    def get_latest(self, file_path: str) -> FileVersion | None:
        """获取文件的最新版本。"""
        resolved = self._resolve(file_path)
        rel = self._to_rel(resolved)
        chain = self._chains.get(rel, [])
        return chain[-1] if chain else None

    def list_versions(self, file_path: str) -> list[FileVersion]:
        """获取文件的完整版本链（时间正序）。"""
        resolved = self._resolve(file_path)
        rel = self._to_rel(resolved)
        return list(self._chains.get(rel, []))

    def list_by_ref(self, ref_id: str) -> list[FileVersion]:
        """按关联 ID 查询所有版本。"""
        return list(self._ref_index.get(ref_id, []))

    def list_all_tracked(self) -> list[str]:
        """返回所有有版本记录的文件相对路径。"""
        return list(self._chains.keys())

    # ── 版本恢复 ────────────────────────────────────────────────

    def restore(self, file_path: str, version_id: str) -> bool:
        """将文件恢复到指定版本。"""
        resolved = self._resolve(file_path)
        rel = self._to_rel(resolved)
        chain = self._chains.get(rel, [])
        target = None
        for v in chain:
            if v.version_id == version_id:
                target = v
                break
        if target is None:
            return False

        if target.invalidated:
            logger.warning("版本 %s 已失效，无法恢复", version_id)
            return False

        if not target.original_existed:
            # tombstone → 删除当前文件
            if resolved.exists():
                resolved.unlink()
            return True

        snapshot = Path(target.snapshot_path)
        if not snapshot.exists():
            logger.warning("快照文件不存在: %s", target.snapshot_path)
            return False

        resolved.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(snapshot), str(resolved))

        # 记录 restore 操作本身为新版本
        self.checkpoint(file_path, reason="restore", ref_id=version_id)
        return True

    def restore_to_original(self, file_path: str) -> bool:
        """将文件恢复到最早的原始版本。"""
        original = self.get_original(file_path)
        if original is None:
            return False
        return self.restore(file_path, original.version_id)

    # ── Staging 兼容层 ──────────────────────────────────────────

    def stage_for_write(
        self,
        file_path: str,
        *,
        ref_id: str = "",
        scope: str = "all",
    ) -> str:
        """确保文件有原始版本快照，返回 staged 副本路径。

        首次调用时复制原文件到 staging 目录并创建 reason='staging' 的版本记录。
        后续调用返回缓存的 staged 路径。
        文件不存在或不在 scope 内时返回原始路径。
        """
        resolved = self._resolve(file_path)
        rel = self._to_rel(resolved)

        # 已有 staging 条目 → 返回缓存
        if rel in self._staging:
            return self._staging[rel].staged_abs

        # scope 检查
        if scope == "excel_only" and resolved.suffix.lower() not in _EXCEL_EXTENSIONS:
            return str(resolved)

        # 文件不存在 → 返回原始路径
        if not (resolved.exists() and resolved.is_file()):
            return str(resolved)

        # 创建原始版本快照
        self.checkpoint(file_path, reason="staging", ref_id=ref_id)

        # 复制到 staging 目录
        staging_dir = self._workspace_root / "outputs" / "backups"
        staging_dir.mkdir(parents=True, exist_ok=True)
        uniq = secrets.token_hex(2)
        ts = time.strftime("%Y%m%dT%H%M%S")
        staged_name = f"{resolved.stem}_{ts}_{uniq}{resolved.suffix}"
        staged_path = staging_dir / staged_name
        shutil.copy2(str(resolved), str(staged_path))

        entry = _StagingEntry(
            original_abs=str(resolved),
            staged_abs=str(staged_path),
            rel_path=rel,
        )
        self._staging[rel] = entry
        self._save_staging()
        return str(staged_path)

    def get_staged_path(self, file_path: str) -> str | None:
        """查询文件的 staged 副本路径，未 staged 时返回 None。"""
        resolved = self._resolve(file_path)
        rel = self._to_rel(resolved)
        entry = self._staging.get(rel)
        return entry.staged_abs if entry else None

    def commit_staged(self, file_path: str) -> dict[str, str] | None:
        """将 staged 文件提交回原始位置。返回 {original, backup} 或 None。"""
        resolved = self._resolve(file_path)
        rel = self._to_rel(resolved)
        entry = self._staging.get(rel)
        if entry is None:
            return None

        staged = Path(entry.staged_abs)
        original = Path(entry.original_abs)
        if staged.exists():
            original.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(staged), str(original))

        del self._staging[rel]
        self._save_staging()
        return {"original": entry.original_abs, "backup": entry.staged_abs}

    def commit_all_staged(self) -> list[dict[str, str]]:
        """提交所有 staged 文件。"""
        results: list[dict[str, str]] = []
        for rel in list(self._staging.keys()):
            entry = self._staging[rel]
            staged = Path(entry.staged_abs)
            original = Path(entry.original_abs)
            if staged.exists():
                original.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(staged), str(original))
            results.append({"original": entry.original_abs, "backup": entry.staged_abs})
        self._staging.clear()
        self._save_staging()
        return results

    def discard_staged(self, file_path: str) -> bool:
        """丢弃 staged 文件，恢复原始版本。"""
        resolved = self._resolve(file_path)
        rel = self._to_rel(resolved)
        entry = self._staging.get(rel)
        if entry is None:
            return False

        staged = Path(entry.staged_abs)
        if staged.exists():
            staged.unlink()
        del self._staging[rel]
        self._save_staging()
        return True

    def discard_all_staged(self) -> int:
        """丢弃所有 staged 文件。返回丢弃数量。"""
        count = 0
        for entry in self._staging.values():
            staged = Path(entry.staged_abs)
            if staged.exists():
                staged.unlink()
            count += 1
        self._staging.clear()
        self._save_staging()
        return count

    def list_staged(self) -> list[dict[str, str]]:
        """列出所有活跃的 staged 文件。"""
        result: list[dict[str, str]] = []
        for entry in self._staging.values():
            result.append({
                "original": entry.original_abs,
                "backup": entry.staged_abs,
                "exists": str(Path(entry.staged_abs).exists()),
            })
        return result

    def staged_file_map(self) -> dict[str, str]:
        """返回 original_abs → staged_abs 的映射（兼容 WorkspaceTransaction._file_map）。"""
        return {e.original_abs: e.staged_abs for e in self._staging.values()}

    def has_staging(self, file_path: str) -> bool:
        """检查文件是否有活跃的 staging 条目。"""
        resolved = self._resolve(file_path)
        rel = self._to_rel(resolved)
        return rel in self._staging

    def register_cow_mapping(
        self,
        src_rel: str,
        dst_rel: str,
    ) -> None:
        """注册 CoW 路径映射（由 run_code 等工具产生）。

        将映射同时记录为 staging 条目和版本快照。
        """
        src_abs = str((self._workspace_root / src_rel).resolve())
        dst_abs = str((self._workspace_root / dst_rel).resolve())
        resolved_src = self._resolve(src_rel)
        rel = self._to_rel(resolved_src)

        if rel in self._staging:
            return  # 已有 staging，不重复注册

        # 记录原始版本
        self.checkpoint(src_rel, reason="cow", ref_id=dst_rel)

        entry = _StagingEntry(
            original_abs=src_abs,
            staged_abs=dst_abs,
            rel_path=rel,
        )
        self._staging[rel] = entry
        self._save_staging()

    def lookup_cow_redirect(self, rel_path: str) -> str | None:
        """查找相对路径是否有 CoW/staging 副本，返回副本路径或 None。"""
        entry = self._staging.get(rel_path)
        return entry.staged_abs if entry else None

    # ── W4/W5: Staging 条目维护 ─────────────────────────────────

    def remove_staging_for_path(self, file_path: str) -> bool:
        """W4: 移除指定文件的 staging 条目（文件被删除时调用）。

        不删除 staged 物理文件（可能仍需保留作为备份），仅清理映射。
        """
        try:
            resolved = self._resolve(file_path)
            rel = self._to_rel(resolved)
        except ValueError:
            return False
        if rel not in self._staging:
            return False
        del self._staging[rel]
        self._save_staging()
        return True

    def rename_staging_path(self, old_path: str, new_path: str) -> bool:
        """W5: 重命名 staging 条目的原始路径（文件被重命名时调用）。"""
        try:
            old_resolved = self._resolve(old_path)
            old_rel = self._to_rel(old_resolved)
            new_resolved = self._resolve(new_path)
            new_rel = self._to_rel(new_resolved)
        except ValueError:
            return False
        entry = self._staging.get(old_rel)
        if entry is None:
            return False
        del self._staging[old_rel]
        self._staging[new_rel] = _StagingEntry(
            original_abs=str(new_resolved),
            staged_abs=entry.staged_abs,
            rel_path=new_rel,
        )
        self._save_staging()
        return True

    def prune_stale_staging(self) -> int:
        """移除 staged 物理文件已不存在的条目。"""
        stale = [
            rel for rel, e in self._staging.items()
            if not Path(e.staged_abs).exists()
        ]
        for rel in stale:
            del self._staging[rel]
        if stale:
            self._save_staging()
        return len(stale)

    # ── Undo 失效 ──────────────────────────────────────────────

    def invalidate_undo(self, file_paths: set[str]) -> int:
        """标记指定文件的版本链为不可恢复。

        在 commit 后调用，确保 undo 不会操作已被覆盖的快照。
        """
        count = 0
        for fp in file_paths:
            try:
                resolved = self._resolve(fp)
                rel = self._to_rel(resolved)
            except ValueError:
                continue
            chain = self._chains.get(rel, [])
            for ver in chain:
                if not ver.invalidated:
                    # frozen dataclass → 需要 object.__setattr__
                    object.__setattr__(ver, "invalidated", True)
                    count += 1
        return count

    # ── Turn Checkpoint ──────────────────────────────────────────

    def create_turn_checkpoint(
        self,
        turn_number: int,
        dirty_files: list[str],
        tool_names: list[str] | None = None,
    ) -> TurnCheckpoint | None:
        """对 dirty_files 做快照，记录为一个轮次 checkpoint。

        文件内容未变时 checkpoint() 返回 None（去重），不计入版本。
        所有文件均未变时返回 None（不创建空 checkpoint）。
        超出 max_turn_checkpoints 时自动淘汰最早的。
        """
        ref_id = f"turn:{turn_number}"
        version_ids: list[str] = []
        files_actually_modified: list[str] = []

        for fp in dirty_files:
            try:
                ver = self.checkpoint(fp, reason="turn", ref_id=ref_id)
                if ver is not None:
                    version_ids.append(ver.version_id)
                    files_actually_modified.append(ver.file_path)
            except Exception:
                logger.warning("turn checkpoint 失败: %s", fp, exc_info=True)

        if not version_ids:
            return None  # 所有文件均未变

        cp = TurnCheckpoint(
            turn_number=turn_number,
            created_at=time.time(),
            version_ids=version_ids,
            files_modified=files_actually_modified,
            tool_names=list(tool_names or []),
        )
        self._turn_checkpoints.append(cp)

        # 淘汰最早的
        while len(self._turn_checkpoints) > self._max_turn_checkpoints:
            self._turn_checkpoints.pop(0)

        return cp

    def rollback_to_turn(self, turn_number: int) -> list[str]:
        """回退到指定轮次之前的状态。

        恢复 turn_number 及之后所有 checkpoint 涉及的文件到
        turn_number 之前的最新版本。返回被恢复的文件路径列表。
        """
        # 找到 target turn 及之后的所有 checkpoint
        affected_idx = None
        for i, cp in enumerate(self._turn_checkpoints):
            if cp.turn_number >= turn_number:
                affected_idx = i
                break
        if affected_idx is None:
            return []

        # 收集所有需要恢复的文件
        files_to_restore: set[str] = set()
        for cp in self._turn_checkpoints[affected_idx:]:
            files_to_restore.update(cp.files_modified)

        restored: list[str] = []
        for rel_path in files_to_restore:
            chain = self._chains.get(rel_path, [])
            if not chain:
                continue

            # 找到 target turn 之前的最新版本
            target_ver = None
            for ver in chain:
                if ver.reason == "turn" and ver.ref_id == f"turn:{turn_number}":
                    break  # 到达 target turn，停止
                target_ver = ver

            if target_ver is None:
                # 没有 target turn 之前的版本，恢复到最早版本
                target_ver = chain[0]

            if self.restore(rel_path, target_ver.version_id):
                restored.append(rel_path)

            # 同步更新 staging 副本（如果存在）
            entry = self._staging.get(rel_path)
            if entry is not None and target_ver.snapshot_path:
                snapshot = Path(target_ver.snapshot_path)
                staged = Path(entry.staged_abs)
                if snapshot.exists() and staged.exists():
                    shutil.copy2(str(snapshot), str(staged))

        # 移除被回退的 checkpoint
        self._turn_checkpoints = self._turn_checkpoints[:affected_idx]

        return restored

    def list_turn_checkpoints(self) -> list[TurnCheckpoint]:
        """返回所有轮次 checkpoint（时间正序）。"""
        return list(self._turn_checkpoints)

    # ── 清理 ────────────────────────────────────────────────────

    def gc(self, max_age_seconds: float = 3600) -> int:
        """清理过期版本快照。返回清理数量。"""
        cutoff = time.time() - max_age_seconds
        removed = 0
        for rel, chain in list(self._chains.items()):
            # 保留最新版本，清理过期的旧版本
            if len(chain) <= 1:
                continue
            keep: list[FileVersion] = []
            for i, ver in enumerate(chain):
                if i == 0 or i == len(chain) - 1:
                    # 始终保留第一个（原始）和最后一个（最新）
                    keep.append(ver)
                elif ver.created_at >= cutoff:
                    keep.append(ver)
                else:
                    # 删除物理快照
                    if ver.snapshot_path:
                        sp = Path(ver.snapshot_path)
                        if sp.exists():
                            sp.unlink()
                        # 清理空目录
                        parent = sp.parent
                        try:
                            parent.rmdir()
                            parent.parent.rmdir()
                        except OSError:
                            pass
                    removed += 1
            self._chains[rel] = keep
        # 重建 ref_index
        self._ref_index.clear()
        for chain in self._chains.values():
            for ver in chain:
                if ver.ref_id:
                    self._ref_index.setdefault(ver.ref_id, []).append(ver)
        return removed

    def prune_stale_staging(self) -> int:
        """清理 staged 文件已不存在的条目。"""
        stale = [
            rel for rel, entry in self._staging.items()
            if not Path(entry.staged_abs).exists()
        ]
        for rel in stale:
            del self._staging[rel]
        return len(stale)
