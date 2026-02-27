from __future__ import annotations

import importlib
import pkgutil
from pathlib import Path

import excelmanus.tools as tools_pkg

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_STEM = "_".join(["cv", "analyzer"])
LEGACY_MODULE_PATH = REPO_ROOT / "excelmanus" / f"{MODULE_STEM}.py"
LEGACY_TEST_PATH = REPO_ROOT / "tests" / f"test_{MODULE_STEM}.py"


def _find_python_references() -> list[str]:
    current_test = Path(__file__).name
    ignored_names = {current_test, LEGACY_TEST_PATH.name}
    matches: list[str] = []

    for search_root in (REPO_ROOT / "excelmanus", REPO_ROOT / "tests"):
        for path in search_root.rglob("*.py"):
            if path.name in ignored_names:
                continue
            if MODULE_STEM in path.read_text(encoding="utf-8"):
                matches.append(path.relative_to(REPO_ROOT).as_posix())

    return sorted(matches)


def test_dead_code_module_and_test_are_removed() -> None:
    assert not LEGACY_MODULE_PATH.exists()
    assert not LEGACY_TEST_PATH.exists()
    assert _find_python_references() == []


def test_all_tools_modules_importable() -> None:
    tool_modules = sorted(
        f"{tools_pkg.__name__}.{entry.name}"
        for entry in pkgutil.iter_modules(tools_pkg.__path__)
        if not entry.name.startswith("_")
    )
    assert tool_modules

    for module_name in tool_modules:
        importlib.import_module(module_name)


def test_image_to_excel_pipeline_modules_importable() -> None:
    for module_name in (
        "excelmanus.vision_extractor",
        "excelmanus.replica_spec",
        "excelmanus.tools.image_tools",
    ):
        importlib.import_module(module_name)
