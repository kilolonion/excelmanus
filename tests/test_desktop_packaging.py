"""Compatibility contracts for the desktop's pruned interpreter payload."""
import importlib.util
from pathlib import Path
import runpy
import sys

import pytest


def test_stdlib_archive_keeps_package_data_native_code_and_namespace_layout(tmp_path):
    tmp_path = tmp_path / "stdlib"
    tmp_path.mkdir()
    script = Path(__file__).resolve().parents[1] / "desktop/scripts/compact-python-stdlib.py"
    spec = importlib.util.spec_from_file_location("compact_stdlib", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    paths = [
        "os.py", "json/__init__.py", "json/decoder.py",
        "email/__init__.py", "email/architecture.rst",
        "ctypes/__init__.py", "ctypes/macholib/__init__.py", "ctypes/macholib/README.ctypes",
        "native/__init__.py", "native/accelerator.pyd",
        "namespace/module.py", "site-packages/library/__init__.py",
        "lib-dynload/support.py", "config-3.12/config.py",
    ]
    for name in paths:
        file = tmp_path / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(name)
    files, retained = module.archive_plan(tmp_path)
    assert {file.relative_to(tmp_path).as_posix() for file in files} == {"os.py", "json/__init__.py", "json/decoder.py"}
    assert set(retained) == {"ctypes", "email", "native", "namespace"}
    # Planning is non-mutating and never moves third-party packages.
    assert all((tmp_path / name).is_file() for name in paths)


def test_frozen_backend_never_falls_back_to_host_browser_or_node(tmp_path, monkeypatch):
    runner = Path(__file__).resolve().parents[1] / "desktop/backend_runner.py"
    resources = tmp_path / "resources"
    internal = resources / "backend/excelmanus-backend/_internal"
    executable = internal.parent / "excelmanus-backend"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(internal), raising=False)
    monkeypatch.setattr(sys, "executable", str(executable))
    host_cache = tmp_path / "host-browser"
    host_cache.mkdir()
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(host_cache))
    monkeypatch.setenv("PLAYWRIGHT_NODEJS_PATH", "host-node")
    with pytest.raises(RuntimeError, match="Bundled browser payload is missing"):
        runpy.run_path(str(runner))
    browser = internal / "playwright-browsers"
    browser.mkdir(parents=True)
    with pytest.raises(RuntimeError, match="Bundled Node runtime is missing"):
        runpy.run_path(str(runner))
    import os
    assert os.environ["PLAYWRIGHT_BROWSERS_PATH"] == str(browser)
