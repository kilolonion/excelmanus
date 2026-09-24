import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from fastapi import FastAPI

from excelmanus.api_app_state import AppRuntime, RuntimeMiddleware, get_runtime
from excelmanus.workspace.file_service import WorkspaceFileService


@pytest.mark.asyncio
async def test_two_asgi_apps_and_child_tasks_use_own_runtime():
    apps = []
    for name in ("a", "b"):
        app = FastAPI()
        app.state.runtime = AppRuntime(config=SimpleNamespace(model=name))
        app.add_middleware(RuntimeMiddleware)
        @app.get("/identity")
        async def identity():
            async def child():
                await asyncio.sleep(0)
                return get_runtime().config.model
            return {"name": await asyncio.create_task(child())}
        apps.append(app)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=apps[0]),base_url="http://test") as a, httpx.AsyncClient(transport=httpx.ASGITransport(app=apps[1]),base_url="http://test") as b:
        ra, rb = await asyncio.gather(a.get("/identity"), b.get("/identity"))
    assert ra.json() == {"name":"a"}
    assert rb.json() == {"name":"b"}


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path,drains", [
    ("GET", "/api/v1/health", False),
    ("GET", "/api/v1/files/workspace/list", False),
    ("POST", "/api/v1/sessions", False),
    ("POST", "/api/v1/sessions/", False),
    ("POST", "/api/v1/upload", True),
    ("POST", "/api/v1/workbooks/changes", True),
    ("POST", "/api/v1/revisions/restore", True),
    ("POST", "/api/v1/chat/stream", True),
    ("POST", "/api/v1/chat", True),
])
async def test_polling_and_new_chat_do_not_scan_workspace_history(method, path, drains):
    app = FastAPI()
    manager = SimpleNamespace(drain_workspace_events=Mock())
    app.state.runtime = AppRuntime(session_manager=manager)
    app.add_middleware(RuntimeMiddleware)

    @app.api_route(path, methods=[method])
    async def endpoint():
        return {"ok": True}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.request(method, path)
    assert response.json() == {"ok": True}
    assert manager.drain_workspace_events.call_count == int(drains)


def test_empty_registered_workspaces_do_not_construct_file_services(tmp_path, monkeypatch):
    from excelmanus.session import SessionManager

    manager = object.__new__(SessionManager)
    manager._sessions = {}
    manager._migrated_workspace_paths = {str(tmp_path / str(i)) for i in range(128)}
    service = Mock(side_effect=AssertionError("empty workspace must not scan history"))
    monkeypatch.setattr("excelmanus.workspace.file_service.WorkspaceFileService", service)
    manager.drain_workspace_events()
    service.assert_not_called()


@pytest.mark.asyncio
async def test_slow_workspace_listing_does_not_block_other_requests():
    import threading
    from excelmanus.api_routes_workspaces import router

    started = threading.Event()
    release = threading.Event()

    def slow_listing():
        started.set()
        assert release.wait(3), "workspace read was not released"
        return []

    app = FastAPI()
    app.state.runtime = AppRuntime(session_manager=SimpleNamespace(list_workspaces=slow_listing))
    app.add_middleware(RuntimeMiddleware)
    app.include_router(router)

    @app.get("/ping")
    async def ping():
        return {"ok": True}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        listing = asyncio.create_task(client.get("/api/v1/workspaces"))
        try:
            assert await asyncio.to_thread(started.wait, 1)
            assert not listing.done()
            assert (await asyncio.wait_for(client.get("/ping"), 1)).json() == {"ok": True}
        finally:
            release.set()
            response = await listing
        assert response.json() == {"workspaces": []}


def test_outbox_retries_failed_consumer_and_acknowledges_once(tmp_path):
    svc = WorkspaceFileService(tmp_path)
    svc.create("a.txt", b"x")
    def failing(event):
        raise RuntimeError("consumer failed")
    with pytest.raises(RuntimeError):
        svc.deliver_outbox(failing, consumer_id="test")
    received = []
    assert svc.deliver_outbox(received.append, consumer_id="test") == 1
    assert svc.deliver_outbox(received.append, consumer_id="test") == 0
    assert received[0]["path"] == "a.txt"
    assert received[0]["event_id"]
