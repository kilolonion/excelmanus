"""Local browser fixture: real HTTP/Driver/SSE, deterministic model, temp workspace.

Run with .venv/bin/python tests/dispatch_browser_server.py.
"""
from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace

import uvicorn
from fastapi import FastAPI

from excelmanus.agent.session import AgentEngine
from excelmanus.api_app_state import AppRuntime, RuntimeMiddleware
from excelmanus.api_routes_chat import router as chat_router
from excelmanus.api_routes_sessions import router as sessions_router
from excelmanus.config import ExcelManusConfig
from excelmanus.events import EventType, ToolCallEvent
from excelmanus.session import SessionManager, _SessionEntry
from excelmanus.tools.registry import ToolDef, ToolRegistry


def create_fixture():
    workspace = tempfile.TemporaryDirectory(prefix="excelmanus-dispatch-")
    os.environ["EXCELMANUS_HOME"] = workspace.name
    config = ExcelManusConfig(api_key="fixture", model="fixture-model", base_url="https://fixture.invalid/v1",
        workspace_root=workspace.name, memory_enabled=False, jev_enabled="off", main_model_vision="false",
        message_dispatch_default="queue")
    registry = ToolRegistry()
    registry.register_tool(ToolDef(name="fixture_tick", description="fixture boundary", func=lambda: "ok",
        input_schema={"type": "object", "properties": {}}, write_effect="none"))
    engine = AgentEngine(config, registry)
    engine._session_id = "dispatch-browser"
    gates: dict[str, asyncio.Event] = {}
    cancelled: list[str] = []

    async def model(**kwargs):
        driver = engine._driver
        latest = next((str(m["content"]) for m in reversed(engine.raw_messages) if m["role"] == "user" and not m.get("_ui_hidden")), "")
        if driver.step_index == 1:
            engine._emit(driver._on_event, ToolCallEvent(event_type=EventType.TEXT_DELTA, text_delta=f"正在处理 {latest}。"))
            try:
                await gates.setdefault(driver.turn_id, asyncio.Event()).wait()
            except asyncio.CancelledError:
                cancelled.append(driver.turn_id)
                raise
            tc = SimpleNamespace(id=f"tick-{driver.turn_id}", function=SimpleNamespace(name="fixture_tick", arguments="{}"))
            message = SimpleNamespace(content="", tool_calls=[tc])
        else:
            message = SimpleNamespace(content=f"已完成：{latest}", tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    engine._client.chat.completions.create = model
    manager = SessionManager(5, 3600, config=config, registry=registry)
    manager._sessions["dispatch-browser"] = _SessionEntry(engine, 0)
    app = FastAPI()
    app.state.workspace = workspace
    app.state.runtime = AppRuntime(config=config, session_manager=manager)
    app.add_middleware(RuntimeMiddleware)
    app.include_router(chat_router)
    app.include_router(sessions_router)

    @app.get("/api/v1/config/runtime")
    async def settings():
        return {"message_dispatch_default": config.message_dispatch_default}

    @app.post("/api/v1/fixture/release/{turn_id}")
    async def release(turn_id: str):
        gates.setdefault(turn_id, asyncio.Event()).set()
        return {"released": turn_id}

    @app.get("/api/v1/fixture/state")
    async def state():
        return {"turn": engine._driver.current_turn(), "cancelled": cancelled,
                "dispatches": engine._driver.dispatch_snapshot()}

    @app.get("/api/v1/{path:path}")
    async def ancillary(path: str):
        return {"models": [], "files": [], "skills": [], "commands": [], "status": "ok"}

    return app


if __name__ == "__main__":
    uvicorn.run(create_fixture(), host="127.0.0.1", port=8325, log_level="warning")
