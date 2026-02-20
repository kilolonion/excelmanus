"""Accept 门禁与变更审计。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from difflib import unified_diff
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
from typing import Any, Callable, Sequence

from excelmanus.tools.policy import (
    AUDIT_TARGET_ARG_RULES_ALL,
    AUDIT_TARGET_ARG_RULES_FIRST,
    MUTATING_ALL_TOOLS,
    MUTATING_AUDIT_ONLY_TOOLS,
    MUTATING_CONFIRM_TOOLS,
    READ_ONLY_SAFE_TOOLS,
    WORKSPACE_SCAN_EXCLUDE_PREFIXES,
    WORKSPACE_SCAN_MAX_FILES,
    WORKSPACE_SCAN_MAX_HASH_BYTES,
)


@dataclass
class FileChangeRecord:
    path: str
    before_exists: bool
    after_exists: bool
    before_hash: str | None
    after_hash: str | None
    before_size: int | None
    after_size: int | None
    is_binary: bool
    text_diff_file: str | None = None
    before_snapshot_file: str | None = None


@dataclass
class BinarySnapshotRecord:
    path: str
    snapshot_file: str
    hash_sha256: str
    size_bytes: int


@dataclass
class PendingApproval:
    approval_id: str
    tool_name: str
    arguments: dict[str, Any]
    tool_scope: list[str]
    created_at_utc: str


@dataclass
class AppliedApprovalRecord:
    approval_id: str
    tool_name: str
    arguments: dict[str, Any]
    tool_scope: list[str]
    created_at_utc: str
    applied_at_utc: str
    undoable: bool
    manifest_file: str
    audit_dir: str
    result_preview: str
    execution_status: str = "success"
    error_type: str | None = None
    error_message: str | None = None
    partial_scan: bool = False
    patch_file: str | None = None
    changes: list[FileChangeRecord] = field(default_factory=list)
    binary_snapshots: list[BinarySnapshotRecord] = field(default_factory=list)
    repo_diff_before_file: str | None = None
    repo_diff_after_file: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class _FileSnapshot:
    exists: bool
    content: bytes | None

    @property
    def size(self) -> int | None:
        return len(self.content) if self.content is not None else None

    @property
    def sha256(self) -> str | None:
        if self.content is None:
            return None
        return hashlib.sha256(self.content).hexdigest()


class ApprovalManager:
    """审批状态与审计管理器。"""

    # 默认策略（私有类常量，仅作为实例初始化模板）
    _READ_ONLY_SAFE_TOOLS: frozenset[str] = frozenset(READ_ONLY_SAFE_TOOLS)
    _CONFIRM_TOOLS: frozenset[str] = frozenset(MUTATING_CONFIRM_TOOLS)
    _AUDIT_ONLY_TOOLS: frozenset[str] = frozenset(MUTATING_AUDIT_ONLY_TOOLS)
    _MUTATING_TOOLS: frozenset[str] = frozenset(MUTATING_ALL_TOOLS)

    def __init__(self, workspace_root: str, audit_root: str = "outputs/approvals") -> None:
        self.workspace_root = Path(workspace_root).expanduser().resolve()
        self.audit_root = (self.workspace_root / audit_root).resolve()
        self.audit_root.mkdir(parents=True, exist_ok=True)
        self._pending: PendingApproval | None = None
        self._applied: dict[str, AppliedApprovalRecord] = {}
        self._mcp_auto_approved: set[str] = set()

        # 会话实例级副本，避免类级可变状态污染。
        self._read_only_safe_tools: set[str] = set(self._READ_ONLY_SAFE_TOOLS)
        self._confirm_tools: set[str] = set(self._CONFIRM_TOOLS)
        self._audit_only_tools: set[str] = set(self._AUDIT_ONLY_TOOLS)
        self._mutating_tools: set[str] = set(self._MUTATING_TOOLS)

    def register_mcp_auto_approve(self, prefixed_names: Sequence[str]) -> None:
        """注册 MCP 工具白名单（自动批准，无需用户确认）。"""
        self._mcp_auto_approved.update(prefixed_names)

    def register_read_only_safe_tools(self, tool_names: Sequence[str]) -> None:
        """注册额外只读安全工具（仅影响当前实例）。"""
        self._read_only_safe_tools.update(
            str(name).strip() for name in tool_names if str(name).strip()
        )

    @property
    def pending(self) -> PendingApproval | None:
        return self._pending

    def has_pending(self) -> bool:
        return self._pending is not None

    def is_read_only_safe_tool(self, tool_name: str) -> bool:
        return tool_name in self._read_only_safe_tools

    def is_audit_only_tool(self, tool_name: str) -> bool:
        return tool_name in self._audit_only_tools

    def is_mutating_tool(self, tool_name: str) -> bool:
        return tool_name in self._mutating_tools

    def is_confirm_required_tool(self, tool_name: str) -> bool:
        if self.is_read_only_safe_tool(tool_name):
            return False
        if self.is_mcp_tool(tool_name):
            return not self.is_mcp_auto_approved(tool_name)
        if self.is_audit_only_tool(tool_name):
            return False
        return tool_name in self._confirm_tools

    def is_high_risk_tool(self, tool_name: str) -> bool:
        return self.is_confirm_required_tool(tool_name)

    def is_mcp_tool(self, tool_name: str) -> bool:
        """判断工具名是否为 MCP 远程工具（以 mcp_ 前缀开头）。"""
        return tool_name.startswith("mcp_")

    def is_mcp_auto_approved(self, tool_name: str) -> bool:
        """判断 MCP 工具是否在白名单中（自动批准）。"""
        return tool_name in self._mcp_auto_approved

    def create_pending(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        tool_scope: Sequence[str] | None = None,
    ) -> PendingApproval:
        if self._pending is not None:
            raise ValueError("存在待确认操作，请先执行 `/accept <id>` 或 `/reject <id>`。")
        pending = PendingApproval(
            approval_id=self._new_approval_id(),
            tool_name=tool_name,
            arguments=dict(arguments),
            tool_scope=list(tool_scope) if tool_scope is not None else [],
            created_at_utc=self._utc_now(),
        )
        self._pending = pending
        return pending

    def new_approval_id(self) -> str:
        return self._new_approval_id()

    def utc_now(self) -> str:
        return self._utc_now()

    def reject_pending(self, approval_id: str) -> str:
        if self._pending is None:
            return "当前没有待确认操作。"
        if self._pending.approval_id != approval_id:
            return f"待确认 ID 不匹配。当前待确认 ID 为 `{self._pending.approval_id}`。"
        tool_name = self._pending.tool_name
        self._pending = None
        return f"已拒绝待确认操作 `{approval_id}`（工具：{tool_name}）。"

    def clear_pending(self) -> None:
        self._pending = None

    def get_applied(self, approval_id: str) -> AppliedApprovalRecord | None:
        cached = self._applied.get(approval_id)
        if cached is not None:
            return cached

        loaded = self._load_applied_from_manifest(approval_id)
        if loaded is None:
            return None
        self._applied[approval_id] = loaded
        return loaded

    def pending_block_message(self) -> str:
        if self._pending is None:
            return "当前没有待确认操作。"
        return (
            "存在待确认操作，已暂停新的高风险执行。\n"
            f"- ID: `{self._pending.approval_id}`\n"
            f"- 工具: `{self._pending.tool_name}`\n"
            "请先执行 `/accept <id>` 或 `/reject <id>`。"
        )

    def execute_and_audit(
        self,
        *,
        approval_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        tool_scope: Sequence[str],
        execute: Callable[[str, dict[str, Any], Sequence[str]], Any],
        undoable: bool,
        created_at_utc: str | None = None,
        code_policy_info: dict[str, Any] | None = None,
    ) -> tuple[str, AppliedApprovalRecord]:
        audit_dir = self.audit_root / approval_id
        audit_dir.mkdir(parents=True, exist_ok=True)
        snapshots_dir = audit_dir / "snapshots"
        snapshots_dir.mkdir(parents=True, exist_ok=True)

        targets = self._resolve_target_paths(tool_name, arguments)
        use_workspace_scan = (not targets) and self.is_mutating_tool(tool_name)

        before_partial = False
        after_partial = False
        if use_workspace_scan:
            before, before_partial, _, _ = self._collect_workspace_snapshots()
        else:
            before = self._collect_file_snapshots(targets)

        repo_diff_before = self._git_diff_text()

        result_text = ""
        execute_error: Exception | None = None
        error_type: str | None = None
        error_message: str | None = None
        try:
            result_text = str(execute(tool_name, arguments, tool_scope))
        except Exception as exc:  # noqa: BLE001
            execute_error = exc
            error_type = type(exc).__name__
            error_message = str(exc)

        # ── 检测 registry 层返回的结构化错误 JSON（工具不再抛异常） ──
        if execute_error is None and result_text.startswith('{"status": "error"'):
            try:
                _err_payload = json.loads(result_text)
                if isinstance(_err_payload, dict) and _err_payload.get("status") == "error":
                    _error_code = _err_payload.get("error_code", "")
                    if _error_code == "TOOL_EXECUTION_ERROR":
                        error_type = "ToolExecutionError"
                    else:
                        error_type = _err_payload.get("exception") or "ToolExecutionError"
                    error_message = _err_payload.get("message") or result_text
                    # 创建一个虚拟异常对象以保持后续逻辑兼容
                    execute_error = RuntimeError(error_message)
            except (json.JSONDecodeError, AttributeError):
                pass

        if use_workspace_scan:
            after, after_partial, _, _ = self._collect_workspace_snapshots()
        else:
            after = self._collect_file_snapshots(targets)
        repo_diff_after = self._git_diff_text()

        if use_workspace_scan:
            changes, patch_text, binary_snapshots = self._build_change_records_from_snapshot_maps(
                before=before,
                after=after,
                snapshots_dir=snapshots_dir,
            )
        else:
            changes, patch_text, binary_snapshots = self._build_change_records(
                target_paths=targets,
                before=before,
                after=after,
                snapshots_dir=snapshots_dir,
            )

        patch_rel = None
        if patch_text:
            patch_file = audit_dir / "changes.patch"
            patch_file.write_text(patch_text, encoding="utf-8")
            patch_rel = str(patch_file.relative_to(self.workspace_root))
            for change in changes:
                if change.text_diff_file is None and not change.is_binary:
                    change.text_diff_file = patch_rel

        repo_before_file = audit_dir / "repo_diff_before.txt"
        repo_after_file = audit_dir / "repo_diff_after.txt"
        repo_before_file.write_text(repo_diff_before, encoding="utf-8")
        repo_after_file.write_text(repo_diff_after, encoding="utf-8")

        execution_status = "failed" if execute_error is not None else "success"
        preview_src = result_text
        if execute_error is not None:
            preview_src = f"{error_type}: {error_message}" if error_type else (error_message or "")

        record = AppliedApprovalRecord(
            approval_id=approval_id,
            tool_name=tool_name,
            arguments=dict(arguments),
            tool_scope=list(tool_scope) if tool_scope is not None else [],
            created_at_utc=created_at_utc or self._utc_now(),
            applied_at_utc=self._utc_now(),
            undoable=undoable,
            manifest_file=str((audit_dir / "manifest.json").relative_to(self.workspace_root)),
            audit_dir=str(audit_dir.relative_to(self.workspace_root)),
            result_preview=self._shorten(preview_src, 300),
            execution_status=execution_status,
            error_type=error_type,
            error_message=error_message,
            partial_scan=(before_partial or after_partial),
            patch_file=patch_rel,
            changes=changes,
            binary_snapshots=binary_snapshots,
            repo_diff_before_file=str(repo_before_file.relative_to(self.workspace_root)),
            repo_diff_after_file=str(repo_after_file.relative_to(self.workspace_root)),
        )

        manifest = self._build_manifest_v2(record, code_policy_info=code_policy_info)
        (audit_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        self._applied[approval_id] = record

        if execute_error is not None:
            raise execute_error
        return result_text, record

    def undo(self, approval_id: str) -> str:
        record = self.get_applied(approval_id)
        if record is None:
            return f"未找到已执行记录 `{approval_id}`。"
        if not record.undoable:
            return f"记录 `{approval_id}` 不支持自动回滚（工具：{record.tool_name}）。"
        if not record.changes:
            return f"记录 `{approval_id}` 没有可回滚的文件变更。"

        conflicts: list[str] = []
        for change in record.changes:
            path = self.workspace_root / change.path
            exists = path.exists() and path.is_file()
            if exists != change.after_exists:
                conflicts.append(f"{change.path}: 当前存在状态与执行后记录不一致")
                continue
            if exists:
                current_hash = self._sha256(path.read_bytes())
                if current_hash != change.after_hash:
                    conflicts.append(f"{change.path}: 文件内容已变化（hash 不匹配）")
        if conflicts:
            detail = "\n".join(f"- {line}" for line in conflicts)
            return f"回滚被拒绝：检测到后续变更冲突。\n{detail}\n请先人工确认后再处理。"

        restored = 0
        deleted = 0
        for change in record.changes:
            path = self.workspace_root / change.path
            if change.before_exists:
                if not change.before_snapshot_file:
                    return f"回滚失败：缺少快照 `{change.path}`。"
                snapshot = self.workspace_root / change.before_snapshot_file
                if not snapshot.exists():
                    return f"回滚失败：快照文件不存在 `{change.before_snapshot_file}`。"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(snapshot.read_bytes())
                restored += 1
            elif path.exists():
                path.unlink()
                deleted += 1
        return f"已回滚 `{approval_id}`：恢复 {restored} 个文件，删除 {deleted} 个新增文件。"

    def _new_approval_id(self) -> str:
        now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        return f"apv_{now}_{secrets.token_hex(3)}"

    @staticmethod
    def _utc_now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _shorten(text: str, limit: int) -> str:
        return text if len(text) <= limit else f"{text[:limit]}...(truncated)"

    def _resolve_target_paths(self, tool_name: str, arguments: dict[str, Any]) -> list[Path]:
        path_args: list[str] = []

        all_fields = AUDIT_TARGET_ARG_RULES_ALL.get(tool_name)
        if all_fields is not None:
            for field_name in all_fields:
                raw = arguments.get(field_name)
                text = str(raw).strip() if raw is not None else ""
                if text:
                    path_args.append(text)
        else:
            first_fields = AUDIT_TARGET_ARG_RULES_FIRST.get(tool_name)
            if first_fields is not None:
                for field_name in first_fields:
                    raw = arguments.get(field_name)
                    text = str(raw).strip() if raw is not None else ""
                    if text:
                        path_args = [text]
                        break

        resolved: list[Path] = []
        seen: set[str] = set()
        for raw in path_args:
            path = self._resolve_workspace_path(raw)
            if path is None:
                continue
            key = str(path)
            if key in seen:
                continue
            seen.add(key)
            resolved.append(path)
        return resolved

    def _resolve_workspace_path(self, value: str) -> Path | None:
        if not value:
            return None
        raw = Path(value).expanduser()
        path = raw if raw.is_absolute() else self.workspace_root / raw
        resolved = path.resolve(strict=False)
        try:
            resolved.relative_to(self.workspace_root)
        except ValueError:
            return None
        return resolved

    def _collect_file_snapshots(self, paths: list[Path]) -> dict[str, _FileSnapshot]:
        snapshots: dict[str, _FileSnapshot] = {}
        for path in paths:
            rel = str(path.relative_to(self.workspace_root))
            if path.exists() and path.is_file():
                snapshots[rel] = _FileSnapshot(exists=True, content=path.read_bytes())
            else:
                snapshots[rel] = _FileSnapshot(exists=False, content=None)
        return snapshots

    def _collect_workspace_snapshots(self) -> tuple[dict[str, _FileSnapshot], bool, int, int]:
        snapshots: dict[str, _FileSnapshot] = {}
        partial_scan = False
        scanned_files = 0
        hashed_bytes = 0

        exclude_prefixes = tuple(
            self._normalize_rel_prefix(prefix)
            for prefix in WORKSPACE_SCAN_EXCLUDE_PREFIXES
            if self._normalize_rel_prefix(prefix)
        )

        for root, dirs, files in os.walk(self.workspace_root):
            root_path = Path(root)
            root_rel = root_path.relative_to(self.workspace_root)

            # 原地裁剪目录，降低遍历开销。
            kept_dirs: list[str] = []
            for name in dirs:
                rel_path = (
                    (root_rel / name).as_posix() if str(root_rel) != "." else Path(name).as_posix()
                )
                if self._is_excluded_rel(rel_path, exclude_prefixes):
                    continue
                kept_dirs.append(name)
            dirs[:] = kept_dirs

            for name in files:
                rel_path = (
                    (root_rel / name).as_posix() if str(root_rel) != "." else Path(name).as_posix()
                )
                if self._is_excluded_rel(rel_path, exclude_prefixes):
                    continue

                path = root_path / name
                if path.is_symlink() or not path.is_file():
                    continue

                scanned_files += 1
                if scanned_files > WORKSPACE_SCAN_MAX_FILES:
                    partial_scan = True
                    return snapshots, partial_scan, scanned_files - 1, hashed_bytes

                try:
                    size = path.stat().st_size
                except OSError:
                    continue

                if (hashed_bytes + size) > WORKSPACE_SCAN_MAX_HASH_BYTES:
                    partial_scan = True
                    return snapshots, partial_scan, scanned_files - 1, hashed_bytes

                try:
                    content = path.read_bytes()
                except OSError:
                    continue

                hashed_bytes += size
                snapshots[rel_path] = _FileSnapshot(exists=True, content=content)

        return snapshots, partial_scan, scanned_files, hashed_bytes

    @staticmethod
    def _normalize_rel_prefix(prefix: str) -> str:
        return prefix.replace("\\", "/").strip("/")

    @staticmethod
    def _is_excluded_rel(rel_path: str, prefixes: Sequence[str]) -> bool:
        normalized = rel_path.replace("\\", "/").strip("/")
        if not normalized:
            return False
        for prefix in prefixes:
            if normalized == prefix or normalized.startswith(f"{prefix}/"):
                return True
        return False

    def _build_change_records(
        self,
        *,
        target_paths: list[Path],
        before: dict[str, _FileSnapshot],
        after: dict[str, _FileSnapshot],
        snapshots_dir: Path,
    ) -> tuple[list[FileChangeRecord], str, list[BinarySnapshotRecord]]:
        before_subset: dict[str, _FileSnapshot] = {}
        after_subset: dict[str, _FileSnapshot] = {}
        for abs_path in target_paths:
            rel = str(abs_path.relative_to(self.workspace_root))
            before_subset[rel] = before.get(rel, _FileSnapshot(exists=False, content=None))
            after_subset[rel] = after.get(rel, _FileSnapshot(exists=False, content=None))
        return self._build_change_records_from_snapshot_maps(
            before=before_subset,
            after=after_subset,
            snapshots_dir=snapshots_dir,
        )

    def _build_change_records_from_snapshot_maps(
        self,
        *,
        before: dict[str, _FileSnapshot],
        after: dict[str, _FileSnapshot],
        snapshots_dir: Path,
    ) -> tuple[list[FileChangeRecord], str, list[BinarySnapshotRecord]]:
        changes: list[FileChangeRecord] = []
        binary_snapshots: list[BinarySnapshotRecord] = []
        patches: list[str] = []

        for rel in sorted(set(before) | set(after)):
            before_snap = before.get(rel, _FileSnapshot(exists=False, content=None))
            after_snap = after.get(rel, _FileSnapshot(exists=False, content=None))
            if (
                before_snap.exists == after_snap.exists
                and before_snap.content == after_snap.content
            ):
                continue

            base_content = (
                before_snap.content if before_snap.content is not None else after_snap.content
            )
            is_binary = self._is_binary_content(base_content)
            before_snapshot_file: str | None = None

            if before_snap.exists and before_snap.content is not None:
                snapshot_path = snapshots_dir / rel
                snapshot_path.parent.mkdir(parents=True, exist_ok=True)
                snapshot_path.write_bytes(before_snap.content)
                before_snapshot_file = str(snapshot_path.relative_to(self.workspace_root))
                if is_binary:
                    binary_snapshots.append(
                        BinarySnapshotRecord(
                            path=rel,
                            snapshot_file=before_snapshot_file,
                            hash_sha256=before_snap.sha256 or "",
                            size_bytes=before_snap.size or 0,
                        )
                    )

            if not is_binary:
                patch = self._build_unified_diff(
                    rel_path=rel,
                    before_content=before_snap.content,
                    after_content=after_snap.content,
                )
                if patch:
                    patches.append(patch)

            changes.append(
                FileChangeRecord(
                    path=rel,
                    before_exists=before_snap.exists,
                    after_exists=after_snap.exists,
                    before_hash=before_snap.sha256,
                    after_hash=after_snap.sha256,
                    before_size=before_snap.size,
                    after_size=after_snap.size,
                    is_binary=is_binary,
                    before_snapshot_file=before_snapshot_file,
                )
            )

        patch_text = "\n".join(patches).strip()
        if patch_text:
            patch_text += "\n"
        return changes, patch_text, binary_snapshots

    def _build_manifest_v2(
        self,
        record: AppliedApprovalRecord,
        *,
        code_policy_info: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "version": 2,
            "approval": {
                "approval_id": record.approval_id,
                "tool_name": record.tool_name,
                "arguments": record.arguments,
                "tool_scope": record.tool_scope,
                "created_at_utc": record.created_at_utc,
                "applied_at_utc": record.applied_at_utc,
                "undoable": record.undoable,
            },
            "execution": {
                "status": record.execution_status,
                "result_preview": record.result_preview,
                "error_type": record.error_type,
                "error_message": record.error_message,
                "partial_scan": record.partial_scan,
            },
            "artifacts": {
                "audit_dir": record.audit_dir,
                "manifest_file": record.manifest_file,
                "repo_diff_before_file": record.repo_diff_before_file,
                "repo_diff_after_file": record.repo_diff_after_file,
                "patch_file": record.patch_file,
            },
            "changes": {
                "files": [asdict(change) for change in record.changes],
                "binary_snapshots": [asdict(item) for item in record.binary_snapshots],
            },
        }
        if code_policy_info is not None:
            result["code_policy"] = code_policy_info
        return result

    def _load_applied_from_manifest(self, approval_id: str) -> AppliedApprovalRecord | None:
        manifest_path = self.audit_root / approval_id / "manifest.json"
        if not manifest_path.exists():
            return None

        try:
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

        if raw.get("version") != 2:
            return None

        approval = raw.get("approval") if isinstance(raw.get("approval"), dict) else {}
        execution = raw.get("execution") if isinstance(raw.get("execution"), dict) else {}
        artifacts = raw.get("artifacts") if isinstance(raw.get("artifacts"), dict) else {}
        changes_block = raw.get("changes") if isinstance(raw.get("changes"), dict) else {}

        changes_raw = changes_block.get("files")
        binary_raw = changes_block.get("binary_snapshots")

        changes: list[FileChangeRecord] = []
        if isinstance(changes_raw, list):
            for item in changes_raw:
                if not isinstance(item, dict):
                    continue
                changes.append(
                    FileChangeRecord(
                        path=str(item.get("path", "")),
                        before_exists=bool(item.get("before_exists", False)),
                        after_exists=bool(item.get("after_exists", False)),
                        before_hash=(
                            str(item.get("before_hash"))
                            if item.get("before_hash") is not None
                            else None
                        ),
                        after_hash=(
                            str(item.get("after_hash"))
                            if item.get("after_hash") is not None
                            else None
                        ),
                        before_size=(
                            int(item.get("before_size"))
                            if item.get("before_size") is not None
                            else None
                        ),
                        after_size=(
                            int(item.get("after_size"))
                            if item.get("after_size") is not None
                            else None
                        ),
                        is_binary=bool(item.get("is_binary", False)),
                        text_diff_file=(
                            str(item.get("text_diff_file"))
                            if item.get("text_diff_file") is not None
                            else None
                        ),
                        before_snapshot_file=(
                            str(item.get("before_snapshot_file"))
                            if item.get("before_snapshot_file") is not None
                            else None
                        ),
                    )
                )

        binary_snapshots: list[BinarySnapshotRecord] = []
        if isinstance(binary_raw, list):
            for item in binary_raw:
                if not isinstance(item, dict):
                    continue
                binary_snapshots.append(
                    BinarySnapshotRecord(
                        path=str(item.get("path", "")),
                        snapshot_file=str(item.get("snapshot_file", "")),
                        hash_sha256=str(item.get("hash_sha256", "")),
                        size_bytes=int(item.get("size_bytes", 0)),
                    )
                )

        applied_id = str(approval.get("approval_id") or approval_id)
        audit_dir = self.audit_root / applied_id

        return AppliedApprovalRecord(
            approval_id=applied_id,
            tool_name=str(approval.get("tool_name", "")),
            arguments=(
                dict(approval.get("arguments"))
                if isinstance(approval.get("arguments"), dict)
                else {}
            ),
            tool_scope=(
                [str(value) for value in approval.get("tool_scope", [])]
                if isinstance(approval.get("tool_scope"), list)
                else []
            ),
            created_at_utc=str(approval.get("created_at_utc", "")),
            applied_at_utc=str(approval.get("applied_at_utc", "")),
            undoable=bool(approval.get("undoable", False)),
            manifest_file=str(manifest_path.relative_to(self.workspace_root)),
            audit_dir=str(audit_dir.relative_to(self.workspace_root)),
            result_preview=str(execution.get("result_preview", "")),
            execution_status=str(execution.get("status", "success")),
            error_type=(
                str(execution.get("error_type"))
                if execution.get("error_type") is not None
                else None
            ),
            error_message=(
                str(execution.get("error_message"))
                if execution.get("error_message") is not None
                else None
            ),
            partial_scan=bool(execution.get("partial_scan", False)),
            patch_file=(
                str(artifacts.get("patch_file"))
                if artifacts.get("patch_file") is not None
                else None
            ),
            changes=changes,
            binary_snapshots=binary_snapshots,
            repo_diff_before_file=(
                str(artifacts.get("repo_diff_before_file"))
                if artifacts.get("repo_diff_before_file") is not None
                else None
            ),
            repo_diff_after_file=(
                str(artifacts.get("repo_diff_after_file"))
                if artifacts.get("repo_diff_after_file") is not None
                else None
            ),
        )

    @staticmethod
    def _sha256(content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()

    @staticmethod
    def _is_binary_content(content: bytes | None) -> bool:
        if content is None:
            return False
        if b"\x00" in content:
            return True
        try:
            content.decode("utf-8")
            return False
        except UnicodeDecodeError:
            return True

    def _build_unified_diff(
        self,
        *,
        rel_path: str,
        before_content: bytes | None,
        after_content: bytes | None,
    ) -> str:
        before_text = before_content.decode("utf-8", errors="replace") if before_content is not None else ""
        after_text = after_content.decode("utf-8", errors="replace") if after_content is not None else ""
        from_file = f"a/{rel_path}" if before_content is not None else "/dev/null"
        to_file = f"b/{rel_path}" if after_content is not None else "/dev/null"
        diff = unified_diff(
            before_text.splitlines(keepends=True),
            after_text.splitlines(keepends=True),
            fromfile=from_file,
            tofile=to_file,
            lineterm="",
        )
        return "\n".join(diff).strip()

    def _git_diff_text(self) -> str:
        if not (self.workspace_root / ".git").exists():
            return "git diff 不可用：当前工作区不是 git 仓库。"
        try:
            completed = subprocess.run(
                ["git", "-C", str(self.workspace_root), "diff", "--no-color"],
                capture_output=True,
                text=True,
                check=False,
            )
            if completed.returncode != 0:
                stderr = completed.stderr.strip()
                return stderr or f"git diff 执行失败，退出码 {completed.returncode}"
            return completed.stdout
        except Exception as exc:  # noqa: BLE001
            return f"git diff 捕获异常：{exc}"
