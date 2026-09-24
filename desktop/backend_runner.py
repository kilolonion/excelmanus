"""Development entrypoint for the bundled ExcelManus API process."""

from __future__ import annotations

import os
import sys
from pathlib import Path


if getattr(sys, "frozen", False):
    browser_root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "playwright-browsers"
    if not browser_root.is_dir():
        raise RuntimeError(f"Bundled browser payload is missing: {browser_root}")
    # A damaged package must not silently pass smoke by using a developer's
    # machine-wide Playwright cache or inherited browser override.
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(browser_root)
    # Reuse the relocatable Node already shipped for Next instead of installing
    # another ~100 MB executable just for Playwright's driver.
    node = Path(sys.executable).parents[2] / "runtime" / ("node.exe" if sys.platform == "win32" else "node")
    if not node.is_file():
        raise RuntimeError(f"Bundled Node runtime is missing: {node}")
    os.environ["PLAYWRIGHT_NODEJS_PATH"] = str(node)

if sys.platform == "win32":
    from windows_runtime import initialize_windows_runtime
    initialize_windows_runtime()

# Keep all relative workspace writes out of the application bundle.
if os.environ.get("EXCELMANUS_DESKTOP") == "1":
    data = Path(os.environ["EXCELMANUS_HOME"]).expanduser().resolve() / "data"
    data.mkdir(parents=True, exist_ok=True)
    os.chdir(data)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Product settings intentionally ignore EXCELMANUS_* environment variables.
# Install the read-only bundled skill directory in the process overlay when a
# frozen layout is present; the source-tree candidate is harmless in dev mode.
from excelmanus.settings_runtime import override_settings

runtime_roots = (
    Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)),
    Path(__file__).resolve().parent,
    PROJECT_ROOT,
)
for runtime_root in runtime_roots:
    for skill_root in (
        runtime_root / "excelmanus" / "skillpacks" / "system",
        runtime_root / "_internal" / "excelmanus" / "skillpacks" / "system",
    ):
        if skill_root.is_dir():
            override_settings({"EXCELMANUS_SKILLS_SYSTEM_DIR": str(skill_root)})
            break

if "--check-runtime" in sys.argv:
    # Fixed, offline smoke path; no user-supplied code and no model calls.
    from runtime_smoke import check_runtime
    check_runtime()
    raise SystemExit(0)

if "--workbook-preview-worker" in sys.argv:
    from excelmanus.workbook.preview_worker import main as preview_main
    preview_main(sys.argv[sys.argv.index("--workbook-preview-worker") + 1:])
    raise SystemExit(0)

from excelmanus.api import main  # noqa: E402


if __name__ == "__main__":
    main()
