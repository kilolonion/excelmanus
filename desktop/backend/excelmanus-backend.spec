# PyInstaller one-folder build for the local FastAPI sidecar.
from pathlib import Path
import sys

from PyInstaller.building.build_main import Analysis, COLLECT, EXE, PYZ
from PyInstaller.utils.hooks import collect_all, collect_submodules


DESKTOP_ROOT = Path(SPECPATH).resolve().parent
PROJECT_ROOT = DESKTOP_ROOT.parent
ENTRYPOINT = PROJECT_ROOT / "desktop" / "backend_runner.py"

datas = []
binaries = []
hiddenimports = []

# Frozen builds have no source checkout; ship pyproject.toml next to the
# package so excelmanus.__version__ keeps reading the single version source.
datas.append((str(PROJECT_ROOT / "pyproject.toml"), "."))

# ExcelManus loads prompts and skillpacks from package data at runtime.
for package in ("excelmanus", "fastapi", "uvicorn", "tiktoken"):
    package_datas, package_binaries, package_hiddenimports = collect_all(package)
    datas.extend(package_datas)
    binaries.extend(package_binaries)
    hiddenimports.extend(package_hiddenimports)

# tiktoken loads its encoding registry through the separate tiktoken_ext
# namespace at runtime.
hiddenimports.extend(collect_submodules("tiktoken_ext", on_error="raise"))
# Exclude optional mcp.cli: it exits if the CLI extras are absent.
hiddenimports.extend(collect_submodules("mcp.client", on_error="raise"))

analysis = Analysis(
    [str(ENTRYPOINT)],
    pathex=[str(PROJECT_ROOT), str(DESKTOP_ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "tests"],
    noarchive=False,
)

pyz = PYZ(analysis.pure)
executable = EXE(
    pyz,
    analysis.scripts,
    [],
    name="excelmanus-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # Pipe stdout/stderr on Windows too; Electron hides the console window.
    console=True,
    manifest=str(DESKTOP_ROOT / "backend" / "windows.manifest") if sys.platform == "win32" else None,
    exclude_binaries=True,
)

COLLECT(
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name="excelmanus-backend",
)
