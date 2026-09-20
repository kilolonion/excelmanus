"""Bench 测试运行器：加载用例 JSON → 走与前端直聊相同的会话链路 → 输出 JSON 日志。

每轮与网页聊天一致：SessionManager.acquire_for_chat → 提及解析 →
``engine.followup(..., mention_contexts, chat_mode)`` →
release_for_chat。问答/审批走 InteractionRegistry（同 ``/answer`` ``/approve``）。

运行方式：
    python -m excelmanus.bench --all
    python -m excelmanus.bench --suite bench/cases/suite_smoke.json
    python -m excelmanus.bench --suite bench/cases/suite_experiential.json
    python -m excelmanus.bench --message "读取销售明细前10行"
    python -m excelmanus.bench "读取销售明细前10行"
"""

from __future__ import annotations

import asyncio
import argparse
import json
import os
import shutil
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

from excelmanus.bench_validator import (
    ValidationSummary,
    aggregate_suite_validation,
    merge_assertions,
    validate_case,
)
from excelmanus.bench_reporter import save_suite_report
from excelmanus.chat_runtime import ChatRuntime, build_chat_runtime
from excelmanus.config import ConfigError, ExcelManusConfig, load_config
from excelmanus.engine import AgentEngine, ChatResult
from excelmanus.events import EventType, ToolCallEvent
from excelmanus.fake_frontend import FakeFrontend
from excelmanus.logger import get_logger, setup_logging
from excelmanus.renderer import StreamRenderer
from excelmanus.session import SessionManager
from excelmanus.stores.workspace_store import WorkspacePathError

logger = get_logger("bench")

# 工具结果最大保留字符数（避免日志过大）
_TOOL_RESULT_MAX_CHARS = 8000


class TurnTimeoutError(RuntimeError):
    """单轮超出硬超时被中止（防止无人值守卡死）。"""

# trace 模式下系统提示最大保留字符数
_TRACE_SYSTEM_PROMPT_MAX_CHARS = 50000

# ── 数据模型 ──────────────────────────────────────────────


@dataclass
class BenchTurn:
    """伪造前端的一轮输入。"""

    text: str
    attachments: list[str] = field(default_factory=list)
    images: list[str] = field(default_factory=list)


@dataclass
class BenchCase:
    """单个测试用例。

    支持单轮和多轮：
    - 单轮：``message``
    - 多轮：``messages``（字符串或 ``{text, attachments, images}``）
    附件按前端上传，不走评分。
    """

    id: str
    name: str
    message: str = ""
    messages: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    expected: dict[str, Any] = field(default_factory=dict)
    # 声明式断言规则（suite 级默认 + case 级覆盖，加载时已合并）
    assertions: dict[str, Any] = field(default_factory=dict)
    source_files: list[str] = field(default_factory=list)
    attachments: list[str] = field(default_factory=list)
    images: list[str] = field(default_factory=list)
    auto_replies: list[str] = field(default_factory=list)
    chat_mode: str = "write"
    auto_approve: str = "fullaccess"
    # 单轮硬超时（秒），0 = 不限制；防止审批/网络挂起导致无人值守卡死
    turn_timeout: float = 0.0
    turns: list[BenchTurn] = field(default_factory=list)


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
    # 非空表示 run_code 内层 SDK 调用（parent 为外层 run_code 的 call_id）
    parent_call_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "arguments": self.arguments,
            "success": self.success,
            "result": self.result[:_TOOL_RESULT_MAX_CHARS] if self.result else "",
            "error": self.error,
            "iteration": self.iteration,
            "duration_ms": round(self.duration_ms, 1),
            "parent_call_id": self.parent_call_id,
        }


def _tc_get(tc: Any, name: str, default: Any = "") -> Any:
    if isinstance(tc, dict):
        return tc.get(name, default)
    return getattr(tc, name, default)


def normalize_tool_error_message(text: str) -> str:
    """把 traceback / HostToolError / JSON 错误收成同一条 root message。"""
    raw = (text or "").strip()
    if not raw:
        return ""
    if raw.startswith("{") or raw.startswith("["):
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, TypeError, ValueError):
            payload = None
        if isinstance(payload, dict):
            msg = str(payload.get("message") or payload.get("error") or "").strip()
            code = str(payload.get("error_code") or payload.get("code") or "").strip()
            if msg:
                return f"{code}:{msg}" if code else msg
    if "Traceback" in raw or "HostToolError" in raw:
        lines = [line.strip() for line in raw.splitlines() if line.strip()]
        for line in reversed(lines):
            if line.startswith("File ") or line.startswith("Traceback"):
                continue
            if "HostToolError" in line:
                _, _, rest = line.partition("HostToolError")
                rest = rest.lstrip(": ").strip()
                if rest.startswith("(") and ")" in rest:
                    inner = rest[1:rest.index(")")]
                    first = inner.split(",", 1)[0].strip().strip("'\"")
                    if first:
                        return first
                return rest or line
            return line
        return lines[-1] if lines else raw
    return " ".join(raw.split())


def _is_wrapper_tool_failure(tc: Any) -> bool:
    if _tc_get(tc, "parent_call_id"):
        return False
    if str(_tc_get(tc, "tool_name") or "") != "run_code":
        return False
    blob = f"{_tc_get(tc, 'error') or ''}\n{_tc_get(tc, 'result') or ''}"
    return "Traceback" in blob or "HostToolError" in blob


def count_distinct_tool_errors(tool_calls: list[Any] | None) -> int:
    """root contracts：内层⊕traceback 计 1；同一 (tool, 规范化 message) 重试计 1。"""
    failed = [tc for tc in (tool_calls or []) if not _tc_get(tc, "success", True)]
    inner_failed = [tc for tc in failed if _tc_get(tc, "parent_call_id")]
    keys: set[tuple[str, str]] = set()
    for tc in failed:
        if not _tc_get(tc, "parent_call_id") and inner_failed and _is_wrapper_tool_failure(tc):
            continue
        name = str(_tc_get(tc, "tool_name") or "")
        msg = normalize_tool_error_message(
            str(_tc_get(tc, "error") or _tc_get(tc, "result") or "")
        )
        keys.add((name, msg))
    return len(keys)


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
    # 当前使用的模型标识
    active_model: str = ""
    # 任务/问答/审批事件
    task_events: list[dict[str, Any]] = field(default_factory=list)
    question_events: list[dict[str, Any]] = field(default_factory=list)
    approval_events: list[dict[str, Any]] = field(default_factory=list)
    # Think-Act 推理质量指标
    reasoning_metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        tool_successes = sum(1 for tc in self.tool_calls if tc.success)
        tool_failures = sum(1 for tc in self.tool_calls if not tc.success)
        inner_tool_calls = sum(1 for tc in self.tool_calls if tc.parent_call_id)
        model_tool_failures = sum(
            1 for tc in self.tool_calls if not tc.success and not tc.parent_call_id
        )
        internal_tool_failures = tool_failures - model_tool_failures
        distinct_tool_errors = count_distinct_tool_errors(self.tool_calls)
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
            "active_model": self.active_model,
            "stats": {
                "tool_call_count": len(self.tool_calls),
                "tool_successes": tool_successes,
                "tool_failures": tool_failures,
                "inner_tool_calls": inner_tool_calls,
                "model_tool_failures": model_tool_failures,
                "internal_tool_failures": internal_tool_failures,
                "distinct_tool_errors": distinct_tool_errors,
                "llm_call_count": len(self.llm_calls),
                "reasoning_metrics": self.reasoning_metrics,
            },
        }
        if self.engine_trace:
            result["engine_trace"] = self.engine_trace
        if self.task_events:
            result["task_events"] = self.task_events
        if self.question_events:
            result["question_events"] = self.question_events
        if self.approval_events:
            result["approval_events"] = self.approval_events
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
    # 当前使用的模型标识
    active_model: str = ""
    # 用户选定 chat_mode 对应的工具可见性（may_write / read_only）
    tool_access: str = "unknown"
    # 关键配置快照（用于事后审计）
    config_snapshot: dict[str, Any] = field(default_factory=dict)
    # 任务/问答/审批事件
    task_events: list[dict[str, Any]] = field(default_factory=list)
    question_events: list[dict[str, Any]] = field(default_factory=list)
    approval_events: list[dict[str, Any]] = field(default_factory=list)
    # Think-Act 推理质量指标（聚合）
    reasoning_metrics: dict[str, Any] = field(default_factory=dict)
    session_id: str = ""
    pipeline: str = "fake_frontend"
    conversation_export: dict[str, Any] = field(default_factory=dict)
    # 声明式断言校验结果（suite/case 声明了 assertions 或 golden 时有值）
    validation: ValidationSummary | None = None

    def to_dict(self) -> dict[str, Any]:
        tool_successes = sum(1 for tc in self.tool_calls if tc.success)
        tool_failures = sum(1 for tc in self.tool_calls if not tc.success)
        inner_tool_calls = sum(1 for tc in self.tool_calls if tc.parent_call_id)
        model_tool_failures = sum(
            1 for tc in self.tool_calls if not tc.success and not tc.parent_call_id
        )
        internal_tool_failures = tool_failures - model_tool_failures
        distinct_tool_errors = count_distinct_tool_errors(self.tool_calls)
        result: dict[str, Any] = {
            "schema_version": 3,
            "kind": "case_result",
            "timestamp": self.timestamp,
            "meta": {
                "case_id": self.case_id,
                "case_name": self.case_name,
                "message": self.message,
                "turn_count": len(self.turns) if self.turns else 1,
                "active_model": self.active_model,
                "config_snapshot": self.config_snapshot,
                "session_id": self.session_id,
                "pipeline": self.pipeline,
            },
            "execution": {
                "duration_seconds": round(self.duration_seconds, 2),
                "iterations": self.iterations,
                "route_mode": self.route_mode,
                "skills_used": self.skills_used,
                "tool_scope": self.tool_scope,
                "status": self.status,
                "error": self.error,
                "tool_access": self.tool_access,
            },
            "artifacts": {
                "tool_calls": [tc.to_dict() for tc in self.tool_calls],
                "thinking_log": self.thinking_log,
                "subagent_events": self.subagent_events,
                "llm_calls": self.llm_calls,
                "conversation_messages": self.conversation_messages,
                "conversation_export": self.conversation_export,
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
                "inner_tool_calls": inner_tool_calls,
                "model_tool_failures": model_tool_failures,
                "internal_tool_failures": internal_tool_failures,
                "distinct_tool_errors": distinct_tool_errors,
                "llm_call_count": len(self.llm_calls),
                "reasoning_metrics": self.reasoning_metrics,
            },
        }
        # 多轮对话时输出各轮次详情
        if self.turns:
            result["turns"] = [t.to_dict() for t in self.turns]
        # 声明式断言校验结果
        if self.validation is not None:
            result["validation"] = self.validation.to_dict()
        # engine 内部交互轨迹
        if self.engine_trace:
            result["engine_trace"] = self.engine_trace
        # 任务/问答/审批事件
        if self.task_events:
            result["artifacts"]["task_events"] = self.task_events
        if self.question_events:
            result["artifacts"]["question_events"] = self.question_events
        if self.approval_events:
            result["artifacts"]["approval_events"] = self.approval_events
        return result


