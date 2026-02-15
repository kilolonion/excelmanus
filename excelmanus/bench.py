"""Bench 测试运行器：加载用例 JSON → 调用 engine.chat() → 收集事件轨迹 → 输出 JSON 日志。

运行方式：
    python -m excelmanus.bench --all
    python -m excelmanus.bench --suite bench/cases/suite_basic.json
    python -m excelmanus.bench bench/cases/suite_basic.json
    python -m excelmanus.bench --message "读取销售明细前10行"
    python -m excelmanus.bench "读取销售明细前10行"
"""

from __future__ import annotations

import asyncio
import argparse
import json
import os
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

from excelmanus.config import ExcelManusConfig, load_config
from excelmanus.engine import AgentEngine, ChatResult
from excelmanus.events import EventType, ToolCallEvent
from excelmanus.logger import get_logger, setup_logging
from excelmanus.renderer import StreamRenderer
from excelmanus.skillpacks import SkillpackLoader, SkillRouter
from excelmanus.tools import ToolRegistry

logger = get_logger("bench")

# 工具结果最大保留字符数（避免日志过大）
_TOOL_RESULT_MAX_CHARS = 8000

# trace 模式下系统提示最大保留字符数
_TRACE_SYSTEM_PROMPT_MAX_CHARS = 50000

# ── 数据模型 ──────────────────────────────────────────────


@dataclass
class BenchCase:
    """单个测试用例。

    支持单轮和多轮两种格式：
    - 单轮：仅设置 ``message``（向后兼容）
    - 多轮：设置 ``messages`` 列表，按顺序发送给同一个 engine 实例
    加载时会统一归一化为 ``messages`` 列表。
    """

    id: str
    name: str
    message: str = ""
    messages: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    expected: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolCallLog:
    """单次工具调用日志。"""

    tool_name: str
    arguments: dict[str, Any]
    success: bool
    result: str
    error: str | None
    iteration: int
    duration_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "arguments": self.arguments,
            "success": self.success,
            "result": self.result[:_TOOL_RESULT_MAX_CHARS] if self.result else "",
            "error": self.error,
            "iteration": self.iteration,
            "duration_ms": round(self.duration_ms, 1),
        }


@dataclass
class TurnResult:
    """多轮对话中单轮的执行结果。"""

    turn_index: int
    message: str
    reply: str
    duration_seconds: float
    iterations: int
    route_mode: str
    skills_used: list[str]
    tool_scope: list[str]
    tool_calls: list[ToolCallLog]
    thinking_log: list[str]
    subagent_events: list[dict[str, Any]]
    llm_calls: list[dict[str, Any]]
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    status: str = "ok"
    error: dict[str, Any] | None = None
    # engine 内部交互轨迹（--trace 启用时有值）
    engine_trace: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        tool_successes = sum(1 for tc in self.tool_calls if tc.success)
        tool_failures = sum(1 for tc in self.tool_calls if not tc.success)
        result = {
            "turn_index": self.turn_index,
            "message": self.message,
            "reply": self.reply,
            "duration_seconds": round(self.duration_seconds, 2),
            "iterations": self.iterations,
            "route_mode": self.route_mode,
            "skills_used": self.skills_used,
            "tool_scope": self.tool_scope,
            "tool_calls": [tc.to_dict() for tc in self.tool_calls],
            "thinking_log": self.thinking_log,
            "subagent_events": self.subagent_events,
            "llm_calls": self.llm_calls,
            "tokens": {
                "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens,
                "total_tokens": self.total_tokens,
            },
            "status": self.status,
            "error": self.error,
            "stats": {
                "tool_call_count": len(self.tool_calls),
                "tool_successes": tool_successes,
                "tool_failures": tool_failures,
                "llm_call_count": len(self.llm_calls),
            },
        }
        if self.engine_trace:
            result["engine_trace"] = self.engine_trace
        return result


@dataclass
class BenchResult:
    """单个用例的执行结果。"""

    case_id: str
    case_name: str
    message: str
    timestamp: str
    duration_seconds: float
    iterations: int
    route_mode: str
    skills_used: list[str]
    tool_scope: list[str]
    tool_calls: list[ToolCallLog]
    thinking_log: list[str]
    reply: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    # subagent 事件
    subagent_events: list[dict[str, Any]] = field(default_factory=list)
    # 完整 LLM 交互记录：每次 API 调用的请求和响应
    llm_calls: list[dict[str, Any]] = field(default_factory=list)
    # 最终对话记忆快照（system prompt + 所有消息）
    conversation_messages: list[dict[str, Any]] = field(default_factory=list)
    # 多轮对话各轮次的独立结果
    turns: list[TurnResult] = field(default_factory=list)
    # 执行状态
    status: str = "ok"
    # 结构化错误信息（status=error 时有值）
    error: dict[str, Any] | None = None
    # engine 内部交互轨迹（--trace 启用时有值）
    engine_trace: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        tool_successes = sum(1 for tc in self.tool_calls if tc.success)
        tool_failures = sum(1 for tc in self.tool_calls if not tc.success)
        result: dict[str, Any] = {
            "schema_version": 2,
            "kind": "case_result",
            "timestamp": self.timestamp,
            "meta": {
                "case_id": self.case_id,
                "case_name": self.case_name,
                "message": self.message,
                "turn_count": len(self.turns) if self.turns else 1,
            },
            "execution": {
                "duration_seconds": round(self.duration_seconds, 2),
                "iterations": self.iterations,
                "route_mode": self.route_mode,
                "skills_used": self.skills_used,
                "tool_scope": self.tool_scope,
                "status": self.status,
                "error": self.error,
            },
            "artifacts": {
                "tool_calls": [tc.to_dict() for tc in self.tool_calls],
                "thinking_log": self.thinking_log,
                "subagent_events": self.subagent_events,
                "llm_calls": self.llm_calls,
                "conversation_messages": self.conversation_messages,
            },
            "result": {
                "reply": self.reply,
            },
            "stats": {
                "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens,
                "total_tokens": self.total_tokens,
                "tool_call_count": len(self.tool_calls),
                "tool_successes": tool_successes,
                "tool_failures": tool_failures,
                "llm_call_count": len(self.llm_calls),
            },
        }
        # 多轮对话时输出各轮次详情
        if self.turns:
            result["turns"] = [t.to_dict() for t in self.turns]
        # engine 内部交互轨迹
        if self.engine_trace:
            result["engine_trace"] = self.engine_trace
        return result


