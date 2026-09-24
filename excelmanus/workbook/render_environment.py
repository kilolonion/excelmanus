"""Renderer prerequisites and reproducible environment/cache identities."""

from __future__ import annotations
import hashlib
import importlib.metadata
import json
import os
import platform
from pathlib import Path
import shutil
from excelmanus.workbook_commit import CommitError


def renderer_assets() -> tuple[Path, str]:
    assets = Path(__file__).with_name("render_assets")
    try:
        manifest = json.loads((assets / "manifest.json").read_text())
        for name, digest in manifest["outputs"].items():
            if hashlib.sha256((assets / name).read_bytes()).hexdigest() != digest:
                raise ValueError(f"Asset digest mismatch: {name}")
        checkout = assets.parents[2]
        for name, digest in manifest["sources"].items():
            source = checkout / name
            if (
                source.is_file()
                and hashlib.sha256(source.read_bytes()).hexdigest() != digest
            ):
                raise ValueError(f"Preview assets are stale: {name}")
    except (OSError, ValueError, KeyError) as exc:
        raise CommitError(
            "RENDERER_UNAVAILABLE",
            f"Build preview assets with npm --prefix web run build:preview ({exc})",
        ) from exc
    return assets, hashlib.sha256(
        json.dumps(manifest, sort_keys=True).encode()
    ).hexdigest()


def renderer_capabilities() -> dict:
    from excelmanus.runtime_capabilities import office_executable

    try:
        renderer_assets()
        import importlib.util

        if importlib.util.find_spec("playwright") is None:
            raise ImportError("Playwright package is unavailable")
        configured = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
        cache = (
            Path(configured)
            if configured and configured != "0"
            else (
                Path.home() / "Library/Caches/ms-playwright"
                if platform.system() == "Darwin"
                else Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".cache")))
            )
        )
        if not configured and platform.system() != "Darwin":
            cache = cache / "ms-playwright"
        installed = cache.is_dir() and (
            any(cache.glob("chromium-*")) or any(cache.glob("chromium_headless_shell-*"))
        )
        workbench = {
            "status": "unknown" if installed or configured == "0" else "unavailable",
            "reason": "dependencies_detected; launch not probed"
            if installed or configured == "0"
            else "Install Chromium using python -m playwright install chromium",
        }
    except (ImportError, CommitError) as exc:
        workbench = {"status": "unavailable", "reason": str(exc)}
    return {
        "workbench": workbench,
        "print": {
            "status": "unknown"
            if office_executable() and shutil.which("pdftoppm")
            else "unavailable",
            "reason": "LibreOffice and pdftoppm required; successful rendering is verified at invocation",
        },
    }


def environment_fingerprint() -> str:
    """Hash font file metadata, including installed user fonts, without reading glyph files."""
    roots = [
        Path("/System/Library/Fonts"),
        Path("/Library/Fonts"),
        Path.home() / "Library/Fonts",
        Path("/usr/share/fonts"),
        Path.home() / ".local/share/fonts",
        Path.home() / ".fonts",
    ]
    if os.name == "nt":
        roots.extend(
            [
                Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts",
                Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/Windows/Fonts",
            ]
        )
    facts = []
    for root in roots:
        if root.is_dir():
            for path in sorted(root.rglob("*")):
                if path.is_file():
                    stat = path.stat()
                    facts.append((str(path), stat.st_size, stat.st_mtime_ns))
    return hashlib.sha256(
        json.dumps(
            [platform.platform(), importlib.metadata.version("playwright"), facts],
            sort_keys=True,
        ).encode()
    ).hexdigest()
