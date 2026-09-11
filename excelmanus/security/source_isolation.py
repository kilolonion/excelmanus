"""工作区产品源码隔离与探测文件拒绝。"""

from __future__ import annotations

from pathlib import Path

PRODUCT_SOURCE_ROOTS: tuple[str, ...] = ("excelmanus", "tests", "docs")
PRODUCT_SOURCE_FORBIDDEN = "PRODUCT_SOURCE_FORBIDDEN"
PROBE_FILE_FORBIDDEN = "PROBE_FILE_FORBIDDEN"

_PROBE_NAME_PREFIXES: tuple[str, ...] = ("_probe",)
_PROBE_DIR_MARKERS: tuple[str, ...] = ("outputs/_",)


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
    return is_product_source_relative(workspace_relative(path, workspace_root))


def command_touches_product_source(command: str) -> bool:
    import re

    text = (command or "").replace("\\", "/")
    if re.search(r"(^|[^\w./])(excelmanus|tests|docs)/", text):
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