# ── 事件收集器 ────────────────────────────────────────────


# 全局 Rich Console
_console = Console()


class _EventCollector:
    """通过 on_event 回调收集引擎事件，同时实时渲染到终端。"""

    def __init__(self, *, render_enabled: bool = True) -> None:
        self._renderer = StreamRenderer(_console)
        self._render_enabled = render_enabled
        self.thinking_log: list[str] = []
        self.tool_calls: list[ToolCallLog] = []
        self.route_mode: str = ""
        self.skills_used: list[str] = []
        self.tool_scope: list[str] = []
        self.subagent_events: list[dict[str, Any]] = []
        # 用于计算工具调用耗时
        self._pending_tool_starts: dict[str, float] = {}

    def on_event(self, event: ToolCallEvent) -> None:
        """引擎事件回调：实时渲染 + 收集日志。"""
        # 实时渲染到终端（和 CLI 一样的效果）
        if self._render_enabled:
            self._renderer.handle_event(event)

        # 同时收集到日志
        if event.event_type == EventType.THINKING:
            if event.thinking and event.thinking.strip():
                self.thinking_log.append(event.thinking.strip())

        elif event.event_type == EventType.TOOL_CALL_START:
            # 记录开始时间
            key = f"{event.tool_name}_{event.iteration}_{len(self.tool_calls)}"
            self._pending_tool_starts[key] = time.monotonic()

        elif event.event_type == EventType.TOOL_CALL_END:
            # 计算耗时
            key_prefix = f"{event.tool_name}_{event.iteration}_"
            duration_ms = 0.0
            for k in list(self._pending_tool_starts.keys()):
                if k.startswith(key_prefix):
                    start_time = self._pending_tool_starts.pop(k)
                    duration_ms = (time.monotonic() - start_time) * 1000
                    break

            self.tool_calls.append(ToolCallLog(
                tool_name=event.tool_name,
                arguments=dict(event.arguments) if event.arguments else {},
                success=event.success,
                result=event.result or "",
                error=event.error,
                iteration=event.iteration,
                duration_ms=duration_ms,
            ))

        elif event.event_type == EventType.ROUTE_END:
            self.route_mode = event.route_mode
            self.skills_used = list(event.skills_used)
            self.tool_scope = list(event.tool_scope)

        elif event.event_type in {
            EventType.SUBAGENT_START,
            EventType.SUBAGENT_SUMMARY,
            EventType.SUBAGENT_END,
        }:
            self.subagent_events.append({
                "event_type": event.event_type.value,
                "name": event.subagent_name,
                "reason": event.subagent_reason,
                "summary": event.subagent_summary,
                "success": event.subagent_success,
                "iterations": event.subagent_iterations,
                "tool_calls": event.subagent_tool_calls,
            })

    def snapshot_and_reset(self) -> dict[str, Any]:
        """快照当前收集的数据并重置，用于多轮对话分轮记录。

        返回本轮收集到的所有数据副本，然后清空内部状态。
        """
        snapshot = {
            "thinking_log": list(self.thinking_log),
            "tool_calls": list(self.tool_calls),
            "route_mode": self.route_mode,
            "skills_used": list(self.skills_used),
            "tool_scope": list(self.tool_scope),
            "subagent_events": list(self.subagent_events),
        }
        # 重置
        self.thinking_log = []
        self.tool_calls = []
        self.route_mode = ""
        self.skills_used = []
        self.tool_scope = []
        self.subagent_events = []
        self._pending_tool_starts.clear()
        return snapshot


# ── LLM 调用拦截器 ───────────────────────────────────────


def _serialize_message(msg: dict[str, Any]) -> dict[str, Any]:
    """将单条消息序列化为可 JSON 化的字典。"""
    out: dict[str, Any] = {}
    for key, value in msg.items():
        if value is None:
            out[key] = None
        elif isinstance(value, (str, int, float, bool)):
            out[key] = value
        elif isinstance(value, list):
            out[key] = value
        elif isinstance(value, dict):
            out[key] = value
        else:
            out[key] = str(value)
    return out


def _serialize_tool_call_obj(tc: Any) -> dict[str, Any]:
    """将 LLM 响应中的 tool_call 对象序列化。"""
    function = getattr(tc, "function", None)
    return {
        "id": getattr(tc, "id", ""),
        "type": getattr(tc, "type", "function"),
        "function": {
            "name": getattr(function, "name", "") if function else "",
            "arguments": getattr(function, "arguments", "") if function else "",
        },
    }


def _serialize_llm_response(response: Any) -> dict[str, Any]:
    """将 LLM API 完整响应序列化为可 JSON 化的字典。"""
    choice = response.choices[0] if response.choices else None
    message = choice.message if choice else None

    result: dict[str, Any] = {}

    if message is not None:
        result["content"] = message.content
        result["role"] = getattr(message, "role", "assistant")

        # 提取 thinking / reasoning 内容
        for thinking_key in ("thinking", "reasoning", "reasoning_content"):
            val = getattr(message, thinking_key, None)
            if val:
                result["thinking"] = str(val)
                break

        # 序列化 tool_calls
        if message.tool_calls:
            result["tool_calls"] = [
                _serialize_tool_call_obj(tc) for tc in message.tool_calls
            ]

    # finish_reason
    if choice is not None:
        result["finish_reason"] = getattr(choice, "finish_reason", None)

    # token 使用
    usage = getattr(response, "usage", None)
    if usage is not None:
        result["usage"] = {
            "prompt_tokens": getattr(usage, "prompt_tokens", 0),
            "completion_tokens": getattr(usage, "completion_tokens", 0),
            "total_tokens": getattr(usage, "total_tokens", 0),
        }

    return result


