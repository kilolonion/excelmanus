"""Bounded session trace, persisted through the existing runtime snapshot.

It correlates existing SSE events without adding a second event transport or
database schema. Snapshots are bounded and can be persisted with session state.
"""

from __future__ import annotations

import time
import asyncio
import inspect
import math
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4


@dataclass
class TraceSpan:
    span_id: str
    trace_id: str
    kind: str
    name: str
    parent_span_id: str | None = None
    start_time: float = field(default_factory=time.time)
    end_time: float | None = None
    status: str = "running"
    attributes: dict[str, Any] = field(default_factory=dict)
    duration_ms: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "span_id": self.span_id,
            "trace_id": self.trace_id,
            "kind": self.kind,
            "name": self.name,
            "parent_span_id": self.parent_span_id,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "status": self.status,
            "attributes": deepcopy(self.attributes),
            "duration_ms": self.duration_ms,
        }


class TraceRecorder:
    def __init__(self, *, trace_id: str | None = None, max_spans: int = 512) -> None:
        self.trace_id = trace_id or uuid4().hex
        self.max_spans = max(32, int(max_spans))
        self.spans: list[TraceSpan] = []
        self._open: dict[str, TraceSpan] = {}
        self._by_key: dict[str, str] = {}
        self._started_mono: dict[str, float] = {}
        self.dropped_spans = 0

    def get(self, key: str) -> TraceSpan | None:
        span_id = self._by_key.get(key)
        return next((span for span in reversed(self.spans) if span.span_id == span_id), None)

    def start(self, key: str, kind: str, name: str, *, parent_key: str | None = None, **attributes: Any) -> TraceSpan:
        existing_id = self._by_key.get(key)
        if existing_id and existing_id in self._open:
            return self._open[existing_id]
        parent_id = self._by_key.get(parent_key or "")
        span = TraceSpan(
            span_id=uuid4().hex[:16], trace_id=self.trace_id, kind=kind,
            name=name, parent_span_id=parent_id, attributes=attributes,
        )
        self.spans.append(span)
        self._open[span.span_id] = span
        self._by_key[key] = span.span_id
        self._started_mono[span.span_id] = time.monotonic()
        self._prune()
        return span

    def _prune(self) -> None:
        while len(self.spans) > self.max_spans:
            # Prefer closed leaves; keep live ancestry as long as capacity permits.
            parents = {span.parent_span_id for span in self.spans}
            old = next((span for span in self.spans if span.end_time is not None
                        and span.span_id not in parents), self.spans[0])
            self.spans.remove(old)
            self._open.pop(old.span_id, None)
            self._started_mono.pop(old.span_id, None)
            self._by_key = {key: sid for key, sid in self._by_key.items() if sid != old.span_id}
            for child in self.spans:
                if child.parent_span_id == old.span_id:
                    child.parent_span_id = None
                    child.attributes["parent_evicted"] = True
            self.dropped_spans += 1

    def finish(self, key: str, *, status: str = "ok", **attributes: Any) -> TraceSpan | None:
        span_id = self._by_key.get(key)
        span = self._open.pop(span_id, None) if span_id else None
        if span is None:
            return self.get(key)
        span.end_time = time.time()
        span.status = status
        span.attributes.update(attributes)
        started = self._started_mono.pop(span.span_id, None)
        span.duration_ms = round(max(0.0, time.monotonic() - started) * 1000, 3) if started is not None else None
        return span

    def finish_children(self, parent: TraceSpan, *, status: str) -> None:
        """Close incomplete work at a turn boundary, leaving delegated runs independent."""
        pending = {parent.span_id}
        while pending:
            children = [s for s in self.spans if s.parent_span_id in pending and s.kind != "subagent"]
            for span in children:
                if span.status == "running":
                    key = next((k for k, sid in self._by_key.items() if sid == span.span_id), "")
                    self.finish(key, status=status)
            pending = {s.span_id for s in children}

    def instant(self, kind: str, name: str, *, parent_key: str | None = None, **attributes: Any) -> TraceSpan:
        key = uuid4().hex
        span = self.start(key, kind, name, parent_key=parent_key, **attributes)
        self.finish(key)
        return span

    def snapshot(self) -> dict[str, Any]:
        return {"trace_id": self.trace_id, "spans": [span.to_dict() for span in self.spans],
                "dropped_spans": self.dropped_spans}

    @classmethod
    def from_snapshot(cls, raw: Any) -> TraceRecorder:
        recorder = cls()
        if not isinstance(raw, dict) or not isinstance(raw.get("trace_id"), str) or not raw["trace_id"]:
            return recorder
        recorder.trace_id = raw["trace_id"]
        rows = raw.get("spans")
        if not isinstance(rows, list):
            return recorder
        seen = set()
        for row in deepcopy(rows[-recorder.max_spans:]):
            if not isinstance(row, dict) or not isinstance(row.get("span_id"), str) or not row["span_id"]:
                continue
            if row["span_id"] in seen or row.get("trace_id") != recorder.trace_id:
                continue
            try:
                span = TraceSpan(**{k: v for k, v in row.items() if k in TraceSpan.__dataclass_fields__})
                span.start_time = float(span.start_time)
                if not math.isfinite(span.start_time) or not isinstance(span.parent_span_id, (str, type(None))):
                    continue
                if not isinstance(span.attributes, dict):
                    span.attributes = {}
                if span.status == "running":
                    span.status = "interrupted"
                    span.end_time = time.time()
                    span.duration_ms = None  # Process loss cannot measure a precise finish time.
                    span.attributes["interrupted_on_restore"] = True
            except (TypeError, ValueError):
                continue
            seen.add(span.span_id)
            recorder.spans.append(span)
        for span in recorder.spans:
            if span.parent_span_id and span.parent_span_id not in seen:
                span.parent_span_id = None
                span.attributes["parent_evicted"] = True
        dropped = raw.get("dropped_spans", 0)
        recorder.dropped_spans = max(0, dropped if isinstance(dropped, int) else 0) + max(0, len(rows) - recorder.max_spans)
        # Restored spans are immutable history. New execution builds new keys/IDs.
        return recorder


