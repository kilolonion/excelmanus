"""FileRegistry：别名索引 — 元数据 + provenance + 路径解析。

权威是磁盘正本 + RevisionStore + ContentVersion。本表只记 original_name
等展示别名。Overlay / FileVersionManager 已删除。
"""
from __future__ import annotations

import logging
import os
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from excelmanus.excel_extensions import EXCEL_EXTENSIONS as _EXCEL_EXTENSIONS_BASE
from excelmanus.security.guard import FileAccessGuard, SecurityViolationError
from excelmanus.workspace.identity import is_hidden_name

if TYPE_CHECKING:
    from excelmanus.database import Database

logger = logging.getLogger(__name__)

# Excel 扩展名集合（共享常量 + CSV）
_EXCEL_EXTENSIONS = _EXCEL_EXTENSIONS_BASE | frozenset({".csv"})

# Word 扩展名
_WORD_EXTENSIONS = frozenset({".docx"})

# 图片扩展名
_IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg"})

# 递归扫描时跳过的噪音目录
_SKIP_DIRS: frozenset[str] = frozenset({
    ".git", ".venv", "node_modules", "__pycache__",
    ".worktrees", "dist", "build",
    # 旧版隔离目录：扫描不得走进归档残骸
    "users",
    # 保留命名空间：scan 不得走进（方案 P1 CatalogFilter）
    ".excelmanus",
    ".versions",
})

_SKIP_REL_PREFIXES: tuple[str, ...] = (
    "outputs/backups",
    "outputs/.versions",
    "outputs/audits",
)

# 全文件扫描时跳过的二进制/编译文件扩展名
_SKIP_EXTENSIONS: frozenset[str] = frozenset({
    ".pyc", ".pyo", ".so", ".dylib", ".dll", ".exe",
    ".o", ".a", ".class", ".jar", ".war",
    ".whl", ".egg", ".tar", ".gz", ".bz2", ".xz", ".zst",
    ".db", ".sqlite", ".sqlite3",
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
    ".DS_Store",
})