class _LLMCallInterceptor:
    """拦截 engine 的 LLM API 调用，记录完整的请求和响应。

    通过 monkey-patch engine._client.chat.completions.create 实现，
    无需修改 engine 源代码。
    """

    def __init__(self, engine: AgentEngine) -> None:
        self.calls: list[dict[str, Any]] = []
        self._engine = engine
        self._original_create = engine._client.chat.completions.create
        # monkey-patch
        engine._client.chat.completions.create = self._intercepted_create

    async def _intercepted_create(self, **kwargs: Any) -> Any:
        """拦截 LLM API 调用，记录请求和响应。"""
        call_record: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "request": {
                "model": kwargs.get("model"),
                "messages": [
                    _serialize_message(m) for m in kwargs.get("messages", [])
                ],
            },
        }

        # 记录 tools 定义（仅名称列表，完整 schema 太大）
        tools = kwargs.get("tools")
        if tools:
            call_record["request"]["tool_names"] = [
                t.get("function", {}).get("name", "")
                for t in tools
                if isinstance(t, dict)
            ]

        start = time.monotonic()
        try:
            response = await self._original_create(**kwargs)
        except Exception as exc:
            call_record["error"] = str(exc)
            call_record["duration_ms"] = round(
                (time.monotonic() - start) * 1000, 1
            )
            self.calls.append(call_record)
            raise

        call_record["duration_ms"] = round(
            (time.monotonic() - start) * 1000, 1
        )
        call_record["response"] = _serialize_llm_response(response)
        self.calls.append(call_record)
        return response

    def restore(self) -> None:
        """恢复原始的 create 方法。"""
        self._engine._client.chat.completions.create = self._original_create


class _EngineTracer:
    """拦截 engine 关键方法，记录程序向 agent 注入的指令和内部决策。

    通过 monkey-patch 以下方法实现：
    - ``_prepare_system_prompts_for_request`` → 记录每轮注入的系统提示（分解各组件）
    - ``_enrich_tool_result_with_window_perception`` → 记录窗口感知增强前后对比
    - ``_get_current_tool_scope`` → 记录每轮工具范围决策

    通过环境变量 ``EXCELMANUS_BENCH_TRACE=1`` 或 CLI ``--trace`` 启用。
    """

    def __init__(self, engine: AgentEngine) -> None:
        self.entries: list[dict[str, Any]] = []
        self._engine = engine
        self._iteration = 0

        # 保存原始方法
        self._orig_prepare = engine._prepare_system_prompts_for_request
        self._orig_enrich = engine._enrich_tool_result_with_window_perception
        self._orig_scope = engine._get_current_tool_scope

        # monkey-patch
        engine._prepare_system_prompts_for_request = self._traced_prepare  # type: ignore[assignment]
        engine._enrich_tool_result_with_window_perception = self._traced_enrich  # type: ignore[assignment]
        engine._get_current_tool_scope = self._traced_scope  # type: ignore[assignment]

    def _traced_prepare(
        self, skill_contexts: list[str],
    ) -> tuple[list[str], str | None]:
        """拦截系统提示构建，记录各组件内容。"""
        self._iteration += 1
        prompts, error = self._orig_prepare(skill_contexts)

        # 分解记录各组件
        components: list[dict[str, Any]] = []
        for idx, prompt in enumerate(prompts):
            label = "base_system_prompt" if idx == 0 else f"context_{idx}"
            # 尝试识别组件类型
            if idx > 0:
                snippet = prompt[:200]
                if "窗口感知" in snippet or "Window Perception" in snippet:
                    label = "window_perception_notice"
                elif "权限提示" in snippet or "fullAccess" in snippet:
                    label = "access_notice"
                elif "MCP" in snippet:
                    label = "mcp_context_notice"
                elif "Hook" in snippet:
                    label = "hook_context"
                elif "计划" in snippet and "已批准" in snippet:
                    label = "approved_plan_context"
                else:
                    label = f"skill_context_{idx}"
            components.append({
                "label": label,
                "char_count": len(prompt),
                "content": prompt[:_TRACE_SYSTEM_PROMPT_MAX_CHARS],
                "truncated": len(prompt) > _TRACE_SYSTEM_PROMPT_MAX_CHARS,
            })

        self.entries.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": "system_prompts_injected",
            "iteration": self._iteration,
            "data": {
                "prompt_count": len(prompts),
                "total_chars": sum(len(p) for p in prompts),
                "skill_context_count": len(skill_contexts),
                "context_error": error,
                "components": components,
            },
        })
        return prompts, error

    def _traced_enrich(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        result_text: str,
        success: bool,
    ) -> str:
        """拦截窗口感知增强，记录前后对比。"""
        enriched = self._orig_enrich(
            tool_name=tool_name,
            arguments=arguments,
            result_text=result_text,
            success=success,
        )
        # 仅在内容实际被增强时记录
        if enriched != result_text:
            self.entries.append({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "event": "window_perception_enrichment",
                "iteration": self._iteration,
                "data": {
                    "tool_name": tool_name,
                    "original_chars": len(result_text),
                    "enriched_chars": len(enriched),
                    "added_chars": len(enriched) - len(result_text),
                    "enriched_suffix": enriched[len(result_text):][:2000],
                },
            })
        return enriched

    def _traced_scope(self, **kwargs: Any) -> list[str]:
        """拦截工具范围决策，记录可用工具列表。"""
        scope = self._orig_scope(**kwargs)
        self.entries.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": "tool_scope_resolved",
            "iteration": self._iteration,
            "data": {
                "tool_count": len(scope),
                "tools": list(scope),
            },
        })
        return scope

    def snapshot_and_reset(self) -> list[dict[str, Any]]:
        """快照当前 trace 数据并重置，用于多轮分轮记录。"""
        snapshot = list(self.entries)
        self.entries = []
        self._iteration = 0
        return snapshot

    def restore(self) -> None:
        """恢复原始方法。"""
        self._engine._prepare_system_prompts_for_request = self._orig_prepare  # type: ignore[assignment]
        self._engine._enrich_tool_result_with_window_perception = self._orig_enrich  # type: ignore[assignment]
        self._engine._get_current_tool_scope = self._orig_scope  # type: ignore[assignment]