def trace_of(engine: Any) -> TraceRecorder | None:
    recorder = getattr(engine, "_trace", None)
    return recorder if isinstance(recorder, TraceRecorder) else None


def scope_key(engine: Any, key: str) -> str:
    scope = getattr(engine, "_trace_scope", "")
    return f"{scope}:{key}" if isinstance(scope, str) and scope else key


def step_key(engine: Any) -> str:
    driver = getattr(engine, "_driver", None)
    turn = str(getattr(driver, "turn_id", "") or "")
    step = str(getattr(driver, "step_id", "") or "")
    return scope_key(engine, f"turn:{turn}:step:{step}" if step else f"turn:{turn}")


def persist_trace(engine: Any) -> None:
    from excelmanus.logger import get_logger

    save = getattr(engine, "_trace_persist", None) or getattr(engine, "save_session_snapshot", None)
    if callable(save):
        try:
            save()
        except Exception:
            get_logger("trace").debug("trace snapshot failed", exc_info=True)


def record_event(engine: Any, event: Any) -> None:
    from excelmanus.events import EventType as E

    trace = trace_of(engine)
    if trace is None or event.span_id:
        return
    driver = getattr(engine, "_driver", None)
    event.turn_id = event.turn_id or str(getattr(driver, "turn_id", "") or "")
    event.step_id = event.step_id or str(getattr(driver, "step_id", "") or "")
    turn = scope_key(engine, f"turn:{event.turn_id}")
    step = scope_key(engine, f"turn:{event.turn_id}:step:{event.step_id}") if event.step_id else turn
    tool = scope_key(engine, f"{event.turn_id}:tool:{event.tool_call_id}")
    approval = scope_key(engine, f"{event.turn_id}:approval:{event.approval_id}")
    child = scope_key(engine, f"subagent:{event.subagent_conversation_id}")
    kind = event.event_type
    span = None
    changed = True
    ids = {"turn_id": event.turn_id, "step_id": event.step_id}
    request_key = getattr(engine, "_trace_last_request_key", "") or ""
    request = trace.get(request_key)
    if request and all(request.attributes.get(k) == v for k, v in ids.items()):
        ids["request_id"] = request.attributes.get("request_id", "")
    else:
        request = None
    if kind == E.TURN_START:
        span = trace.start(turn, "turn", event.turn_id, parent_key=getattr(engine, "_trace_parent_key", None), **ids)
    elif kind in {E.TURN_END, E.TURN_FAILED}:
        reason = event.stop_reason
        if kind == E.TURN_END:
            reason = (getattr(driver, "_turn_record", None) or {}).get("status", "completed")
        status = {"completed": "ok", "wall_clock": "timeout", "shutdown": "cancelled"}.get(reason, reason or "error")
        span = trace.finish(turn, status=status, stop_reason=reason)
        if span:
            trace.finish_children(span, status="interrupted" if span.status == "ok" else span.status)
    elif kind == E.STEP_START:
        span = trace.start(step, "step", event.step_id, parent_key=turn, **ids)
    elif kind == E.STEP_END:
        span = trace.finish(step, status="error" if event.turn_error else "ok")
    elif kind == E.TOOL_CALL_START:
        parent = scope_key(engine, f"{event.turn_id}:tool:{event.parent_call_id}") if event.parent_call_id else request_key if request else step
        span = trace.start(tool, "tool", event.tool_name, parent_key=parent, call_id=event.tool_call_id, **ids)
    elif kind == E.TOOL_CALL_END:
        span = trace.get(tool)
        waiting = span and any(s.parent_span_id == span.span_id and s.kind == "approval"
                               and s.status == "running" for s in trace.spans)
        if not waiting:
            span = trace.finish(tool, status="ok" if event.success else "error")
    elif kind == E.LLM_RETRY:
        span = trace.instant("retry", "llm_retry", parent_key=request_key if request else step,
                             retry_status=event.retry_status, attempt=event.retry_attempt, **ids)
    elif kind == E.PENDING_APPROVAL:
        span = trace.start(approval, "approval", event.approval_tool_name, parent_key=tool if trace.get(tool) else step, **ids)
    elif kind == E.APPROVAL_RESOLVED:
        span = trace.finish(approval, status="ok" if event.success else "denied")
        trace.finish(tool, status="ok" if event.success else "denied")
    elif kind == E.SUBAGENT_START:
        from excelmanus.tools.context import current_call

        call = current_call()
        parent_tool = scope_key(engine, f"{event.turn_id}:tool:{call.call_id}") if call else ""
        span = trace.start(child, "subagent", event.subagent_name,
                           parent_key=parent_tool if trace.get(parent_tool) else step if trace.get(step) else turn,
                           run_id=event.subagent_conversation_id, background=event.subagent_background, **ids)
    elif kind == E.SUBAGENT_END:
        span = trace.finish(child, status="ok" if event.subagent_success else event.subagent_reason or "error")
    else:
        changed = False
        active = getattr(engine, "_trace_active_request_key", "") or ""
        span = (trace.get(active) if kind in {E.TEXT_DELTA, E.THINKING_DELTA, E.TOOL_CALL_ARGS_DELTA} else None)
        span = span or (trace.get(tool) if event.tool_call_id else None) or trace.get(step) or trace.get(turn)
    event.trace_id = trace.trace_id
    if span:
        event.span_id = span.span_id
        event.parent_span_id = span.parent_span_id or ""
        event.turn_id = span.attributes.get("turn_id", event.turn_id)
        event.step_id = span.attributes.get("step_id", event.step_id)
        event.request_id = span.attributes.get("request_id", "")
    if changed:
        persist_trace(engine)


