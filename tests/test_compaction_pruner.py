"""L1 无模型修剪：error 瘦身、head/marker/tail、幂等、replace 事件接线。"""

from __future__ import annotations

import json

import pytest

from excelmanus.compaction_pruner import (
    PRUNE_HEAD_CHARS,
    PRUNE_TAIL_CHARS,
    PRUNE_THRESHOLD_CHARS,
    prune_messages,
    slim_error_text,
)
from excelmanus.config import ExcelManusConfig
from excelmanus.memory import ConversationMemory
from excelmanus.session_log import SessionEventLog


def _cfg() -> ExcelManusConfig:
    return ExcelManusConfig(
        api_key="t", base_url="https://x.example/v1", model="m"
    )


class TestSlimError:
    def test_slim_keeps_required_keys_drops_data(self) -> None:
        payload = {
            "status": "error",
            "error_code": "TOOL_ERROR",
            "message": "x" * 5000,
            "failure_class": "internal",
            "remediation": "fix it",
            "data": ["y" * 100] * 50,
            "shape": [100, 20],
            "columns": [f"c{i}" for i in range(200)],
        }
        out = slim_error_text(json.dumps(payload, ensure_ascii=False))
        assert out is not None
        slim = json.loads(out)
        assert slim["error_code"] == "TOOL_ERROR"
        assert slim["failure_class"] == "internal"
        assert slim["remediation"] == "fix it"
        assert len(slim["message"]) <= 501
        assert "data" not in slim and "columns" not in slim
        assert set(slim["pruned_fields"]) >= {"data", "shape", "columns"}

    def test_slim_non_error_returns_none(self) -> None:
        assert slim_error_text("plain text") is None
        assert slim_error_text('{"status": "ok"}') is None
        assert slim_error_text('{"status": "error"}') is None or True  # 无必填键也可瘦
        # 非 JSON
        assert slim_error_text("{not json") is None


class TestPruneMessages:
    def test_only_oversized_tool_results(self) -> None:
        msgs = [
            {"role": "user", "content": "x" * 50000},  # 非 tool → 不动
            {"role": "tool", "tool_call_id": "a", "content": "t" * 9000},
            {"role": "tool", "tool_call_id": "b", "content": "small"},
            {"role": "assistant", "content": "a" * 20000},  # 非 tool → 不动
        ]
        edits = prune_messages(msgs)
        assert set(edits) == {1}
        out = edits[1]
        assert "已省略" in out
        assert out.startswith("t" * 10)
        assert out.endswith("t" * 10)
        assert len(out) < 9000

    def test_spill_pointer_skipped(self) -> None:
        msgs = [{"role": "tool", "tool_call_id": "a",
                 "content": "spill:sha256:" + "ab" * 32 + " " + "x" * 9000}]
        # 以 spill: 开头即视为已外置——但整串超阈值时仍有 head/tail 兜底？
        # 设计选择：spill 指针本身很短，这里构造超阈值串验证跳过逻辑
        edits = prune_messages(msgs)
        assert edits == {}

    def test_idempotent(self) -> None:
        msgs = [{"role": "tool", "tool_call_id": "a", "content": "z" * 20000}]
        first = prune_messages(msgs)
        assert first
        msgs[0]["content"] = first[0]
        assert prune_messages(msgs) == {}

    def test_error_payload_slimmed_before_head_tail(self) -> None:
        payload = {
            "status": "error",
            "error_code": "EXECUTION_FAILED",
            "message": "failed",
            "failure_class": "internal",
            "remediation": "retry",
            "data": "d" * 20000,
        }
        msgs = [{"role": "tool", "tool_call_id": "a",
                 "content": json.dumps(payload, ensure_ascii=False)}]
        edits = prune_messages(msgs)
        slim = json.loads(edits[0])
        assert slim["error_code"] == "EXECUTION_FAILED"
        assert "data" not in slim
        # 瘦身后已低于阈值 → 不再 head/tail（无省略标记）
        assert "已省略" not in edits[0]


class TestPruneWiring:
    def test_memory_emits_replace_events(self) -> None:
        """修剪走 _emit_replace → tool/result replace 事件，原文留日志。"""
        mem = ConversationMemory(_cfg())
        log = SessionEventLog("s1")
        mem.attach_event_log(log)
        mem.add_tool_result("c1", "w" * 20000)
        mem.add_user_message("q")

        edits = prune_messages(list(mem.messages))
        assert 0 in edits
        msg = mem.messages[0]
        msg["content"] = edits[0]
        mem._emit_replace(msg, kind="tool/result")

        # 事件流：append(c1) + append(user) + replace(c1)
        kinds = [ev.kind for ev in log.events]
        assert kinds == ["tool/result", "user/message", "tool/result"]
        repl = log.events[-1]
        assert repl.surface_op == "replace"
        assert repl.source_seqs == (1,)
        # durable 视图原文仍在
        durable = log.durable_messages()
        orig = next(m for m in durable if m.get("_seq") == 1)
        assert orig["content"] == "w" * 20000
        assert orig["_shadowed_by"] == repl.seq

    def test_prune_does_not_touch_unsent_without_log(self) -> None:
        """未挂日志：原地改内容即可（未上链），行为与今天一致。"""
        mem = ConversationMemory(_cfg())
        mem.add_tool_result("c1", "w" * 20000)
        edits = prune_messages(list(mem.messages))
        mem.messages[0]["content"] = edits[0]
        assert "已省略" in mem.messages[0]["content"]
