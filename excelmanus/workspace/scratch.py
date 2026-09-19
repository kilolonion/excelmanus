"""回合末临时文件残留扫描。只提醒，不自动删除。"""

from __future__ import annotations

from pathlib import Path

from excelmanus.workspace.identity import is_hidden_name

_SCAN_DIRS: tuple[str, ...] = ("scripts", "temp", ".tmp", ".excelmanus/pending")
_ROOT_GLOBS: tuple[str, ...] = ("scratch_*.py",)
_SKIP_PARTS: frozenset[str] = frozenset({"code_mode", ".staging"})


def scan_scratch_leftovers(root: str | Path | None) -> list[str]:
    """返回工作区内 scripts/temp/.tmp/pending 与 scratch_*.py 残留的相对路径。"""
    if not root:
        return []
    base = Path(root)
    if not base.is_dir():
        return []
    found: list[str] = []
    for rel in _SCAN_DIRS:
        folder = base / rel
        if not folder.is_dir():
            continue
        try:
            for path in folder.rglob("*"):
                try:
                    if not path.is_file() or is_hidden_name(path.name):
                        continue
                    rel = path.relative_to(base)
                    if any(part in _SKIP_PARTS for part in rel.parts):
                        continue
                    found.append(rel.as_posix())
                except OSError:
                    continue
        except OSError:
            continue
    for pattern in _ROOT_GLOBS:
        try:
            for path in base.glob(pattern):
                if path.is_file():
                    found.append(path.relative_to(base).as_posix())
        except OSError:
            continue
    return sorted(set(found))


def leftover_reminder(root: str | Path | None, *, limit: int = 12) -> str:
    leftovers = scan_scratch_leftovers(root)
    if not leftovers:
        return ""
    shown = leftovers[:limit]
    extra = f" 等 {len(leftovers)} 个" if len(leftovers) > limit else ""
    return (
        "工作区残留临时文件（未自动删除；清理请用 delete_file 并 confirm=true）："
        + ", ".join(shown)
        + extra
    )
