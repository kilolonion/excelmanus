# PyInstaller one-folder build for the local FastAPI sidecar.
from pathlib import Path
import sys

from PyInstaller.building.build_main import Analysis, COLLECT, EXE, PYZ
from PyInstaller.utils.hooks import collect_all, collect_submodules, copy_metadata


DESKTOP_ROOT = Path(SPECPATH).resolve().parent
PROJECT_ROOT = DESKTOP_ROOT.parent
ENTRYPOINT = PROJECT_ROOT / "desktop" / "backend_runner.py"

# build-backend.mjs stages the independent browser after COLLECT, preserving
# Chromium's native library layout instead of applying PyInstaller fixups.
datas = []
# Workbook observations include the adapter version in their cache identity.
# Importing openpyxl alone does not make PyInstaller retain its dist-info.
datas.extend(copy_metadata("openpyxl"))
binaries = []
hiddenimports = []

# Frozen builds have no source checkout; ship pyproject.toml next to the
# package so excelmanus.__version__ keeps reading the single version source.
datas.append((str(PROJECT_ROOT / "pyproject.toml"), "."))

# ExcelManus loads prompts and skillpacks from package data at runtime.
for package in ("excelmanus", "fastapi", "uvicorn", "tiktoken", "playwright"):
    # Preserve ExcelManus sources (including executable skill scripts). Other
    # packages are already in PYZ; shipping their .py files again is redundant.
    package_datas, package_binaries, package_hiddenimports = collect_all(
        package, include_py_files=package == "excelmanus",
    )
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
    # pandas/Pillow probe optional scientific and GUI integrations. The API
    # process never executes user analysis: run_code launches the separately
    # bundled interpreter, where these libraries and their data remain intact.
    # Keep numpy, pandas, Pillow and all spreadsheet/document engines here.
    excludes=[
        "pytest", "tests", "hypothesis",
        "scipy", "sklearn", "seaborn", "plotly", "matplotlib",
        "tkinter", "IPython", "notebook", "jupyterlab",
    ],
    noarchive=False,
)

# The desktop launcher sets PLAYWRIGHT_NODEJS_PATH to resources/runtime/node.
# Keep the driver JS and licenses, but omit the second Node executable from
# both TOCs (PyInstaller can reclassify a data file as a native binary).
driver_nodes = {"playwright/driver/node", "playwright/driver/node.exe"}
analysis.binaries = [entry for entry in analysis.binaries if entry[0].replace("\\", "/") not in driver_nodes]
analysis.datas = [entry for entry in analysis.datas if entry[0].replace("\\", "/") not in driver_nodes]

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