def _detect_file_type(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext in _EXCEL_EXTENSIONS:
        return "excel" if ext != ".csv" else "csv"
    if ext in _WORD_EXTENSIONS:
        return "word"
    if ext in _IMAGE_EXTENSIONS:
        return "image"
    if ext in (".txt", ".md", ".json", ".xml", ".yaml", ".yml", ".log"):
        return "text"
    return "other"


def _new_id() -> str:
    return secrets.token_hex(8)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── 数据模型 ──────────────────────────────────────────────────


@dataclass
class FileEntry:
    """Registry 中的文件记录。"""

    id: str
    workspace: str
    canonical_path: str
    original_name: str
    file_type: str = "other"
    size_bytes: int = 0
    origin: str = "scan"
    origin_session_id: str | None = None
    origin_turn: int | None = None
    origin_tool: str | None = None
    parent_file_id: str | None = None
    sheet_meta: list[dict] = field(default_factory=list)
    content_hash: str = ""
    mtime_ns: int = 0
    created_at: str = ""
    updated_at: str = ""
    deleted_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "workspace": self.workspace,
            "canonical_path": self.canonical_path,
            "original_name": self.original_name,
            "file_type": self.file_type,
            "size_bytes": self.size_bytes,
            "origin": self.origin,
            "origin_session_id": self.origin_session_id,
            "origin_turn": self.origin_turn,
            "origin_tool": self.origin_tool,
            "parent_file_id": self.parent_file_id,
            "sheet_meta": self.sheet_meta,
            "content_hash": self.content_hash,
            "mtime_ns": self.mtime_ns,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "deleted_at": self.deleted_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> FileEntry:
        return cls(
            id=d["id"],
            workspace=d["workspace"],
            canonical_path=d["canonical_path"],
            original_name=d["original_name"],
            file_type=d.get("file_type", "other"),
            size_bytes=d.get("size_bytes", 0),
            origin=d.get("origin", "scan"),
            origin_session_id=d.get("origin_session_id"),
            origin_turn=d.get("origin_turn"),
            origin_tool=d.get("origin_tool"),
            parent_file_id=d.get("parent_file_id"),
            sheet_meta=d.get("sheet_meta", []),
            content_hash=d.get("content_hash", ""),
            mtime_ns=d.get("mtime_ns", 0),
            created_at=d.get("created_at", ""),
            updated_at=d.get("updated_at", ""),
            deleted_at=d.get("deleted_at"),
        )


@dataclass
class FileEvent:
    """文件生命周期事件。"""

    id: str
    file_id: str
    event_type: str
    session_id: str | None = None
    turn: int | None = None
    tool_name: str | None = None
    details: dict = field(default_factory=dict)
    created_at: str = ""

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> FileEvent:
        return cls(
            id=d["id"],
            file_id=d["file_id"],
            event_type=d["event_type"],
            session_id=d.get("session_id"),
            turn=d.get("turn"),
            tool_name=d.get("tool_name"),
            details=d.get("details", {}),
            created_at=d.get("created_at", ""),
        )


@dataclass
class ScanResult:
    """scan_workspace() 的返回值。"""

    total_files: int = 0
    new_files: int = 0
    updated_files: int = 0
    deleted_files: int = 0
    cache_hits: int = 0
    scan_duration_ms: int = 0


@dataclass
class FileGroup:
    """文件组记录。"""

    id: str
    workspace: str
    name: str
    description: str = ""
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "workspace": self.workspace,
            "name": self.name,
            "description": self.description,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> FileGroup:
        return cls(
            id=d["id"],
            workspace=d["workspace"],
            name=d["name"],
            description=d.get("description", ""),
            created_at=d.get("created_at", ""),
            updated_at=d.get("updated_at", ""),
        )


# ── FileRegistry 核心类 ──────────────────────────────────────


class FileRegistry:
    """别名索引。enable_versions 已忽略；版本权威是 RevisionStore。"""

    def __init__(
        self,
        database: "Database",
        workspace_root: str | Path,
        *,
        enable_versions: bool = False,
    ) -> None:
        from excelmanus.stores.file_registry_store import FileRegistryStore

        self._store = FileRegistryStore(database)
        self._workspace_root = Path(workspace_root).resolve()
        self._workspace_key = str(self._workspace_root)
        self._guard = FileAccessGuard(self._workspace_key)

        # 内存热缓存（启动时从 DB 加载）
        self._path_cache: dict[str, FileEntry] = {}  # canonical_path → entry
        self._id_to_path: dict[str, str] = {}  # file_id → canonical_path
        self._alias_cache: dict[str, str] = {}  # alias_value → file_id

        _ = enable_versions
        self._fvm = None

        self._load_cache()

    @property
    def workspace_root(self) -> Path:
        return self._workspace_root

    # ── 缓存管理 ─────────────────────────────────────────────

    def _load_cache(self) -> None:
        """从 DB 加载到内存缓存。"""
        try:
            rows = self._store.list_all(self._workspace_key, include_deleted=False)
            file_ids: list[str] = []
            for row in rows:
                entry = FileEntry.from_dict(row)
                self._path_cache[entry.canonical_path] = entry
                self._id_to_path[entry.id] = entry.canonical_path
                file_ids.append(entry.id)
            # 单次批量加载所有别名（避免 N+1 查询）
            if file_ids:
                all_aliases = self._store.get_all_aliases_for_files(file_ids)
                for fid, aliases in all_aliases.items():
                    for a in aliases:
                        self._alias_cache[a["alias_value"]] = fid
        except Exception:
            logger.debug("FileRegistry 缓存加载失败", exc_info=True)

    def _cache_entry(self, entry: FileEntry) -> None:
        """更新缓存中的 entry。"""
        self._path_cache[entry.canonical_path] = entry
        self._id_to_path[entry.id] = entry.canonical_path

    def _invalidate_entry(self, canonical_path: str) -> None:
        """从缓存中移除 entry。"""
        entry = self._path_cache.pop(canonical_path, None)
        if entry:
            self._id_to_path.pop(entry.id, None)

    # ── 路径工具 ─────────────────────────────────────────────

    def _resolve(self, file_path: str) -> Path:
        try:
            return self._guard.resolve_and_validate(file_path)
        except SecurityViolationError as exc:
            raise ValueError(str(exc)) from exc

    def _to_rel(self, abs_path: Path) -> str:
        # canonical 一律 POSIX 风格：模型/DB/测试都以 / 传路径，
        # Windows 上 str(relative) 会给反斜杠导致 get_by_path 查不到。
        return abs_path.relative_to(self._workspace_root).as_posix()

    # ── 注册入口 ─────────────────────────────────────────────

    def register_upload(
        self,
        canonical_path: str,
        original_name: str,
        file_type: str = "",
        size_bytes: int = 0,
        session_id: str | None = None,
        turn: int | None = None,
        sheet_meta: list[dict] | None = None,
    ) -> FileEntry:
        """注册上传文件。"""
        if not file_type:
            file_type = _detect_file_type(canonical_path)
        now = _now_iso()

        # 活文件复用 id；软删后再注册必须新 id（不能把 registry 当 lineage）
        existing = self._path_cache.get(canonical_path)
        if existing is not None and existing.deleted_at:
            try:
                self._store.purge_path(self._workspace_key, canonical_path)
            except Exception:
                logger.debug("purge soft-deleted registry row failed", exc_info=True)
            self._invalidate_entry(canonical_path)
            existing = None
        entry = FileEntry(
            id=existing.id if existing else _new_id(),
            workspace=self._workspace_key,
            canonical_path=canonical_path,
            original_name=original_name,
            file_type=file_type,
            size_bytes=size_bytes,
            origin="uploaded",
            origin_session_id=session_id,
            origin_turn=turn,
            sheet_meta=sheet_meta or [],
            created_at=existing.created_at if existing else now,
            updated_at=now,
        )
        self._store.upsert_file(entry.to_dict())
        self._cache_entry(entry)

        # 添加 display_name 别名
        if original_name != canonical_path:
            alias_id = _new_id()
            self._store.add_alias(alias_id, entry.id, "display_name", original_name)
            self._alias_cache[original_name] = entry.id

        # 记录上传事件
        self.record_event(
            entry.id, "uploaded",
            session_id=session_id, turn=turn,
            details={"original_name": original_name, "size_bytes": size_bytes},
        )

        # Tier 1 引用图谱扫描（.xlsx/.xlsm）
        self._try_tier1_scan(canonical_path)

        return entry

    def register_from_scan(
        self,
        canonical_path: str,
        original_name: str,
        size_bytes: int = 0,
        mtime_ns: int = 0,
        file_type: str = "",
        sheet_meta: list[dict] | None = None,
        content_hash: str = "",
    ) -> FileEntry:
        """从工作区扫描注册文件（新增或更新）。"""
        if not file_type:
            file_type = _detect_file_type(canonical_path)
        now = _now_iso()

        # 检查是否已存在
        existing = self._path_cache.get(canonical_path)
        if existing:
            # 更新已有记录
            existing.size_bytes = size_bytes
            existing.mtime_ns = mtime_ns
            existing.file_type = file_type
            existing.content_hash = content_hash
            if sheet_meta is not None:
                existing.sheet_meta = sheet_meta
            existing.updated_at = now
            existing.deleted_at = None  # 复活
            self._store.upsert_file(existing.to_dict())
            self._cache_entry(existing)
            return existing

        entry = FileEntry(
            id=_new_id(),
            workspace=self._workspace_key,
            canonical_path=canonical_path,
            original_name=original_name,
            file_type=file_type,
            size_bytes=size_bytes,
            origin="scan",
            sheet_meta=sheet_meta or [],
            content_hash=content_hash,
            mtime_ns=mtime_ns,
            created_at=now,
            updated_at=now,
        )
        self._store.upsert_file(entry.to_dict())
        self._cache_entry(entry)
        return entry

    def register_agent_output(
        self,
        canonical_path: str,
        original_name: str,
        parent_canonical: str | None = None,
        session_id: str | None = None,
        turn: int | None = None,
        tool_name: str | None = None,
        sheet_meta: list[dict] | None = None,
    ) -> FileEntry:
        """注册 agent 产出文件。"""
        file_type = _detect_file_type(canonical_path)
        now = _now_iso()
        parent_id = None
        if parent_canonical:
            parent = self._path_cache.get(parent_canonical)
            if parent:
                parent_id = parent.id

        existing = self._path_cache.get(canonical_path)
        entry = FileEntry(
            id=existing.id if existing else _new_id(),
            workspace=self._workspace_key,
            canonical_path=canonical_path,
            original_name=original_name,
            file_type=file_type,
            origin="agent_created",
            origin_session_id=session_id,
            origin_turn=turn,
            origin_tool=tool_name,
            parent_file_id=parent_id,
            sheet_meta=sheet_meta or [],
            created_at=existing.created_at if existing else now,
            updated_at=now,
        )

        # 尝试获取文件大小
        try:
            resolved = self._resolve(canonical_path)
            if resolved.exists():
                entry.size_bytes = resolved.stat().st_size
        except (ValueError, OSError):
            pass

        self._store.upsert_file(entry.to_dict())
        self._cache_entry(entry)

        self.record_event(
            entry.id, "created",
            session_id=session_id, turn=turn, tool_name=tool_name,
            details={"parent": parent_canonical},
        )
        return entry

    def _try_tier1_scan(self, canonical_path: str) -> None:
        """对 Excel 文件尝试 Tier 1 引用图谱扫描。"""
        ext = os.path.splitext(canonical_path)[1].lower()
        if ext not in (".xlsx", ".xlsm"):
            return
        try:
            from excelmanus.reference_graph.scanner import Tier1Scanner
            from excelmanus.tools.reference_tools import get_cache

            from excelmanus.workbook.snapshot import open_snapshot_at
            from excelmanus.workspace.refs import WorkspaceRef

            cache = get_cache()
            try:
                rel = str(Path(canonical_path).resolve().relative_to(self._workspace_root)).replace("\\", "/")
            except ValueError:
                rel = Path(canonical_path).name
            snap = open_snapshot_at(
                canonical_path,
                relative=rel,
                workspace=WorkspaceRef.from_root(self._workspace_root),
            )
            key = snap.id.key()
            if cache.get_tier1(key) is None:
                index = Tier1Scanner().scan(str(snap.backing_path))
                cache.put_tier1(key, index)
                logger.info("Tier 1 reference scan completed for %s", canonical_path)
        except Exception:
            logger.debug("Tier 1 reference scan skipped for %s", canonical_path, exc_info=True)

    # ── 事件记录 ─────────────────────────────────────────────

    def record_event(
        self,
        file_id: str,
        event_type: str,
        *,
        session_id: str | None = None,
        turn: int | None = None,
        tool_name: str | None = None,
        details: dict | None = None,
    ) -> None:
        """记录文件生命周期事件。"""
        self._store.add_event({
            "id": _new_id(),
            "file_id": file_id,
            "event_type": event_type,
            "session_id": session_id,
            "turn": turn,
            "tool_name": tool_name,
            "details": details or {},
            "created_at": _now_iso(),
        })

    # ── 查询 ─────────────────────────────────────────────────

    def get_by_path(self, canonical_path: str) -> FileEntry | None:
        """按规范路径查询（优先缓存）。"""
        cached = self._path_cache.get(canonical_path)
        if cached:
            return cached
        row = self._store.get_by_path(self._workspace_key, canonical_path)
        if row:
            entry = FileEntry.from_dict(row)
            self._cache_entry(entry)
            return entry
        return None

    def get_by_alias(self, alias_value: str) -> FileEntry | None:
        """通过别名查找文件。"""
        file_id = self._alias_cache.get(alias_value)
        if file_id:
            path = self._id_to_path.get(file_id)
            if path and path in self._path_cache:
                return self._path_cache[path]
        row = self._store.find_by_alias(alias_value)
        if row:
            entry = FileEntry.from_dict(row)
            self._cache_entry(entry)
            return entry
        return None

    def get_by_id(self, file_id: str) -> FileEntry | None:
        """按 ID 查询文件。"""
        # 先查内存缓存
        path = self._id_to_path.get(file_id)
        if path and path in self._path_cache:
            return self._path_cache[path]
        row = self._store.get_by_id(file_id)
        if row:
            entry = FileEntry.from_dict(row)
            self._cache_entry(entry)
            return entry
        return None

    def list_all(self, include_deleted: bool = False) -> list[FileEntry]:
        """列出所有文件。"""
        from excelmanus.security.source_isolation import is_product_source_path

        if not include_deleted:
            return [
                e for e in self._path_cache.values()
                if e.deleted_at is None
                and not is_product_source_path(e.canonical_path, self._workspace_root)
            ]
        rows = self._store.list_all(self._workspace_key, include_deleted=True)
        return [FileEntry.from_dict(r) for r in rows
                if not is_product_source_path(r["canonical_path"], self._workspace_root)]

    def get_children(self, file_id: str) -> list[FileEntry]:
        """获取文件的子文件（备份/副本）。"""
        rows = self._store.get_children(file_id)
        return [FileEntry.from_dict(r) for r in rows]

    def get_lineage(self, file_id: str) -> list[FileEntry]:
        """获取文件的祖先链（从当前到根）。"""
        result: list[FileEntry] = []
        seen: set[str] = set()
        current_id: str | None = file_id
        while current_id and current_id not in seen:
            seen.add(current_id)
            row = self._store.get_by_id(current_id)
            if not row:
                break
            entry = FileEntry.from_dict(row)
            result.append(entry)
            current_id = entry.parent_file_id
        return result

    def get_events(self, file_id: str) -> list[FileEvent]:
        """获取文件的事件历史。"""
        rows = self._store.get_events(file_id)
        return [FileEvent.from_dict(r) for r in rows]

    # ── 路径解析 ─────────────────────────────────────────────

    def resolve_for_tool(self, path_or_alias: str) -> str:
        """任何路径/别名 → 实际可用的规范路径。

        查找顺序：canonical_path 精确 → alias → original_name 模糊匹配。
        """
        # 1. canonical_path 精确匹配
        if path_or_alias in self._path_cache:
            return path_or_alias

        # 2. alias 匹配
        entry = self.get_by_alias(path_or_alias)
        if entry:
            return entry.canonical_path

        # 3. original_name 模糊匹配
        for e in self._path_cache.values():
            if e.deleted_at is None and e.original_name == path_or_alias:
                return e.canonical_path

        # 4. 直接返回原始路径
        return path_or_alias

    def resolve_for_display(self, canonical_path: str) -> str:
        """规范路径 → 用户友好的原始名。"""
        entry = self._path_cache.get(canonical_path)
        if entry:
            return entry.original_name
        return Path(canonical_path).name

    # ── 软删除 ───────────────────────────────────────────────

    def mark_deleted(self, canonical_path: str) -> None:
        """软删除（文件从磁盘消失时调用）。"""
        entry = self._path_cache.get(canonical_path)
        if entry:
            entry.deleted_at = _now_iso()
            entry.updated_at = entry.deleted_at
        self._store.soft_delete(self._workspace_key, canonical_path)
        # 不从缓存移除，保留 provenance

    # ── 重命名/移动 ──────────────────────────────────────────

    def rename_entry(
        self,
        old_path: str,
        new_path: str,
        *,
        session_id: str | None = None,
        turn: int | None = None,
    ) -> bool:
        """原子重命名文件路径，保留 file_id / provenance / events / 别名。

        同时同步别名缓存，并记录 renamed 事件。
        返回 True 表示成功迁移，False 表示旧路径不存在于注册表。
        """
        entry = self._path_cache.get(old_path)
        if entry is None:
            return False

        # 1. DB 层原子更新 canonical_path
        ok = self._store.rename_path(self._workspace_key, old_path, new_path)
        if not ok:
            return False

        # 2. 更新内存缓存：删除旧路径，以新路径重建索引
        self._path_cache.pop(old_path, None)
        entry.canonical_path = new_path
        entry.original_name = Path(new_path).name
        entry.file_type = _detect_file_type(new_path)
        # 刷新 mtime / size
        try:
            resolved = self._resolve(new_path)
            if resolved.exists():
                stat = resolved.stat()
                entry.size_bytes = stat.st_size
                entry.mtime_ns = stat.st_mtime_ns
        except (ValueError, OSError):
            pass
        entry.updated_at = _now_iso()
        self._cache_entry(entry)

        # 3. 添加旧路径为 previous_path 别名，保证旧路径仍可被解析
        self.add_alias(entry.id, "previous_path", old_path)

        # 4. 记录 renamed 事件
        self.record_event(
            entry.id, "renamed",
            session_id=session_id, turn=turn,
            tool_name="rename_file",
            details={"old_path": old_path, "new_path": new_path},
        )

        logger.info("FileRegistry rename_entry: %s → %s (id=%s)", old_path, new_path, entry.id)
        return True

    # ── 添加别名 ─────────────────────────────────────────────

    def add_alias(
        self,
        file_id: str,
        alias_type: str,
        alias_value: str,
    ) -> None:
        """为文件添加别名。"""
        alias_id = _new_id()
        self._store.add_alias(alias_id, file_id, alias_type, alias_value)
        self._alias_cache[alias_value] = file_id

    # ── 文件组管理 ─────────────────────────────────────────────

    def create_group(
        self,
        name: str,
        file_ids: list[str] | None = None,
        description: str = "",
    ) -> FileGroup:
        """创建文件组，可选同时添加初始成员。"""
        now = _now_iso()
        group = FileGroup(
            id=_new_id(),
            workspace=self._workspace_key,
            name=name,
            description=description,
            created_at=now,
            updated_at=now,
        )
        self._store.create_group(group.to_dict())
        if file_ids:
            for fid in file_ids:
                self._store.add_group_member(group.id, fid)
        return group

    def update_group(
        self,
        group_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
    ) -> FileGroup | None:
        """更新文件组名称/描述。"""
        updates: dict[str, Any] = {}
        if name is not None:
            updates["name"] = name
        if description is not None:
            updates["description"] = description
        if not updates:
            row = self._store.get_group(group_id)
            return FileGroup.from_dict(row) if row else None
        self._store.update_group(group_id, updates)
        row = self._store.get_group(group_id)
        return FileGroup.from_dict(row) if row else None

    def delete_group(self, group_id: str) -> bool:
        """删除文件组（CASCADE 自动删成员）。"""
        return self._store.delete_group(group_id)

    def list_groups(self) -> list[FileGroup]:
        """列出当前工作区所有文件组。"""
        rows = self._store.list_groups(self._workspace_key)
        return [FileGroup.from_dict(r) for r in rows]

    def get_group(self, group_id: str) -> FileGroup | None:
        """按 ID 查询文件组。"""
        row = self._store.get_group(group_id)
        return FileGroup.from_dict(row) if row else None

    def add_to_group(
        self,
        group_id: str,
        file_id: str,
        role: str = "member",
    ) -> None:
        """将文件添加到组。"""
        self._store.add_group_member(group_id, file_id, role)

    def remove_from_group(self, group_id: str, file_id: str) -> bool:
        """从组中移除文件。"""
        return self._store.remove_group_member(group_id, file_id)

    def get_group_files(self, group_id: str) -> list[dict[str, Any]]:
        """获取组内所有文件成员（含文件信息和角色）。"""
        return self._store.list_group_members(group_id)

    def get_file_groups(self, file_id: str) -> list[FileGroup]:
        """查询文件所属的所有组。"""
        rows = self._store.get_file_groups(file_id)
        return [FileGroup.from_dict(r) for r in rows]

    # ── 扫描 ─────────────────────────────────────────────────

    def scan_workspace(
        self,
        *,
        max_files: int = 1000,
        header_scan_rows: int = 5,
        excel_only: bool = False,
        extract_sheet_meta: bool = True,
    ) -> ScanResult:
        """递归扫描工作区，注册/更新文件到 registry。

        扫描范围：工作区根目录 + uploads/ + outputs/ 下的所有文件。
        ``extract_sheet_meta=True`` 时 Excel 额外提取 sheet 元数据。
        Agent 首轮扫描应关此项，避免在模型请求前打开 xlsx。
        """
        start_ts = time.monotonic()
        result = ScanResult()

        collected = self._collect_file_paths(max_files, excel_only=excel_only)
        current_rel_paths: set[str] = set()

        for fp in collected:
            try:
                stat = fp.stat()
            except OSError:
                continue

            rel_path = self._to_rel(fp)
            current_rel_paths.add(rel_path)
            mtime_ns = stat.st_mtime_ns

            existing = self._path_cache.get(rel_path)
            if existing and existing.mtime_ns == mtime_ns and existing.size_bytes == stat.st_size:
                result.cache_hits += 1
                continue

            file_type = _detect_file_type(rel_path)
            sheet_meta: list[dict] = []
            if extract_sheet_meta and file_type in ("excel",):
                try:
                    sheet_meta = self._scan_file_sheets(fp, header_scan_rows)
                except Exception:
                    logger.debug("扫描文件 %s 失败", fp, exc_info=True)
            elif extract_sheet_meta and file_type == "word":
                try:
                    sheet_meta = self._scan_word_meta(fp)
                except Exception:
                    logger.debug("扫描 Word 文件 %s 失败", fp, exc_info=True)

            if existing:
                result.updated_files += 1
            else:
                result.new_files += 1

            self.register_from_scan(
                canonical_path=rel_path,
                original_name=fp.name,
                size_bytes=stat.st_size,
                mtime_ns=mtime_ns,
                file_type=file_type,
                sheet_meta=sheet_meta,
            )

        # 软删除磁盘已不存在的文件（仅 scan origin 的文件）
        for path, entry in list(self._path_cache.items()):
            if (
                entry.origin == "scan"
                and entry.deleted_at is None
                and path not in current_rel_paths
            ):
                self.mark_deleted(path)
                result.deleted_files += 1

        result.total_files = len(current_rel_paths)
        result.scan_duration_ms = int((time.monotonic() - start_ts) * 1000)

        logger.info(
            "FileRegistry scan: %d 文件 (新增 %d, 更新 %d, 删除 %d, 缓存命中 %d), 耗时 %dms",
            result.total_files, result.new_files, result.updated_files,
            result.deleted_files, result.cache_hits, result.scan_duration_ms,
        )
        return result

    def scan_uploads(self, *, header_scan_rows: int = 5) -> ScanResult:
        """专门扫描 uploads/ 目录并注册未跟踪的上传文件。

        区别于 scan_workspace：仅扫描 uploads/ 子目录，
        且对已通过 register_upload 注册的文件只做增量更新。
        """
        uploads_dir = self._workspace_root / "uploads"
        if not uploads_dir.exists():
            return ScanResult()

        start_ts = time.monotonic()
        result = ScanResult()

        for walk_root, dirs, files in os.walk(uploads_dir):
            dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
            for name in files:
                if is_hidden_name(name):
                    continue
                fp = Path(walk_root, name)
                try:
                    stat = fp.stat()
                except OSError:
                    continue

                rel_path = self._to_rel(fp)
                mtime_ns = stat.st_mtime_ns

                existing = self._path_cache.get(rel_path)
                if existing:
                    if existing.mtime_ns == mtime_ns and existing.size_bytes == stat.st_size:
                        result.cache_hits += 1
                    elif existing.mtime_ns == 0 and existing.size_bytes == stat.st_size:
                        # register_upload 未设 mtime → 补填 mtime，视为缓存命中
                        existing.mtime_ns = mtime_ns
                        self._store.upsert_file(existing.to_dict())
                        self._cache_entry(existing)
                        result.cache_hits += 1
                    else:
                        # 已注册但内容变化 → 更新 mtime/size
                        existing.mtime_ns = mtime_ns
                        existing.size_bytes = stat.st_size
                        existing.updated_at = _now_iso()
                        self._store.upsert_file(existing.to_dict())
                        self._cache_entry(existing)
                        result.updated_files += 1
                    result.total_files += 1
                    continue

                # 未注册的上传文件 → 按 scan origin 注册
                file_type = _detect_file_type(rel_path)
                sheet_meta: list[dict] = []
                if file_type in ("excel",):
                    try:
                        sheet_meta = self._scan_file_sheets(fp, header_scan_rows)
                    except Exception:
                        logger.debug("扫描上传文件 %s 失败", fp, exc_info=True)
                elif file_type == "word":
                    try:
                        sheet_meta = self._scan_word_meta(fp)
                    except Exception:
                        logger.debug("扫描上传 Word 文件 %s 失败", fp, exc_info=True)

                self.register_from_scan(
                    canonical_path=rel_path,
                    original_name=name,
                    size_bytes=stat.st_size,
                    mtime_ns=mtime_ns,
                    file_type=file_type,
                    sheet_meta=sheet_meta,
                )
                result.new_files += 1
                result.total_files += 1

        result.scan_duration_ms = int((time.monotonic() - start_ts) * 1000)
        return result

    def _collect_file_paths(self, max_files: int, *, excel_only: bool = False) -> list[Path]:
        """递归收集工作区中的文件路径。"""
        from excelmanus.security.source_isolation import is_product_source_path
        root = self._workspace_root
        paths: list[Path] = []

        for walk_root, dirs, files in os.walk(root):
            try:
                rel_dir = Path(walk_root).relative_to(root).as_posix()
            except ValueError:
                rel_dir = "."
            if rel_dir in _SKIP_REL_PREFIXES or any(
                rel_dir.startswith(prefix + "/") for prefix in _SKIP_REL_PREFIXES
            ):
                dirs[:] = []
                continue
            dirs[:] = [d for d in dirs if d not in _SKIP_DIRS
                       and not is_product_source_path(Path(walk_root) / d, root)]
            if rel_dir == "outputs":
                dirs[:] = [d for d in dirs if d not in {"backups", "audits", ".versions"}]
            for name in files:
                if is_hidden_name(name):
                    continue
                if is_product_source_path(Path(walk_root) / name, root):
                    continue
                _, ext = os.path.splitext(name)
                ext_lower = ext.lower()

                if excel_only:
                    if ext_lower not in _EXCEL_EXTENSIONS:
                        continue
                else:
                    # 跳过完全无用的二进制文件
                    if ext_lower in _SKIP_EXTENSIONS:
                        continue

                paths.append(Path(walk_root, name))
                if len(paths) >= max_files:
                    paths.sort(key=lambda p: str(p.relative_to(root)).lower())
                    return paths

        paths.sort(key=lambda p: str(p.relative_to(root)).lower())
        return paths

    @staticmethod
    def _scan_word_meta(fp: Path) -> list[dict]:
        """扫描单个 Word 文件的结构元数据。"""
        try:
            from docx import Document
        except ImportError:
            return []
        doc = Document(str(fp))
        headings = 0
        for para in doc.paragraphs:
            style = para.style.name or "" if para.style else ""
            if style.startswith("Heading") or style == "Title":
                headings += 1
        return [{
            "paragraphs": len(doc.paragraphs),
            "tables": len(doc.tables),
            "headings": headings,
            "sections": len(doc.sections),
        }]

    @staticmethod
    def _scan_file_sheets(fp: Path, header_scan_rows: int) -> list[dict]:
        """扫描单个 Excel 文件的 sheet 元数据。

        支持 .xlsx/.xlsm (openpyxl)、.xls (xlrd)、.xlsb (pyxlsb)、.csv。
        """
        ext = fp.suffix.lower()
        before = fp.stat()
        scanner = {
            ".csv": FileRegistry._scan_csv_sheets,
            ".xls": FileRegistry._scan_xls_sheets,
            ".xlsb": FileRegistry._scan_xlsb_sheets,
        }.get(ext, FileRegistry._scan_xlsx_sheets)
        sheets = scanner(fp, header_scan_rows)
        after = fp.stat()
        # A freshness hint, never a substitute for the tool's content_version.
        if (before.st_mtime_ns, before.st_size) == (after.st_mtime_ns, after.st_size):
            for sheet in sheets:
                sheet["cache_stamp"] = f"{after.st_mtime_ns}:{after.st_size}"
        return sheets

    @staticmethod
    def _scan_csv_sheets(fp: Path, header_scan_rows: int) -> list[dict]:
        """扫描 CSV 文件的元数据。"""
        import csv as _csv

        _enc = "utf-8"
        for _try_enc in ("utf-8-sig", "utf-8", "gbk", "gb18030", "latin-1"):
            try:
                with open(fp, "r", encoding=_try_enc) as _f:
                    _f.read(4096)
                _enc = _try_enc
                break
            except (UnicodeDecodeError, LookupError):
                continue

        with open(fp, "r", encoding=_enc, newline="") as _f:
            reader = _csv.reader(_f)
            rows_raw: list[list[str]] = []
            for row in reader:
                rows_raw.append(row)
                if len(rows_raw) >= header_scan_rows + 1:
                    break

        total_rows = len(rows_raw)
        total_cols = max((len(r) for r in rows_raw), default=0)
        headers: list[str] = []
        if rows_raw:
            best_idx = 0
            best_score = -1
            for idx, r in enumerate(rows_raw):
                non_empty = [v for v in r if v and v.strip()]
                score = len(non_empty) * 2
                if score > best_score:
                    best_score = score
                    best_idx = idx
            headers = [v.strip() for v in rows_raw[best_idx] if v and v.strip()]

        return [{
            "name": "Sheet1",
            "rows": total_rows,
            "columns": total_cols,
            "headers": headers,
            "column_names": [v.strip() if v and v.strip() else None for v in rows_raw[best_idx]] if rows_raw else [],
            "header_heuristic": True,
            "header_row": (best_idx + 1) if rows_raw else None,
        }]

    @staticmethod
    def _scan_xls_sheets(fp: Path, header_scan_rows: int) -> list[dict]:
        """用 xlrd 扫描 .xls 文件的 sheet 元数据。"""
        try:
            import xlrd
        except ImportError:
            logger.warning("xlrd 未安装，无法扫描 .xls 文件: %s", fp.name)
            return []

        xls_wb = xlrd.open_workbook(str(fp), on_demand=True)
        sheets: list[dict] = []
        try:
            for si in range(xls_wb.nsheets):
                xls_ws = xls_wb.sheet_by_index(si)
                total_rows = xls_ws.nrows
                total_cols = xls_ws.ncols

                headers: list[str] = []
                if total_rows > 0:
                    scan_limit = min(header_scan_rows, total_rows)
                    rows_raw: list[list[Any]] = []
                    for r in range(scan_limit):
                        row_vals = []
                        for c in range(min(total_cols, 30)):
                            row_vals.append(xls_ws.cell_value(r, c))
                        rows_raw.append(row_vals)

                    if rows_raw:
                        best_idx = 0
                        best_score = -1
                        for idx, r in enumerate(rows_raw):
                            non_empty = [v for v in r if v is not None and str(v).strip()]
                            str_count = sum(1 for v in non_empty if isinstance(v, str))
                            score = str_count * 2 + len(non_empty)
                            if score > best_score:
                                best_score = score
                                best_idx = idx
                        header_row = rows_raw[best_idx]
                        headers = [
                            str(v).strip()
                            for v in header_row
                            if v is not None and str(v).strip()
                        ]

                sheets.append({
                    "name": xls_ws.name,
                    "rows": total_rows,
                    "columns": total_cols,
                    "headers": headers,
                    "header_heuristic": True,
                })
        finally:
            xls_wb.release_resources()
        return sheets

    @staticmethod
    def _scan_xlsb_sheets(fp: Path, header_scan_rows: int) -> list[dict]:
        """用 pyxlsb 扫描 .xlsb 文件的 sheet 元数据。"""
        try:
            from pyxlsb import open_workbook as open_xlsb
        except ImportError:
            logger.warning("pyxlsb 未安装，无法扫描 .xlsb 文件: %s", fp.name)
            return []

        sheets: list[dict] = []
        xlsb_wb = open_xlsb(str(fp))
        try:
            for sheet_name in xlsb_wb.sheets:
                total_rows = 0
                total_cols = 0
                rows_raw: list[list[Any]] = []

                with xlsb_wb.get_sheet(sheet_name) as xlsb_ws:
                    for row in xlsb_ws.rows():
                        total_rows += 1
                        row_vals = [cell.v for cell in row]
                        if len(row_vals) > total_cols:
                            total_cols = len(row_vals)
                        if len(rows_raw) < header_scan_rows:
                            rows_raw.append(row_vals)

                headers: list[str] = []
                if rows_raw:
                    best_idx = 0
                    best_score = -1
                    for idx, r in enumerate(rows_raw):
                        non_empty = [v for v in r if v is not None and str(v).strip()]
                        str_count = sum(1 for v in non_empty if isinstance(v, str))
                        score = str_count * 2 + len(non_empty)
                        if score > best_score:
                            best_score = score
                            best_idx = idx
                    header_row = rows_raw[best_idx]
                    headers = [
                        str(v).strip()
                        for v in header_row
                        if v is not None and str(v).strip()
                    ]

                sheets.append({
                    "name": sheet_name,
                    "rows": total_rows,
                    "columns": total_cols,
                    "headers": headers,
                    "header_heuristic": True,
                })
        finally:
            xlsb_wb.close()
        return sheets

    @staticmethod
    def _scan_xlsx_sheets(fp: Path, header_scan_rows: int) -> list[dict]:
        """用 openpyxl 扫描 .xlsx/.xlsm 文件的 sheet 元数据。"""
        from openpyxl import load_workbook

        sheets: list[dict] = []
        wb = load_workbook(fp, read_only=True, data_only=True)
        try:
            for sn in wb.sheetnames:
                ws = wb[sn]
                total_rows = ws.max_row or 0
                total_cols = ws.max_column or 0

                headers: list[str] = []
                column_names: list[str | None] = []
                detected_header: int | None = None
                if total_rows > 0:
                    scan_limit = min(header_scan_rows, total_rows)
                    rows_raw: list[list[Any]] = []
                    for row in ws.iter_rows(
                        min_row=1,
                        max_row=scan_limit,
                        min_col=1,
                        max_col=min(total_cols, 30),
                        values_only=True,
                    ):
                        rows_raw.append(list(row))

                    if rows_raw:
                        best_idx = 0
                        best_score = -1
                        for idx, r in enumerate(rows_raw):
                            non_empty = [v for v in r if v is not None and str(v).strip()]
                            str_count = sum(1 for v in non_empty if isinstance(v, str))
                            score = str_count * 2 + len(non_empty)
                            if score > best_score:
                                best_score = score
                                best_idx = idx
                        header_row = rows_raw[best_idx]
                        detected_header = best_idx + 1
                        column_names = [str(v).strip() if v is not None and str(v).strip() else None
                                        for v in header_row]
                        headers = [
                            str(v).strip()
                            for v in header_row
                            if v is not None and str(v).strip()
                        ]

                sheets.append({
                    "name": sn,
                    "rows": total_rows,
                    "columns": total_cols,
                    "headers": headers,
                    "column_names": column_names,
                    "header_row": detected_header,
                    "header_heuristic": True,
                })
        finally:
            wb.close()
        return sheets

    @property
    def has_versions(self) -> bool:
        """FileVersionManager is gone. History is RevisionStore."""
        return False


_SHARED_REGISTRIES: dict[str, FileRegistry] = {}


def get_shared_file_registry(database: "Database", workspace_root: str | Path) -> FileRegistry:
    """Process-wide FileRegistry: engine and API share one instance per workspace."""
    key = str(Path(workspace_root).expanduser().resolve())
    existing = _SHARED_REGISTRIES.get(key)
    if existing is not None:
        return existing
    reg = FileRegistry(database, workspace_root)
    _SHARED_REGISTRIES[key] = reg
    return reg
