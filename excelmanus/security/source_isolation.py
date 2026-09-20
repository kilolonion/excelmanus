"""工作区产品源码隔离与探测文件拒绝。"""

from __future__ import annotations

from pathlib import Path

PRODUCT_SOURCE_ROOTS: tuple[str, ...] = ("excelmanus", "tests", "docs", "web", "desktop", "src")
_PACKAGE_ONLY_ROOTS = ("bench", "deploy")
_PACKAGE_SOURCE_FILES = (
    "main.js", "package.json", "package-lock.json", "pyproject.toml", "uv.lock",
    "README.md", "AGENTS.md", "Dockerfile", "Makefile",
)
PRODUCT_SOURCE_FORBIDDEN = "PRODUCT_SOURCE_FORBIDDEN"
PROBE_FILE_FORBIDDEN = "PROBE_FILE_FORBIDDEN"

_PROBE_NAME_PREFIXES: tuple[str, ...] = ("_probe",)
_PROBE_DIR_MARKERS: tuple[str, ...] = ("outputs/_",)
_EXPLICIT_SOURCE_WORKSPACES: set[str] = set()


def set_workspace_source_access(root: str | Path, allowed: bool) -> None:
    """Load the persisted, user-created workspace permission into the runtime."""
    key = str(Path(root).expanduser().resolve())
    if allowed:
        _EXPLICIT_SOURCE_WORKSPACES.add(key)
    else:
        _EXPLICIT_SOURCE_WORKSPACES.discard(key)


def workspace_allows_source(root: str | Path) -> bool:
    return str(Path(root).expanduser().resolve()) in _EXPLICIT_SOURCE_WORKSPACES


def protected_source_paths(workspace_root: str | Path) -> list[str]:
    """Use the same source boundary in file discovery and subprocess guards."""
    root = Path(workspace_root).expanduser().resolve()
    if workspace_allows_source(root):
        return []
    paths = [str(root / name) for name in PRODUCT_SOURCE_ROOTS]
    from excelmanus.data_home import get_package_root
    if root == get_package_root().resolve():
        paths.extend(str(root / name) for name in (*_PACKAGE_ONLY_ROOTS, *_PACKAGE_SOURCE_FILES))
    return paths


def workspace_relative(path: str | Path, workspace_root: str | Path) -> str:
    raw = Path(path)
    root = Path(workspace_root).resolve()
    try:
        resolved = raw if raw.is_absolute() else (root / raw)
        return resolved.resolve(strict=False).relative_to(root).as_posix()
    except ValueError:
        return Path(path).as_posix().replace("\\", "/").lstrip("./")


def is_product_source_relative(rel: str) -> bool:
    posix = (rel or "").replace("\\", "/").lstrip("./")
    if not posix:
        return False
    first = posix.split("/", 1)[0]
    return first in PRODUCT_SOURCE_ROOTS


def is_product_source_path(path: str | Path, workspace_root: str | Path) -> bool:
    if workspace_allows_source(workspace_root):
        return False
    from excelmanus.data_home import get_package_root
    root = Path(workspace_root).expanduser().resolve()
    rel = workspace_relative(path, root)
    if root == get_package_root().resolve() and (rel in _PACKAGE_SOURCE_FILES or rel.split("/", 1)[0] in _PACKAGE_ONLY_ROOTS):
        return True
    return is_product_source_relative(rel)


def command_touches_product_source(command: str, workspace_root: str | Path | None = None) -> bool:
    import re

    if workspace_root is not None and workspace_allows_source(workspace_root):
        return False

    text = (command or "").replace("\\", "/")
    if re.search(r"(^|[^\w./])(" + "|".join(PRODUCT_SOURCE_ROOTS) + r")/", text):
        return True
    if "replica_spec.py" in text or "intent_tools.py" in text:
        return True
    if re.search(r"\b(?:import|from)\s+excelmanus\b", text):
        return True
    return False


def is_probe_path(path: str | Path) -> bool:
    posix = str(path or "").replace("\\", "/").lstrip("./")
    name = Path(posix).name
    if any(name.startswith(prefix) for prefix in _PROBE_NAME_PREFIXES):
        return True
    if "_probe_" in name:
        return True
    if posix.startswith("outputs/_") or "/outputs/_" in f"/{posix}":
        return True
    return False


def probe_error_message(path: str) -> str:
    return f"禁止创建或写入探测文件: {path}"
