"""Bench 测试运行器：加载用例 JSON → 逐条调用 engine.chat() → 收集完整事件轨迹 → 输出 JSON 日志。

运行方式：
    python -m excelmanus.bench                            # 运行 bench/cases/ 下所有套件
    python -m excelmanus.bench bench/cases/suite_basic.json  # 指定套件文件
"""

from __future__ import annotations

import asyncio
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

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "case_name": self.case_name,
            "message": self.message,
            "timestamp": self.timestamp,
            "duration_seconds": round(self.duration_seconds, 2),
            "iterations": self.iterations,
            "route_mode": self.route_mode,
            "skills_used": self.skills_used,
            "tool_scope": self.tool_scope,
            "tool_calls": [tc.to_dict() for tc in self.tool_calls],
            "thinking_log": self.thinking_log,
            "reply": self.reply,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "subagent_events": self.subagent_events,
            "llm_calls": self.llm_calls,
            "conversation_messages": self.conversation_messages,
            "summary": {
                "tool_call_count": len(self.tool_calls),
                "tool_failures": sum(1 for tc in self.tool_calls if not tc.success),
                "tool_successes": sum(1 for tc in self.tool_calls if tc.success),
            },
        }


# ── 事件收集器 ────────────────────────────────────────────


# 全局 Rich Console
_console = Console()


class _EventCollector:
    """通过 on_event 回调收集引擎事件，同时实时渲染到终端。"""

    def __init__(self) -> None:
        self._renderer = StreamRenderer(_console)
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


async def run_case(case: BenchCase, config: ExcelManusConfig) -> BenchResult:
    """执行单个测试用例，返回完整结果（含完整 LLM 交互日志）。"""
    engine = _create_engine(config)
    collector = _EventCollector()
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
        )
    finally:
        interceptor.restore()

    elapsed = time.monotonic() - start

    result = BenchResult(
        case_id=case.id,
        case_name=case.name,
        message=case.message,
        timestamp=timestamp,
        duration_seconds=elapsed,
        iterations=chat_result.iterations,
        route_mode=collector.route_mode or engine.last_route_result.route_mode,
        skills_used=collector.skills_used or list(engine.last_route_result.skills_used),
        tool_scope=collector.tool_scope or list(engine.last_route_result.tool_scope),
        tool_calls=collector.tool_calls,
        thinking_log=collector.thinking_log,
        reply=chat_result.reply,
        prompt_tokens=chat_result.prompt_tokens,
        completion_tokens=chat_result.completion_tokens,
        total_tokens=chat_result.total_tokens,
        subagent_events=collector.subagent_events,
        llm_calls=interceptor.calls,
        conversation_messages=_dump_conversation_messages(engine),
    )

    # 打印最终回复（和 CLI 一样用 Panel 包裹）
    if result.reply:
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
    results: list[BenchResult],
    output_dir: Path,
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

    summary = {
        "suite_name": suite_name,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "case_count": len(results),
        "total_tokens": total_tokens,
        "total_duration_seconds": round(total_duration, 2),
        "average_iterations": round(avg_iterations, 2),
        "cases": [r.to_dict() for r in results],
    }

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    return filepath


async def run_suite(
    suite_path: str | Path,
    config: ExcelManusConfig,
    output_dir: Path,
) -> list[BenchResult]:
    """运行整个测试套件。"""
    suite_name, cases = _load_suite(suite_path)
    logger.info("═" * 50)
    logger.info("开始执行套件: %s (%d 个用例)", suite_name, len(cases))
    logger.info("═" * 50)

    results: list[BenchResult] = []
    for i, case in enumerate(cases, 1):
        logger.info("── 用例 %d/%d ──", i, len(cases))
        result = await run_case(case, config)
        # 保存单个用例结果
        filepath = _save_result(result, output_dir)
        logger.info("  日志已保存: %s", filepath)
        results.append(result)

    # 保存套件汇总
    summary_path = _save_suite_summary(suite_name, results, output_dir)
    logger.info("═" * 50)
    logger.info("套件执行完毕: %s", suite_name)
    logger.info("  汇总日志: %s", summary_path)

    # 打印简要统计
    total_tokens = sum(r.total_tokens for r in results)
    total_duration = sum(r.duration_seconds for r in results)
    total_failures = sum(
        sum(1 for tc in r.tool_calls if not tc.success) for r in results
    )
    logger.info(
        "  统计: %d 用例 │ 总 %d tokens │ 总 %.1fs │ 工具失败 %d 次",
        len(results),
        total_tokens,
        total_duration,
        total_failures,
    )
    logger.info("═" * 50)
    return results


# ── 入口 ──────────────────────────────────────────────────


async def run_single(message: str, config: ExcelManusConfig, output_dir: Path) -> BenchResult:
    """直接运行一条用户消息作为测试用例。"""
    case = BenchCase(
        id="adhoc",
        name="临时用例",
        message=message,
    )
    result = await run_case(case, config)
    filepath = _save_result(result, output_dir)
    logger.info("日志已保存: %s", filepath)
    return result


async def _main() -> None:
    """脚本入口。

    用法：
        python -m excelmanus.bench "你的测试消息"
        python -m excelmanus.bench --suite bench/cases/suite_basic.json
    """
    config = load_config()
    setup_logging(config.log_level)

    output_dir = Path("outputs/bench")

    args = sys.argv[1:]

    if not args:
        print("用法：")
        print('  python -m excelmanus.bench "读取销售明细前10行"')
        print("  python -m excelmanus.bench --suite bench/cases/suite_basic.json")
        print("  python -m excelmanus.bench --all")
        sys.exit(0)

    if args[0] == "--suite":
        # 运行指定套件文件
        for path in args[1:]:
            p = Path(path)
            if not p.is_file():
                logger.warning("跳过不存在的文件: %s", p)
                continue
            await run_suite(p, config, output_dir)
    elif args[0] == "--all":
        # 运行 bench/cases/ 下所有套件
        cases_dir = Path("bench/cases")
        if not cases_dir.is_dir():
            logger.error("未找到测试用例目录: %s", cases_dir)
            sys.exit(1)
        suite_paths = sorted(cases_dir.glob("*.json"))
        if not suite_paths:
            logger.error("目录 %s 下无 JSON 用例文件", cases_dir)
            sys.exit(1)
        for suite_path in suite_paths:
            await run_suite(suite_path, config, output_dir)
    else:
        # 直接传入消息作为用例
        message = " ".join(args)
        await run_single(message, config, output_dir)


if __name__ == "__main__":
    asyncio.run(_main())