# ── 执行器 ────────────────────────────────────────────────


def _create_engine(config: ExcelManusConfig) -> AgentEngine:
    """创建独立的 AgentEngine 实例（不复用 session）。

    自动启用 bench sandbox 模式，解除所有交互式阻塞
    （fullAccess / plan 拦截 / 确认门禁）。
    """
    registry = ToolRegistry()
    registry.register_builtin_tools(config.workspace_root)
    loader = SkillpackLoader(config, registry)
    loader.load_all()
    router = SkillRouter(config, loader)
    engine = AgentEngine(
        config=config,
        registry=registry,
        skill_router=router,
    )
    engine.enable_bench_sandbox()
    return engine


def _dump_conversation_messages(engine: AgentEngine) -> list[dict[str, Any]]:
    """从 engine 的 memory 中导出完整对话消息快照。"""
    try:
        messages = engine._memory.get_messages()
        return [_serialize_message(m) for m in messages]
    except Exception:
        return []


async def run_case(
    case: BenchCase,
    config: ExcelManusConfig,
    *,
    render_enabled: bool = True,
    trace_enabled: bool = False,
) -> BenchResult:
    """执行单个测试用例，返回完整结果（含完整 LLM 交互日志）。

    支持多轮对话：当 case.messages 包含多条消息时，依次发送给同一个
    engine 实例，ConversationMemory 自然保持上下文。

    Args:
        trace_enabled: 启用 engine 内部交互轨迹记录（系统提示注入、
            窗口感知增强、工具范围决策等）。通过 ``--trace`` 或
            ``EXCELMANUS_BENCH_TRACE=1`` 启用。
    """
    engine = _create_engine(config)
    collector = _EventCollector(render_enabled=render_enabled)
    interceptor = _LLMCallInterceptor(engine)
    tracer = _EngineTracer(engine) if trace_enabled else None
    timestamp = datetime.now(timezone.utc).isoformat()

    # 归一化消息列表：兼容单轮 message 和多轮 messages
    messages = case.messages if case.messages else [case.message]
    is_multi_turn = len(messages) > 1

    logger.info(
        "▶ 开始执行用例: %s (%s) [%d 轮]",
        case.id, case.name, len(messages),
    )
    case_start = time.monotonic()

    # 累计统计
    all_turns: list[TurnResult] = []
    all_tool_calls: list[ToolCallLog] = []
    all_thinking_log: list[str] = []
    all_subagent_events: list[dict[str, Any]] = []
    total_iterations = 0
    total_prompt_tokens = 0
    total_completion_tokens = 0
    total_total_tokens = 0
    last_reply = ""
    last_route_mode = ""
    last_skills_used: list[str] = []
    last_tool_scope: list[str] = []
    case_status = "ok"
    case_error: dict[str, Any] | None = None

    try:
        for turn_idx, msg in enumerate(messages):
            if is_multi_turn:
                logger.info(
                    "  ── 轮次 %d/%d ──", turn_idx + 1, len(messages),
                )

            # 每轮开始前记录 interceptor 的调用数，用于切分本轮的 llm_calls
            llm_calls_before = len(interceptor.calls)
            turn_start = time.monotonic()

            try:
                chat_result: ChatResult = await engine.chat(
                    msg,
                    on_event=collector.on_event,
                )
            except Exception as exc:
                logger.error(
                    "用例 %s 轮次 %d 执行异常: %s",
                    case.id, turn_idx, exc, exc_info=True,
                )
                turn_elapsed = time.monotonic() - turn_start
                # 快照本轮收集器数据
                snap = collector.snapshot_and_reset()
                turn_llm_calls = list(interceptor.calls[llm_calls_before:])

                turn_result = TurnResult(
                    turn_index=turn_idx,
                    message=msg,
                    reply=f"[ERROR] {exc}",
                    duration_seconds=turn_elapsed,
                    iterations=0,
                    route_mode=snap["route_mode"] or "error",
                    skills_used=snap["skills_used"],
                    tool_scope=snap["tool_scope"],
                    tool_calls=snap["tool_calls"],
                    thinking_log=snap["thinking_log"],
                    subagent_events=snap["subagent_events"],
                    llm_calls=turn_llm_calls,
                    status="error",
                    error={
                        "type": type(exc).__name__,
                        "message": str(exc),
                    },
                    engine_trace=tracer.snapshot_and_reset() if tracer else [],
                )
                all_turns.append(turn_result)
                all_tool_calls.extend(snap["tool_calls"])
                all_thinking_log.extend(snap["thinking_log"])
                all_subagent_events.extend(snap["subagent_events"])
                last_reply = f"[ERROR] {exc}"
                case_status = "error"
                case_error = {
                    "type": type(exc).__name__,
                    "message": str(exc),
                }
                # 某轮异常后中止后续轮次
                break

            turn_elapsed = time.monotonic() - turn_start
            # 快照本轮收集器数据
            snap = collector.snapshot_and_reset()
            turn_llm_calls = list(interceptor.calls[llm_calls_before:])

            # 回退路由信息
            last_route = getattr(engine, "last_route_result", None)
            fallback_route_mode = getattr(last_route, "route_mode", "")
            fallback_skills = list(getattr(last_route, "skills_used", []) or [])
            fallback_scope = list(getattr(last_route, "tool_scope", []) or [])

            turn_route_mode = snap["route_mode"] or fallback_route_mode or "unknown"
            turn_skills = snap["skills_used"] or fallback_skills
            turn_scope = snap["tool_scope"] or fallback_scope

            turn_result = TurnResult(
                turn_index=turn_idx,
                message=msg,
                reply=chat_result.reply,
                duration_seconds=turn_elapsed,
                iterations=chat_result.iterations,
                route_mode=turn_route_mode,
                skills_used=turn_skills,
                tool_scope=turn_scope,
                tool_calls=snap["tool_calls"],
                thinking_log=snap["thinking_log"],
                subagent_events=snap["subagent_events"],
                llm_calls=turn_llm_calls,
                prompt_tokens=chat_result.prompt_tokens,
                completion_tokens=chat_result.completion_tokens,
                total_tokens=chat_result.total_tokens,
                engine_trace=tracer.snapshot_and_reset() if tracer else [],
            )
            all_turns.append(turn_result)

            # 累计
            all_tool_calls.extend(snap["tool_calls"])
            all_thinking_log.extend(snap["thinking_log"])
            all_subagent_events.extend(snap["subagent_events"])
            total_iterations += chat_result.iterations
            total_prompt_tokens += chat_result.prompt_tokens
            total_completion_tokens += chat_result.completion_tokens
            total_total_tokens += chat_result.total_tokens
            last_reply = chat_result.reply
            last_route_mode = turn_route_mode
            last_skills_used = turn_skills
            last_tool_scope = turn_scope

            # 多轮时打印每轮回复
            if render_enabled and is_multi_turn and chat_result.reply:
                _console.print()
                _console.print(
                    Panel(
                        Markdown(chat_result.reply),
                        title=f"轮次 {turn_idx + 1}/{len(messages)}",
                        border_style="#5f875f",
                        padding=(1, 2),
                        expand=False,
                    )
                )
    finally:
        interceptor.restore()
        if tracer is not None:
            tracer.restore()

    case_elapsed = time.monotonic() - case_start

    # 单轮用例的 engine_trace 直接从 tracer 获取（多轮已在各 turn 中记录）
    case_engine_trace: list[dict[str, Any]] = []
    if tracer is not None and not is_multi_turn:
        case_engine_trace = tracer.snapshot_and_reset()

    result = BenchResult(
        case_id=case.id,
        case_name=case.name,
        message=case.message or (messages[0] if messages else ""),
        timestamp=timestamp,
        duration_seconds=case_elapsed,
        iterations=total_iterations,
        route_mode=last_route_mode or "unknown",
        skills_used=last_skills_used,
        tool_scope=last_tool_scope,
        tool_calls=all_tool_calls,
        thinking_log=all_thinking_log,
        reply=last_reply,
        prompt_tokens=total_prompt_tokens,
        completion_tokens=total_completion_tokens,
        total_tokens=total_total_tokens,
        subagent_events=all_subagent_events,
        llm_calls=interceptor.calls,
        conversation_messages=_dump_conversation_messages(engine),
        turns=all_turns if is_multi_turn else [],
        status=case_status,
        error=case_error,
        engine_trace=case_engine_trace,
    )

    # 单轮时打印最终回复（多轮已在循环中逐轮打印）
    if render_enabled and not is_multi_turn and result.reply:
        _console.print()
        _console.print(
            Panel(
                Markdown(result.reply),
                border_style="#5f875f",
                padding=(1, 2),
                expand=False,
            )
        )

    failures = sum(1 for tc in result.tool_calls if not tc.success)
    turn_info = f" ({len(messages)} 轮)" if is_multi_turn else ""
    logger.info(
        "✓ 用例 %s 完成%s: %d 迭代 │ %d 工具调用(失败%d) │ %d tokens │ %.1fs │ %d 次 LLM 调用",
        case.id,
        turn_info,
        result.iterations,
        len(result.tool_calls),
        failures,
        result.total_tokens,
        result.duration_seconds,
        len(result.llm_calls),
    )
    return result


