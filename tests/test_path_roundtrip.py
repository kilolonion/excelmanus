"""Paths survive upload, scan, mutation, history, SSE and sandbox boundaries."""

from __future__ import annotations

import json
import os
import sys
import time
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from httpx import ASGITransport, AsyncClient
from openpyxl import Workbook, load_workbook

from excelmanus.database import Database
from excelmanus.events import EventType, ToolCallEvent
from excelmanus.file_registry import FileRegistry, get_shared_file_registry
from excelmanus.security.guard import FileAccessGuard, SecurityViolationError
from excelmanus.workspace.file_service import WorkspaceFileService
from excelmanus.workspace.identity import IdentityError, public_identity, resolve_canonical
from excelmanus.workspace.refs import FileRef, WorkspaceRef
from tests.test_workspace_sessions import _workspace_api, app


def test_canonical_ref_and_guard_share_separator_rules(tmp_path):
    file = tmp_path / "目录" / "报表.txt"
    file.parent.mkdir()
    file.write_text("same", encoding="utf-8")
    for raw in (r".\目录\报表.txt", "././目录//./报表.txt", str(file)):
        assert resolve_canonical(tmp_path, raw).relative == "目录/报表.txt"
        assert FileAccessGuard(str(tmp_path)).resolve_and_validate(raw) == file
    ref = FileRef(WorkspaceRef.from_root(tmp_path), r".\目录\报表.txt")
    assert ref.relative == "目录/报表.txt"
    assert public_identity("././目录//报表.txt", None) == "./目录/报表.txt"
    assert public_identity("outputs/backups-2024/report.txt", tmp_path)


@pytest.mark.parametrize("path", ["C:book.xlsx", "../book.xlsx", "folder/../book.xlsx"])
def test_ambiguous_paths_are_never_rebased(tmp_path, path):
    with pytest.raises(IdentityError):
        resolve_canonical(tmp_path, path)
    with pytest.raises((ValueError, IdentityError)):
        FileRef(WorkspaceRef.from_root(tmp_path), path)
    with pytest.raises(SecurityViolationError):
        FileAccessGuard(str(tmp_path)).resolve_and_validate(path)


@pytest.mark.asyncio
async def test_upload_scan_write_and_history_keep_one_identity(tmp_path):
    with _workspace_api(tmp_path) as state:
        manager = state["manager"]
        session = await manager.create_or_reuse_session()
        scope = {"session_id": session["id"], "workspace_id": session["workspace_id"]}
        root = state["default_ws"]
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/v1/upload", data={**scope, "folder": "季度"},
                                         files={"file": ("销售.txt", b"before")})
            assert response.status_code == 200, response.text
            path = response.json()["path"]
            assert "\\" not in path and path.startswith("./uploads/季度/")
            registry = get_shared_file_registry(state["db"], root)
            uploaded = registry.get_by_path(path)
            assert uploaded is not None
            registry.scan_workspace(extract_sheet_meta=False)
            assert registry.get_by_path(path.replace("/", "\\")).id == uploaded.id
            assert len([f for f in registry.list_all() if f.canonical_path.endswith("销售.txt")]) == 1
            listed = (await client.get("/api/v1/files/workspace/list", params=scope)).json()
            leaf = next(f for f in listed["files"] if not f["is_dir"])
            assert leaf["filename"] == "销售.txt"
            assert leaf["path"] == path.removeprefix("./")
            svc = WorkspaceFileService(root)
            before = svc.locate(path)
            receipt = svc.update(path.replace("/", "\\"), b"after", expected_version=before["version"])
            svc.raise_if_failed(receipt)
            current = await client.get("/api/v1/files/download", params={**scope, "path": path})
            assert current.content == b"after"
            revisions = (await client.get("/api/v1/revisions", params={**scope, "path": path})).json()
            previous = next(r for r in revisions["revisions"] if r["reason"] == "beforeEdit")
            old = await client.get("/api/v1/revisions/content", params={**scope, "path": path, "revision_id": previous["revision_id"]})
            assert old.content == b"before"
            assert len(list((root / "uploads" / "季度").glob("*.txt"))) == 1


