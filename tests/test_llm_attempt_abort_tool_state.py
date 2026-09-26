"""模型流中断时"工具调用状态"定案测试。

用户可见的回归点：

1. 参数已经流式下发给前端、但这次尝试被整段丢弃的调用（写入类）必须当场
   定案为**未执行**，不能永远停在"进行中"（``tool_call_aborted``）；
2. 真正进入执行器的调用不受影响，由 ToolRuntime 自己发终态事件；
3. 重试耗尽/不可恢复失败时，最后一次尝试流出去的调用同样要定案；
4. 面向模型的悬空 tool_call 占位按效果给确切状态：写入 = 结果未确认、
   命令 = 可能仍在后台运行、读取 = 未执行。
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from excelmanus.agent.loop import run_tool_loop
from excelmanus.config import ExcelManusConfig
from excelmanus.engine import AgentEngine
from excelmanus.engine_core import aborted_calls
from excelmanus.engine_core.error_payload import RESULT_UNCERTAIN, TOOL_CALL_NOT_EXECUTED
from excelmanus.engine_core.llm_caller import LLMRetryExhaustedError
from excelmanus.events import EventType
from excelmanus.memory import ConversationMemory
from excelmanus.skillpacks import SkillMatchResult
from excelmanus.tools import ToolRegistry

_DISCONNECT_MESSAGE = (
    "peer closed connection without sending complete message body "
    "(incomplete chunked read)"
)


def _make_config(**overrides) -> ExcelManusConfig:
    defaults = {
        "api_key": "test-key",
        "base_url": "https://test.example.com/v1",
        "model": "test-model",
        "max_iterations": 20,
        "max_consecutive_failures": 3,
        "workspace_root": str(Path(__file__).resolve().parent),
        "llm_retry_max_attempts": 2,
    }
    defaults.update(overrides)
    return ExcelManusConfig(**defaults)


def _make_engine() -> AgentEngine:
    return AgentEngine(_make_config(), ToolRegistry())


def _disconnect_error() -> Exception:
    inner = httpx.RemoteProtocolError(_DISCONNECT_MESSAGE)
    outer = httpx.RemoteProtocolError(_DISCONNECT_MESSAGE)
    outer.__cause__ = inner
    return outer


def _chunk(
    *,
    tool_call_id: str | None = None,
    name: str | None = None,
    args: str | None = None,
    content: str | None = None,
    finish_reason: str | None = None,
) -> SimpleNamespace:
    """OpenAI 形状的 chunk（consume_stream 的 OpenAI 分支才会发 args delta）。"""
    tool_calls = None
    if name is not None or args is not None:
        tool_calls = [
            SimpleNamespace(
                index=0,
                id=tool_call_id or "",
                function=SimpleNamespace(name=name, arguments=args),
            )
        ]
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                delta=SimpleNamespace(content=content, tool_calls=tool_calls),
                finish_reason=finish_reason,
            )
        ],
        usage=None,
    )


def _text_response(text: str) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text, tool_calls=None))]
    )


async def _stream_then_disconnect(*chunks: SimpleNamespace):
    for chunk in chunks:
        yield chunk
    raise _disconnect_error()


async def _ok_stream(*chunks: SimpleNamespace):
    for chunk in chunks:
        yield chunk


def _aborted_events(events: list) -> list:
    return [e for e in events if e.event_type == EventType.TOOL_CALL_ABORTED]


def _route_result() -> SkillMatchResult:
    return SkillMatchResult(skills_used=[], route_mode="fallback", system_contexts=[])


@pytest.mark.asyncio
async def test_streamed_write_call_is_settled_when_attempt_is_retried(monkeypatch) -> None:
    """流式写入参数已下发、随后断流重试：该调用定案为未执行，不留"进行中"。"""
    monkeypatch.setattr("excelmanus.agent.loop.compute_retry_delay", lambda *_: 0)
    engine = _make_engine()
    engine.memory.add_user_message("写一个回归脚本")
    engine._llm_caller.create_chat_completion_with_retry = AsyncMock(
        side_effect=[
            _stream_then_disconnect(
                _chunk(
                    tool_call_id="call_write",
                    name="write_text_file",
                    args='{"file_path": "regression_analysis.py", "content": "import pan',
                )
            ),
            _text_response("已改为直接分析"),
        ]
    )
    events: list = []

    result = await run_tool_loop(engine, _route_result(), on_event=events.append)

    assert result.reply == "已改为直接分析"
    aborted = _aborted_events(events)
    assert len(aborted) == 1, "被丢弃的流式调用必须定案一次"
    event = aborted[0]
    assert event.tool_call_id == "call_write"
    assert event.tool_name == "write_text_file"
    assert event.execution_state == "failed"
    assert event.success is False
    assert event.error == TOOL_CALL_NOT_EXECUTED
    assert event.abort_reason == aborted_calls.ABORT_REASON_RETRY
    assert event.abort_effect == aborted_calls.EFFECT_WRITE
    assert "本次写入未执行" in event.result
    # 定案发生在"正在重试"播报之前：用户先看到失败结论，再看到重试。
    retry_idx = next(
        i
        for i, item in enumerate(events)
        if item.event_type == EventType.LLM_RETRY and item.retry_status == "retrying"
    )
    assert events.index(event) < retry_idx


@pytest.mark.asyncio
async def test_retry_exhaustion_settles_last_attempt_streamed_call(monkeypatch) -> None:
    """重试耗尽：最后一次尝试流出去的调用也要定案为未执行。"""
    monkeypatch.setattr("excelmanus.agent.loop.compute_retry_delay", lambda *_: 0)
    engine = _make_engine()
    engine.memory.add_user_message("写文件")
    engine._llm_caller.create_chat_completion_with_retry = AsyncMock(
        side_effect=[
            _stream_then_disconnect(
                _chunk(tool_call_id="call_1", name="write_text_file", args='{"file_path": "a.py"')
            ),
            _stream_then_disconnect(
                _chunk(tool_call_id="call_2", name="write_text_file", args='{"file_path": "a.py"')
            ),
        ]
    )
    events: list = []

    with pytest.raises(LLMRetryExhaustedError):
        await run_tool_loop(engine, _route_result(), on_event=events.append)

    aborted = _aborted_events(events)
    assert [e.tool_call_id for e in aborted] == ["call_1", "call_2"]
    assert [e.abort_reason for e in aborted] == [
        aborted_calls.ABORT_REASON_RETRY,
        aborted_calls.ABORT_REASON_EXHAUSTED,
    ]
    assert all(e.execution_state == "failed" for e in aborted)


@pytest.mark.asyncio
async def test_streamed_call_missing_from_final_list_is_settled(monkeypatch) -> None:
    """流式 id 与最终调用 id 不一致：旧 id 的卡片定案，不留在"进行中"。"""
    monkeypatch.setattr("excelmanus.agent.loop.compute_retry_delay", lambda *_: 0)
    engine = _make_engine()
    engine.memory.add_user_message("写文件")
    engine._llm_caller.create_chat_completion_with_retry = AsyncMock(
        side_effect=[
            _ok_stream(
                _chunk(
                    tool_call_id="call_ghost",
                    name="write_text_file",
                    args='{"file_path": "a.py"',
                ),
                # 同一 index 的 id 被改写：最终调用是 call_real，call_ghost 不会执行
                _chunk(tool_call_id="call_real", args=', "content": "x"}', finish_reason="tool_calls"),
            ),
            _text_response("完成"),
        ]
    )
    events: list = []

    await run_tool_loop(engine, _route_result(), on_event=events.append)

    aborted = _aborted_events(events)
    assert [e.tool_call_id for e in aborted] == ["call_ghost"]
    assert aborted[0].abort_reason == aborted_calls.ABORT_REASON_INCOMPLETE
    # 真正的调用照常进入执行器并发终态，不被误判为未执行。
    executed_ids = {
        e.tool_call_id for e in events if e.event_type == EventType.TOOL_CALL_END
    }
    assert "call_real" in executed_ids
    assert "call_ghost" not in executed_ids


@pytest.mark.asyncio
async def test_executed_call_is_never_reported_as_aborted(monkeypatch) -> None:
    """完整流式抵达并执行的调用不产生 abort 事件（执行器自己的终态说了算）。"""
    monkeypatch.setattr("excelmanus.agent.loop.compute_retry_delay", lambda *_: 0)
    engine = _make_engine()
    engine.memory.add_user_message("写文件")
    engine._llm_caller.create_chat_completion_with_retry = AsyncMock(
        side_effect=[
            _ok_stream(
                _chunk(
                    tool_call_id="call_ok",
                    name="write_text_file",
                    args='{"file_path": "a.py", "content": "x"}',
                    finish_reason="tool_calls",
                )
            ),
            _text_response("完成"),
        ]
    )
    events: list = []

    await run_tool_loop(engine, _route_result(), on_event=events.append)

    assert _aborted_events(events) == []
    ends = [e for e in events if e.event_type == EventType.TOOL_CALL_END]
    assert [e.tool_call_id for e in ends] == ["call_ok"]


@pytest.mark.asyncio
async def test_nonretryable_failure_settles_streamed_call(monkeypatch) -> None:
    """内容安全拦截等不可恢复失败：已流出的调用定案为未执行。"""
    engine = _make_engine()
    engine.memory.add_user_message("写文件")

    class _ContentFilter(Exception):
        pass

    async def _filtered():
        yield _chunk(tool_call_id="call_blocked", name="write_text_file", args='{"file_path"')
        raise _ContentFilter("content_filter triggered")

    monkeypatch.setattr("excelmanus.agent.loop.is_content_filter_error", lambda *_: True)
    engine._llm_caller.create_chat_completion_with_retry = AsyncMock(side_effect=[_filtered()])
    events: list = []

    with pytest.raises(_ContentFilter):
        await run_tool_loop(engine, _route_result(), on_event=events.append)

    aborted = _aborted_events(events)
    assert [e.tool_call_id for e in aborted] == ["call_blocked"]
    assert aborted[0].abort_reason == aborted_calls.ABORT_REASON_FAILURE


# ── 面向模型的悬空占位：按效果给确切状态 ─────────────────────────


def _memory() -> ConversationMemory:
    return ConversationMemory(
        ExcelManusConfig(api_key="k", base_url="https://test.example.com/v1", model="m")
    )


def _placeholder_for(tool_name: str) -> dict:
    memory = _memory()
    memory.add_assistant_tool_message({
        "role": "assistant",
        "content": None,
        "tool_calls": [{
            "id": "call_x",
            "type": "function",
            "function": {"name": tool_name, "arguments": "{}"},
        }],
    })
    assert memory.repair_dangling_tool_calls() == 1
    content = next(m["content"] for m in memory.messages if m.get("role") == "tool")
    return json.loads(content)


def test_dangling_write_placeholder_is_marked_unconfirmed_failure() -> None:
    payload = _placeholder_for("write_text_file")
    assert payload["status"] == "error"
    assert payload["error_code"] == RESULT_UNCERTAIN
    assert payload["executed"] is None
    assert "不能假定已经生效" in payload["message"]


def test_dangling_command_placeholder_stays_running() -> None:
    for tool_name in ("run_code", "run_shell"):
        payload = _placeholder_for(tool_name)
        assert payload["status"] == "running", f"{tool_name} 不应被标成失败"
        assert "可能仍在后台运行" in payload["message"]
        assert "不要重复发起" in payload["message"]


def test_dangling_read_placeholder_is_not_executed() -> None:
    payload = _placeholder_for("read_text_file")
    assert payload["status"] == "error"
    assert payload["error_code"] == TOOL_CALL_NOT_EXECUTED
    assert payload["executed"] is False


def test_effect_classification_covers_write_command_and_read() -> None:
    assert aborted_calls.classify_tool_effect("write_text_file") == aborted_calls.EFFECT_WRITE
    assert aborted_calls.classify_tool_effect("apply_spreadsheet_changes") == aborted_calls.EFFECT_WRITE
    assert aborted_calls.classify_tool_effect("write_plan") == aborted_calls.EFFECT_WRITE
    assert aborted_calls.classify_tool_effect("run_code") == aborted_calls.EFFECT_COMMAND
    assert aborted_calls.classify_tool_effect("run_shell") == aborted_calls.EFFECT_COMMAND
    assert aborted_calls.classify_tool_effect("observe_spreadsheet") == aborted_calls.EFFECT_READ
    assert aborted_calls.classify_tool_effect("read_text_file") == aborted_calls.EFFECT_READ


def test_unknown_tool_effect_is_treated_conservatively() -> None:
    """未登记效果的工具（MCP/自定义）不能当只读：悬空时按"未确认"给。"""
    assert aborted_calls.classify_tool_effect("mcp_erp_write_order") == aborted_calls.EFFECT_UNKNOWN
    assert aborted_calls.classify_tool_effect("gated_write") == aborted_calls.EFFECT_UNKNOWN
    payload = _placeholder_for("gated_write")
    assert payload["error_code"] == RESULT_UNCERTAIN
    assert "不能假定已经生效" in payload["message"]
    assert "未执行，没有产生任何副作用" in aborted_calls.aborted_call_message(
        "gated_write", aborted_calls.ABORT_REASON_RETRY
    )


def test_sse_payload_carries_the_fields_the_frontend_settles_on() -> None:
    """SSE 载荷字段名与前端 handler 的约定必须一致（前后端契约）。"""
    from excelmanus.api_sse import sse_event_to_sse
    from excelmanus.events import ToolCallEvent

    payload = aborted_calls.aborted_call_payload(
        "write_text_file", aborted_calls.ABORT_REASON_RETRY
    )
    text = sse_event_to_sse(ToolCallEvent(
        event_type=EventType.TOOL_CALL_ABORTED,
        tool_call_id="call_write",
        tool_name="write_text_file",
        execution_state="failed",
        success=False,
        error=str(payload["error_code"]),
        result=str(payload["message"]),
        abort_reason=aborted_calls.ABORT_REASON_RETRY,
        abort_effect=aborted_calls.EFFECT_WRITE,
        iteration=2,
    ))

    assert text is not None
    assert text.startswith("event: tool_call_aborted\n")
    data = json.loads(text.split("data: ", 1)[1].strip())
    assert data["tool_call_id"] == "call_write"
    assert data["tool_name"] == "write_text_file"
    assert data["execution_state"] == "failed"
    assert data["reason"] == "llm_retry"
    assert data["effect"] == "write"
    assert data["error"] == TOOL_CALL_NOT_EXECUTED
    assert "本次写入未执行" in data["message"]
    assert data["iteration"] == 2