def _load_suite(path: str | Path) -> tuple[str, list[BenchCase]]:
    """从 JSON 文件加载测试套件。

    兼容两种 case 格式：
    - 单轮：``{"message": "..."}``
    - 多轮：``{"messages": ["...", "..."]}``
    加载时统一归一化为 messages 列表。
    """
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    suite_name = data.get("suite_name", Path(path).stem)
    cases: list[BenchCase] = []
    for item in data.get("cases", []):
        # 多轮格式优先
        raw_messages = item.get("messages")
        raw_message = item.get("message", "")
        if raw_messages and isinstance(raw_messages, list):
            messages = [str(m) for m in raw_messages if m]
            message = messages[0] if messages else ""
        else:
            message = raw_message
            messages = [message] if message else []

        cases.append(BenchCase(
            id=item["id"],
            name=item.get("name", item["id"]),
            message=message,
            messages=messages,
            tags=item.get("tags", []),
            expected=item.get("expected", {}),
        ))
    return suite_name, cases


def _save_result(result: BenchResult, output_dir: Path) -> Path:
    """保存单个用例结果到 JSON 文件。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    short_id = uuid.uuid4().hex[:6]
    filename = f"run_{ts}_{result.case_id}_{short_id}.json"
    filepath = output_dir / filename
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(result.to_dict(), f, ensure_ascii=False, indent=2)
    return filepath


def _save_suite_summary(
    suite_name: str,
    suite_path: str | Path,
    results: list[BenchResult],
    output_dir: Path,
    *,
    concurrency: int,
    case_log_files: list[Path],
) -> Path:
    """保存套件汇总结果。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    short_id = uuid.uuid4().hex[:6]
    filename = f"suite_{ts}_{short_id}.json"
    filepath = output_dir / filename

    total_tokens = sum(r.total_tokens for r in results)
    total_duration = sum(r.duration_seconds for r in results)
    avg_iterations = (
        sum(r.iterations for r in results) / len(results) if results else 0
    )
    total_prompt_tokens = sum(r.prompt_tokens for r in results)
    total_completion_tokens = sum(r.completion_tokens for r in results)
    total_tool_calls = sum(len(r.tool_calls) for r in results)
    total_tool_failures = sum(
        sum(1 for tc in r.tool_calls if not tc.success) for r in results
    )
    failed_case_ids = [r.case_id for r in results if r.status != "ok"]
    suite_status = "ok" if not failed_case_ids else "completed_with_errors"

    summary = {
        "schema_version": 2,
        "kind": "suite_summary",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "meta": {
            "suite_name": suite_name,
            "suite_path": str(suite_path),
            "case_count": len(results),
        },
        "execution": {
            "concurrency": concurrency,
            "status": suite_status,
        },
        "artifacts": {
            "case_log_files": [str(p) for p in case_log_files],
            "cases": [r.to_dict() for r in results],
        },
        "result": {
            "failed_case_ids": failed_case_ids,
        },
        "stats": {
            "total_tokens": total_tokens,
            "total_prompt_tokens": total_prompt_tokens,
            "total_completion_tokens": total_completion_tokens,
            "total_duration_seconds": round(total_duration, 2),
            "average_iterations": round(avg_iterations, 2),
            "tool_call_count": total_tool_calls,
            "tool_failures": total_tool_failures,
        },
    }

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    return filepath


