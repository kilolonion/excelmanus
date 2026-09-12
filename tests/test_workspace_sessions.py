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
    old_api = (api_module._config, api_module._session_manager)
    set_config(config)
    set_session_manager(manager)
    set_database(db)
    api_module._config = config
    api_module._session_manager = manager
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
        api_module._config, api_module._session_manager = old_api
        db.close()


def test_isolated_workspace_missing_dir_without_create(tmp_path: Path) -> None:
    missing = tmp_path / "no-such-folder"
    with pytest.raises(FileNotFoundError):
        IsolatedWorkspace(root_dir=missing, create_missing=False)


def test_isolated_workspace_create_missing_makes_dir(tmp_path: Path) -> None:
    target = tmp_path / "created"
    IsolatedWorkspace(root_dir=target, create_missing=True)
    assert target.is_dir()


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

            default_id = state["manager"].ensure_default_workspace()["id"]
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
