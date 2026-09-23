"""History browsing must not initialize agents or silently lose older sessions."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from excelmanus.chat_history import ChatHistoryStore
from excelmanus.database import Database
from excelmanus.stores.session_state_store import SessionStateStore


@pytest.fixture
def history_db(tmp_path):
    db = Database(str(tmp_path / "history.db"))
    yield db
    db.close()


@pytest.mark.asyncio
async def test_cold_task_reads_preserve_saved_results_without_restoring_engine(monkeypatch, history_db):
    import excelmanus.api_routes_sessions as routes
    from excelmanus.task_list import TaskStore

    history = ChatHistoryStore(history_db)
    history.create_session("cold")
    history.save_turn_messages("cold", [{"role": "user", "content": "old conversation"}])
    tasks = TaskStore()
    tasks.create("Review", ["Read", "Write"])
    tasks.plan_file_path = "plan.md"
    snapshots = SessionStateStore(history_db)
    snapshots.save_session_snapshot(
        session_id="cold",
        state_dict={"runtime_state": {"subagents": [
            {"run_id": "done", "status": "completed", "background": True,
             "result": {"output": "Saved result"}, "history": [{"private": "large transcript"}]},
            {"run_id": "stopped", "status": "running", "background": True, "stop_requested": True},
        ]}},
        task_list_dict=tasks.to_dict(),
    )
    restore = AsyncMock(side_effect=AssertionError("reading history must not start an agent"))
    manager = SimpleNamespace(get_engine=lambda sid: None, get_or_restore_engine=restore,
                              database=history_db, chat_history=history)
    monkeypatch.setattr(routes, "get_session_manager", lambda: manager)
    monkeypatch.setattr(routes, "_has_session_access", AsyncMock(return_value=True))
    app = FastAPI()
    app.include_router(routes.router)
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
        response = await client.get("/api/v1/sessions/cold/subagents")
        assert response.status_code == 200
        runs = response.json()["runs"]
        assert runs[0]["result"]["output"] == "Saved result"
        assert "history" not in runs[0]
        assert runs[1]["status"] == "interrupted"
        assert "stop_requested" not in runs[1]
        response = await client.get("/api/v1/sessions/cold/task-list")
        assert response.status_code == 200
        assert response.json()["task_list"]["plan_file_path"] == "plan.md"
        assert [row["title"] for row in response.json()["task_list"]["items"]] == ["Read", "Write"]
        monkeypatch.setattr(routes, "_has_session_access", AsyncMock(return_value=False))
        assert (await client.get("/api/v1/sessions/cold/subagents")).status_code == 404
    restore.assert_not_called()


def test_task_projection_tolerates_corrupt_snapshot(history_db):
    history = ChatHistoryStore(history_db)
    history.create_session("broken")
    snapshots = SessionStateStore(history_db)
    snapshots.save_session_snapshot(session_id="broken", state_dict={}, task_list_dict={})
    history_db.conn.execute("UPDATE session_state_snapshots SET state_json = ?, task_list_json = ?",
                            ("invalid json", "invalid json"))
    assert snapshots.load_task_snapshot("broken") == {"runs": [], "task_store": {}}
    assert snapshots.load_task_snapshot("absent") == {"runs": [], "task_store": {}}


@pytest.mark.asyncio
async def test_session_inventory_includes_history_older_than_first_100(history_db):
    import asyncio
    from excelmanus.session import SessionManager

    history = ChatHistoryStore(history_db)
    for index in range(115):
        history.create_session(f"session-{index}")
    # Use the production inventory logic without any model/config initialization.
    manager = object.__new__(SessionManager)
    manager._chat_history = history
    manager._sessions = {}
    manager._lock = asyncio.Lock()
    manager._session_public_dict = lambda row, **kwargs: {**row, **kwargs}
    rows = await manager.list_sessions()
    assert len(rows) == 115
    assert "session-0" in {row["id"] for row in rows}
    assert len(history.list_sessions(limit=10, offset=10)) == 10


@pytest.mark.asyncio
async def test_filtered_excel_events_restore_session_files(monkeypatch, history_db):
    import excelmanus.api_routes_sessions as routes

    history = ChatHistoryStore(history_db)
    history.create_session("events")
    history.save_affected_file("events", "book.xlsx")
    history.save_excel_preview("events", "visible", "book.xlsx", "Sheet1", ["A"], [[1]], 1, False)
    history.save_excel_preview("events", "hidden", "other.xlsx", "Sheet1", ["A"], [[2]], 1, False)
    manager = SimpleNamespace(chat_history=history, workspace_path_for_session=lambda sid: None)
    monkeypatch.setattr(routes, "get_session_manager", lambda: manager)
    app = FastAPI()
    app.include_router(routes.router)
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
        response = await client.get("/api/v1/sessions/events/excel-events?limit=100&tool_call_id=visible")
    assert response.status_code == 200
    assert response.json()["affected_files"] == ["./book.xlsx"]
    assert [row["tool_call_id"] for row in response.json()["previews"]] == ["visible"]
    assert history.load_affected_files("events", tool_call_ids=["visible"], limit=1) == ["book.xlsx"]
