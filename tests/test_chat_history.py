"""ChatHistoryStore 单元测试。"""

import pytest

from excelmanus.chat_history import ChatHistoryStore


@pytest.fixture
def store(tmp_path):
    db_path = str(tmp_path / "test_history.db")
    s = ChatHistoryStore(db_path)
    yield s
    s.close()


def test_create_and_list_session(store):
    store.create_session("s1", "测试会话")
    sessions = store.list_sessions()
    assert len(sessions) == 1
    assert sessions[0]["id"] == "s1"
    assert sessions[0]["title"] == "测试会话"


def test_save_and_load_messages(store):
    store.create_session("s1", "测试")
    messages = [
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "你好！有什么可以帮你的？"},
    ]
    store.save_turn_messages("s1", messages, turn_number=1)
    loaded = store.load_messages("s1")
    assert len(loaded) == 2
    assert loaded[0]["role"] == "user"
    assert loaded[0]["content"] == "你好"
    assert loaded[1]["role"] == "assistant"


def test_save_complex_message(store):
    """tool_calls 等复杂结构应能正确序列化/反序列化。"""
    store.create_session("s1", "测试")
    msg = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "tc_1",
                "type": "function",
                "function": {"name": "read_excel", "arguments": "{}"},
            }
        ],
    }
    store.save_turn_messages("s1", [msg], turn_number=1)
    loaded = store.load_messages("s1")
    assert len(loaded) == 1
    assert loaded[0]["tool_calls"][0]["id"] == "tc_1"


def test_load_messages_pagination(store):
    store.create_session("s1", "测试")
    msgs = [{"role": "user", "content": f"msg{i}"} for i in range(10)]
    store.save_turn_messages("s1", msgs, turn_number=1)
    page = store.load_messages("s1", limit=3, offset=0)
    assert len(page) == 3
    assert page[0]["content"] == "msg0"
    page2 = store.load_messages("s1", limit=3, offset=3)
    assert page2[0]["content"] == "msg3"


def test_session_exists(store):
    assert not store.session_exists("nope")
    store.create_session("s1", "测试")
    assert store.session_exists("s1")


def test_delete_session_cascades(store):
    store.create_session("s1", "测试")
    store.save_turn_messages(
        "s1", [{"role": "user", "content": "hi"}], turn_number=1
    )
    store.delete_session("s1")
    assert not store.session_exists("s1")
    assert store.load_messages("s1") == []


def test_update_session(store):
    store.create_session("s1", "旧标题")
    store.update_session("s1", title="新标题", status="archived")
    sessions = store.list_sessions(include_archived=True)
    assert sessions[0]["title"] == "新标题"
    assert sessions[0]["status"] == "archived"


def test_list_sessions_excludes_archived_by_default(store):
    store.create_session("s1", "活跃")
    store.create_session("s2", "归档")
    store.update_session("s2", status="archived")
    active = store.list_sessions(include_archived=False)
    assert len(active) == 1
    all_sessions = store.list_sessions(include_archived=True)
    assert len(all_sessions) == 2


def test_message_count_updated(store):
    store.create_session("s1", "测试")
    store.save_turn_messages(
        "s1",
        [
            {"role": "user", "content": "a"},
            {"role": "assistant", "content": "b"},
        ],
        turn_number=1,
    )
    sessions = store.list_sessions()
    assert sessions[0]["message_count"] == 2


def test_multiple_turns_accumulate(store):
    store.create_session("s1", "测试")
    store.save_turn_messages(
        "s1", [{"role": "user", "content": "turn1"}], turn_number=1
    )
    store.save_turn_messages(
        "s1", [{"role": "assistant", "content": "reply1"}], turn_number=1
    )
    loaded = store.load_messages("s1")
    assert len(loaded) == 2
    sessions = store.list_sessions()
    assert sessions[0]["message_count"] == 2


def test_list_sessions_ordered_by_updated_at(store):
    store.create_session("s1", "先创建")
    store.create_session("s2", "后创建")
    # s2 更新时间更晚，应排在前面
    sessions = store.list_sessions()
    assert sessions[0]["id"] == "s2"
    # 更新 s1 使其更新时间变为最新
    store.save_turn_messages(
        "s1", [{"role": "user", "content": "new"}], turn_number=1
    )
    sessions = store.list_sessions()
    assert sessions[0]["id"] == "s1"


def test_empty_messages_noop(store):
    store.create_session("s1", "测试")
    store.save_turn_messages("s1", [], turn_number=1)
    assert store.load_messages("s1") == []


def test_get_message_count(store):
    store.create_session("s1", "测试")
    assert store.get_message_count("s1") == 0
    store.save_turn_messages(
        "s1", [{"role": "user", "content": "hi"}], turn_number=1
    )
    assert store.get_message_count("s1") == 1