# ── 进度追踪 ──────────────────────────────────────────────


@dataclass
class _SuiteProgress:
    """追踪单个 suite 的 case 级执行进度（用于并发面板实时显示）。"""

    suite_name: str
    total_cases: int = 0
    done_cases: int = 0
    ok_cases: int = 0
    fail_cases: int = 0
    total_tokens: int = 0
    current_case: str = ""  # 当前正在执行的 case 名
    start_time: float = field(default_factory=time.monotonic)
    status: str = "⏳ 等待中"  # 面板显示的状态文本

    def elapsed(self) -> float:
        return time.monotonic() - self.start_time

    def elapsed_str(self) -> str:
        s = self.elapsed()
        if s < 60:
            return f"{s:.0f}s"
        return f"{s / 60:.1f}m"

    def progress_bar(self) -> str:
        """生成简易进度条，如 ████░░░░ 3/8。"""
        if self.total_cases == 0:
            return ""
        filled = int(self.done_cases / self.total_cases * 8)
        bar = "█" * filled + "░" * (8 - filled)
        return f"{bar} {self.done_cases}/{self.total_cases}"

    def status_line(self) -> str:
        """构建面板中的状态行。"""
        if self.status.startswith("⏳"):
            return self.status
        if self.status.startswith("✅") or self.status.startswith("⚠"):
            # 已完成
            tok = f"{self.total_tokens:,}" if self.total_tokens else "0"
            return f"{self.status}  {self.elapsed_str()}  {tok} tok"
        if self.status.startswith("💥"):
            return self.status
        # 执行中
        parts = [f"🔄 {self.progress_bar()}"]
        if self.ok_cases or self.fail_cases:
            parts.append(f"{self.ok_cases}✅")
            if self.fail_cases:
                parts.append(f"{self.fail_cases}❌")
        parts.append(self.elapsed_str())
        if self.total_tokens:
            parts.append(f"{self.total_tokens:,} tok")
        if self.current_case:
            parts.append(f"▸ {self.current_case}")
        return "  ".join(parts)


