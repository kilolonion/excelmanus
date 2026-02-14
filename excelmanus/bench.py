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
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

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

# ── 数据模型 ──────────────────────────────────────────────


@dataclass
class BenchCase:
    """单个测试用例。"""

    id: str
    name: str
    message: str
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
    # 执行状态
    status: str = "ok"
    # 结构化错误信息（status=error 时有值）
    error: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        tool_successes = sum(1 for tc in self.tool_calls if tc.success)
        tool_failures = sum(1 for tc in self.tool_calls if not tc.success)
        return {
            "schema_version": 2,
            "kind": "case_result",
            "timestamp": self.timestamp,
            "meta": {
                "case_id": self.case_id,
                "case_name": self.case_name,
                "message": self.message,
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


# ── 执行器 ────────────────────────────────────────────────


def _create_engine(config: ExcelManusConfig) -> AgentEngine:
    """创建独立的 AgentEngine 实例（不复用 session）。"""
    registry = ToolRegistry()
    registry.register_builtin_tools(config.workspace_root)
    loader = SkillpackLoader(config, registry)
    loader.load_all()
    router = SkillRouter(config, loader)
    return AgentEngine(
        config=config,
        registry=registry,
        skill_router=router,
    )


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
) -> BenchResult:
    """执行单个测试用例，返回完整结果（含完整 LLM 交互日志）。"""
    engine = _create_engine(config)
    collector = _EventCollector(render_enabled=render_enabled)
    interceptor = _LLMCallInterceptor(engine)
    timestamp = datetime.now(timezone.utc).isoformat()

    logger.info("▶ 开始执行用例: %s (%s)", case.id, case.name)
    start = time.monotonic()

    try:
        chat_result: ChatResult = await engine.chat(
            case.message,
            on_event=collector.on_event,
        )
    except Exception as exc:
        logger.error("用例 %s 执行异常: %s", case.id, exc, exc_info=True)
        elapsed = time.monotonic() - start
        return BenchResult(
            case_id=case.id,
            case_name=case.name,
            message=case.message,
            timestamp=timestamp,
            duration_seconds=elapsed,
            iterations=0,
            route_mode=collector.route_mode or "error",
            skills_used=collector.skills_used,
            tool_scope=collector.tool_scope,
            tool_calls=collector.tool_calls,
            thinking_log=collector.thinking_log,
            reply=f"[ERROR] {exc}",
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            subagent_events=collector.subagent_events,
            llm_calls=interceptor.calls,
            conversation_messages=_dump_conversation_messages(engine),
            status="error",
            error={
                "type": type(exc).__name__,
                "message": str(exc),
            },
        )
    finally:
        interceptor.restore()

    elapsed = time.monotonic() - start
    last_route = getattr(engine, "last_route_result", None)
    fallback_route_mode = getattr(last_route, "route_mode", "")
    fallback_skills = list(getattr(last_route, "skills_used", []) or [])
    fallback_scope = list(getattr(last_route, "tool_scope", []) or [])

    result = BenchResult(
        case_id=case.id,
        case_name=case.name,
        message=case.message,
        timestamp=timestamp,
        duration_seconds=elapsed,
        iterations=chat_result.iterations,
        route_mode=collector.route_mode or fallback_route_mode or "unknown",
        skills_used=collector.skills_used or fallback_skills,
        tool_scope=collector.tool_scope or fallback_scope,
        tool_calls=collector.tool_calls,
        thinking_log=collector.thinking_log,
        reply=chat_result.reply,
        prompt_tokens=chat_result.prompt_tokens,
        completion_tokens=chat_result.completion_tokens,
        total_tokens=chat_result.total_tokens,
        subagent_events=collector.subagent_events,
        llm_calls=interceptor.calls,
        conversation_messages=_dump_conversation_messages(engine),
        status="ok",
        error=None,
    )

    # 打印最终回复（和 CLI 一样用 Panel 包裹）
    if render_enabled and result.reply:
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
    logger.info(
        "✓ 用例 %s 完成: %d 轮 │ %d/%d 工具调用(失败%d) │ %d tokens │ %.1fs │ %d 次 LLM 调用",
        case.id,
        result.iterations,
        len(result.tool_calls),
        len(result.tool_calls),
        failures,
        result.total_tokens,
        result.duration_seconds,
        len(result.llm_calls),
    )
    return result


def _load_suite(path: str | Path) -> tuple[str, list[BenchCase]]:
    """从 JSON 文件加载测试套件。"""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    suite_name = data.get("suite_name", Path(path).stem)
    cases: list[BenchCase] = []
    for item in data.get("cases", []):
        cases.append(BenchCase(
            id=item["id"],
            name=item.get("name", item["id"]),
            message=item["message"],
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
) -> list[BenchResult]:
    """运行整个测试套件。"""
    if concurrency < 1:
        raise ValueError("concurrency 必须 >= 1")

    suite_name, cases = _load_suite(suite_path)
    logger.info("═" * 50)
    logger.info(
        "开始执行套件: %s (%d 个用例, 并发=%d)",
        suite_name,
        len(cases),
        concurrency,
    )
    logger.info("═" * 50)

    async def _execute_case(
        index: int,
        case: BenchCase,
        *,
        render_enabled: bool,
    ) -> tuple[int, BenchResult, Path]:
        try:
            result = await run_case(case, config, render_enabled=render_enabled)
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


async def run_single(message: str, config: ExcelManusConfig, output_dir: Path) -> BenchResult:
    """直接运行一条用户消息作为测试用例。"""
    case = BenchCase(
        id="adhoc",
        name="临时用例",
        message=message,
    )
    result = await run_case(case, config, render_enabled=True)
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
        help="suite 执行并发度（默认 1）",
    )
    parser.add_argument(
        "--output-dir",
        default="outputs/bench",
        help="日志输出目录（默认 outputs/bench）",
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

    if plan.mode == "message":
        config = load_config()
        setup_logging(config.log_level)
        output_dir = Path(args.output_dir)
        await run_single(plan.message, config, output_dir)
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
        for suite_path in plan.suite_paths:
            await run_suite(
                suite_path,
                config,
                output_dir,
                concurrency=args.concurrency,
            )
        return 0

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
    for suite_path in suite_paths:
        await run_suite(
            suite_path,
            config,
            output_dir,
            concurrency=args.concurrency,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
