"""Accept 门禁与变更审计。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from difflib import unified_diff
import hashlib
import json
from pathlib import Path
import secrets
import subprocess
from typing import Any, Callable, Sequence

from excelmanus.tools.policy import (
    MUTATING_ALL_TOOLS,
    MUTATING_AUDIT_ONLY_TOOLS,
    MUTATING_CONFIRM_TOOLS,
    READ_ONLY_SAFE_TOOLS,
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
        return hashlib.sha256(self.content).hexdigest() if self.content else None


class ApprovalManager:
    """审批状态与审计管理器。"""

    # 显式只读白名单
    READ_ONLY_SAFE_TOOLS: set[str] = set(READ_ONLY_SAFE_TOOLS)
    # Tier A：需 /accept 确认后执行
    CONFIRM_TOOLS: set[str] = set(MUTATING_CONFIRM_TOOLS)
    # Tier B：不拦截确认，但必须进入审计
    AUDIT_ONLY_TOOLS: set[str] = set(MUTATING_AUDIT_ONLY_TOOLS)
    # 兼容历史字段：保留高风险集合别名（等价于 Tier A）。
    HIGH_RISK_TOOLS: set[str] = set(MUTATING_CONFIRM_TOOLS)
    # 所有会修改工作区文件的工具（Tier A + Tier B）
    MUTATING_TOOLS: set[str] = set(MUTATING_ALL_TOOLS)

    def __init__(self, workspace_root: str, audit_root: str = "outputs/approvals") -> None:
        self.workspace_root = Path(workspace_root).expanduser().resolve()
        self.audit_root = (self.workspace_root / audit_root).resolve()
        self.audit_root.mkdir(parents=True, exist_ok=True)
        self._pending: PendingApproval | None = None
        self._applied: dict[str, AppliedApprovalRecord] = {}
        # MCP 工具白名单：prefixed_name → True 表示自动批准
        self._mcp_auto_approved: set[str] = set()

    def register_mcp_auto_approve(self, prefixed_names: Sequence[str]) -> None:
        """注册 MCP 工具白名单（自动批准，无需用户确认）。"""
        self._mcp_auto_approved.update(prefixed_names)

    def register_read_only_safe_tools(self, tool_names: Sequence[str]) -> None:
        """注册额外只读安全工具（用于扩展白名单）。"""
        self.READ_ONLY_SAFE_TOOLS.update(str(name).strip() for name in tool_names if str(name).strip())

    @property
    def pending(self) -> PendingApproval | None:
        return self._pending

    def has_pending(self) -> bool:
        return self._pending is not None

    def is_read_only_safe_tool(self, tool_name: str) -> bool:
        return tool_name in self.READ_ONLY_SAFE_TOOLS

    def is_audit_only_tool(self, tool_name: str) -> bool:
        return tool_name in self.AUDIT_ONLY_TOOLS

    def is_mutating_tool(self, tool_name: str) -> bool:
        return tool_name in self.MUTATING_TOOLS

    def is_confirm_required_tool(self, tool_name: str) -> bool:
        if self.is_read_only_safe_tool(tool_name):
            return False
        if self.is_mcp_tool(tool_name):
            return not self.is_mcp_auto_approved(tool_name)
        if self.is_audit_only_tool(tool_name):
            return False
        if tool_name in self.CONFIRM_TOOLS:
            return True
        return False

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
        tool_scope: Sequence[str],
    ) -> PendingApproval:
        if self._pending is not None:
            raise ValueError("存在待确认操作，请先执行 `/accept <id>` 或 `/reject <id>`。")
        pending = PendingApproval(
            approval_id=self._new_approval_id(),
            tool_name=tool_name,
            arguments=dict(arguments),
            tool_scope=list(tool_scope),
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
        return self._applied.get(approval_id)

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
    ) -> tuple[str, AppliedApprovalRecord]:
        audit_dir = self.audit_root / approval_id
        audit_dir.mkdir(parents=True, exist_ok=True)
        snapshots_dir = audit_dir / "snapshots"
        snapshots_dir.mkdir(parents=True, exist_ok=True)

        targets = self._resolve_target_paths(tool_name, arguments)
        before = self._collect_file_snapshots(targets)
        repo_diff_before = self._git_diff_text()
        result_text = str(execute(tool_name, arguments, tool_scope))
        after = self._collect_file_snapshots(targets)
        repo_diff_after = self._git_diff_text()

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

        record = AppliedApprovalRecord(
            approval_id=approval_id,
            tool_name=tool_name,
            arguments=dict(arguments),
            tool_scope=list(tool_scope),
            created_at_utc=created_at_utc or self._utc_now(),
            applied_at_utc=self._utc_now(),
            undoable=undoable,
            manifest_file=str((audit_dir / "manifest.json").relative_to(self.workspace_root)),
            audit_dir=str(audit_dir.relative_to(self.workspace_root)),
            result_preview=self._shorten(result_text, 300),
            changes=changes,
            binary_snapshots=binary_snapshots,
            repo_diff_before_file=str(repo_before_file.relative_to(self.workspace_root)),
            repo_diff_after_file=str(repo_after_file.relative_to(self.workspace_root)),
        )
        manifest = {
            "approval_id": record.approval_id,
            "tool_name": record.tool_name,
            "arguments": record.arguments,
            "tool_scope": record.tool_scope,
            "created_at_utc": record.created_at_utc,
            "applied_at_utc": record.applied_at_utc,
            "undoable": record.undoable,
            "result_preview": record.result_preview,
            "audit_dir": record.audit_dir,
            "repo_diff_before_file": record.repo_diff_before_file,
            "repo_diff_after_file": record.repo_diff_after_file,
            "changes": [asdict(c) for c in record.changes],
            "binary_snapshots": [asdict(s) for s in record.binary_snapshots],
        }
        (audit_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self._applied[approval_id] = record
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
        if tool_name == "write_text_file":
            path_args = [str(arguments.get("file_path", ""))]
        elif tool_name == "copy_file":
            path_args = [str(arguments.get("destination", ""))]
        elif tool_name == "rename_file":
            path_args = [str(arguments.get("source", "")), str(arguments.get("destination", ""))]
        elif tool_name == "delete_file":
            path_args = [str(arguments.get("file_path", ""))]
        elif tool_name == "write_excel":
            path_args = [str(arguments.get("file_path", ""))]
        elif tool_name == "transform_data":
            out = arguments.get("output_path")
            path_args = [str(out)] if out else [str(arguments.get("file_path", ""))]
        elif tool_name in {
            "format_cells",
            "adjust_column_width",
            "adjust_row_height",
            "merge_cells",
            "unmerge_cells",
            "create_sheet",
            "copy_sheet",
            "rename_sheet",
            "delete_sheet",
            "create_excel_chart",
            "write_cells",
            "insert_rows",
            "insert_columns",
            "apply_threshold_icon_format",
            "style_card_blocks",
            "scale_range_unit",
            "apply_dashboard_dark_theme",
            "add_color_scale",
            "add_data_bar",
            "add_conditional_rule",
            "set_print_layout",
            "set_page_header_footer",
        }:
            path_args = [str(arguments.get("file_path", ""))]
        elif tool_name == "copy_range_between_sheets":
            target = arguments.get("target_file")
            path_args = [str(target)] if target else [str(arguments.get("source_file", ""))]
        elif tool_name == "create_chart":
            path_args = [str(arguments.get("output_path", ""))]

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

    def _build_change_records(
        self,
        *,
        target_paths: list[Path],
        before: dict[str, _FileSnapshot],
        after: dict[str, _FileSnapshot],
        snapshots_dir: Path,
    ) -> tuple[list[FileChangeRecord], str, list[BinarySnapshotRecord]]:
        changes: list[FileChangeRecord] = []
        binary_snapshots: list[BinarySnapshotRecord] = []
        patches: list[str] = []
        for abs_path in target_paths:
            rel = str(abs_path.relative_to(self.workspace_root))
            before_snap = before.get(rel, _FileSnapshot(exists=False, content=None))
            after_snap = after.get(rel, _FileSnapshot(exists=False, content=None))
            if before_snap.exists == after_snap.exists and before_snap.content == after_snap.content:
                continue

            base_content = before_snap.content if before_snap.content is not None else after_snap.content
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
        before_text = before_content.decode("utf-8", errors="replace") if before_content else ""
        after_text = after_content.decode("utf-8", errors="replace") if after_content else ""
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
