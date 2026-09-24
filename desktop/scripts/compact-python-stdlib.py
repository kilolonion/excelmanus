"""Put pure standard-library modules in CPython's native zip search location.

Run with the staged interpreter, never a build/system Python. Native modules,
site-packages and sysconfig/build data stay on disk. ZIP_STORED avoids another
decompression layer: NSIS compresses the payload, Python reads source directly.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import sysconfig
import zipfile


def archive_plan(stdlib: Path) -> tuple[list[Path], list[str]]:
    """Zip only source-only packages; keep resource/native-bearing trees intact.

    A package imported from a zip resolves importlib.resources/pkgutil data from
    that zip too. Leaving its data outside while moving just .py breaks those
    APIs, even though ordinary imports and the spreadsheet smoke still work.
    """
    files: list[Path] = []
    retained: list[str] = []
    for entry in sorted(stdlib.iterdir()):
        if entry.is_symlink():
            continue
        if entry.is_file():
            if entry.suffix == ".py":
                files.append(entry)
            continue
        if entry.name in {"site-packages", "lib-dynload", "__pycache__"} or entry.name.startswith("config-"):
            continue
        members = sorted(entry.rglob("*"))
        if not (entry / "__init__.py").is_file() or any(
            path.is_symlink() or (path.is_file() and path.suffix != ".py")
            for path in members
        ):
            retained.append(entry.name)
            continue
        files.extend(path for path in members if path.is_file())
    return files, retained


def compact(root: Path) -> dict:
    root = root.resolve()
    staging = Path(__file__).resolve().parents[1] / ".build"
    if root == staging or not root.is_relative_to(staging):
        raise RuntimeError("Only interpreters inside desktop/.build may be compacted")
    if Path(sys.base_prefix).resolve() != root:
        raise RuntimeError("Run compact-python-stdlib.py with the staged interpreter")
    if root == Path(sys.executable).resolve().parent and sys.platform != "win32":
        raise RuntimeError("Unexpected Python layout")
    stdlib = Path(sysconfig.get_path("stdlib")).resolve()
    stdlib.relative_to(root)
    archive = (root if sys.platform == "win32" else root / "lib") / (
        f"python{sys.version_info.major}{sys.version_info.minor}.zip"
    )
    if str(archive) not in sys.path:
        raise RuntimeError(f"Interpreter does not search {archive}")
    if archive.exists():
        raise RuntimeError(f"Standard library already compacted: {archive}")
    files, retained = archive_plan(stdlib)
    if not files or not (stdlib / "os.py").is_file():
        raise RuntimeError("Missing standard-library sources")
    pending = archive.with_suffix(".zip.tmp")
    with zipfile.ZipFile(pending, "w", compression=zipfile.ZIP_STORED) as bundle:
        for path in files:
            info = zipfile.ZipInfo(path.relative_to(stdlib).as_posix(), (2020, 1, 1, 0, 0, 0))
            info.external_attr = 0o644 << 16
            bundle.writestr(info, path.read_bytes())
    with zipfile.ZipFile(pending) as bundle:
        if bundle.testzip() is not None:
            raise RuntimeError("Invalid standard-library archive")
    pending.replace(archive)
    for path in files:
        # getpath needs this landmark to find the relocated prefix, even when
        # imports subsequently resolve from python312.zip.
        if path != stdlib / "os.py":
            path.unlink()
    # Remove only directories whose sources we moved. Preserve intentionally
    # empty directories in native/data packages and in site-packages as well.
    parents = {parent for path in files for parent in path.parents if parent != stdlib and parent.is_relative_to(stdlib)}
    for folder in sorted(parents, key=lambda p: len(p.parts), reverse=True):
        try:
            folder.rmdir()
        except OSError:
            pass
    return {"archive": str(archive), "modules": len(files), "files_saved": len(files) - 2, "retained_packages": retained}


if __name__ == "__main__":
    print(json.dumps(compact(Path(sys.argv[1]))))
