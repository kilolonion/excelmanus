"""Session event log：append-only 校验、surface fold、持久化往返。"""

from __future__ import annotations

import pytest

from excelmanus.chat_history import ChatHistoryStore
from excelmanus.database import Database
from excelmanus.session_log import (
    OP_APPEND,
    OP_REPLACE,
    OP_VOID,
    SessionEventLog,
    SurfaceContractError,
    fold_events,
    reconstruct_tool_call_timeline,
)


def _user(text: str, **extra: object) -> dict:
    return {"role": "user", "content": text, "message_id": f"m-{text}", **extra}


def _tool(call_id: str, content: str, **extra: object) -> dict:
    return {
        "role": "tool",
        "tool_call_id": call_id,
        "content": content,
        "message_id": f"m-{call_id}",
        **extra,
    }


class TestAppendAndFold:
    def test_append_assigns_monotonic_seq(self) -> None:
        log = SessionEventLog("s1")
        e1 = log.append("user/message", _user("a"))
        e2 = log.append("assistant/message", {"role": "assistant", "content": "hi"})
        assert (e1.seq, e2.seq) == (1, 2)
        assert e1.surface_op == OP_APPEND

    def test_bookkeeping_events_produce_no_surface(self) -> None:
        log = SessionEventLog("s1")
        log.append("user/message", _user("a"))
        log.append("usage", {"prompt_tokens": 100})
        log.append("turn/start", {"turn": 1})
        surface = log.surface_messages()
        assert len(surface) == 1
        assert surface[0]["content"] == "a"
        assert len(log.events) == 3

    def test_surface_kind_requires_op(self) -> None:
        log = SessionEventLog("s1")
        with pytest.raises(SurfaceContractError, match="surface_op"):
            log.append("user/message", _user("a"), surface_op="bogus")

    def test_non_surface_kind_rejects_append(self) -> None:
        log = SessionEventLog("s1")
        with pytest.raises(SurfaceContractError, match="非 surface kind"):
            log.append("usage", {"x": 1}, surface_op=OP_APPEND)

    def test_out_of_order_seq_rejected(self) -> None:
        log = SessionEventLog("s1", events=[
            {"seq": 1, "kind": "user/message", "payload": _user("a"),
             "surface_op": "append"},
        ])
        # fold 已消费 seq=1；再喂同 seq 必须拒绝
        from excelmanus.session_log import SurfaceFold, SessionEvent

        fold = SurfaceFold()
        fold.apply(log.events[0])
        with pytest.raises(SurfaceContractError, match="乱序"):
            fold.apply(
                SessionEvent(seq=1, kind="user/message",
                             payload=_user("b"), surface_op=OP_APPEND)
            )