# 进度回调类型：(case_id, case_name, result_or_none) — result 为 None 表示开始执行
ProgressCallback = Callable[[str, str, BenchResult | None], None]


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
        self.task_events: list[dict[str, Any]] = []
        self.question_events: list[dict[str, Any]] = []
        self.approval_events: list[dict[str, Any]] = []
        # CHAT_SUMMARY 事件中的 token 统计
        self.summary_prompt_tokens: int = 0
        self.summary_completion_tokens: int = 0
        self.summary_total_tokens: int = 0
        # 用于计算工具调用耗时
        self._pending_tool_starts: dict[str, float] = {}

    def on_event(self, event: ToolCallEvent) -> None:
        """引擎事件回调：实时渲染 + 收集日志。"""
        # 实时渲染到终端
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
                parent_call_id=getattr(event, "parent_call_id", "") or "",
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

        elif event.event_type == EventType.CHAT_SUMMARY:
            self.summary_prompt_tokens = event.prompt_tokens
            self.summary_completion_tokens = event.completion_tokens
            self.summary_total_tokens = event.total_tokens

        elif event.event_type == EventType.TASK_LIST_CREATED:
            self.task_events.append({
                "event_type": event.event_type.value,
                "task_list_data": event.task_list_data,
            })

        elif event.event_type == EventType.TASK_ITEM_UPDATED:
            self.task_events.append({
                "event_type": event.event_type.value,
                "task_index": event.task_index,
                "task_status": event.task_status,
                "task_result": event.task_result,
            })

        elif event.event_type == EventType.USER_QUESTION:
            self.question_events.append({
                "event_type": event.event_type.value,
                "question_id": event.question_id,
                "question_header": event.question_header,
                "question_text": event.question_text,
            })

        elif event.event_type == EventType.PENDING_APPROVAL:
            self.approval_events.append({
                "event_type": event.event_type.value,
                "approval_id": event.approval_id,
                "approval_tool_name": event.approval_tool_name,
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
            "task_events": list(self.task_events),
            "question_events": list(self.question_events),
            "approval_events": list(self.approval_events),
            "summary_prompt_tokens": self.summary_prompt_tokens,
            "summary_completion_tokens": self.summary_completion_tokens,
            "summary_total_tokens": self.summary_total_tokens,
        }
        # 重置
        self.thinking_log = []
        self.tool_calls = []
        self.route_mode = ""
        self.skills_used = []
        self.tool_scope = []
        self.subagent_events = []
        self.task_events = []
        self.question_events = []
        self.approval_events = []
        self.summary_prompt_tokens = 0
        self.summary_completion_tokens = 0
        self.summary_total_tokens = 0
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


class _StreamRecorder:
    """包装异步流式响应，透传 chunk 同时累计 call 级指标。

    在 stream 消费完毕后，将累计的 finish_reason / usage
    回写到 call_record["response"]，使 run_*.json 中每个 llm_call
    都具备完整的观测数据。
    """

    def __init__(self, stream: Any, call_record: dict[str, Any]) -> None:
        self._stream = stream
        self._call_record = call_record
        # 累计指标
        self._finish_reason: str | None = None
        self._usage: dict[str, int] | None = None

    def __aiter__(self):  # noqa: D105
        return self

    async def __anext__(self):  # noqa: D105
        try:
            chunk = await self._stream.__anext__()
        except StopAsyncIteration:
            self._finalize()
            raise

        # ── 从 chunk 中提取指标 ──

        # openai ChatCompletionChunk 格式解析
        choices = getattr(chunk, "choices", None)
        if choices:
            fr = getattr(choices[0], "finish_reason", None)
            if fr:
                self._finish_reason = fr

        # 自定义 provider _StreamDelta 格式
        if hasattr(chunk, "content_delta"):
            if getattr(chunk, "finish_reason", None):
                self._finish_reason = chunk.finish_reason

        # usage（通常在最后一个 chunk）
        chunk_usage = getattr(chunk, "usage", None)
        if chunk_usage is not None:
            self._usage = {
                "prompt_tokens": getattr(chunk_usage, "prompt_tokens", 0),
                "completion_tokens": getattr(chunk_usage, "completion_tokens", 0),
                "total_tokens": getattr(chunk_usage, "total_tokens", 0),
            }

        return chunk

    # 支持 async for ... 以外的 aclose 调用（如提前中断）
    async def aclose(self):  # noqa: D102
        close_fn = getattr(self._stream, "aclose", None)
        if close_fn:
            await close_fn()
        self._finalize()

    def _finalize(self) -> None:
        """将累计指标写入 call_record。"""
        resp: dict[str, Any] = {"_stream": True}
        if self._finish_reason is not None:
            resp["finish_reason"] = self._finish_reason
        if self._usage is not None:
            resp["usage"] = self._usage
        self._call_record["response"] = resp


class _LLMCallInterceptor:
    """拦截 engine 的 LLM API 调用，记录完整的请求和响应。

    通过 monkey-patch ``engine._client.chat.completions.create`` 实现，
    无需修改 engine 源代码。

    模型切换（``switch_model`` → ``_sync_from_llm_clients``）和 OAuth
    凭证热更新（``_refresh_credential_if_needed``）都会重建
    ``engine._client``；拦截器包装这两个入口并在每轮开始时通过
    ``ensure_patched`` 重新挂接，避免切换后静默漏记调用。
    """

    def __init__(self, engine: AgentEngine) -> None:
        self.calls: list[dict[str, Any]] = []
        self._engine = engine
        if not hasattr(engine, "_client"):
            raise AttributeError(
                "bench requires engine._client (openai AsyncOpenAI client); "
                "engine may have been refactored"
            )
        # id(client) → (client, 原始 create)；保留 client 强引用确保 id 不复用
        self._patched: dict[int, tuple[Any, Any]] = {}
        # 包装客户端重建的两个入口，替换后立即重新挂接
        self._orig_sync_clients: Any = None
        self._orig_refresh_credential: Any = None
        if hasattr(engine, "_sync_from_llm_clients"):
            self._orig_sync_clients = engine._sync_from_llm_clients
            engine._sync_from_llm_clients = self._synced_repatch  # type: ignore[method-assign]
        if hasattr(engine, "_refresh_credential_if_needed"):
            self._orig_refresh_credential = engine._refresh_credential_if_needed
            engine._refresh_credential_if_needed = self._refreshed_repatch  # type: ignore[method-assign]
        self._patch_current_client()

    def _patch_current_client(self) -> None:
        """给当前 engine._client 挂接拦截（已挂接的 client 跳过）。"""
        client = getattr(self._engine, "_client", None)
        if client is None or id(client) in self._patched:
            return
        original_create = client.chat.completions.create

        async def _intercepted_create(**kwargs: Any) -> Any:
            return await self._record_and_call(original_create, kwargs)

        self._intercepted_create = _intercepted_create
        self._patched[id(client)] = (client, original_create)
        client.chat.completions.create = _intercepted_create

    def ensure_patched(self) -> None:
        """每轮开始时调用：客户端可能已被切换/热更新，重新挂接拦截器。"""
        self._patch_current_client()

    def _synced_repatch(self, *args: Any, **kwargs: Any) -> Any:
        result = self._orig_sync_clients(*args, **kwargs)
        self._patch_current_client()
        return result

    async def _refreshed_repatch(self, *args: Any, **kwargs: Any) -> Any:
        result = await self._orig_refresh_credential(*args, **kwargs)
        self._patch_current_client()
        return result

    async def _record_and_call(
        self, original_create: Any, kwargs: dict[str, Any],
    ) -> Any:
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

        is_stream = bool(kwargs.get("stream"))

        start = time.monotonic()
        try:
            response = await original_create(**kwargs)
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

        if is_stream:
            # 先 append，_StreamRecorder 消费完毕后回写 response
            call_record["response"] = {"_stream": True}
            self.calls.append(call_record)
            return _StreamRecorder(response, call_record)
        else:
            call_record["response"] = _serialize_llm_response(response)
            self.calls.append(call_record)
            return response

    def restore(self) -> None:
        """恢复原始的 create 方法与被包装的引擎方法。"""
        for client, original_create in self._patched.values():
            try:
                client.chat.completions.create = original_create
            except Exception:
                logger.debug("恢复 LLM client create 失败", exc_info=True)
        self._patched.clear()
        for attr, original in (
            ("_sync_from_llm_clients", self._orig_sync_clients),
            ("_refresh_credential_if_needed", self._orig_refresh_credential),
        ):
            if original is None:
                continue
            try:
                # 移除实例级包装，恢复类上的原始方法
                delattr(self._engine, attr)
            except AttributeError:
                pass


class _EngineTracer:
    """拦截 engine 关键方法，记录程序向 agent 注入的指令和内部决策。

    通过 monkey-patch 以下方法实现：
    - ``_prepare_system_prompts_for_request`` → 记录每轮注入的系统提示（分解各组件）

    通过环境变量 ``EXCELMANUS_BENCH_TRACE=0`` 或 CLI ``--no-trace`` 禁用。
    """

    def __init__(self, engine: AgentEngine) -> None:
        self.entries: list[dict[str, Any]] = []
        self._engine = engine
        self._iteration = 0
        # label → (first_seen_iter, content_hash) — 用于折叠不变的 component
        self._component_seen: dict[str, tuple[int, int]] = {}

        # 保存原始方法
        if not hasattr(engine, "_prepare_system_prompts_for_request"):
            raise AttributeError(
                "bench requires engine._prepare_system_prompts_for_request"
            )
        self._orig_prepare = engine._prepare_system_prompts_for_request

        engine._prepare_system_prompts_for_request = self._traced_prepare  # type: ignore[assignment]

    def _traced_prepare(
        self, skill_contexts: list[str], **kwargs: Any,
    ) -> tuple[list[str], str | None]:
        """拦截系统提示构建，记录各组件内容。

        对跨轮次内容完全不变的 component，省略 ``content`` 字段，
        改为记录 ``same_as_iter`` 指向首次出现的迭代轮次，避免重复存储。
        """
        self._iteration += 1
        prompts, error = self._orig_prepare(skill_contexts, **kwargs)

        # 分解记录各组件
        components: list[dict[str, Any]] = []
        for idx, prompt in enumerate(prompts):
            label = "base_system_prompt" if idx == 0 else f"context_{idx}"
            # 尝试识别组件类型
            if idx > 0:
                snippet = prompt[:200]
                if "权限提示" in snippet or "fullAccess" in snippet:
                    label = "access_notice"
                elif "MCP" in snippet:
                    label = "mcp_context_notice"
                elif "Hook" in snippet:
                    label = "hook_context"
                elif "计划" in snippet and "已批准" in snippet:
                    label = "approved_plan_context"
                else:
                    label = f"skill_context_{idx}"

            content_hash = hash(prompt)
            prev = self._component_seen.get(label)
            if prev is not None and prev[1] == content_hash:
                # 内容与首次出现完全相同 — 折叠，不重复存 content
                comp: dict[str, Any] = {
                    "label": label,
                    "char_count": len(prompt),
                    "same_as_iter": prev[0],
                }
            else:
                # 首次出现或内容已变化 — 完整记录
                self._component_seen[label] = (self._iteration, content_hash)
                comp = {
                    "label": label,
                    "char_count": len(prompt),
                    "content": prompt[:_TRACE_SYSTEM_PROMPT_MAX_CHARS],
                    "truncated": len(prompt) > _TRACE_SYSTEM_PROMPT_MAX_CHARS,
                }
            components.append(comp)

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

    def snapshot_and_reset(self) -> list[dict[str, Any]]:
        """快照当前 trace 数据并重置，用于多轮分轮记录。

        注意：``_iteration`` 不重置（保持单调递增），确保 ``same_as_iter``
        引用在整个 engine 生命周期内始终有效。``_component_seen`` 同样保留，
        跨轮次内容不变的 component 继续折叠。
        """
        snapshot = list(self.entries)
        self.entries = []
        return snapshot

    def restore(self) -> None:
        """恢复原始方法。"""
        self._engine._prepare_system_prompts_for_request = self._orig_prepare  # type: ignore[assignment]


# ── 执行器 ────────────────────────────────────────────────


def _create_engine(config: ExcelManusConfig) -> AgentEngine:
    """兼容旧测试的薄封装；正式路径请走 SessionManager。"""
    runtime = build_chat_runtime(config)
    engine = AgentEngine(
        config=config,
        registry=runtime.manager._registry,
        skill_router=runtime.manager._skill_router,
    )
    return engine


@dataclass
class _CaseSession:
    """单个用例对应的伪造前端会话。"""

    session_id: str
    manager: SessionManager
    workdir: Path | None
    owns_runtime: bool
    runtime: ChatRuntime | None


def _case_turns(case: BenchCase) -> list[BenchTurn]:
    """把用例归一化为伪造前端的输入轮次。"""
    if case.turns:
        turns = [
            BenchTurn(
                text=turn.text,
                attachments=list(turn.attachments),
                images=list(turn.images),
            )
            for turn in case.turns
        ]
    else:
        texts = list(case.messages) if case.messages else (
            [case.message] if case.message else []
        )
        turns = [BenchTurn(text=str(text)) for text in texts]
    if not turns:
        return []
    first = turns[0]
    attachments = list(first.attachments)
    images = list(first.images)
    if not attachments:
        attachments = list(case.attachments or case.source_files)
    if not images:
        images = list(case.images)
    turns[0] = BenchTurn(text=first.text, attachments=attachments, images=images)
    return turns


async def _open_case_session(
    case: BenchCase,
    config: ExcelManusConfig,
    *,
    output_dir: Path | None,
    suite_name: str,
    session_manager: SessionManager | None,
) -> _CaseSession:
    """创建空白会话并绑定独立工作区（附件稍后由伪造前端上传）。"""
    owns_runtime = session_manager is None
    runtime: ChatRuntime | None = None
    if session_manager is None:
        runtime = build_chat_runtime(config)
        session_manager = runtime.manager

    workdir: Path | None = None
    if output_dir is not None:
        workdir = output_dir / "workfiles" / (suite_name or "adhoc") / case.id
        workdir.mkdir(parents=True, exist_ok=True)
        # 每次运行从干净工作区开始：上次运行的产物会让
        # "文件已存在"/golden 比对等断言出现不可复现的失败
        for child in workdir.iterdir():
            try:
                if child.is_dir() and not child.is_symlink():
                    shutil.rmtree(child, ignore_errors=True)
                else:
                    child.unlink()
            except OSError:
                logger.debug("清理工作区残留失败: %s", child, exc_info=True)

    workspace_path: str | None = None
    if workdir is not None:
        workspace_path = str(workdir.resolve())
        try:
            session_manager.register_workspace(workspace_path, title=f"bench:{case.id}")
        except WorkspacePathError as exc:
            logger.warning("工作区登记失败，回退默认工作区: %s", exc)
            workspace_path = None
        except Exception:
            logger.warning("工作区登记异常，回退默认工作区", exc_info=True)
            workspace_path = None

    session = await session_manager.create_or_reuse_session(
        workspace_path=workspace_path,
        title=case.name or case.id,
    )
    session_id = str(session.get("id") or "")
    if not session_id:
        raise RuntimeError("创建评测会话失败：缺少 session_id")
    if workspace_path:
        session_manager.remember_session_workspace(session_id, workspace_path)
    return _CaseSession(
        session_id=session_id,
        manager=session_manager,
        workdir=workdir,
        owns_runtime=owns_runtime,
        runtime=runtime,
    )


async def _close_case_session(case_session: _CaseSession) -> None:
    if case_session.owns_runtime and case_session.runtime is not None:
        await case_session.runtime.aclose()


def _dump_conversation_messages(
    engine: AgentEngine,
    interceptor: _LLMCallInterceptor | None = None,
) -> list[dict[str, Any]]:
    """导出完整对话消息快照，反映实际发送给 LLM 的请求内容。

    当 interceptor 有调用记录时，使用最后一次请求的 messages（完全准确，
    包含所有动态注入的 system prompts：file_structure_preview、
    skill_context 等）。
    否则回退到 memory.get_messages()（仅含静态 base system prompt）。
    """
    try:
        if interceptor is not None and interceptor.calls:
            last_messages = interceptor.calls[-1]["request"].get("messages", [])
            if last_messages:
                return list(last_messages)
        messages = engine.memory.get_messages()
        return [_serialize_message(m) for m in messages]
    except Exception:
        return []


def _write_conversation_export(
    export: dict[str, Any],
    output_dir: Path | None,
    case_id: str,
) -> Path | None:
    """把伪造前端对话历史单独落盘，便于事后阅读。"""
    if output_dir is None:
        return None
    conv_dir = output_dir / "conversations"
    conv_dir.mkdir(parents=True, exist_ok=True)
    path = conv_dir / f"{case_id}.json"
    _write_json(path, export)
    logger.info("  对话历史已导出: %s", path)
    return path


async def run_case(
    case: BenchCase,
    config: ExcelManusConfig,
    *,
    render_enabled: bool = True,
    trace_enabled: bool = True,
    output_dir: Path | None = None,
    suite_name: str = "",
    session_manager: SessionManager | None = None,
) -> BenchResult:
    """用伪造前端跑完一个用例，只记录结果、不评分。"""
    case_session = await _open_case_session(
        case,
        config,
        output_dir=output_dir,
        suite_name=suite_name,
        session_manager=session_manager,
    )
    collector = _EventCollector(render_enabled=render_enabled)
    interceptor: _LLMCallInterceptor | None = None
    tracer: _EngineTracer | None = None
    engine: AgentEngine | None = None
    timestamp = datetime.now(timezone.utc).isoformat()
    turns = _case_turns(case)
    is_multi_turn = len(turns) > 1

    def _on_engine(ready: AgentEngine) -> None:
        nonlocal interceptor, tracer, engine
        engine = ready
        if interceptor is None:
            interceptor = _LLMCallInterceptor(ready)
            if trace_enabled:
                try:
                    tracer = _EngineTracer(ready)
                except AttributeError as exc:
                    logger.debug("trace 已降级：engine 不支持完整 tracer 钩子（%s）", exc)
        else:
            # 多轮会话中引擎客户端可能已被切换/热更新，重新挂接拦截器
            interceptor.ensure_patched()

    frontend = FakeFrontend(
        manager=case_session.manager,
        session_id=case_session.session_id,
        auto_replies=case.auto_replies,
        auto_approve=case.auto_approve,
        chat_mode=case.chat_mode,
        on_event=collector.on_event,
        on_engine=_on_engine,
    )

    logger.info(
        "▶ 伪造前端开始: %s (%s) [%d 轮] session=%s",
        case.id, case.name, len(turns), case_session.session_id,
    )
    case_start = time.monotonic()

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
    chat_result: ChatResult | None = None
    conversation_export: dict[str, Any] = {}

    try:
        for turn_idx, turn in enumerate(turns):
            msg = turn.text
            if is_multi_turn:
                logger.info(
                    "  ── 轮次 %d/%d ──", turn_idx + 1, len(turns),
                )

            llm_calls_before = len(interceptor.calls) if interceptor else 0
            turn_start = time.monotonic()

            try:
                try:
                    chat_result = await asyncio.wait_for(
                        frontend.send(
                            turn.text,
                            attachments=turn.attachments,
                            images=turn.images,
                        ),
                        timeout=case.turn_timeout if case.turn_timeout > 0 else None,
                    )
                except (asyncio.TimeoutError, TimeoutError) as timeout_exc:
                    raise TurnTimeoutError(
                        f"单轮硬超时（turn_timeout={case.turn_timeout:.0f}s），"
                        "已中止以防空挂"
                    ) from timeout_exc
            except Exception as exc:
                logger.error(
                    "用例 %s 轮次 %d 执行异常: %s",
                    case.id, turn_idx, exc, exc_info=True,
                )
                turn_elapsed = time.monotonic() - turn_start
                # 快照本轮收集器数据
                snap = collector.snapshot_and_reset()
                turn_llm_calls = list(
                    interceptor.calls[llm_calls_before:]
                ) if interceptor else []

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
                    engine_trace=(
                        tracer.snapshot_and_reset() if tracer else []
                    ) if is_multi_turn else [],
                    active_model=engine.current_model if engine is not None else "",
                    task_events=snap["task_events"],
                    question_events=snap["question_events"],
                    approval_events=snap["approval_events"],
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
            snap = collector.snapshot_and_reset()
            turn_llm_calls = list(
                interceptor.calls[llm_calls_before:]
            ) if interceptor else []

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
                engine_trace=(
                    tracer.snapshot_and_reset() if tracer else []
                ) if is_multi_turn else [],
                active_model=engine.current_model if engine is not None else "",
                task_events=snap["task_events"],
                question_events=snap["question_events"],
                approval_events=snap["approval_events"],
                reasoning_metrics=getattr(chat_result, "reasoning_metrics", {}),
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
                        title=f"轮次 {turn_idx + 1}/{len(turns)}",
                        border_style="#5f875f",
                        padding=(1, 2),
                        expand=False,
                    )
                )
    finally:
        if interceptor is not None:
            interceptor.restore()
        if tracer is not None:
            tracer.restore()
        try:
            conversation_export = frontend.export_conversation()
            _write_conversation_export(conversation_export, output_dir, case.id)
        except Exception:
            logger.debug("导出对话失败", exc_info=True)
        try:
            await _close_case_session(case_session)
        except Exception:
            logger.debug("关闭用例会话失败", exc_info=True)

    case_elapsed = time.monotonic() - case_start

    case_engine_trace: list[dict[str, Any]] = []
    if tracer is not None and not is_multi_turn:
        case_engine_trace = tracer.snapshot_and_reset()

    _chat_mode = str(getattr(engine, "_current_chat_mode", "write") or "write")
    _tool_access = "read_only" if _chat_mode == "read" else "may_write"
    _result_access = getattr(chat_result, "tool_access", "") if chat_result is not None else ""
    if _result_access:
        _tool_access = str(_result_access)

    _config_snapshot = {
        "model": getattr(config, "model", ""),
        "base_url": getattr(config, "base_url", ""),
        "pipeline": "fake_frontend",
        "chat_mode": case.chat_mode,
        "auto_approve": case.auto_approve,
    }

    result = BenchResult(
        case_id=case.id,
        case_name=case.name,
        message=case.message or (turns[0].text if turns else ""),
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
        llm_calls=interceptor.calls if interceptor is not None else [],
        conversation_messages=conversation_export.get("transcript") or (
            _dump_conversation_messages(engine, interceptor) if engine is not None else []
        ),
        turns=all_turns if is_multi_turn else [],
        status=case_status,
        error=case_error,
        engine_trace=case_engine_trace,
        active_model=engine.current_model if engine is not None else "",
        tool_access=_tool_access,
        config_snapshot=_config_snapshot,
        reasoning_metrics=getattr(chat_result, "reasoning_metrics", {}) if chat_result is not None else {},
        session_id=case_session.session_id,
        pipeline="fake_frontend",
        conversation_export=conversation_export,
    )

    # 声明式断言校验（suite/case 声明了 assertions 或 golden 时产生结果）
    try:
        result.validation = validate_case(
            result.to_dict(),
            case.assertions,
            expected=case.expected,
            workfile_dir=case_session.workdir,
        )
        if result.validation.failed:
            logger.warning(
                "✗ 用例 %s 断言未通过 %d/%d（error=%d warning=%d）",
                case.id, result.validation.failed, result.validation.total,
                result.validation.errors, result.validation.warnings,
            )
    except Exception:
        logger.warning("用例 %s 断言校验异常", case.id, exc_info=True)

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
    model_failures = sum(
        1 for tc in result.tool_calls if not tc.success and not tc.parent_call_id
    )
    distinct_failures = count_distinct_tool_errors(result.tool_calls)
    turn_info = f" ({len(turns)} 轮)" if is_multi_turn else ""
    extra = ""
    if frontend.auto_reply_count:
        extra += f" │ {frontend.auto_reply_count} 次问答"
    if frontend.auto_approve_count:
        extra += f" │ {frontend.auto_approve_count} 次审批"
    logger.info(
        "✓ 用例 %s 完成%s: %d 迭代 │ %d 工具调用(失败%d/可见%d/root%d) │ %d tokens │ %.1fs │ %d 次 LLM 调用%s",
        case.id,
        turn_info,
        result.iterations,
        len(result.tool_calls),
        failures,
        model_failures,
        distinct_failures,
        result.total_tokens,
        result.duration_seconds,
        len(result.llm_calls),
        extra,
    )
    return result