@pytest.mark.asyncio
async def test_conflicting_scopes_and_unscoped_upload_do_not_touch_disk(tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    (other / "same.txt").write_bytes(b"other")
    with _workspace_api(tmp_path) as state:
        manager = state["manager"]
        session = await manager.create_or_reuse_session()
        workspace, _ = manager.register_workspace(str(other))
        (state["default_ws"] / "same.txt").write_bytes(b"default")
        scope = {"session_id": session["id"], "workspace_id": workspace["id"]}
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            read = await client.get("/api/v1/files/read", params={**scope, "path": "same.txt"})
            assert read.status_code == 409
            assert read.json()["code"] == "FILE_SCOPE_MISMATCH"
            upload = await client.post("/api/v1/upload", data=scope, files={"file": ("new.txt", b"new")})
            assert upload.status_code == 409
            upload = await client.post("/api/v1/upload", files={"file": ("new.txt", b"new")})
            assert upload.status_code == 400
        assert not list(other.rglob("*new.txt"))
        assert not list(state["default_ws"].rglob("*new.txt"))


@pytest.mark.asyncio
async def test_nondefault_history_and_sse_roundtrip(tmp_path):
    from excelmanus.api_routes_chat import _persist_excel_event, _sse_event_to_sse

    other = tmp_path / "other"
    other.mkdir()
    file = other / "nested" / "sales.xlsx"
    file.parent.mkdir()
    file.write_bytes(b"workbook")
    with _workspace_api(tmp_path) as state:
        manager = state["manager"]
        workspace, _ = manager.register_workspace(str(other))
        session = await manager.create_or_reuse_session(workspace_id=workspace["id"])
        event = ToolCallEvent(event_type=EventType.EXCEL_PREVIEW, tool_call_id="preview", excel_file_path=str(file))
        _persist_excel_event(session["id"], event)
        sse = _sse_event_to_sse(event, session["id"])
        assert '"file_path": "./nested/sales.xlsx"' in sse
        assert "<path>" not in sse
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            recovered = (await client.get(f"/api/v1/sessions/{session['id']}/excel-events")).json()
            assert recovered["affected_files"] == ["./nested/sales.xlsx"]
            downloaded = await client.get("/api/v1/files/download", params={"session_id": session["id"], "path": recovered["previews"][0]["file_path"]})
            assert downloaded.content == b"workbook"


@pytest.mark.asyncio
async def test_directory_routes_use_canonical_identity(tmp_path):
    with _workspace_api(tmp_path) as state:
        session = await state["manager"].create_or_reuse_session()
        root = state["default_ws"]
        scope = {"session_id": session["id"]}
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            created = await client.post("/api/v1/files/workspace/mkdir", json={**scope, "path": str(root / "目录")})
            assert created.json()["path"] == "./目录"
            (root / "目录" / "file.txt").write_text("retained")
            renamed = await client.post("/api/v1/files/workspace/rename", json={**scope, "old_path": str(root / "目录"), "new_path": r".\新目录"})
            assert renamed.json()["new_path"] == "./新目录"
            assert (root / "新目录" / "file.txt").read_text() == "retained"
            invalid = await client.post("/api/v1/files/workspace/mkdir", json={**scope, "path": "../escape"})
            assert invalid.status_code == 400


def test_registry_legacy_aliases_preserve_events_groups_and_ids(tmp_path):
    db = Database(str(tmp_path / "registry.db"))
    root = tmp_path / "files"
    root.mkdir()
    reg = FileRegistry(db, root)
    canonical = reg.register_upload("uploads/book.txt", "Original name.txt")
    legacy = {**canonical.to_dict(), "id": "legacy", "canonical_path": r"uploads\book.txt"}
    reg._store.upsert_file(legacy)
    reg.record_event("legacy", "uploaded", details={"source": "legacy"})
    group = reg.create_group("batch", ["legacy"])
    restored = FileRegistry(db, root)
    assert len(restored.list_all()) == 1
    assert restored.get_by_id("legacy").id == canonical.id
    assert restored.get_by_path(r".\uploads\book.txt").id == canonical.id
    assert restored.get_by_path("uploads/book.txt").original_name == "Original name.txt"
    assert restored.get_group_files(group.id)[0]["file_id"] == canonical.id
    events = restored.get_events(canonical.id)
    assert any(e.details.get("source") == "legacy" for e in events)
    assert any(e.event_type == "path_normalized" and e.details["previous"]["id"] == "legacy" for e in events)


def test_registry_aliases_are_workspace_scoped_and_unambiguous(tmp_path):
    db = Database(str(tmp_path / "registry.db"))
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    ra, rb = FileRegistry(db, a), FileRegistry(db, b)
    first = ra.register_upload("uploads/1.txt", "same.txt")
    second = rb.register_upload("uploads/2.txt", "same.txt")
    assert rb.resolve_for_tool("same.txt") == second.canonical_path
    assert rb.get_by_id(first.id) is None
    ra.register_upload("uploads/3.txt", "same.txt")
    assert ra.get_by_alias("same.txt") is None
    assert ra.resolve_for_tool("same.txt") == "same.txt"


def test_bounded_registry_scan_does_not_retire_existing_paths(tmp_path):
    db = Database(str(tmp_path / "registry.db"))
    root = tmp_path / "files"
    root.mkdir()
    for name in ("a.csv", "b.csv", "note.txt"):
        (root / name).write_text("x")
    reg = FileRegistry(db, root)
    reg.scan_workspace(extract_sheet_meta=False)
    reg.scan_workspace(max_files=1, excel_only=True, extract_sheet_meta=False)
    assert len(reg.list_all()) == 3

def test_legacy_preview_never_adopts_or_creates_live_sidecar(tmp_path, monkeypatch):
    import xlrd
    from excelmanus.workbook.snapshot import open_snapshot_at
    from excelmanus.workbook_commit import content_version_of

    def source_book(**kwargs):
        value = kwargs["file_contents"].decode()
        sheet = SimpleNamespace(name="Data", nrows=1, ncols=1, merged_cells=[],
                                cell_value=lambda r, c: value, cell_type=lambda r, c: xlrd.XL_CELL_TEXT,
                                cell_xf_index=lambda r, c: 0)
        return SimpleNamespace(xf_list=[], font_list=[], format_map={}, nsheets=1,
                               sheet_by_index=lambda i: sheet, release_resources=lambda: None)

    monkeypatch.setattr(xlrd, "open_workbook", source_book)
    src, sidecar = tmp_path / "report.xls", tmp_path / "report.xlsx"
    src.write_bytes(b"original")
    sidecar.write_bytes(b"unrelated file")
    ref = WorkspaceRef.from_root(tmp_path)
    snap = open_snapshot_at(src, relative="report.xls", workspace=ref)
    assert snap.content_version == content_version_of(b"original")
    book = load_workbook(BytesIO(snap.read_bytes()))
    assert book.active["A1"].value == "original"
    book.close()
    assert sidecar.read_bytes() == b"unrelated file"
    sidecar.unlink()
    src.write_bytes(b"changed")
    new = open_snapshot_at(src, relative="report.xls", workspace=ref)
    assert new.content_version == content_version_of(b"changed")
    assert not sidecar.exists()
    assert new.file.relative == "report.xls"


def test_pending_cleanup_leaves_recent_runs_and_validates_ids(tmp_path):
    from excelmanus.workspace.runtime import allocate_pending_run_id, prepare_pending_run_dir, discard_orphan_pending_dirs

    active = prepare_pending_run_dir(tmp_path, allocate_pending_run_id())
    old = prepare_pending_run_dir(tmp_path, allocate_pending_run_id())
    stale = time.time() - 90000
    os.utime(old, (stale, stale))
    assert discard_orphan_pending_dirs(tmp_path) == 1
    assert active.is_dir()
    assert not old.exists()
    with pytest.raises(ValueError):
        prepare_pending_run_dir(tmp_path, "../outside")


def test_sandbox_pending_reads_and_publishes_same_path(tmp_path):
    from excelmanus.tools.code_tools import run_code, init_guard

    init_guard(str(tmp_path))
    result = run_code(code="from pathlib import Path\np = Path(r'folder\\item.csv')\np.parent.mkdir(exist_ok=True)\np.write_text('a,b\\n1,2')\nprint(p.read_text())",
                      python_command=sys.executable, require_excel_deps=False, sandbox_tier="GREEN")
    assert result.success, result.model_text
    assert result.value["published"][0]["path"] == "folder/item.csv"
    assert (tmp_path / "folder" / "item.csv").read_text() == "a,b\n1,2"
    assert len(list(tmp_path.rglob("item.csv"))) == 1


def test_shared_registry_uses_database_identity(tmp_path):
    a = Database(str(tmp_path / "a.db"))
    b = Database(str(tmp_path / "b.db"))
    assert get_shared_file_registry(a, tmp_path) is not get_shared_file_registry(b, tmp_path)