def _usage_attributes(usage: Any) -> dict[str, Any]:
    """Store numeric provider usage only; missing cost/usage is unknown, not zero."""
    def get(name: str) -> Any:
        return usage.get(name) if isinstance(usage, dict) else getattr(usage, name, None)

    result: dict[str, Any] = {}
    for name in ("prompt_tokens", "completion_tokens", "total_tokens", "cost_usd"):
        value = get(name)
        result[name] = value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None
    from excelmanus.request.usage import extract_cache_usage

    cache = extract_cache_usage(usage)
    result.update(cached_tokens=cache.hit, cache_write_tokens=cache.write)
    return result


async def traced_request(engine: Any, create: Any, kwargs: dict[str, Any]) -> Any:
    """One actual create attempt, including stream consumption; never persist its body."""
    trace = trace_of(engine)
    if trace is None:
        return await create(**kwargs)
    try:
        parent = step_key(engine)
        previous = trace.get(getattr(engine, "_trace_last_request_key", "") or "")
        parent_span = trace.get(parent)
        attempt = (int(previous.attributes.get("attempt", 0)) + 1
                   if previous and parent_span and previous.parent_span_id == parent_span.span_id else 1)
        prepared = getattr(engine, "_sent_prepared_request", None)
        from excelmanus.request.types import PreparedRequest

        request_id = prepared.request_id if isinstance(prepared, PreparedRequest) else uuid4().hex
        route = prepared.route if isinstance(prepared, PreparedRequest) else None
        cache_info = {}
        if isinstance(prepared, PreparedRequest):
            header = prepared.header
            cache_info = {
                "prompt_cache_key": header.prompt_cache_key,
                "route_fingerprint": header.route_fingerprint,
                "provider_config_digest": header.provider_config_digest,
                "tools_digest": header.tools_digest, "system_digest": header.system_head_digest,
                "transport": header.transport, "continuation": bool(header.continuation_id),
                "model_idle_seconds": getattr(engine, "_model_idle_seconds", None),
                "idle_breakdown": getattr(engine, "_model_idle_breakdown", None),
            }
        key = scope_key(engine, f"request:{uuid4().hex}")
        driver = getattr(engine, "_driver", None)
        trace.start(key, "request", "llm", parent_key=parent, request_id=request_id,
                    series_id=prepared.series_id if isinstance(prepared, PreparedRequest) else None,
                    model=route.model if route else str(kwargs.get("model", "")),
                    protocol=route.protocol if route else None, attempt=attempt,
                    stream=bool(kwargs.get("stream")), turn_id=getattr(driver, "turn_id", ""),
                    step_id=getattr(driver, "step_id", ""), **_usage_attributes(None), **cache_info)
        engine._trace_last_request_key = key
        engine._trace_active_request_key = key
        persist_trace(engine)
    except Exception:
        # This branch runs before create: trace failures never duplicate requests.
        return await create(**kwargs)

    def finish(status: str, usage: Any = None, exc: BaseException | None = None) -> None:
        try:
            fields = _usage_attributes(usage)
            if exc is not None:
                fields["error_type"] = type(exc).__name__  # Error strings may contain credentials/prompts.
                code = getattr(exc, "status_code", None)
                fields["http_status"] = code if isinstance(code, int) else None
            trace.finish(key, status=status, **fields)
            if getattr(engine, "_trace_active_request_key", None) == key:
                engine._trace_active_request_key = None
            persist_trace(engine)
        except Exception:
            pass  # Never replace a provider result with an observation failure.

    try:
        response = await create(**kwargs)
    except BaseException as exc:
        finish("cancelled" if isinstance(exc, asyncio.CancelledError) else "error", exc=exc)
        raise
    if not hasattr(response, "__aiter__"):
        usage = response.get("usage") if isinstance(response, dict) else getattr(response, "usage", None)
        choices = getattr(response, "choices", None) or []
        truncated = bool(choices and getattr(choices[0], "finish_reason", None) == "length")
        finish("incomplete" if truncated else "ok", usage)
        return response

    async def stream():
        usage = None
        status = "incomplete"
        error = None
        truncated = False
        try:
            async for chunk in response:
                current = chunk.get("usage") if isinstance(chunk, dict) else getattr(chunk, "usage", None)
                if current is not None:
                    usage = current
                choices = getattr(chunk, "choices", None) or []
                reason = getattr(chunk, "finish_reason", None) or (getattr(choices[0], "finish_reason", None) if choices else None)
                truncated = truncated or reason == "length"
                yield chunk
            status = "incomplete" if truncated else "ok"
        except BaseException as exc:
            error = exc
            status = "cancelled" if isinstance(exc, asyncio.CancelledError) else "incomplete" if isinstance(exc, GeneratorExit) else "error"
            raise
        finally:
            try:
                close = getattr(response, "aclose", None) or getattr(response, "close", None)
                if callable(close):
                    closed = close()
                    if inspect.isawaitable(closed):
                        await closed
            except Exception:
                # Transport cleanup must not replace the original result/error.
                pass
            finally:
                finish(status, usage, error)

    return stream()
