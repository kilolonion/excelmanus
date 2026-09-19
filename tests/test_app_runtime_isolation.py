import asyncio
from types import SimpleNamespace

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
