"""Folder workspaces: session binding, registration, and cwd isolation."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from excelmanus.chat_history import ChatHistoryStore
from excelmanus.config import ExcelManusConfig
from excelmanus.database import Database
from excelmanus.security.guard import FileAccessGuard, SecurityViolationError
from excelmanus.session import SessionManager
from excelmanus.stores.workspace_store import WorkspacePathError, WorkspaceStore
from excelmanus.workspace.paths import unique_workspace_title
from excelmanus.tools import ToolRegistry
from excelmanus.workspace.isolated import IsolatedWorkspace

import excelmanus.api as api_module
from excelmanus.api import app
from excelmanus.api_app_state import (
    get_config,
    get_database,
    get_session_manager,
    set_config,
    set_database,
    set_session_manager,
)


def _config(root: Path, **overrides) -> ExcelManusConfig:
    defaults = dict(
        api_key="test-key",
        base_url="https://test.example.com/v1",
        model="test-model",
        session_ttl_seconds=60,
        max_sessions=10,
        workspace_root=str(root),
        data_root=str(root),
        memory_enabled=False,
    )
    defaults.update(overrides)
    return ExcelManusConfig(**defaults)


@contextmanager
def _workspace_api(tmp_path: Path):
    default_ws = tmp_path / "default-ws"
    default_ws.mkdir()
    config = _config(default_ws)
    db = Database(str(tmp_path / "data.db"))
    chat = ChatHistoryStore(db)
    manager = SessionManager(
        max_sessions=10,
        ttl_seconds=60,
        config=config,
        registry=ToolRegistry(),
        chat_history=chat,
        database=db,
    )
    manager.ensure_default_workspace()
    old = (get_config(), get_session_manager(), get_database())
    old_api = (api_module.app.state.runtime.config, api_module.app.state.runtime.session_manager)
    set_config(config)
    set_session_manager(manager)
    set_database(db)
    api_module.app.state.runtime.config = config
    api_module.app.state.runtime.session_manager = manager
    try:
        yield {
            "config": config,
            "manager": manager,
            "db": db,
            "default_ws": default_ws,
        }
    finally:
        set_config(old[0])
        set_session_manager(old[1])
        set_database(old[2])
        api_module.app.state.runtime.config, api_module.app.state.runtime.session_manager = old_api
        db.close()


def test_isolated_workspace_missing_dir_without_create(tmp_path: Path) -> None:
    missing = tmp_path / "no-such-folder"
    with pytest.raises(FileNotFoundError):
        IsolatedWorkspace(root_dir=missing, create_missing=False)


def test_isolated_workspace_create_missing_makes_dir(tmp_path: Path) -> None:
    target = tmp_path / "created"
    IsolatedWorkspace(root_dir=target, create_missing=True)
    assert target.is_dir()


def test_unique_workspace_title_appends_ascii_counter() -> None:
    assert unique_workspace_title("报表", set()) == "报表"
    assert unique_workspace_title("报表", {"报表"}) == "报表 (1)"
    assert unique_workspace_title("报表", {"报表", "报表 (1)"}) == "报表 (2)"
    assert unique_workspace_title("报表 (1)", {"报表 (1)"}) == "报表 (2)"
    assert unique_workspace_title("报表（1）", {"报表（1）"}) == "报表 (1)"
    assert unique_workspace_title("  ", {"工作区"}) == "工作区 (1)"


def test_workspace_store_adopts_existing_only(tmp_path: Path) -> None:
    db = Database(str(tmp_path / "data.db"))
    store = WorkspaceStore(db)
    missing = tmp_path / "ghost"
    with pytest.raises(WorkspacePathError, match="不存在"):
        store.create(str(missing))
    real = tmp_path / "real-folder"
    real.mkdir()
    rec, created = store.create(str(real), title="报表")
    assert created is True
    rec2, created2 = store.create(str(real))
    assert created2 is False
    assert rec2["id"] == rec["id"]
    db.close()


def test_workspace_store_dedupes_titles(tmp_path: Path) -> None:
    db = Database(str(tmp_path / "data.db"))
    store = WorkspaceStore(db)
    first = tmp_path / "alpha" / "same"
    second = tmp_path / "beta" / "same"
    third = tmp_path / "gamma" / "other"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    third.mkdir(parents=True)

    a, _ = store.create(str(first))
    b, _ = store.create(str(second))
    c, _ = store.create(str(third), title="same")
    assert a["title"] == "same"
    assert b["title"] == "same (1)"
    assert c["title"] == "same (2)"

    renamed = store.update(c["id"], title="same")
    assert renamed is not None
    assert renamed["title"] == "same (2)"
    kept = store.update(a["id"], title="same")
    assert kept is not None
    assert kept["title"] == "same"

    leftover = tmp_path / "delta" / "same"
    leftover.mkdir(parents=True)
    now = "2026-09-13T00:00:00+00:00"
    db.conn.execute(
        "INSERT INTO workspaces (id, path, title, created_at, updated_at, sort_index) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("dup-1", str(leftover.resolve()), "same", now, now, 9),
    )
    db.conn.commit()
    titles = [item["title"] for item in store.list()]
    assert titles.count("same") == 1
    assert "same (1)" in titles
    assert "same (2)" in titles
    assert "same (3)" in titles
    db.close()


@pytest.mark.asyncio
async def test_create_or_reuse_blank_session(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    db = Database(str(tmp_path / "data.db"))
    chat = ChatHistoryStore(db)
    manager = SessionManager(
        max_sessions=10,
        ttl_seconds=60,
        config=_config(ws),
        registry=ToolRegistry(),
        chat_history=chat,
        database=db,
    )
    manager.ensure_default_workspace()
    first = await manager.create_or_reuse_session()
    second = await manager.create_or_reuse_session()
    assert first["id"] == second["id"]
    assert first["blank"] is True
    assert first["workspace_path"] == str(ws.resolve())
    chat.save_turn_messages(
        first["id"],
        [{"role": "user", "content": "hi", "message_id": "u1"}],
        turn_number=1,
    )
    third = await manager.create_or_reuse_session()
    assert third["id"] != first["id"]
    explicit = await manager.create_or_reuse_session(reuse_blank=False)
    assert explicit["id"] != third["id"]
    assert explicit["blank"] is True
    db.close()


@pytest.mark.asyncio
async def test_create_or_reuse_binds_last_used_workspace(tmp_path: Path) -> None:
    default_ws = tmp_path / "ws-a"
    other = tmp_path / "ws-b"
    default_ws.mkdir()
    other.mkdir()
    db = Database(str(tmp_path / "data.db"))
    chat = ChatHistoryStore(db)
    manager = SessionManager(
        max_sessions=10,
        ttl_seconds=60,
        config=_config(default_ws),
        registry=ToolRegistry(),
        chat_history=chat,
        database=db,
    )
    manager.ensure_default_workspace()
    rec, _ = manager.register_workspace(str(other))
    default_sess = await manager.create_or_reuse_session()
    chat.save_turn_messages(
        default_sess["id"],
        [{"role": "user", "content": "a", "message_id": "u-a"}],
        turn_number=1,
    )
    other_sess = await manager.create_or_reuse_session(workspace_id=rec["id"])
    chat.save_turn_messages(
        other_sess["id"],
        [{"role": "user", "content": "b", "message_id": "u-b"}],
        turn_number=1,
    )
    with patch.object(manager, "list_workspaces", side_effect=AssertionError("must use recent workspace")) as list_all:
        landing = await manager.create_or_reuse_session()
    list_all.assert_not_called()
    assert landing["workspace_id"] == rec["id"]
    assert landing["blank"] is True
    assert landing["id"] != other_sess["id"]
    db.close()


@pytest.mark.asyncio
async def test_create_or_reuse_falls_back_to_first_workspace(tmp_path: Path) -> None:
    first = tmp_path / "alpha"
    second = tmp_path / "beta"
    first.mkdir()
    second.mkdir()
    db = Database(str(tmp_path / "data.db"))
    chat = ChatHistoryStore(db)
    manager = SessionManager(
        max_sessions=10,
        ttl_seconds=60,
        config=_config(first),
        registry=ToolRegistry(),
        chat_history=chat,
        database=db,
    )
    manager.ensure_default_workspace()
    manager.register_workspace(str(second))
    landing = await manager.create_or_reuse_session()
    assert Path(landing["workspace_path"]) == first.resolve()
    workspaces = manager.list_workspaces()
    assert workspaces[0]["path"] == str(first.resolve())
    db.close()


@pytest.mark.asyncio
async def test_engine_cwd_is_session_folder_not_process_data_root(tmp_path: Path) -> None:
    default_ws = tmp_path / "data-home"
    other = tmp_path / "xlsx-folder"
    default_ws.mkdir()
    other.mkdir()
    (other / "secret.xlsx").write_text("x", encoding="utf-8")
    db = Database(str(tmp_path / "data.db"))
    chat = ChatHistoryStore(db)
    manager = SessionManager(
        max_sessions=10,
        ttl_seconds=60,
        config=_config(default_ws),
        registry=ToolRegistry(),
        chat_history=chat,
        database=db,
    )
    manager.ensure_default_workspace()
    rec, _ = manager.register_workspace(str(other))
    sess_default = await manager.create_or_reuse_session()
    sess_other = await manager.create_or_reuse_session(workspace_id=rec["id"])
    ws_default = manager._engine_workspace(sess_default["id"])
    ws_other = manager._engine_workspace(sess_other["id"])
    assert ws_default.root_dir == default_ws.resolve()
    assert ws_other.root_dir == other.resolve()
    guard = FileAccessGuard(str(ws_default.root_dir))
    with pytest.raises(SecurityViolationError):
        guard.resolve_and_validate(str(other / "secret.xlsx"))
    with patch(
        "excelmanus.engine.AgentEngine.initialize_mcp",
        new=AsyncMock(return_value=None),
    ):
        sid, engine = await manager.acquire_for_chat(sess_other["id"])
        await manager.release_for_chat(sid)
    assert Path(engine.workspace.root_dir) == other.resolve()
    assert Path(engine.workspace.root_dir) != default_ws.resolve()
    db.close()


@pytest.mark.asyncio
async def test_sessions_and_workspaces_http(tmp_path: Path) -> None:
    extra = tmp_path / "extra-folder"
    extra.mkdir()
    (extra / "only-in-b.txt").write_text("b", encoding="utf-8")
    with _workspace_api(tmp_path) as state:
        default_ws: Path = state["default_ws"]
        (default_ws / "only-in-a.txt").write_text("a", encoding="utf-8")
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post("/api/v1/sessions", json={})
            assert created.status_code == 200
            sess_a = created.json()
            reused = await client.post("/api/v1/sessions", json={})
            assert reused.json()["id"] == sess_a["id"]
            explicit = await client.post("/api/v1/sessions", json={"reuse_blank": False})
            assert explicit.status_code == 200
            assert explicit.json()["id"] != sess_a["id"]
            listed = await client.get("/api/v1/sessions")
            assert listed.status_code == 200
            row = listed.json()["sessions"][0]
            assert row["workspace_path"]
            assert "blank" in row

            missing = await client.post(
                "/api/v1/workspaces", json={"path": str(tmp_path / "nope")},
            )
            assert missing.status_code == 400

            adopted = await client.post(
                "/api/v1/workspaces", json={"path": str(extra)},
            )
            assert adopted.status_code in (200, 201)
            workspace_b = adopted.json()["workspace"]
            sess_b = (
                await client.post(
                    "/api/v1/sessions",
                    json={"workspace_id": workspace_b["id"]},
                )
            ).json()
            assert sess_b["id"] != sess_a["id"]
            assert sess_b["workspace_path"] == str(extra.resolve())

            default_id = state["manager"].ensure_default_workspace()["id"]
            reordered = await client.put(
                "/api/v1/workspaces/order",
                json={"workspace_ids": [workspace_b["id"], default_id]},
            )
            assert reordered.status_code == 200
            assert [item["id"] for item in reordered.json()["workspaces"][:2]] == [
                workspace_b["id"],
                default_id,
            ]

            files_a = await client.get(
                f"/api/v1/files/workspace/list?session_id={sess_a['id']}"
            )
            files_b = await client.get(
                f"/api/v1/files/workspace/list?session_id={sess_b['id']}"
            )
            names_a = {f["filename"] for f in files_a.json()["files"]}
            names_b = {f["filename"] for f in files_b.json()["files"]}
            assert "only-in-a.txt" in names_a
            assert "only-in-b.txt" not in names_a
            assert "only-in-b.txt" in names_b
            assert "only-in-a.txt" not in names_b

            denied = await client.delete(f"/api/v1/workspaces/{default_id}")
            assert denied.status_code == 400

            renamed = await client.patch(
                f"/api/v1/workspaces/{workspace_b['id']}",
                json={"title": "extra-renamed"},
            )
            assert renamed.status_code == 200
            assert renamed.json()["workspace"]["title"] == "extra-renamed"

            listed_ws = await client.get("/api/v1/workspaces")
            flags = {w["id"]: w.get("is_default") for w in listed_ws.json()["workspaces"]}
            assert flags.get(default_id) is True
            assert flags.get(workspace_b["id"]) is False

            deleted = await client.delete(
                f"/api/v1/workspaces/{workspace_b['id']}"
            )
            assert deleted.status_code == 200
            assert extra.is_dir()
            still = await client.get("/api/v1/sessions")
            ids = {s["id"] for s in still.json()["sessions"]}
            assert sess_b["id"] in ids
            assert sess_a["id"] in ids


@pytest.mark.asyncio
async def test_file_routes_honor_explicit_workspace_scope(tmp_path: Path) -> None:
    extra = tmp_path / "external-workspace"
    extra.mkdir()
    (extra / "note.txt").write_text("external", encoding="utf-8")
    (extra / "shot.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    with _workspace_api(tmp_path):
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            adopted = await client.post("/api/v1/workspaces", json={"path": str(extra)})
            workspace_id = adopted.json()["workspace"]["id"]
            scope = {"workspace_id": workspace_id}

            text = await client.get(
                "/api/v1/files/read",
                params={"path": "note.txt", **scope},
            )
            assert text.status_code == 200
            assert text.json()["content"] == "external"

            image = await client.get(
                "/api/v1/files/image",
                params={"path": "shot.png", **scope},
            )
            assert image.status_code == 200
            assert image.headers["content-type"] == "image/png"

            download = await client.get(
                "/api/v1/files/download",
                params={"path": "note.txt", **scope},
            )
            assert download.status_code == 200
            assert download.content == b"external"
            assert download.headers["cache-control"] == "private, no-store"

            uploaded = await client.post(
                "/api/v1/upload",
                data={"workspace_id": workspace_id},
                files={"file": ("added.txt", b"added", "text/plain")},
            )
            assert uploaded.status_code == 200
            uploaded_path = uploaded.json()["path"].removeprefix("./")
            assert (extra / uploaded_path).read_bytes() == b"added"

            with patch(
                "excelmanus.security.url_fetch.fetch_public_http",
                new=AsyncMock(return_value=b"remote"),
            ):
                remote = await client.post(
                    "/api/v1/upload-from-url",
                    json={
                        "url": "https://example.com/remote.txt",
                        "workspace_id": workspace_id,
                    },
                )
            assert remote.status_code == 200
            remote_path = remote.json()["path"].removeprefix("./")
            assert (extra / remote_path).read_bytes() == b"remote"

            with patch("subprocess.Popen") as popen:
                revealed = await client.post(
                    "/api/v1/files/reveal",
                    json={"path": "note.txt", "workspace_id": workspace_id},
                )
            assert revealed.status_code == 200
            assert popen.called


def test_register_workspace_runs_overlay_migration(tmp_path: Path) -> None:
    from excelmanus.workspace.revisions import RevisionStore

    with _workspace_api(tmp_path) as env:
        ws = tmp_path / "adopted"
        ws.mkdir()
        (ws / "sales.xlsx").write_bytes(b"live")
        backups = ws / "outputs" / "backups"
        backups.mkdir(parents=True)
        (backups / "sales_20260911T091344_f525.xlsx").write_bytes(b"overlay-copy")

        rec, created = env["manager"].register_workspace(str(ws), title="历史工作区")
        assert created is True
        assert rec["path"] == str(ws.resolve())

        recs = RevisionStore(ws).list("sales.xlsx")
        assert recs
        assert any(r.label == "migrated-overlay" for r in recs)
        assert (ws / ".excelmanus" / "migrations" / "overlay-backups.json").is_file()


@pytest.mark.asyncio
async def test_file_list_uses_historical_session_after_workspace_unregistered(tmp_path: Path) -> None:
    folder = tmp_path / "historical"
    folder.mkdir()
    (folder / "report.csv").write_text("name,value\na,1\n", encoding="utf-8")

    with _workspace_api(tmp_path) as state:
        manager = state["manager"]
        registered, _ = manager.register_workspace(str(folder))
        session = await manager.create_or_reuse_session(workspace_id=registered["id"])
        assert manager.delete_workspace_registration(registered["id"])

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/v1/files/workspace/list", params={
                "session_id": session["id"], "workspace_id": registered["id"],
            })
            assert response.status_code == 200
            assert "report.csv" in {item["path"] for item in response.json()["files"]}

            mismatch = await client.get("/api/v1/files/workspace/list", params={
                "session_id": session["id"], "workspace_id": "not-the-session-workspace",
            })
            assert mismatch.status_code == 400
            assert mismatch.json()["detail"]["code"] == "FILE_SCOPE_REQUIRED"

            wrong_registered = await client.get("/api/v1/files/workspace/list", params={
                "session_id": session["id"],
                "workspace_id": manager.ensure_default_workspace()["id"],
            })
            assert wrong_registered.status_code == 409
            assert wrong_registered.json()["detail"]["code"] == "FILE_SCOPE_MISMATCH"
