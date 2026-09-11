"""FILES_CHANGED / uploads probe emit user-facing identity only."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from excelmanus.engine_core.session_state import SessionState
from excelmanus.engine_core.tool_dispatcher import ToolDispatcher


def _engine(tmp_path: Path) -> MagicMock:
    engine = MagicMock()
    engine.config = SimpleNamespace(workspace_root=str(tmp_path))
    engine.workspace = SimpleNamespace(root_dir=tmp_path)
    state = SessionState()
    state._file_registry = SimpleNamespace(workspace_root=tmp_path)
    engine._state = state
    captured: list = []

    def _emit(_on_event, event):
        captured.append(event)

    engine.emit = _emit
    engine._captured = captured
    return engine


def test_record_run_code_maps_backup_to_original(tmp_path: Path) -> None:
    (tmp_path / "sales.xlsx").write_bytes(b"orig")
    backup = tmp_path / "outputs" / "backups"
    backup.mkdir(parents=True)
    (backup / "sales_20260911T091344_f525.xlsx").write_bytes(b"copy")

    engine = _engine(tmp_path)
    dispatcher = ToolDispatcher(engine)
    dispatcher._record_files_from_run_code(
        engine,
        extra_changed_paths=["outputs/backups/sales_20260911T091344_f525.xlsx"],
    )
    assert engine._state.affected_files == ["./sales.xlsx"]


def test_record_run_code_skips_backup_when_original_missing(tmp_path: Path) -> None:
    backup = tmp_path / "outputs" / "backups"
    backup.mkdir(parents=True)
    (backup / "gone_20260911T091344_aaaa.xlsx").write_bytes(b"c")

    engine = _engine(tmp_path)
    dispatcher = ToolDispatcher(engine)
    dispatcher._record_files_from_run_code(
        engine,
        extra_changed_paths=["outputs/backups/gone_20260911T091344_aaaa.xlsx"],
    )
    assert engine._state.affected_files == []


def test_uploads_snapshot_paths_include_uploads_prefix(tmp_path: Path) -> None:
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    (uploads / "deadbeef_foo.xlsx").write_bytes(b"x")
    snap = ToolDispatcher._snapshot_uploads_dir(str(tmp_path))
    assert snap is not None
    assert "uploads/deadbeef_foo.xlsx" in snap
    assert "deadbeef_foo.xlsx" not in snap

    after = dict(snap)
    after["uploads/deadbeef_foo.xlsx"] = snap["uploads/deadbeef_foo.xlsx"] + 1
    changed = ToolDispatcher._diff_uploads_snapshots(snap, after)
    assert changed == ["uploads/deadbeef_foo.xlsx"]


def test_record_affected_file_stores_public_identity(tmp_path: Path) -> None:
    state = SessionState()
    state._file_registry = SimpleNamespace(workspace_root=tmp_path)
    (tmp_path / "a.xlsx").write_bytes(b"a")
    backup = tmp_path / "outputs" / "backups"
    backup.mkdir(parents=True)
    (backup / "a_20260911T091344_f525.xlsx").write_bytes(b"c")
    state.record_affected_file(str(tmp_path / "a.xlsx"))
    state.record_affected_file("outputs/backups/a_20260911T091344_f525.xlsx")
    state.record_affected_file("./a.xlsx")
    assert state.affected_files == ["./a.xlsx"]