class TestReplace:
    def test_tool_result_replace_shadows_content_only(self) -> None:
        log = SessionEventLog("s1")
        log.append("user/message", _user("q"))
        log.append("tool/result", _tool("c1", "x" * 100))
        ev = log.append(
            "tool/result",
            _tool("c1", "x" * 20 + "…pruned"),
            surface_op=OP_REPLACE,
            shadow=(2, 2),
            source_seqs=(2,),
        )
        surface = log.surface_messages()
        assert len(surface) == 2
        assert surface[1]["content"].endswith("pruned")
        # durable 视图保留原文与遮蔽标记
        durable = log.durable_messages()
        orig = next(m for m in durable if m.get("_seq") == 2)
        assert orig["content"] == "x" * 100
        assert orig["_shadowed_by"] == ev.seq
        repl = next(m for m in durable if m.get("_seq") == ev.seq)
        assert repl["_shadows"] == [2]

    def test_tool_result_replace_rejects_field_mutation(self) -> None:
        log = SessionEventLog("s1")
        log.append("tool/result", _tool("c1", "big"))
        with pytest.raises(SurfaceContractError, match="不允许改字段"):
            log.append(
                "tool/result",
                _tool("c2", "small"),  # tool_call_id 被改 → 拒绝
                surface_op=OP_REPLACE,
                shadow=(1, 1),
                source_seqs=(1,),
            )

    def test_tool_result_replace_requires_single_tool_node(self) -> None:
        log = SessionEventLog("s1")
        log.append("tool/result", _tool("c1", "a"))
        log.append("user/message", _user("u"))
        with pytest.raises(SurfaceContractError, match="单个 tool/result"):
            log.append(
                "tool/result",
                _tool("c1", "b"),
                surface_op=OP_REPLACE,
                shadow=(1, 2),
                source_seqs=(1, 2),
            )

    def test_replace_shadows_exact_source_set(self) -> None:
        """source_seqs 是精确遮蔽集：只移除列出的节点，区间内其他节点保留。"""
        log = SessionEventLog("s1")
        log.append("user/message", _user("a"))
        log.append("user/message", _user("b"))
        log.append("user/message", _user("c"))
        log.append(
            "user/message",
            _user("summary-of-a-c"),
            surface_op=OP_REPLACE,
            source_seqs=(1, 3),  # b 保留
        )
        surface = log.surface_messages()
        assert [m["content"] for m in surface] == ["summary-of-a-c", "b"]

    def test_replace_rejects_shadow_mismatch(self) -> None:
        log = SessionEventLog("s1")
        log.append("user/message", _user("a"))
        log.append("user/message", _user("b"))
        with pytest.raises(SurfaceContractError, match="不一致"):
            log.append(
                "user/message",
                _user("s"),
                surface_op=OP_REPLACE,
                shadow=(1, 2),
                source_seqs=(1,),
            )

    def test_replace_empty_range_rejected(self) -> None:
        log = SessionEventLog("s1")
        log.append("user/message", _user("a"))
        with pytest.raises(SurfaceContractError):
            log.append(
                "user/message",
                _user("s"),
                surface_op=OP_REPLACE,
                source_seqs=(5,),  # seq 5 非 live
            )

    def test_range_replace_inserts_at_shadow_start(self) -> None:
        log = SessionEventLog("s1")
        log.append("user/message", _user("first"))
        log.append("assistant/message", {"role": "assistant", "content": "r1"})
        log.append("assistant/message", {"role": "assistant", "content": "r2"})
        log.append("user/message", _user("last"))
        log.append(
            "user/message",
            _user("[summary]"),
            surface_op=OP_REPLACE,
            source_seqs=(2, 3),
        )
        surface = log.surface_messages()
        assert [m["content"] for m in surface] == ["first", "[summary]", "last"]

    def test_multi_node_replace_occupies_seq_range(self) -> None:
        """压缩摘要的双消息形态：一个事件产出 [user, assistant] 两个节点。"""
        log = SessionEventLog("s1")
        log.append("user/message", _user("q1"))
        log.append("assistant/message", {"role": "assistant", "content": "a1"})
        log.append("user/message", _user("q2"))
        ev = log.append(
            "user/message",
            {"messages": [
                {"role": "user", "content": "[系统] 请基于以下对话摘要继续工作。"},
                {"role": "assistant", "content": "[对话摘要]\n..."},
            ]},
            surface_op=OP_REPLACE,
            source_seqs=(1, 2),
        )
        assert ev.seq == 4
        surface = log.surface_messages()
        assert [m["content"] for m in surface] == [
            "[系统] 请基于以下对话摘要继续工作。",
            "[对话摘要]\n...",
            "q2",
        ]
        # 多节点占用号段：下一事件 seq = 4+2 = 6
        nxt = log.append("user/message", _user("after"))
        assert nxt.seq == 6


class TestVoid:
    def test_void_retracts_without_replacement(self) -> None:
        log = SessionEventLog("s1")
        log.append("user/message", _user("a"))
        log.append("system/update", {"role": "system", "content": "pol"})
        log.append("user/message", _user("b"))
        log.append(
            "rollback/edit", {},
            surface_op=OP_VOID, shadow=(2, 2), source_seqs=(2,),
        )
        surface = log.surface_messages()
        assert [m["content"] for m in surface] == ["a", "b"]
        durable = log.durable_messages()
        sysmsg = next(m for m in durable if m["content"] == "pol")
        assert "_shadowed_by" in sysmsg


