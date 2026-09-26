import copy
import json
import asyncio
from types import SimpleNamespace

import pytest

from excelmanus.chat_history import ChatHistoryStore
from excelmanus.database import Database
from excelmanus.history_projection import project_task_lists
from excelmanus.session import SessionManager


def operation(call_id, name, args, *, error=False):
    return [
        {"role": "assistant", "thinking": "核对进度", "tool_calls": [
            {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}
        ]},
        {"role": "tool", "tool_call_id": call_id,
         "content": json.dumps({"status": "error", "message": "拒绝更新"}) if error else "操作成功"},
    ]


@pytest.fixture
def history():
    return [
        *operation("create", "task_create", {"title": "收据", "subtasks": ["读取", {"title": "核对", "verification": "金额一致"}]}),
        *operation("start", "task_update", {"task_index": 0, "status": "in_progress"}),
        *operation("done", "task_update", {"task_index": 0, "status": "completed"}),
        *operation("failed", "task_update", {"task_index": 1, "status": "completed"}, error=True),
        *operation("last", "task_update", {"task_index": 1, "status": "completed"}),
    ]


def statuses(message):
    return [item["status"] for item in message["tool_calls"][0]["task_list"]["items"]]


def test_snapshots_are_historical_not_the_current_final_state(history):
    original = copy.deepcopy(history)
    projected = project_task_lists(history, history)
    assert statuses(projected[0]) == ["pending", "pending"]
    assert statuses(projected[2]) == ["in_progress", "pending"]
    assert statuses(projected[4]) == ["completed", "pending"]
    assert "task_list" not in projected[6]["tool_calls"][0]
    assert statuses(projected[8]) == ["completed", "completed"]
    assert history == original  # UI metadata never enters model history.


def test_cold_paged_history_uses_durable_prefix(tmp_path, history):
    db = Database(str(tmp_path / "history.db"))
    store = ChatHistoryStore(db)
    store.create_session("receipt", "收据")
    store.create_session("other", "其他")
    store.save_turn_messages("receipt", history)
    store.save_turn_messages("receipt", operation("unrelated", "list_directory", {}))
    store.save_turn_messages("other", operation("create", "task_create", {"subtasks": ["其他会话"]}))
    loaded = store.load_task_history("receipt")
    assert len(loaded) == len(history)
    page = store.load_messages("receipt", offset=4, limit=2)
    projected = project_task_lists(page, loaded)
    assert statuses(projected[0]) == ["completed", "pending"]
    assert projected[0]["tool_calls"][0]["task_list"]["items"][1]["title"] == "核对"
    assert "task_list" not in store.load_messages("receipt")[0]["tool_calls"][0]


@pytest.mark.asyncio
@pytest.mark.parametrize("live", [True, False])
async def test_session_message_api_projects_both_live_and_restored_pages(tmp_path, history, live):
    store = ChatHistoryStore(Database(str(tmp_path / "api-history.db")))
    store.create_session("receipt", "收据")
    store.save_turn_messages("receipt", history)
    manager = object.__new__(SessionManager)
    manager._lock = asyncio.Lock()
    manager._chat_history = store
    manager._sessions = {"receipt": SimpleNamespace(engine=SimpleNamespace(raw_messages=history))} if live else {}
    page = await manager.get_session_messages("receipt", limit=2, offset=4)
    assert len(page) == 2
    assert statuses(page[0]) == ["completed", "pending"]
    assert page[0]["thinking"] == "核对进度"
    assert "task_list" not in history[4]["tool_calls"][0]


def test_plan_creation_and_replacement_reset_later_snapshots():
    history = [
        *operation("plan", "write_plan", {"content": "# 计划\n## 任务清单\n- [ ] 核对数据\n- [ ] 输出结果"}),
        *operation("update", "task_update", {"task_index": 0, "status": "completed"}),
        *operation("replace", "task_create", {"title": "新任务", "subtasks": ["检查"], "replace_existing": True}),
        *operation("new-update", "task_update", {"task_index": 0, "status": "in_progress"}),
        *operation("missing", "task_update", {"task_index": 0, "status": "completed"})[:1],
    ]
    projected = project_task_lists(history, history)
    assert statuses(projected[0]) == ["pending", "pending"]
    assert statuses(projected[2]) == ["completed", "pending"]
    assert statuses(projected[4]) == ["pending"]
    assert statuses(projected[6]) == ["in_progress"]
    assert "task_list" not in projected[8]["tool_calls"][0]
