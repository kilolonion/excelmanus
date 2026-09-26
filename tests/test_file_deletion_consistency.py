"""MUTATION deleted 标记一致性：事件构造 → 会话状态 → SSE 序列化。

被测链路（实现已完成，本文件只做验证）：
1. ``excelmanus.events``：``MutationEvent.deleted`` 仅在 True 时输出、
   ``changed_mutations`` / ``mutations_from_identities`` 的 ``deleted`` 形参、
   ``_normalize_identity`` 统一身份比较（``./`` 前缀、反斜杠）。
2. ``excelmanus.engine_core.session_state``：``record_affected_file`` 三态语义
   （True 标记 / False 清除 / None 不动）与快照往返。
3. ``excelmanus.api_sse``：MUTATION 序列化在 mutation 项上透出 ``deleted``。
4. ``excelmanus.session.SessionManager._consume_file_event``：unlink 目标与
   rename 后已不存在的源路径标记为 deleted。
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from excelmanus.engine_core.session_state import SessionState
from excelmanus.events import (
    EventType,
    MutationEvent,
    ToolCallEvent,
    changed_mutations,
    mutations_from_identities,
)


def _mutations_by_identity(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {item["identity"]: item for item in items}


# ── 1. events.py：deleted 形参与身份归一化 ─────────────────────────────


def test_mutation_event_to_dict_omits_deleted_key_when_false() -> None:
    payload = MutationEvent(identity="./a.xlsx").to_dict()
    assert payload["identity"] == "./a.xlsx"
    assert "deleted" not in payload

    explicit_false = MutationEvent(identity="./a.xlsx", deleted=False).to_dict()
    assert "deleted" not in explicit_false


def test_mutation_event_to_dict_emits_deleted_true() -> None:
    payload = MutationEvent(identity="./gone.xlsx", deleted=True).to_dict()
    assert payload["deleted"] is True
    # 只能是 True，绝不输出 False，避免客户端把删除标记当普通写入。
    assert payload["deleted"] is not False


def test_changed_mutations_marks_only_listed_identities() -> None:
    items = changed_mutations(
        ["./a.xlsx", "./b.xlsx"],
        deleted=["./a.xlsx"],
    )
    by_identity = _mutations_by_identity(items)
    assert by_identity["./a.xlsx"]["deleted"] is True
    assert "deleted" not in by_identity["./b.xlsx"]


def test_changed_mutations_without_deleted_arg_has_no_deleted_key() -> None:
    items = changed_mutations(["./a.xlsx", "./b.xlsx"])
    assert all("deleted" not in item for item in items)

    empty = changed_mutations(["./a.xlsx"], deleted=[])
    assert all("deleted" not in item for item in empty)


def test_changed_mutations_deleted_entry_not_in_changed_has_no_effect() -> None:
    items = changed_mutations(["./a.xlsx"], deleted=["./other.xlsx"])
    assert "deleted" not in items[0]


@pytest.mark.parametrize(
    "changed_identity,deleted_identity",
    [
        ("./a.xlsx", "./a.xlsx"),
        ("./a.xlsx", "a.xlsx"),
        ("a.xlsx", "./a.xlsx"),
        ("a.xlsx", "a.xlsx"),
        # 反斜杠（Windows 风格）与 "./" 前缀一起归一化
        (".\\a.xlsx", "./a.xlsx"),
        ("./a.xlsx", ".\\a.xlsx"),
    ],
)
def test_changed_mutations_normalizes_identity_prefix(
    changed_identity: str, deleted_identity: str
) -> None:
    items = changed_mutations([changed_identity], deleted=[deleted_identity])
    assert len(items) == 1
    assert items[0]["deleted"] is True


def test_mutations_from_identities_normalizes_and_skips_empty() -> None:
    items = mutations_from_identities(
        ["./a.xlsx", "", "./b.xlsx"],
        deleted=["a.xlsx"],
    )
    assert [item["identity"] for item in items] == ["./a.xlsx", "./b.xlsx"]
    by_identity = _mutations_by_identity(items)
    assert by_identity["./a.xlsx"]["deleted"] is True
    assert "deleted" not in by_identity["./b.xlsx"]


# ── 2. session_state.record_affected_file 三态语义 ────────────────────


def _state_with_root(root: Any) -> SessionState:
    state = SessionState()
    state._file_registry = SimpleNamespace(workspace_root=root)
    return state


def test_record_affected_file_none_does_not_touch_deletion_mark(tmp_path) -> None:
    state = _state_with_root(tmp_path)
    state.record_affected_file("a.xlsx")
    assert state.affected_files == ["./a.xlsx"]
    assert state.affected_file_deletions == set()

    state.record_affected_file("a.xlsx", deleted=True)
    assert state.affected_file_deletions == {"./a.xlsx"}

    # deleted=None：保持既有标记，不清除
    state.record_affected_file("a.xlsx", deleted=None)
    assert state.affected_file_deletions == {"./a.xlsx"}
    # 也不重复登记 affected_files
    assert state.affected_files == ["./a.xlsx"]


def test_record_affected_file_false_clears_deletion_mark(tmp_path) -> None:
    state = _state_with_root(tmp_path)
    state.record_affected_file("a.xlsx", deleted=True)
    assert state.affected_file_deletions == {"./a.xlsx"}

    # 同轮先删后建：后续写入确认路径存活，旧删除标记必须清除。
    state.record_affected_file(str(tmp_path / "a.xlsx"), deleted=False)
    assert state.affected_file_deletions == set()
    assert state.affected_files == ["./a.xlsx"]


def test_record_affected_file_true_marks_canonical_identity(tmp_path) -> None:
    state = _state_with_root(tmp_path)
    # 绝对路径、相对路径、"./" 前缀、反斜杠都归一到同一公开身份
    state.record_affected_file(str(tmp_path / "sub" / "a.xlsx"), deleted=True)
    state.record_affected_file("./sub/a.xlsx", deleted=None)
    state.record_affected_file("sub\\a.xlsx", deleted=None)
    assert state.affected_file_deletions == {"./sub/a.xlsx"}
    assert state.affected_files == ["./sub/a.xlsx"]


def test_record_affected_file_false_on_unmarked_identity_is_noop(tmp_path) -> None:
    state = _state_with_root(tmp_path)
    state.record_affected_file("a.xlsx", deleted=False)
    assert state.affected_file_deletions == set()
    assert state.affected_files == ["./a.xlsx"]


def test_record_affected_file_ignores_unmappable_path(tmp_path) -> None:
    state = _state_with_root(tmp_path)
    # 备份残留不是公开身份，删除标记同样不能泄漏到客户端
    state.record_affected_file(
        "outputs/backups/a_20260911T091344_abcd.xlsx", deleted=True
    )
    assert state.affected_files == []
    assert state.affected_file_deletions == set()


# ── 2b. 快照往返 / 重置 ──────────────────────────────────────────────


def test_affected_file_deletions_to_dict_from_dict_roundtrip(tmp_path) -> None:
    state = _state_with_root(tmp_path)
    state.record_affected_file("b.xlsx", deleted=True)
    state.record_affected_file("a.xlsx", deleted=True)
    state.record_affected_file("kept.xlsx")

    snapshot = state.to_dict()
    assert snapshot["affected_file_deletions"] == ["./a.xlsx", "./b.xlsx"]  # sorted
    assert snapshot["affected_files"] == ["./b.xlsx", "./a.xlsx", "./kept.xlsx"]

    restored = SessionState.from_dict(snapshot)
    assert restored.affected_file_deletions == {"./a.xlsx", "./b.xlsx"}
    assert restored.affected_files == state.affected_files


def test_legacy_snapshot_without_deletions_restores_empty_set() -> None:
    restored = SessionState.from_dict({"affected_files": ["./a.xlsx"]})
    assert restored.affected_file_deletions == set()


@pytest.mark.parametrize("bad_value", [None, "not-a-list", 42])
def test_malformed_deletions_payload_restores_empty_set(bad_value: Any) -> None:
    restored = SessionState.from_dict({"affected_file_deletions": bad_value})
    assert restored.affected_file_deletions == set()


def test_reset_loop_stats_and_reset_session_clear_deletions(tmp_path) -> None:
    state = _state_with_root(tmp_path)
    state.record_affected_file("a.xlsx", deleted=True)
    state.reset_loop_stats()
    assert state.affected_file_deletions == set()
    assert state.affected_files == []

    state.record_affected_file("b.xlsx", deleted=True)
    state.reset_session()
    assert state.affected_file_deletions == set()
    assert state.affected_files == []


def test_fresh_session_state_starts_without_deletions() -> None:
    assert SessionState().affected_file_deletions == set()


# ── 3. api_sse：MUTATION 序列化透出 deleted ──────────────────────────


def _sse_payload(event: ToolCallEvent) -> dict[str, Any]:
    from excelmanus.api_sse import sse_event_to_sse

    text = sse_event_to_sse(event)
    assert text is not None
    lines = [line for line in text.splitlines() if line.startswith("data: ")]
    assert len(lines) == 1
    return json.loads(lines[0][len("data: "):])


def test_sse_mutation_exposes_deleted_only_for_marked_items() -> None:
    event = ToolCallEvent(
        event_type=EventType.MUTATION,
        changed_files=["./gone.xlsx", "./kept.xlsx"],
        mutations=[
            {
                "identity": "./gone.xlsx",
                "content_version": "",
                "source": "runtime",
                "deleted": True,
            },
            {
                "identity": "./kept.xlsx",
                "content_version": "sha256:abc",
                "source": "runtime",
            },
        ],
    )
    payload = _sse_payload(event)
    by_identity = _mutations_by_identity(payload["mutations"])
    assert by_identity["./gone.xlsx"]["deleted"] is True
    assert "deleted" not in by_identity["./kept.xlsx"]
    assert payload["files"] == ["./gone.xlsx", "./kept.xlsx"]


def test_sse_mutation_ignores_falsy_deleted_flag() -> None:
    event = ToolCallEvent(
        event_type=EventType.MUTATION,
        mutations=[{"identity": "./a.xlsx", "deleted": 0}],
    )
    payload = _sse_payload(event)
    assert "deleted" not in payload["mutations"][0]


def test_sse_mutation_end_to_end_from_changed_mutations() -> None:
    """changed_mutations 的输出直接喂给 SSE 序列化，deleted 不丢失。"""
    event = ToolCallEvent(
        event_type=EventType.MUTATION,
        changed_files=["./gone.xlsx", "./kept.xlsx"],
        mutations=changed_mutations(
            ["./gone.xlsx", "./kept.xlsx"], deleted=["gone.xlsx"]
        ),
    )
    payload = _sse_payload(event)
    by_identity = _mutations_by_identity(payload["mutations"])
    assert by_identity["./gone.xlsx"]["deleted"] is True
    assert "deleted" not in by_identity["./kept.xlsx"]


# ── 4. session._consume_file_event：unlink / rename 源标记删除 ─────────


def _fake_engine(root) -> SimpleNamespace:
    return SimpleNamespace(
        _workspace=SimpleNamespace(root_dir=str(root)),
        _state=SimpleNamespace(file_content_versions={}),
        _registry_refresh_needed=False,
    )


def _build_manager(root):
    from excelmanus.config import ExcelManusConfig
    from excelmanus.session import SessionManager
    from excelmanus.tools import ToolRegistry

    config = ExcelManusConfig(
        api_key="test-key",
        base_url="https://test.example.com/v1",
        model="test-model",
        memory_enabled=False,
        workspace_root=str(root),
    )
    return SessionManager(
        max_sessions=5,
        ttl_seconds=60,
        config=config,
        registry=ToolRegistry(),
    )


def _consume(manager, event: dict) -> list[ToolCallEvent]:
    """在隔离的 AppRuntime 中消费一个文件事件，返回投递到 SSE 流的事件。"""
    from excelmanus.api_app_state import AppRuntime, bind_runtime, reset_runtime

    runtime = AppRuntime()
    token = bind_runtime(runtime)
    try:
        manager._consume_file_event(event)
    finally:
        reset_runtime(token)
    assert len(runtime.session_stream_states) == 1
    stream = next(iter(runtime.session_stream_states.values()))
    return [ev for _seq, ev in stream.event_buffer]


def test_consume_file_event_marks_unlinked_target_deleted(tmp_path) -> None:
    manager = _build_manager(tmp_path)
    engine = _fake_engine(tmp_path)
    manager._sessions["s1"] = SimpleNamespace(engine=engine)

    events = _consume(
        manager,
        {
            "event_id": "evt-unlink",
            "workspace_root": str(tmp_path),
            "path": "gone.xlsx",
            "exists_after": False,
            "context": {"source": "runtime"},
        },
    )

    assert len(events) == 1
    mutation = events[0]
    assert mutation.event_type == EventType.MUTATION
    assert mutation.tool_call_id == "evt-unlink"
    by_identity = _mutations_by_identity(mutation.mutations)
    assert by_identity["./gone.xlsx"]["deleted"] is True
    # 该身份仍要到达客户端（用于剔除），因此必须出现在 changed_files 中
    assert mutation.changed_files == ["./gone.xlsx"]
    assert engine._registry_refresh_needed is True


def test_consume_file_event_marks_rename_source_deleted_not_target(tmp_path) -> None:
    manager = _build_manager(tmp_path)
    engine = _fake_engine(tmp_path)
    manager._sessions["s1"] = SimpleNamespace(engine=engine)
    (tmp_path / "new.xlsx").write_bytes(b"new")

    events = _consume(
        manager,
        {
            "event_id": "evt-rename",
            "workspace_root": str(tmp_path),
            "path": "new.xlsx",
            "from_path": "old.xlsx",
            "exists_after": True,
            "after_version": "sha256:new",
            "context": {"source": "runtime"},
        },
    )

    assert len(events) == 1
    by_identity = _mutations_by_identity(events[0].mutations)
    # rename 源已不存在 → 标记删除
    assert by_identity["./old.xlsx"]["deleted"] is True
    # 目标仍存活 → 普通写入，不能带删除标记
    assert "deleted" not in by_identity["./new.xlsx"]
    # MutationEvent.to_dict() 用 camelCase 输出内容版本
    assert by_identity["./new.xlsx"]["contentVersion"] == "sha256:new"


def test_consume_file_event_clears_seen_versions(tmp_path) -> None:
    """删除/重命名后必须让会话重读，避免基于陈旧坐标写入。"""
    manager = _build_manager(tmp_path)
    engine = _fake_engine(tmp_path)
    engine._state.file_content_versions = {
        "old.xlsx": "sha256:old",
        "new.xlsx": "sha256:old-target",
        "other.xlsx": "sha256:other",
    }
    manager._sessions["s1"] = SimpleNamespace(engine=engine)
    (tmp_path / "new.xlsx").write_bytes(b"new")

    _consume(
        manager,
        {
            "event_id": "evt-clear",
            "workspace_root": str(tmp_path),
            "path": "new.xlsx",
            "from_path": "old.xlsx",
            "exists_after": True,
            "context": {"source": "runtime"},
        },
    )

    assert "old.xlsx" not in engine._state.file_content_versions
    assert "new.xlsx" not in engine._state.file_content_versions
    assert engine._state.file_content_versions == {"other.xlsx": "sha256:other"}


def test_consume_file_event_skips_other_workspace_sessions(tmp_path) -> None:
    other_root = tmp_path / "other"
    other_root.mkdir()
    manager = _build_manager(tmp_path)
    engine = _fake_engine(other_root)
    manager._sessions["s-other"] = SimpleNamespace(engine=engine)

    from excelmanus.api_app_state import AppRuntime, bind_runtime, reset_runtime

    runtime = AppRuntime()
    token = bind_runtime(runtime)
    try:
        manager._consume_file_event(
            {
                "event_id": "evt-other",
                "workspace_root": str(tmp_path),
                "path": "gone.xlsx",
                "exists_after": False,
                "context": {"source": "runtime"},
            }
        )
    finally:
        reset_runtime(token)

    assert runtime.session_stream_states == {}
    assert engine._registry_refresh_needed is False