async def run_suite(
    suite_path: str | Path,
    config: ExcelManusConfig,
    output_dir: Path,
    *,
    concurrency: int = 1,
    trace_enabled: bool = False,
) -> list[BenchResult]:
    """运行整个测试套件。"""
    if concurrency < 1:
        raise ValueError("concurrency 必须 >= 1")

    suite_name, cases = _load_suite(suite_path)
    logger.info("═" * 50)
    logger.info(
        "开始执行套件: %s (%d 个用例, 并发=%d%s)",
        suite_name,
        len(cases),
        concurrency,
        ", trace=ON" if trace_enabled else "",
    )
    logger.info("═" * 50)

    async def _execute_case(
        index: int,
        case: BenchCase,
        *,
        render_enabled: bool,
    ) -> tuple[int, BenchResult, Path]:
        try:
            result = await run_case(
                case, config,
                render_enabled=render_enabled,
                trace_enabled=trace_enabled,
            )
        except Exception as exc:  # pragma: no cover - 兜底保护
            logger.error("用例 %s 执行崩溃: %s", case.id, exc, exc_info=True)
            result = BenchResult(
                case_id=case.id,
                case_name=case.name,
                message=case.message,
                timestamp=datetime.now(timezone.utc).isoformat(),
                duration_seconds=0.0,
                iterations=0,
                route_mode="error",
                skills_used=[],
                tool_scope=[],
                tool_calls=[],
                thinking_log=[],
                reply=f"[CRASH] {exc}",
                prompt_tokens=0,
                completion_tokens=0,
                total_tokens=0,
                status="error",
                error={
                    "type": type(exc).__name__,
                    "message": str(exc),
                },
            )
        try:
            filepath = _save_result(result, output_dir)
            logger.info("  日志已保存: %s", filepath)
        except Exception as exc:  # pragma: no cover - 文件系统异常兜底
            logger.error("用例 %s 日志保存失败: %s", case.id, exc, exc_info=True)
            filepath = output_dir / f"run_save_error_{case.id}.json"
        return index, result, filepath

    results: list[BenchResult | None] = [None] * len(cases)
    case_log_files: list[Path | None] = [None] * len(cases)

    if concurrency == 1:
        for i, case in enumerate(cases, 1):
            logger.info("── 用例 %d/%d ──", i, len(cases))
            index, result, filepath = await _execute_case(
                i - 1,
                case,
                render_enabled=True,
            )
            results[index] = result
            case_log_files[index] = filepath
    else:
        logger.info("并发模式已启用：关闭逐事件终端渲染，避免输出交错。")
        semaphore = asyncio.Semaphore(concurrency)

        async def _worker(index: int, case: BenchCase) -> tuple[int, BenchResult, Path]:
            async with semaphore:
                logger.info("── 用例 %d/%d (并发) ──", index + 1, len(cases))
                return await _execute_case(
                    index,
                    case,
                    render_enabled=False,
                )

        tasks = [
            asyncio.create_task(_worker(index, case))
            for index, case in enumerate(cases)
        ]
        for index, result, filepath in await asyncio.gather(*tasks):
            results[index] = result
            case_log_files[index] = filepath

    # 理论上 results 不会为 None，此处加兜底保证类型稳定
    normalized_results: list[BenchResult] = []
    normalized_case_files: list[Path] = []
    for index, case in enumerate(cases):
        current = results[index]
        if current is None:  # pragma: no cover - 防御性逻辑
            current = BenchResult(
                case_id=case.id,
                case_name=case.name,
                message=case.message,
                timestamp=datetime.now(timezone.utc).isoformat(),
                duration_seconds=0.0,
                iterations=0,
                route_mode="error",
                skills_used=[],
                tool_scope=[],
                tool_calls=[],
                thinking_log=[],
                reply="[CRASH] case result missing",
                prompt_tokens=0,
                completion_tokens=0,
                total_tokens=0,
                status="error",
                error={
                    "type": "InternalError",
                    "message": "missing case result",
                },
            )
        normalized_results.append(current)
        if case_log_files[index] is not None:
            normalized_case_files.append(case_log_files[index])

    # 保存套件汇总
    summary_path = _save_suite_summary(
        suite_name,
        suite_path,
        normalized_results,
        output_dir,
        concurrency=concurrency,
        case_log_files=normalized_case_files,
    )
    logger.info("═" * 50)
    logger.info("套件执行完毕: %s", suite_name)
    logger.info("  汇总日志: %s", summary_path)

    # 打印简要统计
    total_tokens = sum(r.total_tokens for r in normalized_results)
    total_duration = sum(r.duration_seconds for r in normalized_results)
    total_failures = sum(
        sum(1 for tc in r.tool_calls if not tc.success) for r in normalized_results
    )
    case_errors = sum(
        1 for r in normalized_results if r.status != "ok"
    )
    logger.info(
        "  统计: %d 用例 │ 总 %d tokens │ 总 %.1fs │ 工具失败 %d 次 │ 用例失败 %d",
        len(normalized_results),
        total_tokens,
        total_duration,
        total_failures,
        case_errors,
    )
    logger.info("═" * 50)
    return normalized_results


