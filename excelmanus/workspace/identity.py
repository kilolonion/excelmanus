"""Canonical workspace file identity.

规则：

    CanonicalPath：工作区相对、``/`` 规范化、经 realpath。
    展示名可以不同（上传原名）。锁、CAS、事件只认它。

    解析 ``resolve()`` 与 ``catalog()`` 拒绝第一段为 ``.excelmanus`` 的路径
    （字面量和 realpath）。点文件、``~$``、symlink 进保留树同样拒绝。
    旧 ``outputs/backups``、``outputs/.versions`` 在清库前也当保留名拒绝写入。

``uploads/`` + ``8hex_`` 前缀留下：展示剥前缀，identity 用磁盘相对路径。
Overlay 残留先映射到正本再发；映射不到则跳过，禁止把备份路径当公开身份。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Literal

# 保留第一段 / 前缀：resolve + catalog 均拒绝（方案 §8 / P1）
RESERVED_FIRST_SEGMENTS: frozenset[str] = frozenset({
    ".excelmanus",
    ".versions",
})

RESERVED_PREFIXES: tuple[str, ...] = (
    ".excelmanus",
    "outputs/backups",
    "outputs/.versions",
    "outputs/audits",
    ".versions",
)

# FVM 时间戳副本：{stem}_{YYYYMMDDTHHMMSS}_{4hex}{ext}
_TS_BACKUP_NAME_RE = re.compile(
    r"^(?P<stem>.+)_(?P<ts>\d{8}T\d{6})_(?P<hex>[0-9a-fA-F]{4})(?P<ext>\.[^.]+)$"
)

_UPLOAD_REL_PREFIX_RE = re.compile(
    r"^uploads/(?P<hex>[0-9a-fA-F]{8})_(?P<rest>.+)$"
)
_UPLOAD_FILE_PREFIX_RE = re.compile(r"^[0-9a-fA-F]{8}_")


class IdentityError(ValueError):
    """Path is not a valid user-facing workspace identity."""


@dataclass(frozen=True)
class CanonicalPath:
    """Workspace-relative, ``/``-normalized identity. Events only recognize this."""

    relative: str  # no leading ./

    @property
    def public(self) -> str:
        return f"./{self.relative}" if self.relative else ""

    @property
    def display_name(self) -> str:
        return display_name_for(self.relative)


def display_name_for(relative: str) -> str:
    """Strip ``uploads/{8hex}_`` for display only; identity is unchanged."""
    rel = _normalize_slashes(relative).removeprefix("./")
    match = _UPLOAD_REL_PREFIX_RE.match(rel)
    if match:
        return Path(match.group("rest")).name
    return Path(rel).name


def is_hidden_name(name: str) -> bool:
    """CatalogFilter: hide ``.`` and ``~$`` names."""
    return name.startswith(".") or name.startswith("~$")


def is_reserved_relative(rel: str) -> bool:
    """True if the workspace-relative path is a reserved namespace."""
    rel = _normalize_slashes(rel).removeprefix("./").strip("/")
    if not rel:
        return False
    first = rel.split("/", 1)[0]
    if first in RESERVED_FIRST_SEGMENTS:
        return True
    for prefix in RESERVED_PREFIXES:
        if rel == prefix or rel.startswith(prefix + "/"):
            return True
    return False


def is_overlay_leftover(rel: str) -> bool:
    """True if the path is (or sits under) an overlay/backup leftover."""
    rel = _normalize_slashes(rel).removeprefix("./").strip("/")
    lowered = rel.lower()
    # Strip a drive / abs prefix down to the reserved suffix when present.
    for marker in ("outputs/backups/", "outputs/backups"):
        idx = lowered.find(marker)
        if idx >= 0:
            return True
    return is_reserved_relative(rel)


def looks_like_timestamped_backup_name(name: str) -> bool:
    return bool(_TS_BACKUP_NAME_RE.match(Path(name).name))


def workspace_root_of(obj: Any) -> Path | None:
    """Best-effort workspace root from an engine / session / config object."""
    if obj is None:
        return None
    if isinstance(obj, (str, Path)):
        text = str(obj).strip()
        return Path(text) if text else None
    for attr in ("workspace", "_workspace"):
        ws = getattr(obj, attr, None)
        if ws is not None:
            root = getattr(ws, "root_dir", None) or getattr(ws, "workspace_root", None)
            if root:
                return Path(root)
    cfg = getattr(obj, "config", None) or getattr(obj, "_config", None)
    if cfg is not None:
        wr = getattr(cfg, "workspace_root", None)
        if wr:
            return Path(wr)
    registry = getattr(obj, "_file_registry", None)
    if registry is not None:
        root = getattr(registry, "workspace_root", None)
        if root:
            return Path(root)
    return None


def resolve_canonical(workspace_root: str | Path | None, raw: str) -> CanonicalPath:
    """Resolve ``raw`` to a CanonicalPath.

    Rejects ``..``, reserved namespaces, hidden ``.`` / ``~$`` names, and
    paths that realpath outside the workspace. The file need not exist.
    """
    rel = _resolve_relative(workspace_root, raw)
    return CanonicalPath(relative=rel)


def public_identity(path: str, workspace_root: str | Path | None) -> str:
    """SSE-suitable public identity (``./rel``), or ``""`` if it must be omitted.

    Overlay leftovers map to the logical original when that file exists
    (uploads/ or root). If mapping fails, skip — never emit the backup path.
    """
    raw = str(path or "").strip()
    if not raw:
        return ""
    if raw.startswith("<path>/"):
        raw = raw.removeprefix("<path>/").strip()
        if not raw:
            return ""

    root = Path(workspace_root).expanduser().resolve() if workspace_root else None
    mapped = _map_overlay_leftover(raw, root)
    if mapped is _SKIP:
        return ""
    if mapped:
        raw = mapped

    try:
        return resolve_canonical(root, raw).public
    except IdentityError:
        if root is None and not _looks_absolute(raw):
            rel = _normalize_slashes(raw).removeprefix("./").strip("/")
            if (
                rel
                and not is_reserved_relative(rel)
                and not is_overlay_leftover(rel)
                and ".." not in Path(rel).parts
                and not _has_hidden_component(rel)
            ):
                return f"./{rel}"
        return ""


def collect_public_identities(
    paths: Iterable[str],
    workspace_root: str | Path | None,
) -> list[str]:
    """Dedupe and collapse paths to public identities; drop unmappable leftovers."""
    out: list[str] = []
    seen: set[str] = set()
    for path in paths:
        if not isinstance(path, str) or not path.strip():
            continue
        ident = public_identity(path, workspace_root)
        if ident and ident not in seen:
            seen.add(ident)
            out.append(ident)
    return out


def catalog(workspace_root: str | Path) -> list[CanonicalPath]:
    """Walk the live workspace; skip reserved namespaces, hidden names, leftovers."""
    root = Path(workspace_root).expanduser().resolve()
    found: list[CanonicalPath] = []
    if not root.is_dir():
        return found
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        rel_dir = Path(dirpath).resolve().relative_to(root).as_posix()
        if rel_dir == ".":
            rel_dir = ""
        dirnames[:] = [
            name
            for name in dirnames
            if not is_hidden_name(name)
            and not is_reserved_relative(f"{rel_dir}/{name}".lstrip("/"))
        ]
        for name in filenames:
            rel = f"{rel_dir}/{name}".lstrip("/") if rel_dir else name
            try:
                found.append(resolve_canonical(root, rel))
            except IdentityError:
                continue
    found.sort(key=lambda item: item.relative)
    return found


class StaleVersionError(IdentityError):
    """Current bytes do not match the VersionRef fingerprint."""

    def __init__(self, path: str, expected: str | None, actual: str | None) -> None:
        super().__init__(f"stale version for {path}: expected {expected}, actual {actual}")
        self.path = path
        self.expected = expected
        self.actual = actual
        self.status = "stale"


@dataclass(frozen=True)
class WorkbookVersionRef:
    """Read target: live path or a revision blob. Diff input is two of these."""

    kind: Literal["current", "revision"]
    path: str
    content_version: str | None = None
    revision_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "kind": self.kind,
            "path": self.path,
        }
        if self.content_version:
            payload["contentVersion"] = self.content_version
        if self.revision_id:
            payload["revisionId"] = self.revision_id
        return payload


@dataclass(frozen=True)
class DiffQuery:
    left: WorkbookVersionRef
    right: WorkbookVersionRef


# ── internals ──────────────────────────────────────────────


_SKIP = object()


def _normalize_slashes(path: str) -> str:
    return str(path or "").strip().replace("\\", "/")


def _looks_absolute(path: str) -> bool:
    text = _normalize_slashes(path)
    if text.startswith("/"):
        return True
    return bool(re.match(r"^[A-Za-z]:/", text))


def _has_hidden_component(rel: str) -> bool:
    return any(is_hidden_name(part) for part in Path(rel).parts if part not in (".", ""))


def _assert_allowed_relative(rel: str) -> None:
    if not rel or rel == ".":
        raise IdentityError("empty identity")
    if ".." in Path(rel).parts:
        raise IdentityError(f"path escape: {rel}")
    if is_reserved_relative(rel):
        raise IdentityError(f"reserved namespace: {rel}")
    if _has_hidden_component(rel):
        raise IdentityError(f"hidden name: {rel}")


def _resolve_relative(workspace_root: str | Path | None, raw: str) -> str:
    text = _normalize_slashes(raw)
    if not text:
        raise IdentityError("empty path")
    if text.startswith("<path>/"):
        text = text.removeprefix("<path>/").strip()
        if not text:
            raise IdentityError("empty path")

    if workspace_root is None:
        if _looks_absolute(text):
            raise IdentityError("absolute path without workspace")
        rel = text.removeprefix("./").strip("/")
        _assert_allowed_relative(rel)
        return rel

    root = Path(workspace_root).expanduser().resolve()
    candidate = Path(text)
    if candidate.is_absolute() or _looks_absolute(text):
        resolved = Path(text).expanduser().resolve()
    else:
        rel_in = text.removeprefix("./")
        if ".." in Path(rel_in).parts:
            raise IdentityError(f"path escape: {text}")
        resolved = (root / rel_in).resolve()

    try:
        rel_path = resolved.relative_to(root)
    except ValueError as exc:
        raise IdentityError(f"path outside workspace: {raw}") from exc

    rel = rel_path.as_posix()
    _assert_allowed_relative(rel)
    return rel


def _backup_relative(raw: str, workspace_root: Path | None) -> str | None:
    """Return ``outputs/backups/...`` relative, or None if this is not an overlay path."""
    text = _normalize_slashes(raw).removeprefix("./")
    marker = "outputs/backups"
    idx = text.lower().find(marker)
    if idx >= 0:
        return text[idx:].lstrip("/")
    if workspace_root is not None:
        try:
            abs_path = Path(text).expanduser() if _looks_absolute(text) else (workspace_root / text)
            resolved = abs_path.resolve()
            rel = resolved.relative_to(workspace_root.resolve()).as_posix()
            if rel == marker or rel.startswith(marker + "/"):
                return rel
        except (ValueError, OSError):
            pass
    return None


def _find_original_for_basename(workspace: Path, basename: str) -> str | None:
    """Prefer ``{basename}`` at root, then uploads/ (plain or ``8hex_``)."""
    if not basename or is_hidden_name(basename):
        return None
    root_hit = workspace / basename
    if root_hit.is_file():
        return basename
    uploads = workspace / "uploads"
    plain = uploads / basename
    if plain.is_file():
        return f"uploads/{basename}"
    if uploads.is_dir():
        try:
            for entry in uploads.iterdir():
                if not entry.is_file():
                    continue
                name = entry.name
                if _UPLOAD_FILE_PREFIX_RE.match(name) and name[9:] == basename:
                    return f"uploads/{name}"
        except OSError:
            pass
    return None


def _find_original_for_stem(workspace: Path, stem: str, ext: str) -> str | None:
    return _find_original_for_basename(workspace, f"{stem}{ext}")


def _map_overlay_leftover(raw: str, workspace_root: Path | None) -> str | object | None:
    """Map overlay leftover → original relative, ``_SKIP`` to omit, or None if not overlay."""
    backup_rel = _backup_relative(raw, workspace_root)
    if backup_rel is None:
        return None
    name = Path(backup_rel).name
    if not name or is_hidden_name(name):
        return _SKIP
    if workspace_root is None:
        return _SKIP

    ts_match = _TS_BACKUP_NAME_RE.match(name)
    if ts_match:
        found = _find_original_for_stem(
            workspace_root, ts_match.group("stem"), ts_match.group("ext")
        )
        return found if found else _SKIP

    # sandbox CoW: outputs/backups/{basename} → {basename} if found
    found = _find_original_for_basename(workspace_root, name)
    return found if found else _SKIP