class TestPersistence:
    def _store(self, tmp_path) -> ChatHistoryStore:
        db = Database(str(tmp_path / "t.db"))
        return ChatHistoryStore(db)

    def test_events_roundtrip(self, tmp_path) -> None:
        store = self._store(tmp_path)
        store.create_session("s1")
        log = SessionEventLog("s1")
        log.append("user/message", _user("q"))
        log.append("tool/result", _tool("c1", "big"))
        log.append(
            "tool/result", _tool("c1", "pruned"),
            surface_op=OP_REPLACE, shadow=(2, 2), source_seqs=(2,),
        )
        store.save_events("s1", [ev.to_row() for ev in log.events])

        rows = store.iter_events("s1")
        assert len(rows) == 3
        fold = fold_events(rows)
        surface = fold.surface_messages()
        assert surface[1]["content"] == "pruned"

    def test_replayed_log_continues_seq(self, tmp_path) -> None:
        store = self._store(tmp_path)
        store.create_session("s1")
        log = SessionEventLog("s1")
        log.append("user/message", _user("q"))
        store.save_events("s1", [ev.to_row() for ev in log.events])

        # 模拟 resume：从持久化事件重建 log 后继续追加
        restored = SessionEventLog("s1", events=store.iter_events("s1"))
        ev = restored.append("user/message", _user("q2"))
        assert ev.seq == 2
        assert [m["content"] for m in restored.surface_messages()] == ["q", "q2"]

    def test_duplicate_seq_insert_is_idempotent(self, tmp_path) -> None:
        """崩溃重试场景：同 seq 重复写走 OR IGNORE 幂等，不报错不重复。"""
        store = self._store(tmp_path)
        store.create_session("s1")
        log = SessionEventLog("s1")
        log.append("user/message", _user("q"))
        rows = [ev.to_row() for ev in log.events]
        store.save_events("s1", rows)
        store.save_events("s1", rows)
        assert store.max_event_seq("s1") == 1
        assert len(store.iter_events("s1")) == 1

    def test_persisted_seq_reconciliation(self, tmp_path) -> None:
        """持久化对账：max_event_seq 过滤已落盘事件，只写新增。"""
        store = self._store(tmp_path)
        store.create_session("s1")
        log = SessionEventLog("s1")
        log.append("user/message", _user("q1"))
        store.save_events("s1", [ev.to_row() for ev in log.events_after(0)])
        log.append("user/message", _user("q2"))
        persisted = store.max_event_seq("s1")
        rows = [ev.to_row() for ev in log.events_after(persisted)]
        assert len(rows) == 1 and rows[0]["seq"] == 2
        store.save_events("s1", rows)
        assert store.max_event_seq("s1") == 2

    def test_clear_messages_keeps_events_by_default(self, tmp_path) -> None:
        store = self._store(tmp_path)
        store.create_session("s1")
        log = SessionEventLog("s1")
        log.append("user/message", _user("q"))
        store.save_turn_messages("s1", log.surface_messages(), turn_number=1)
        store.save_events("s1", [ev.to_row() for ev in log.events])

        # 压缩式快照重写：messages 清空，events 保留
        store.clear_messages("s1")
        assert store.get_message_count("s1") == 0
        assert store.has_events("s1")

        # 用户显式清除：events 一并删除
        store.save_turn_messages("s1", log.surface_messages(), turn_number=1)
        store.clear_messages("s1", clear_events=True)
        assert not store.has_events("s1")

    def test_delete_session_removes_events(self, tmp_path) -> None:
        store = self._store(tmp_path)
        store.create_session("s1")
        log = SessionEventLog("s1")
        log.append("user/message", _user("q"))
        store.save_events("s1", [ev.to_row() for ev in log.events])
        assert store.delete_session("s1")
        assert not store.has_events("s1")

    def test_legacy_import_events(self) -> None:
        """无 events 的旧会话：messages 行合成 legacy/import append。"""
        legacy = [_user("old1"), _tool("c9", "res")]
        log = SessionEventLog("s-old")
        for msg in legacy:
            log.append("legacy/import", msg)
        assert log.surface_messages() == legacy


class TestToolCallAuditReplay:
    def test_call_events_stay_off_surface(self) -> None:
        log = SessionEventLog("s1")
        log.append("user/message", _user("q"))
        log.append(
            "tool/call_start",
            {
                "tool_call_id": "child",
                "tool_name": "inspect_spreadsheet",
                "parent_call_id": "run-1",
            },
        )
        log.append(
            "assistant/message",
            {"role": "assistant", "content": "", "message_id": "m-a"},
        )
        log.append(
            "tool/call_end",
            {
                "tool_call_id": "child",
                "tool_name": "inspect_spreadsheet",
                "parent_call_id": "run-1",
                "success": True,
            },
        )
        assert [m["role"] for m in log.surface_messages()] == ["user", "assistant"]

    def test_reconstruct_nests_children_before_parent_end(self) -> None:
        events = [
            {"seq": 1, "kind": "tool/call_start",
             "payload": {"tool_call_id": "run-1", "tool_name": "run_code"}},
            {"seq": 2, "kind": "tool/call_start",
             "payload": {"tool_call_id": "c1", "tool_name": "inspect_spreadsheet",
                         "parent_call_id": "run-1"}},
            {"seq": 3, "kind": "tool/call_end",
             "payload": {"tool_call_id": "c1", "tool_name": "inspect_spreadsheet",
                         "parent_call_id": "run-1", "success": True}},
            {"seq": 4, "kind": "tool/call_end",
             "payload": {"tool_call_id": "run-1", "tool_name": "run_code",
                         "success": True}},
        ]
        timeline = reconstruct_tool_call_timeline(events)
        assert [n["tool_call_id"] for n in timeline] == ["run-1"]
        child = timeline[0]["children"][0]
        assert child["tool_call_id"] == "c1"
        assert child["seq_end"] < timeline[0]["seq_end"]