def _as_str_list(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    return [str(item) for item in value if item]


def _parse_turn_item(item: Any) -> BenchTurn | None:
    """解析一轮伪造前端输入。"""
    if isinstance(item, str):
        text = item.strip()
        return BenchTurn(text=text) if text else None
    if not isinstance(item, dict):
        return None
    text = str(item.get("text") or item.get("message") or "").strip()
    if not text:
        return None
    return BenchTurn(
        text=text,
        attachments=_as_str_list(item.get("attachments")),
        images=_as_str_list(item.get("images")),
    )


def suite_include_in_all(path: str | Path) -> bool:
    """``--all`` 是否收录该套件。显式 ``include_in_all: false`` 的长评测需手动指定。"""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return True
    if not isinstance(data, dict):
        return True
    return data.get("include_in_all", True) is not False


def list_default_suite_paths(cases_dir: str | Path) -> list[Path]:
    """列出 ``bench/cases`` 下会被 ``--all`` 执行的套件。"""
    return sorted(
        path
        for path in Path(cases_dir).glob("*.json")
        if suite_include_in_all(path)
    )


def _load_suite(path: str | Path) -> tuple[str, list[BenchCase], bool]:
    """从 JSON 加载套件，并把 suite 级断言合并进各 case（case 级覆盖同名规则）。"""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    suite_name = data.get("suite_name", Path(path).stem)
    suite_trace = bool(data.get("trace", True))
    suite_assertions_raw = data.get("assertions")
    suite_assertions: dict[str, Any] = (
        suite_assertions_raw if isinstance(suite_assertions_raw, dict) else {}
    )
    try:
        suite_turn_timeout = max(0.0, float(data.get("turn_timeout") or 0.0))
    except (TypeError, ValueError):
        suite_turn_timeout = 0.0
    # suite 级审批默认（case 可覆盖）；网页默认是 ask，对应这里的 accept
    suite_auto_approve = str(data.get("auto_approve") or "fullaccess")
    cases: list[BenchCase] = []
    for item in data.get("cases", []):
        raw_messages = item.get("messages")
        raw_message = item.get("message", "")
        turns: list[BenchTurn] = []
        messages: list[str] = []
        if raw_messages and isinstance(raw_messages, list):
            for raw in raw_messages:
                parsed = _parse_turn_item(raw)
                if parsed is None:
                    continue
                turns.append(parsed)
                messages.append(parsed.text)
            message = messages[0] if messages else ""
        else:
            message = str(raw_message or "")
            messages = [message] if message else []
            if message:
                turns.append(BenchTurn(text=message))

        case_assertions_raw = item.get("assertions")
        case_assertions: dict[str, Any] = (
            case_assertions_raw if isinstance(case_assertions_raw, dict) else {}
        )
        try:
            case_turn_timeout = float(item.get("turn_timeout") or 0.0)
        except (TypeError, ValueError):
            case_turn_timeout = 0.0
        cases.append(BenchCase(
            id=item["id"],
            name=item.get("name", item["id"]),
            message=message,
            messages=messages,
            tags=item.get("tags", []),
            expected=item.get("expected", {}),
            assertions=merge_assertions(suite_assertions, case_assertions),
            source_files=_as_str_list(item.get("source_files")),
            attachments=_as_str_list(item.get("attachments")),
            images=_as_str_list(item.get("images")),
            auto_replies=_as_str_list(item.get("auto_replies")),
            chat_mode=str(item.get("chat_mode") or "write"),
            auto_approve=str(item.get("auto_approve") or suite_auto_approve),
            turn_timeout=case_turn_timeout if case_turn_timeout > 0 else suite_turn_timeout,
            turns=turns,
        ))
    return suite_name, cases, suite_trace


def _filter_cases(
    cases: list[BenchCase],
    *,
    wave: str = "",
    case_ids: list[str] | None = None,
) -> list[BenchCase]:
    """按 wave 标签 / case id 裁剪用例（与 bench/fixtures/filter_suite.py 同语义）。"""
    wanted_ids = {item.strip() for item in (case_ids or []) if item.strip()}
    wave_tag = f"wave-{wave.strip()}" if wave.strip() else ""
    filtered = []
    for case in cases:
        if wanted_ids and case.id not in wanted_ids:
            continue
        if wave_tag and wave_tag not in [str(tag) for tag in case.tags]:
            continue
        filtered.append(case)
    return filtered


def _write_json(path: Path, payload: Any) -> None:
    """把 bench 工件原子写成 UTF-8 JSON；日期等工具值转为展示字符串。"""
    text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temp.write_text(text, encoding="utf-8")
        os.replace(temp, path)
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


def _save_result(result: BenchResult, output_dir: Path) -> Path:
    """保存单个用例结果到 JSON，只记录执行结果，不做评分。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    short_id = uuid.uuid4().hex[:6]
    filename = f"run_{ts}_{result.case_id}_{short_id}.json"
    filepath = output_dir / filename
    _write_json(filepath, result.to_dict())
    return filepath


# ── 用例分析摘要（digest）──────────────────────────────────
#
# conversations/{case_id}.digest.md 是给人/子代理做设计复盘用的紧凑视图：
# 一轮一节，含回复全文、工具调用（参数/返回截断）、LLM 调用指标、思考摘要、
# 交互事件与系统提示注入概况。全量数据仍在 run_*.json 与 conversations/*.json。
_DIGEST_ARG_MAX_CHARS = 300
_DIGEST_RESULT_MAX_CHARS = 500
_DIGEST_THINKING_MAX_CHARS = 800


def _compact_inline(value: Any, limit: int) -> str:
    """把任意值压成单行截断文本。"""
    if value is None:
        text = ""
    elif isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError):
            text = str(value)
    text = " ".join(str(text).split())
    if len(text) > limit:
        text = text[:limit] + "…"
    return text


def _digest_tool_call_lines(tool_calls: list[ToolCallLog]) -> list[str]:
    lines: list[str] = []
    for i, tc in enumerate(tool_calls, 1):
        args = _compact_inline(tc.arguments, _DIGEST_ARG_MAX_CHARS)
        head = f"{i}. iter{tc.iteration} `{tc.tool_name}` `{args}`"
        if tc.success:
            head += f" → ok ({tc.duration_ms:.0f}ms)"
        else:
            err = _compact_inline(tc.error or "", 200)
            head += f" → **FAIL** {err} ({tc.duration_ms:.0f}ms)"
        lines.append(head)
        if tc.result:
            lines.append(
                f"   返回: {_compact_inline(tc.result, _DIGEST_RESULT_MAX_CHARS)}"
            )
    return lines


def _digest_llm_call_lines(llm_calls: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for i, call in enumerate(llm_calls, 1):
        request = call.get("request") or {}
        response = call.get("response") or {}
        usage = response.get("usage") or {}
        parts = [
            f"#{i}",
            f"{call.get('duration_ms', 0):.0f}ms",
            f"prompt={usage.get('prompt_tokens', 0):,}",
            f"completion={usage.get('completion_tokens', 0):,}",
            f"msgs={len(request.get('messages') or [])}",
            f"tools={len(request.get('tool_names') or [])}",
        ]
        finish = response.get("finish_reason")
        if finish:
            parts.append(f"finish={finish}")
        req_tools = [
            (tc.get("function") or {}).get("name", "")
            for tc in (response.get("tool_calls") or [])
            if isinstance(tc, dict)
        ]
        if req_tools:
            parts.append(f"→ {','.join(t for t in req_tools if t)}")
        if call.get("error"):
            parts.append(f"ERROR={_compact_inline(call['error'], 120)}")
        lines.append("- " + " ".join(parts))
    return lines


def _digest_trace_lines(engine_trace: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for entry in engine_trace:
        data = entry.get("data") or {}
        comps = data.get("components") or []
        labels = ", ".join(
            f"{c.get('label', '?')}({c.get('char_count', 0)}字"
            + (",同前" if c.get("same_as_iter") else "")
            + ("…" if c.get("truncated") else "")
            + ")"
            for c in comps
        )
        lines.append(
            f"- iter{entry.get('iteration', '?')}: "
            f"{data.get('prompt_count', 0)} 个组件 "
            f"{data.get('total_chars', 0):,} 字 │ {labels}"
        )
    return lines


def _digest_event_lines(turn: TurnResult) -> list[str]:
    lines: list[str] = []
    for q in turn.question_events:
        lines.append(
            f"- 问答 {q.get('question_id', '')}: "
            f"{_compact_inline(q.get('question_text', ''), 200)}"
        )
    for a in turn.approval_events:
        lines.append(
            f"- 审批 {a.get('approval_id', '')}: {a.get('approval_tool_name', '')}"
        )
    for t in turn.task_events:
        if t.get("event_type") == "task_list_created":
            lines.append(f"- 任务清单创建: {_compact_inline(t.get('task_list_data'), 200)}")
        else:
            lines.append(
                f"- 任务[{t.get('task_index')}] {t.get('task_status', '')}: "
                f"{_compact_inline(t.get('task_result'), 150)}"
            )
    return lines


def _build_case_digest(
    result: BenchResult,
    case: BenchCase,
    *,
    suite_name: str,
    output_dir: Path,
    run_file: Path | None,
) -> str:
    """生成单用例 Markdown 摘要，供事后评审/子代理分析快速定位问题。"""
    lines: list[str] = []
    tool_failures = sum(1 for tc in result.tool_calls if not tc.success)
    model_tool_failures = sum(
        1 for tc in result.tool_calls if not tc.success and not tc.parent_call_id
    )
    distinct_tool_errors = count_distinct_tool_errors(result.tool_calls)

    lines.append(f"# {result.case_id} {result.case_name}")
    lines.append("")
    meta = [
        f"套件 {suite_name or '∅'}",
        f"标签 {','.join(case.tags) or '∅'}",
        f"chat_mode={case.chat_mode}",
        f"auto_approve={case.auto_approve}",
        f"model={result.active_model or '∅'}",
    ]
    lines.append("- " + " · ".join(meta))
    lines.append(
        "- "
        f"status={result.status} · {result.duration_seconds:.1f}s · "
        f"{result.iterations} 迭代 · "
        f"{len(result.tool_calls)} 工具(失败 {tool_failures}/可见 {model_tool_failures}/root {distinct_tool_errors}) · "
        f"{len(result.llm_calls)} LLM · {result.total_tokens:,} tok"
    )
    if result.error:
        lines.append(f"- 错误: {_compact_inline(result.error, 300)}")

    conv_rel = f"conversations/{result.case_id}.json"
    workdir = output_dir / "workfiles" / (suite_name or "adhoc") / result.case_id
    pointers = [f"run={run_file.name if run_file else '∅'}", f"conv={conv_rel}", f"workdir={workdir}"]
    lines.append("- 全量数据: " + " · ".join(pointers))

    expected = case.expected or {}
    review_focus = expected.get("review_focus") or []
    if expected.get("lens"):
        lines.append(f"- 评测视角: {expected['lens']}")
    if review_focus:
        lines.append("")
        lines.append("## 评测点 (review_focus)")
        lines.extend(f"- {item}" for item in review_focus)

    attachments = (result.conversation_export or {}).get("attachments") or []
    if attachments:
        lines.append("")
        lines.append("## 附件")
        for att in attachments:
            lines.append(
                f"- {att.get('path')} ({att.get('kind')}, {att.get('size', 0)}B, "
                f"源: {att.get('source', '∅')})"
            )

    # 统一成「轮次列表」：多轮用 turns，单轮用 case 级字段合成
    if result.turns:
        turns: list[tuple[str, TurnResult]] = [
            (t.message, t) for t in result.turns
        ]
    else:
        pseudo = TurnResult(
            turn_index=0,
            message=result.message,
            reply=result.reply,
            duration_seconds=result.duration_seconds,
            iterations=result.iterations,
            route_mode=result.route_mode,
            skills_used=result.skills_used,
            tool_scope=result.tool_scope,
            tool_calls=result.tool_calls,
            thinking_log=result.thinking_log,
            subagent_events=result.subagent_events,
            llm_calls=result.llm_calls,
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            total_tokens=result.total_tokens,
            status=result.status,
            error=result.error,
            engine_trace=result.engine_trace,
            task_events=result.task_events,
            question_events=result.question_events,
            approval_events=result.approval_events,
            reasoning_metrics=result.reasoning_metrics,
        )
        turns = [(result.message, pseudo)]

    for turn in turns:
        msg, t = turn
        lines.append("")
        lines.append(f"## 轮次 {t.turn_index + 1} · 用户: 「{_compact_inline(msg, 200)}」")
        lines.append("")
        lines.append(
            f"{t.duration_seconds:.1f}s · {t.iterations} 迭代 · "
            f"route={t.route_mode or '∅'} · "
            f"skills={','.join(t.skills_used) or '∅'} · "
            f"scope={','.join(t.tool_scope) or '∅'} · "
            f"{t.total_tokens:,} tok"
        )
        if t.status != "ok" and t.error:
            lines.append(f"**轮次错误**: {_compact_inline(t.error, 300)}")

        lines.append("")
        lines.append("### 回复")
        lines.append("")
        lines.append(t.reply or "∅")

        if t.tool_calls:
            lines.append("")
            lines.append(f"### 工具调用 ({len(t.tool_calls)})")
            lines.append("")
            lines.extend(_digest_tool_call_lines(t.tool_calls))

        if t.llm_calls:
            lines.append("")
            lines.append(f"### LLM 调用 ({len(t.llm_calls)})")
            lines.append("")
            lines.extend(_digest_llm_call_lines(t.llm_calls))

        if t.thinking_log:
            lines.append("")
            lines.append(f"### 思考 ({len(t.thinking_log)})")
            lines.append("")
            lines.extend(
                f"- {_compact_inline(item, _DIGEST_THINKING_MAX_CHARS)}"
                for item in t.thinking_log
            )

        event_lines = _digest_event_lines(t)
        if event_lines:
            lines.append("")
            lines.append("### 交互事件")
            lines.append("")
            lines.extend(event_lines)

        if t.subagent_events:
            lines.append("")
            lines.append("### 子代理事件")
            lines.append("")
            for ev in t.subagent_events:
                lines.append(
                    f"- {ev.get('event_type', '')} {ev.get('name', '')}: "
                    f"{_compact_inline(ev.get('summary') or ev.get('reason'), 200)}"
                )

        trace_lines = _digest_trace_lines(t.engine_trace or [])
        if trace_lines:
            lines.append("")
            lines.append("### 系统提示注入")
            lines.append("")
            lines.extend(trace_lines)

        if t.reasoning_metrics:
            lines.append("")
            lines.append(
                "### 推理指标\n\n- "
                + _compact_inline(t.reasoning_metrics, 400)
            )

    lines.append("")
    return "\n".join(lines)


def _write_case_digest(
    result: BenchResult,
    case: BenchCase,
    *,
    suite_name: str,
    output_dir: Path | None,
    run_file: Path | None,
) -> Path | None:
    """把单用例 Markdown 摘要落盘到 conversations/{case_id}.digest.md。"""
    if output_dir is None:
        return None
    try:
        digest = _build_case_digest(
            result,
            case,
            suite_name=suite_name,
            output_dir=output_dir,
            run_file=run_file,
        )
        conv_dir = output_dir / "conversations"
        conv_dir.mkdir(parents=True, exist_ok=True)
        path = conv_dir / f"{result.case_id}.digest.md"
        path.write_text(digest, encoding="utf-8")
        return path
    except Exception:
        logger.debug("用例 %s digest 落盘失败", result.case_id, exc_info=True)
        return None


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
    total_model_tool_failures = sum(
        sum(1 for tc in r.tool_calls if not tc.success and not tc.parent_call_id)
        for r in results
    )
    total_distinct_tool_errors = sum(
        count_distinct_tool_errors(r.tool_calls) for r in results
    )
    failed_case_ids = [r.case_id for r in results if r.status != "ok"]
    suite_status = "ok" if not failed_case_ids else "completed_with_errors"

    summary: dict[str, Any] = {
        "schema_version": 3,
        "kind": "suite_summary",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "meta": {
            "suite_name": suite_name,
            "suite_path": str(suite_path),
            "case_count": len(results),
            "pipeline": "fake_frontend",
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
            "model_tool_failures": total_model_tool_failures,
            "internal_tool_failures": total_tool_failures - total_model_tool_failures,
            "distinct_tool_errors": total_distinct_tool_errors,
        },
    }
    # 断言校验聚合（仅当 suite 声明了 assertions/golden 时存在）
    validations = [
        (r.case_id, r.validation) for r in results if r.validation is not None
    ]
    suite_validation = aggregate_suite_validation(validations) if validations else None
    if suite_validation is not None:
        summary["validation"] = suite_validation.to_dict()

    _write_json(filepath, summary)

    # 自动 Markdown 报告（bench_reporter）：失败也不影响主流程
    try:
        report_path = save_suite_report(
            summary, output_dir, suite_validation=suite_validation,
        )
        logger.info("套件报告: %s", report_path)
    except Exception:
        logger.warning("套件 Markdown 报告生成失败", exc_info=True)
    return filepath


async def run_suite(
    suite_path: str | Path,
    config: ExcelManusConfig,
    output_dir: Path,
    *,
    concurrency: int = 1,
    trace_enabled: bool = True,
    on_progress: ProgressCallback | None = None,
    turn_timeout: float = 0.0,
    case_ids: list[str] | None = None,
    wave: str = "",
) -> list[BenchResult]:
    """运行整个测试套件（可按 case id / wave 标签裁剪）。"""
    if concurrency < 1:
        raise ValueError("concurrency 必须 >= 1")

    suite_name, cases, suite_trace = _load_suite(suite_path)
    if case_ids or wave:
        total_before = len(cases)
        cases = _filter_cases(cases, wave=wave, case_ids=case_ids)
        if not cases:
            raise ValueError(
                f"过滤后没有用例: suite={suite_path} "
                f"case={case_ids or []} wave={wave or '∅'}"
            )
        logger.info(
            "用例过滤: %d/%d 命中 (case=%s wave=%s)",
            len(cases), total_before, case_ids or "∅", wave or "∅",
        )
    # suite JSON 中的 trace 字段与参数取 OR
    trace_enabled = trace_enabled or suite_trace
    logger.info("═" * 50)
    logger.info(
        "开始执行套件: %s (%d 个用例, 并发=%d%s)",
        suite_name,
        len(cases),
        concurrency,
        ", trace=ON" if trace_enabled else "",
    )
    logger.info("═" * 50)

    runtime: ChatRuntime | None = None
    shared_manager: SessionManager | None = None
    if hasattr(config, "workspace_root"):
        try:
            runtime = build_chat_runtime(config)
            shared_manager = runtime.manager
        except Exception:
            logger.warning("共享 ChatRuntime 初始化失败，各用例独立建会话", exc_info=True)

    async def _execute_case(
        index: int,
        case: BenchCase,
        *,
        render_enabled: bool,
    ) -> tuple[int, BenchResult, Path]:
        # CLI 级默认只在 case/suite 未声明时生效
        if case.turn_timeout <= 0 and turn_timeout > 0:
            case.turn_timeout = turn_timeout
        if on_progress:
            on_progress(case.id, case.name, None)
        try:
            result = await run_case(
                case, config,
                render_enabled=render_enabled,
                trace_enabled=trace_enabled,
                output_dir=output_dir,
                suite_name=suite_name,
                session_manager=shared_manager,
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
                pipeline="fake_frontend",
            )
        filepath = _save_result(result, output_dir)
        logger.info("  日志已保存: %s", filepath)
        digest_path = _write_case_digest(
            result,
            case,
            suite_name=suite_name,
            output_dir=output_dir,
            run_file=filepath,
        )
        if digest_path is not None:
            logger.info("  分析摘要: %s", digest_path)
        if on_progress:
            on_progress(case.id, case.name, result)
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
    try:
        return normalized_results
    finally:
        if runtime is not None:
            await runtime.aclose()


# ── 凭据导入（test.env → model_profiles）──────────────────


def _parse_env_file(path: str | Path) -> dict[str, str]:
    """解析 .env 风格文件：KEY=VALUE，跳过注释与空行。"""
    rows: dict[str, str] = {}
    for raw_line in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        rows[key.strip()] = value
    return rows


def _normalize_gateway_url(raw: str) -> str:
    """把网关 URL 规范化为 OpenAI 兼容 base（去掉补全端点后缀与尾斜杠）。"""
    url = (raw or "").strip().rstrip("/")
    for suffix in ("/chat/completions", "/completions", "/embeddings"):
        if url.endswith(suffix):
            url = url[: -len(suffix)]
    return url


def import_env_to_database(
    env_path: str | Path,
    *,
    profile_name: str = "bench-test-gateway",
    model: str = "",
    activate: bool = True,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    """把 .env 风格凭据文件导入 model_profiles，并（默认）设为激活模型。

    识别的键：``url``/``base_url``/``EXCELMANUS_BASE_URL``、
    ``key``/``api_key``/``EXCELMANUS_API_KEY``、``model``/``EXCELMANUS_MODEL``。
    已存在同名档案时做更新（幂等），因此重复导入安全。

    Returns:
        导入摘要 dict（profile_name/model/base_url/db_path/action/activated）。
    """
    from excelmanus.data_home import get_default_db_path
    from excelmanus.database import Database
    from excelmanus.stores.config_store import GlobalConfigStore, UserConfigStore

    rows = _parse_env_file(env_path)
    base_url = _normalize_gateway_url(
        rows.get("base_url") or rows.get("EXCELMANUS_BASE_URL") or rows.get("url") or ""
    )
    api_key = (
        rows.get("api_key") or rows.get("EXCELMANUS_API_KEY") or rows.get("key") or ""
    ).strip()
    model_name = (
        model or rows.get("model") or rows.get("EXCELMANUS_MODEL") or ""
    ).strip()
    if not base_url or not api_key:
        raise ValueError(
            f"凭据文件 {env_path} 缺少 url/key（或 base_url/api_key）键，无法导入"
        )
    if not model_name:
        raise ValueError(
            f"凭据文件 {env_path} 未提供模型名，请通过 --model 指定（如 mimo-v2.5-pro）"
        )

    resolved_db = str(db_path) if db_path else (
        os.environ.get("EXCELMANUS_DB_PATH", "").strip() or str(get_default_db_path())
    )
    database = Database(resolved_db)
    try:
        store = GlobalConfigStore(database)
        existing = store.get_profile(profile_name)
        if existing is not None:
            store.update_profile(
                profile_name, model=model_name, api_key=api_key, base_url=base_url,
            )
            action = "updated"
        else:
            store.add_profile(
                profile_name,
                model_name,
                api_key=api_key,
                base_url=base_url,
            )
            action = "added"
        if activate:
            UserConfigStore(database.conn).set_active_model(profile_name)
    finally:
        database.close()

    summary = {
        "profile_name": profile_name,
        "model": model_name,
        "base_url": base_url,
        "db_path": resolved_db,
        "action": action,
        "activated": activate,
    }
    logger.info(
        "已导入模型档案 %s (%s)：model=%s base_url=%s db=%s",
        profile_name, action, model_name, base_url, resolved_db,
    )
    return summary


def _load_config_for_bench() -> ExcelManusConfig:
    """加载配置；设置未配凭证时回退到数据库激活档案。"""
    from excelmanus.data_home import resolve_db_path
    from excelmanus.database import Database
    from excelmanus.settings_persist import bind_settings_store

    try:
        return load_config()
    except ConfigError:
        resolved = resolve_db_path()
        if not Path(resolved).is_file():
            raise
        database = Database(resolved)
        bind_settings_store(database)
        return load_config()


# ── 入口 ──────────────────────────────────────────────────


async def run_single(
    message: str,
    config: ExcelManusConfig,
    output_dir: Path,
    *,
    trace_enabled: bool = True,
    turn_timeout: float = 0.0,
) -> BenchResult:
    """直接运行一条用户消息作为测试用例。"""
    case = BenchCase(
        id="adhoc",
        name="临时用例",
        message=message,
        messages=[message],
        turn_timeout=turn_timeout,
    )
    result = await run_case(
        case, config,
        render_enabled=True,
        trace_enabled=trace_enabled,
        output_dir=output_dir,
    )
    filepath = _save_result(result, output_dir)
    logger.info("日志已保存: %s", filepath)
    digest_path = _write_case_digest(
        result,
        case,
        suite_name="adhoc",
        output_dir=output_dir,
        run_file=filepath,
    )
    if digest_path is not None:
        logger.info("分析摘要: %s", digest_path)
    return result


@dataclass
class _RunPlan:
    """bench CLI 解析后的执行计划。"""

    mode: str
    suite_paths: list[Path] = field(default_factory=list)
    message: str = ""
    env_path: str = ""
    model: str = ""
    profile_name: str = "bench-test-gateway"
    activate: bool = True
    turn_timeout: float = 0.0
    case_ids: list[str] = field(default_factory=list)
    wave: str = ""
    strict_efficiency: bool = False


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
        help="运行 bench/cases/ 下默认套件（跳过 include_in_all=false）",
    )
    group.add_argument(
        "--message",
        help="显式指定单条消息作为用例",
    )
    group.add_argument(
        "--import-env",
        metavar="PATH",
        help="把本地凭据清单（如 test.env 的 url/key）导入主库 "
        "model_profiles 并设为激活模型，然后退出。",
    )
    parser.add_argument(
        "--model",
        default="",
        help="与 --import-env 搭配：档案使用的模型名（清单未提供 model 键时必填）",
    )
    parser.add_argument(
        "--profile-name",
        default="bench-test-gateway",
        help="与 --import-env 搭配：导入的档案名（默认 bench-test-gateway）",
    )
    parser.add_argument(
        "--no-activate",
        action="store_true",
        help="与 --import-env 搭配：只导入不设为激活模型",
    )
    parser.add_argument(
        "--turn-timeout",
        type=float,
        default=0.0,
        metavar="SECONDS",
        help="单轮硬超时（秒），防止审批/网络挂起导致卡死；0 = 不限制（默认）。"
        "suite/case 级 turn_timeout 优先于该值",
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
        "--case",
        action="append",
        default=[],
        metavar="ID",
        help="只运行指定用例 id（可多次传入）；替代 filter_suite 中间产物",
    )
    parser.add_argument(
        "--wave",
        default="",
        metavar="N",
        help="只运行带 wave-N 标签的用例",
    )
    parser.add_argument(
        "--strict-efficiency",
        action="store_true",
        help="严格模式：效率预算（max_llm_calls 等 warn-only 项）超限也判 fail。"
        "默认关闭（只告警）；nightly 回归收紧时可用。",
    )
    trace_group = parser.add_mutually_exclusive_group()
    trace_group.add_argument(
        "--trace",
        action="store_true",
        dest="trace",
        default=True,
        help="启用 engine 内部交互轨迹记录（默认行为，可显式指定）",
    )
    trace_group.add_argument(
        "--no-trace",
        action="store_false",
        dest="trace",
        help="禁用 engine 内部交互轨迹记录。"
        "也可通过 EXCELMANUS_BENCH_TRACE=0 环境变量禁用。",
    )
    return parser


def _is_json_like_path(raw: str) -> bool:
    """判断参数是否满足 suite 文件路径语义。"""
    return raw.lower().endswith(".json")


def _resolve_run_mode(args: argparse.Namespace) -> _RunPlan:
    """将 argparse 结果映射为执行计划。"""
    targets = list(args.targets or [])

    # 新增 CLI 参数在旧测试桩的 Namespace 中可能缺失，统一 getattr 兜底
    common: dict[str, Any] = {
        "turn_timeout": float(getattr(args, "turn_timeout", 0.0) or 0.0),
        "case_ids": [
            str(item).strip()
            for item in (getattr(args, "case", None) or [])
            if str(item).strip()
        ],
        "wave": str(getattr(args, "wave", "") or "").strip(),
        "strict_efficiency": bool(getattr(args, "strict_efficiency", False)),
    }
    import_env = getattr(args, "import_env", None)
    if import_env is not None:
        if targets:
            raise ValueError("使用 --import-env 时不应再传入位置参数。")
        return _RunPlan(
            mode="import_env",
            env_path=import_env,
            model=getattr(args, "model", "") or "",
            profile_name=getattr(args, "profile_name", "") or "bench-test-gateway",
            activate=not getattr(args, "no_activate", False),
            **common,
        )

    if args.suite is not None:
        if targets:
            raise ValueError("使用 --suite 时不应再传入位置参数。")
        return _RunPlan(
            mode="suite",
            suite_paths=[Path(p) for p in args.suite],
            **common,
        )

    if args.all:
        if targets:
            raise ValueError("使用 --all 时不应再传入位置参数。")
        return _RunPlan(mode="all", **common)

    if args.message is not None:
        if targets:
            raise ValueError("使用 --message 时不应再传入位置参数。")
        return _RunPlan(mode="message", message=args.message, **common)

    if not targets:
        return _RunPlan(mode="help")

    # 智能识别：全部看起来像 *.json 时，视为 suite 模式；否则按 message 模式。
    if all(_is_json_like_path(item) for item in targets):
        return _RunPlan(
            mode="suite",
            suite_paths=[Path(item) for item in targets],
            **common,
        )

    return _RunPlan(mode="message", message=" ".join(targets), **common)


async def _run_suites(
    suite_paths: list[Path],
    config: ExcelManusConfig,
    output_dir: Path,
    *,
    concurrency: int = 1,
    suite_concurrency: int = 1,
    trace_enabled: bool = True,
    turn_timeout: float = 0.0,
    case_ids: list[str] | None = None,
    wave: str = "",
    strict_efficiency: bool = False,
) -> int:
    """并发运行多个 suite，带 Rich Live 进度面板和全局汇总。

    返回 shell 退出码（0 = 全部成功，1 = 存在失败）。
    ``strict_efficiency`` 为真时效率 warn 也判 fail（见 --strict-efficiency）。
    """
    total_suites = len(suite_paths)
    global_start = time.monotonic()

    # 每个 suite 的进度追踪
    progress_map: dict[str, _SuiteProgress] = {}
    suite_results: list[tuple[str, list[BenchResult]]] = []
    results_lock = asyncio.Lock()

    def _short_name(p: Path) -> str:
        return p.stem

    # 预加载 suite 获取 case 数量（与 run_suite 同样的过滤口径）
    for p in suite_paths:
        name = _short_name(p)
        try:
            _, cases, _ = _load_suite(p)
            total = len(_filter_cases(cases, wave=wave, case_ids=case_ids))
        except Exception:
            total = 0
        progress_map[name] = _SuiteProgress(suite_name=name, total_cases=total)

    def _build_progress_table() -> Table:
        """构建实时进度表格。"""
        elapsed = time.monotonic() - global_start
        elapsed_str = f"{elapsed:.0f}s" if elapsed < 60 else f"{elapsed / 60:.1f}m"
        # 全局统计
        g_done = sum(p.done_cases for p in progress_map.values())
        g_total = sum(p.total_cases for p in progress_map.values())
        g_ok = sum(p.ok_cases for p in progress_map.values())
        g_fail = sum(p.fail_cases for p in progress_map.values())
        g_tok = sum(p.total_tokens for p in progress_map.values())

        title = (
            f"Bench 并发执行面板  ⏱ {elapsed_str}"
            f"  │  {g_done}/{g_total} cases"
            f"  {g_ok}✅ {g_fail}❌"
            f"  │  {g_tok:,} tok"
        )
        table = Table(title=title, show_lines=True, expand=True)
        table.add_column("Suite", style="cyan", min_width=20, max_width=35)
        table.add_column("进度", min_width=12, max_width=18)
        table.add_column("状态", min_width=30)
        table.add_column("耗时", justify="right", min_width=6, max_width=8)
        table.add_column("Tokens", justify="right", min_width=8, max_width=12)

        for prog in progress_map.values():
            # 进度列
            if prog.done_cases > 0 or prog.status.startswith("🔄"):
                progress_col = prog.progress_bar()
            else:
                progress_col = ""

            # 状态列
            if prog.status.startswith("🔄"):
                # 执行中：显示当前 case 和通过/失败
                parts = []
                if prog.ok_cases or prog.fail_cases:
                    parts.append(f"{prog.ok_cases}✅")
                    if prog.fail_cases:
                        parts.append(f"{prog.fail_cases}❌")
                if prog.current_case:
                    case_display = prog.current_case
                    if len(case_display) > 20:
                        case_display = case_display[:18] + "…"
                    parts.append(f"▸ {case_display}")
                status_col = "🔄 " + "  ".join(parts) if parts else "🔄 执行中"
            else:
                status_col = prog.status

            # 耗时列
            if prog.status.startswith("⏳"):
                time_col = ""
            else:
                time_col = prog.elapsed_str()

            # Token 列
            tok_col = f"{prog.total_tokens:,}" if prog.total_tokens else ""

            table.add_row(prog.suite_name, progress_col, status_col, time_col, tok_col)

        return table

    def _make_progress_cb(name: str) -> ProgressCallback:
        """为指定 suite 创建进度回调。"""
        def _cb(case_id: str, case_name: str, result: BenchResult | None) -> None:
            prog = progress_map[name]
            if result is None:
                # case 开始执行
                prog.current_case = case_name or case_id
            else:
                # case 完成
                prog.done_cases += 1
                prog.total_tokens += result.total_tokens
                if result.status == "ok":
                    prog.ok_cases += 1
                else:
                    prog.fail_cases += 1
                prog.current_case = ""
        return _cb

    async def _suite_worker(
        suite_path: Path,
        sem: asyncio.Semaphore,
    ) -> None:
        name = _short_name(suite_path)
        async with sem:
            prog = progress_map[name]
            prog.status = "🔄 执行中"
            prog.start_time = time.monotonic()
            try:
                try:
                    results = await run_suite(
                        suite_path,
                        config,
                        output_dir,
                        concurrency=concurrency,
                        trace_enabled=trace_enabled,
                        on_progress=_make_progress_cb(name),
                        turn_timeout=turn_timeout,
                        case_ids=case_ids,
                        wave=wave,
                    )
                except TypeError as exc:
                    if "on_progress" not in str(exc):
                        raise
                    # 兼容旧测试桩 / 自定义 wrapper：不支持 on_progress 时退化调用。
                    results = await run_suite(
                        suite_path,
                        config,
                        output_dir,
                        concurrency=concurrency,
                        trace_enabled=trace_enabled,
                        turn_timeout=turn_timeout,
                    )
                ok_count = sum(1 for r in results if r.status == "ok")
                fail_count = len(results) - ok_count
                if fail_count:
                    prog.status = f"⚠️  完成 ({ok_count}✅ {fail_count}❌)"
                else:
                    prog.status = f"✅ 完成 ({ok_count} 用例)"
                async with results_lock:
                    suite_results.append((name, results))
            except Exception as exc:
                prog.status = f"💥 崩溃: {exc}"
                async with results_lock:
                    suite_results.append((name, []))

    sem = asyncio.Semaphore(suite_concurrency)
    is_parallel = suite_concurrency > 1 and total_suites > 1

    if is_parallel:
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
            while not all(t.done() for t in tasks):
                live.update(_build_progress_table())
                await asyncio.sleep(0.5)
            live.update(_build_progress_table())

        for t in tasks:
            if t.exception():  # pragma: no cover
                logger.error("suite 任务异常: %s", t.exception())
    else:
        # 串行模式：逐个执行，保持原有日志输出
        for suite_path in suite_paths:
            await _suite_worker(suite_path, sem)

    # ── 全局汇总报告 ──
    all_results = [r for _, results in suite_results for r in results]

    def _case_failed(r: BenchResult) -> bool:
        """执行失败或 error 级断言失败视为未通过；warn-only 效率告警不影响退出码。

        strict_efficiency 模式下 warn 也判 fail（nightly 收紧用）。
        """
        if r.status != "ok":
            return True
        v = r.validation
        if v is None:
            return False
        if v.results:
            def _failed(res: Any) -> bool:
                passed = res.passed if hasattr(res, "passed") else res.get("passed", True)
                return not passed
            def _sev(res: Any) -> str:
                return res.severity if hasattr(res, "severity") else res.get("severity", "error")
            if strict_efficiency:
                return any(_failed(res) for res in v.results)
            return any(_failed(res) and _sev(res) == "error" for res in v.results)
        return v.failed > 0

    if all_results:
        total_cases = len(all_results)
        total_ok = sum(1 for r in all_results if r.status == "ok")
        total_fail = total_cases - total_ok
        total_tokens = sum(r.total_tokens for r in all_results)
        total_duration = sum(r.duration_seconds for r in all_results)
        total_tool_failures = sum(
            sum(1 for tc in r.tool_calls if not tc.success) for r in all_results
        )
        total_model_tool_failures = sum(
            sum(1 for tc in r.tool_calls if not tc.success and not tc.parent_call_id)
            for r in all_results
        )
        total_distinct_tool_errors = sum(
            count_distinct_tool_errors(r.tool_calls) for r in all_results
        )
        validation_failed_assertions = sum(
            r.validation.failed for r in all_results if r.validation is not None
        )
        assertion_failed_cases = sum(
            1 for r in all_results if r.validation is not None and r.validation.failed > 0
        )

        # 保存全局汇总 JSON
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        short_id = uuid.uuid4().hex[:6]
        global_summary = {
            "schema_version": 3,
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
                "model_tool_failures": total_model_tool_failures,
                "internal_tool_failures": total_tool_failures - total_model_tool_failures,
                "distinct_tool_errors": total_distinct_tool_errors,
                "validation_failed_assertions": validation_failed_assertions,
                "assertion_failed_cases": assertion_failed_cases,
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
        _write_json(global_path, global_summary)

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
            "  总 %d tokens │ 总 %.1fs │ 工具失败 %d 次（模型可见 %d，root %d）",
            total_tokens,
            total_duration,
            total_tool_failures,
            total_model_tool_failures,
            total_distinct_tool_errors,
        )
        if validation_failed_assertions:
            logger.info(
                "  断言失败: %d 条（涉及 %d 个用例）",
                validation_failed_assertions,
                assertion_failed_cases,
            )
        logger.info("  全局汇总: %s", global_path)
        logger.info("═" * 60)

    return 1 if any(_case_failed(r) for r in all_results) else 0


async def _main(argv: list[str] | None = None) -> int:
    """脚本入口，返回 shell 退出码。"""
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        plan = _resolve_run_mode(args)
    except ValueError as exc:
        logger.error("参数错误：%s", exc)
        return 1

    if plan.mode == "help":
        parser.print_help()
        return 0

    # trace 模式：默认开启，可通过 --no-trace 或 EXCELMANUS_BENCH_TRACE=0 禁用
    trace_enabled = args.trace and os.environ.get("EXCELMANUS_BENCH_TRACE", "1") != "0"
    turn_timeout = max(0.0, float(plan.turn_timeout or 0.0))

    if plan.mode == "import_env":
        try:
            import_env_to_database(
                plan.env_path,
                profile_name=plan.profile_name,
                model=plan.model,
                activate=plan.activate,
            )
        except (OSError, ValueError) as exc:
            logger.error("导入失败：%s", exc)
            return 1
        return 0

    if plan.mode == "message":
        config = _load_config_for_bench()
        setup_logging(config.log_level)
        output_dir = Path(args.output_dir)
        await run_single(
            plan.message,
            config,
            output_dir,
            trace_enabled=trace_enabled,
            turn_timeout=turn_timeout,
        )
        return 0

    if plan.mode == "suite":
        missing_paths = [p for p in plan.suite_paths if not p.is_file()]
        if missing_paths:
            for p in missing_paths:
                logger.error("未找到 suite 文件: %s", p)
            return 1
        config = _load_config_for_bench()
        setup_logging(config.log_level)
        output_dir = Path(args.output_dir)
        return await _run_suites(
            plan.suite_paths,
            config,
            output_dir,
            concurrency=args.concurrency,
            suite_concurrency=args.suite_concurrency,
            trace_enabled=trace_enabled,
            turn_timeout=turn_timeout,
            case_ids=plan.case_ids,
            wave=plan.wave,
            strict_efficiency=plan.strict_efficiency,
        )

    # all 模式：只跑默认套件，体验向长套件需显式 --suite
    cases_dir = Path("bench/cases")
    if not cases_dir.is_dir():
        logger.error("未找到测试用例目录: %s", cases_dir)
        return 1

    suite_paths = list_default_suite_paths(cases_dir)
    if not suite_paths:
        logger.error("目录 %s 下无默认 JSON 用例文件", cases_dir)
        return 1

    config = _load_config_for_bench()
    setup_logging(config.log_level)
    output_dir = Path(args.output_dir)
    return await _run_suites(
        suite_paths,
        config,
        output_dir,
        concurrency=args.concurrency,
        suite_concurrency=args.suite_concurrency,
        trace_enabled=trace_enabled,
        turn_timeout=turn_timeout,
        case_ids=plan.case_ids,
        wave=plan.wave,
        strict_efficiency=plan.strict_efficiency,
    )


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
