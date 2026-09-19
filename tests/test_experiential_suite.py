"""体验套件结构检查：能加载、无 golden、夹具文件名对齐。不跑模型。"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from excelmanus import bench

ROOT = Path(__file__).resolve().parents[1]
SUITE = ROOT / "bench" / "cases" / "suite_experiential.json"
CASES_DIR = ROOT / "bench" / "cases"
FIXTURE_PREFIX = "bench/fixtures/experiential/"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_builder = _load_module(
    "build_experiential",
    ROOT / "bench" / "fixtures" / "build_experiential.py",
)
_filter = _load_module(
    "filter_suite",
    ROOT / "bench" / "fixtures" / "filter_suite.py",
)


def _load_raw() -> dict:
    return json.loads(SUITE.read_text(encoding="utf-8"))


def test_experiential_suite_loads_without_assertions() -> None:
    name, cases, _trace = bench._load_suite(SUITE)
    assert name == "experiential_office"
    assert len(cases) == 24
    assert {case.id for case in cases} == {f"E{i:02d}" for i in range(1, 25)}
    for case in cases:
        assert case.assertions == {}
        assert not case.expected.get("golden_file")
        assert not case.expected.get("answer_position")
        assert case.expected.get("review_focus")


def test_experiential_excluded_from_all() -> None:
    raw = _load_raw()
    assert raw["include_in_all"] is False
    assert raw["scoring"] == "none"
    paths = bench.list_default_suite_paths(CASES_DIR)
    names = {path.name for path in paths}
    assert "suite_experiential.json" not in names
    assert "suite_smoke.json" in names
    assert "suite_write_approval.json" in names


def test_attachments_match_fixture_builder() -> None:
    allowed = _builder.expected_filenames()
    raw = _load_raw()
    used: set[str] = set()
    for case in raw["cases"]:
        for key in ("attachments", "images"):
            for item in case.get(key, []):
                assert item.startswith(FIXTURE_PREFIX), item
                used.add(Path(item).name)
        for turn in case.get("messages", []):
            if not isinstance(turn, dict):
                continue
            for key in ("attachments", "images"):
                for item in turn.get(key, []):
                    assert item.startswith(FIXTURE_PREFIX), item
                    used.add(Path(item).name)
    assert used <= allowed
    assert used


def test_filter_suite_by_wave_and_id() -> None:
    raw = _load_raw()
    wave1 = _filter.filter_suite(raw, wave="1")
    assert {case["id"] for case in wave1["cases"]} == {
        "E01", "E02", "E03", "E10", "E16", "E19",
    }
    one = _filter.filter_suite(raw, case_ids=["E07"])
    assert [case["id"] for case in one["cases"]] == ["E07"]