# ── 入口 ──────────────────────────────────────────────────


async def run_single(
    message: str,
    config: ExcelManusConfig,
    output_dir: Path,
    *,
    trace_enabled: bool = False,
) -> BenchResult:
    """直接运行一条用户消息作为测试用例。"""
    case = BenchCase(
        id="adhoc",
        name="临时用例",
        message=message,
        messages=[message],
    )
    result = await run_case(
        case, config,
        render_enabled=True,
        trace_enabled=trace_enabled,
    )
    filepath = _save_result(result, output_dir)
    logger.info("日志已保存: %s", filepath)
    return result


@dataclass
class _RunPlan:
    """bench CLI 解析后的执行计划。"""

    mode: str
    suite_paths: list[Path] = field(default_factory=list)
    message: str = ""


def _positive_int(raw: str) -> int:
    """argparse 使用的正整数解析器。"""
    try:
        value = int(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("必须是整数") from exc
    if value < 1:
        raise argparse.ArgumentTypeError("必须是 >= 1 的整数")
    return value


def _build_parser() -> argparse.ArgumentParser:
    """构建 bench CLI 参数解析器。"""
    parser = argparse.ArgumentParser(
        prog="python -m excelmanus.bench",
        description="Bench 测试运行器",
    )
    parser.add_argument(
        "targets",
        nargs="*",
        help="位置参数：智能识别为 suite 路径（*.json）或 message 文本",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--suite",
        nargs="+",
        metavar="PATH",
        help="显式指定一个或多个 suite JSON 文件",
    )
    group.add_argument(
        "--all",
        action="store_true",
        help="运行 bench/cases/ 下所有 suite",
    )
    group.add_argument(
        "--message",
        help="显式指定单条消息作为用例",
    )
    parser.add_argument(
        "--concurrency",
        type=_positive_int,
        default=1,
        help="单个 suite 内用例并发度（默认 1）",
    )
    parser.add_argument(
        "--suite-concurrency",
        type=_positive_int,
        default=1,
        help="suite 间并发度（默认 1，仅多 suite 时生效）",
    )
    parser.add_argument(
        "--output-dir",
        default="outputs/bench",
        help="日志输出目录（默认 outputs/bench）",
    )
    parser.add_argument(
        "--trace",
        action="store_true",
        default=False,
        help="启用 engine 内部交互轨迹记录（系统提示注入、窗口感知增强、工具范围等）。"
        "也可通过 EXCELMANUS_BENCH_TRACE=1 环境变量启用。",
    )
    return parser


def _is_json_like_path(raw: str) -> bool:
    """判断参数是否满足 suite 文件路径语义。"""
    return raw.lower().endswith(".json")


def _resolve_run_mode(args: argparse.Namespace) -> _RunPlan:
    """将 argparse 结果映射为执行计划。"""
    targets = list(args.targets or [])

    if args.suite is not None:
        if targets:
            raise ValueError("使用 --suite 时不应再传入位置参数。")
        return _RunPlan(
            mode="suite",
            suite_paths=[Path(p) for p in args.suite],
        )

    if args.all:
        if targets:
            raise ValueError("使用 --all 时不应再传入位置参数。")
        return _RunPlan(mode="all")

    if args.message is not None:
        if targets:
            raise ValueError("使用 --message 时不应再传入位置参数。")
        return _RunPlan(mode="message", message=args.message)

    if not targets:
        return _RunPlan(mode="help")

    # 智能识别：全部看起来像 *.json 时，视为 suite 模式；否则按 message 模式。
    if all(_is_json_like_path(item) for item in targets):
        return _RunPlan(
            mode="suite",
            suite_paths=[Path(item) for item in targets],
        )

    return _RunPlan(mode="message", message=" ".join(targets))


async def _run_suites(
    suite_paths: list[Path],
    config: ExcelManusConfig,
    output_dir: Path,
    *,
    concurrency: int = 1,
    suite_concurrency: int = 1,
    trace_enabled: bool = False,
) -> int:
    """并发运行多个 suite，带 Rich Live 进度面板和全局汇总。

    返回 shell 退出码（0 = 全部成功，1 = 存在失败）。
    """
    total_suites = len(suite_paths)

    # 每个 suite 的状态追踪
    suite_states: dict[str, str] = {}  # suite_name -> 状态文本
    suite_results: list[tuple[str, list[BenchResult]]] = []
    results_lock = asyncio.Lock()

    # 用 suite 文件名做简短标识
    def _short_name(p: Path) -> str:
        return p.stem

    for p in suite_paths:
        suite_states[_short_name(p)] = "⏳ 等待中"

    def _build_progress_table() -> Table:
        """构建实时进度表格。"""
        table = Table(
            title="Bench 并发执行面板",
            show_lines=True,
            expand=False,
        )
        table.add_column("Suite", style="cyan", min_width=25)
        table.add_column("状态", min_width=20)

        for name, status in suite_states.items():
            table.add_row(name, status)
        return table

    async def _suite_worker(
        suite_path: Path,
        sem: asyncio.Semaphore,
    ) -> None:
        name = _short_name(suite_path)
        async with sem:
            suite_states[name] = "🔄 执行中..."
            try:
                results = await run_suite(
                    suite_path,
                    config,
                    output_dir,
                    concurrency=concurrency,
                    trace_enabled=trace_enabled,
                )
                ok_count = sum(1 for r in results if r.status == "ok")
                fail_count = len(results) - ok_count
                if fail_count:
                    suite_states[name] = f"⚠️  完成 ({ok_count}✅ {fail_count}❌)"
                else:
                    suite_states[name] = f"✅ 完成 ({ok_count} 用例)"
                async with results_lock:
                    suite_results.append((name, results))
            except Exception as exc:
                suite_states[name] = f"💥 崩溃: {exc}"
                async with results_lock:
                    suite_results.append((name, []))

    sem = asyncio.Semaphore(suite_concurrency)
    is_parallel = suite_concurrency > 1 and total_suites > 1

    if is_parallel:
        # 并发模式：启动 Live 进度面板
        logger.info(
            "启动并发模式：%d 个 suite，suite 并发=%d，case 并发=%d",
            total_suites,
            suite_concurrency,
            concurrency,
        )
        tasks = [
            asyncio.create_task(_suite_worker(p, sem))
            for p in suite_paths
        ]

        console = Console()
        with Live(
            _build_progress_table(),
            console=console,
            refresh_per_second=2,
        ) as live:
            # 定期刷新进度表
            while not all(t.done() for t in tasks):
                live.update(_build_progress_table())
                await asyncio.sleep(0.5)
            # 最终刷新一次
            live.update(_build_progress_table())

        # 收集异常（不应发生，_suite_worker 内部已兜底）
        for t in tasks:
            if t.exception():  # pragma: no cover
                logger.error("suite 任务异常: %s", t.exception())
    else:
        # 串行模式：逐个执行，保持原有日志输出
        for suite_path in suite_paths:
            await _suite_worker(suite_path, sem)

    # ── 全局汇总报告 ──
    all_results = [r for _, results in suite_results for r in results]
    if all_results:
        total_cases = len(all_results)
        total_ok = sum(1 for r in all_results if r.status == "ok")
        total_fail = total_cases - total_ok
        total_tokens = sum(r.total_tokens for r in all_results)
        total_duration = sum(r.duration_seconds for r in all_results)
        total_tool_failures = sum(
            sum(1 for tc in r.tool_calls if not tc.success) for r in all_results
        )

        # 保存全局汇总 JSON
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        short_id = uuid.uuid4().hex[:6]
        global_summary = {
            "schema_version": 2,
            "kind": "global_summary",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "execution": {
                "suite_count": total_suites,
                "suite_concurrency": suite_concurrency,
                "case_concurrency": concurrency,
            },
            "stats": {
                "total_cases": total_cases,
                "passed": total_ok,
                "failed": total_fail,
                "total_tokens": total_tokens,
                "total_duration_seconds": round(total_duration, 2),
                "tool_failures": total_tool_failures,
            },
            "suites": [
                {
                    "name": name,
                    "case_count": len(results),
                    "passed": sum(1 for r in results if r.status == "ok"),
                    "failed": sum(1 for r in results if r.status != "ok"),
                }
                for name, results in suite_results
            ],
        }
        output_dir.mkdir(parents=True, exist_ok=True)
        global_path = output_dir / f"global_{ts}_{short_id}.json"
        with open(global_path, "w", encoding="utf-8") as f:
            json.dump(global_summary, f, ensure_ascii=False, indent=2)

        logger.info("═" * 60)
        logger.info("全局汇总")
        logger.info("═" * 60)
        logger.info(
            "  %d 个 suite │ %d 用例 │ %d 通过 │ %d 失败",
            total_suites,
            total_cases,
            total_ok,
            total_fail,
        )
        logger.info(
            "  总 %d tokens │ 总 %.1fs │ 工具失败 %d 次",
            total_tokens,
            total_duration,
            total_tool_failures,
        )
        logger.info("  全局汇总: %s", global_path)
        logger.info("═" * 60)

    return 0 if all(r.status == "ok" for r in all_results) else 1


async def _main(argv: list[str] | None = None) -> int:
    """脚本入口，返回 shell 退出码。"""
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        plan = _resolve_run_mode(args)
    except ValueError as exc:
        print(f"参数错误：{exc}", file=sys.stderr)
        return 1

    if plan.mode == "help":
        parser.print_help()
        return 0

    # trace 模式：CLI --trace 或环境变量 EXCELMANUS_BENCH_TRACE=1
    trace_enabled = args.trace or os.environ.get("EXCELMANUS_BENCH_TRACE", "0") == "1"

    if plan.mode == "message":
        config = load_config()
        setup_logging(config.log_level)
        output_dir = Path(args.output_dir)
        await run_single(plan.message, config, output_dir, trace_enabled=trace_enabled)
        return 0

    if plan.mode == "suite":
        missing_paths = [p for p in plan.suite_paths if not p.is_file()]
        if missing_paths:
            for p in missing_paths:
                logger.error("未找到 suite 文件: %s", p)
            return 1
        config = load_config()
        setup_logging(config.log_level)
        output_dir = Path(args.output_dir)
        return await _run_suites(
            plan.suite_paths,
            config,
            output_dir,
            concurrency=args.concurrency,
            suite_concurrency=args.suite_concurrency,
            trace_enabled=trace_enabled,
        )

    # all 模式
    cases_dir = Path("bench/cases")
    if not cases_dir.is_dir():
        logger.error("未找到测试用例目录: %s", cases_dir)
        return 1

    suite_paths = sorted(cases_dir.glob("*.json"))
    if not suite_paths:
        logger.error("目录 %s 下无 JSON 用例文件", cases_dir)
        return 1

    config = load_config()
    setup_logging(config.log_level)
    output_dir = Path(args.output_dir)
    return await _run_suites(
        suite_paths,
        config,
        output_dir,
        concurrency=args.concurrency,
        suite_concurrency=args.suite_concurrency,
        trace_enabled=trace_enabled,
    )


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
