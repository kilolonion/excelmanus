"""Agent 核心引擎：Tool Calling 循环与 LLM 交互。"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

import openai

from excelmanus.config import ExcelManusConfig
from excelmanus.events import EventCallback, EventType, ToolCallEvent
from excelmanus.logger import get_logger, log_tool_call
from excelmanus.memory import ConversationMemory
from excelmanus.security import FileAccessGuard
from excelmanus.skills import SkillRegistry

logger = get_logger("engine")


def _to_plain(value: Any) -> Any:
    """将 SDK 对象/命名空间对象转换为纯 Python 结构。"""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {k: _to_plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_plain(v) for v in value]

    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return _to_plain(model_dump(exclude_none=False))
        except TypeError:
            return _to_plain(model_dump())

    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return _to_plain(to_dict())

    if hasattr(value, "__dict__"):
        return {
            k: _to_plain(v)
            for k, v in vars(value).items()
            if not k.startswith("_")
        }

    return str(value)


def _assistant_message_to_dict(message: Any) -> dict[str, Any]:
    """提取 assistant 消息字典，尽量保留供应商扩展字段。"""
    payload = _to_plain(message)
    if not isinstance(payload, dict):
        payload = {"content": str(getattr(message, "content", "") or "")}

    payload["role"] = "assistant"
    return payload


def _summarize_text(text: str, max_len: int = 120) -> str:
    """将文本压缩为单行摘要，避免日志过长。"""
    compact = " ".join(text.split())
    if not compact:
        return "(空)"
    if len(compact) <= max_len:
        return compact
    return f"{compact[: max_len - 3]}..."


# ── 数据模型 ──────────────────────────────────────────────


@dataclass
class ToolCallResult:
    """单次工具调用的结果记录。"""

    tool_name: str
    arguments: dict
    result: str
    success: bool
    error: str | None = None


@dataclass
class ChatResult:
    """一次 chat 调用的完整结果。"""

    reply: str
    tool_calls: list[ToolCallResult] = field(default_factory=list)
    iterations: int = 0
    truncated: bool = False


# ── AgentEngine ───────────────────────────────────────────


class AgentEngine:
    """核心代理引擎，驱动 LLM 与工具之间的 Tool Calling 循环。

    使用 AsyncOpenAI 客户端与 OpenAI Chat Completions API（tool calling 语义），
    支持单轮多 tool_calls、迭代上限保护和连续失败熔断。
    """

    def __init__(self, config: ExcelManusConfig, registry: SkillRegistry) -> None:
        self._client = openai.AsyncOpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
        )
        self._config = config
        self._registry = registry
        self._memory = ConversationMemory(config)
        self._file_guard = FileAccessGuard(config.workspace_root)

    @property
    def memory(self) -> ConversationMemory:
        """暴露 memory 供外部访问（如测试）。"""
        return self._memory
    def _emit(self, on_event: EventCallback | None, event: ToolCallEvent) -> None:
        """安全地发出事件，捕获回调异常。"""
        if on_event is None:
            return
        try:
            on_event(event)
        except Exception as exc:
            logger.warning("事件回调异常: %s", exc)

    async def chat(
        self,
        user_message: str,
        on_event: EventCallback | None = None,
    ) -> str:
        """执行 Tool Calling 循环，返回最终文本回复。

        流程：
        1) 追加 user message 到对话记忆
        2) 调用 LLM（附 tools schema）
        3) 若响应包含 tool_calls：解析、执行工具、结果回填，继续循环
        4) 若响应为纯文本：返回最终回复
        5) 达到 max_iterations：截断返回
        6) 连续失败超过 max_consecutive_failures：熔断返回

        Args:
            user_message: 用户输入的自然语言指令。
            on_event: 可选的事件回调函数，用于接收工具调用过程中的结构化事件。
        """
        # 追加用户消息
        self._memory.add_user_message(user_message)
        logger.info("用户指令摘要: %s", _summarize_text(user_message))

        # 获取工具 schema
        # 当前实现使用 Chat Completions，需传入其兼容的 tool schema 结构。
        tools = self._registry.get_openai_schemas(mode="chat_completions")

        max_iter = self._config.max_iterations
        max_failures = self._config.max_consecutive_failures
        consecutive_failures = 0
        all_tool_results: list[ToolCallResult] = []

        for iteration in range(1, max_iter + 1):
            # 发出迭代开始事件
            self._emit(
                on_event,
                ToolCallEvent(
                    event_type=EventType.ITERATION_START,
                    iteration=iteration,
                ),
            )

            # 构建请求参数
            messages = self._memory.get_messages()
            kwargs: dict = {
                "model": self._config.model,
                "messages": messages,
            }
            if tools:
                kwargs["tools"] = tools

            # 调用 LLM
            response = await self._client.chat.completions.create(**kwargs)
            choice = response.choices[0]
            message = choice.message

            # 提取 LLM 思考内容（兼容不同供应商的字段名）
            thinking_content = ""
            for thinking_key in ("thinking", "reasoning", "reasoning_content"):
                candidate = getattr(message, thinking_key, None)
                if candidate:
                    thinking_content = str(candidate)
                    break

            # 发出思考事件
            if thinking_content:
                self._emit(
                    on_event,
                    ToolCallEvent(
                        event_type=EventType.THINKING,
                        thinking=thinking_content,
                        iteration=iteration,
                    ),
                )

            # 情况 1：纯文本回复，终止循环
            if not message.tool_calls:
                reply_text = message.content or ""
                self._memory.add_assistant_message(reply_text)
                logger.info("最终结果摘要: %s", _summarize_text(reply_text))
                return reply_text

            # 情况 2：包含 tool_calls，逐个执行
            # 先将完整的 assistant 消息（含所有 tool_calls）加入记忆
            assistant_msg = _assistant_message_to_dict(message)
            self._memory.add_assistant_tool_message(assistant_msg)

            breaker_triggered = False
            breaker_summary = ""
            breaker_skip_error = (
                f"工具未执行：连续 {max_failures} 次工具调用失败，已触发熔断。"
            )

            # 逐个处理 tool_calls
            for tc in message.tool_calls:
                function = getattr(tc, "function", None)
                tool_name = getattr(function, "name", "")
                raw_args = getattr(function, "arguments", None)
                tool_call_id = getattr(tc, "id", "")

                if breaker_triggered:
                    # 熔断后补齐当前轮剩余 tool_call 的 tool_result，保证消息闭环。
                    all_tool_results.append(
                        ToolCallResult(
                            tool_name=tool_name,
                            arguments={},
                            result=breaker_skip_error,
                            success=False,
                            error=breaker_skip_error,
                        )
                    )
                    self._memory.add_tool_result(tool_call_id, breaker_skip_error)
                    continue

                # 解析参数
                parse_error: str | None = None
                try:
                    if raw_args is None or raw_args == "":
                        arguments: dict[str, Any] = {}
                    elif isinstance(raw_args, dict):
                        arguments = raw_args
                    elif isinstance(raw_args, str):
                        parsed = json.loads(raw_args)
                        if not isinstance(parsed, dict):
                            parse_error = (
                                f"参数必须为 JSON 对象，当前类型: {type(parsed).__name__}"
                            )
                            arguments = {}
                        else:
                            arguments = parsed
                    else:
                        parse_error = (
                            f"参数类型无效: {type(raw_args).__name__}"
                        )
                        arguments = {}
                except (json.JSONDecodeError, TypeError) as exc:
                    parse_error = f"JSON 解析失败: {exc}"
                    arguments = {}

                # 发出工具调用开始事件
                self._emit(
                    on_event,
                    ToolCallEvent(
                        event_type=EventType.TOOL_CALL_START,
                        tool_name=tool_name,
                        arguments=arguments,
                        iteration=iteration,
                    ),
                )

                if parse_error is not None:
                    result_str = f"工具参数解析错误: {parse_error}"
                    success = False
                    error = result_str
                    consecutive_failures += 1
                    log_tool_call(
                        logger,
                        tool_name,
                        {"_raw_arguments": raw_args},
                        error=error,
                    )
                else:
                    # 执行工具
                    try:
                        # 使用 asyncio.to_thread 隔离阻塞型工具调用
                        result_value = await asyncio.to_thread(
                            self._registry.call_tool, tool_name, arguments
                        )
                        result_str = str(result_value)
                        success = True
                        error = None
                        consecutive_failures = 0  # 重置连续失败计数

                        log_tool_call(logger, tool_name, arguments, result=result_str)

                    except Exception as exc:
                        result_str = f"工具执行错误: {exc}"
                        success = False
                        error = str(exc)
                        consecutive_failures += 1

                        log_tool_call(logger, tool_name, arguments, error=error)

                # 发出工具调用结束事件
                self._emit(
                    on_event,
                    ToolCallEvent(
                        event_type=EventType.TOOL_CALL_END,
                        tool_name=tool_name,
                        arguments=arguments,
                        result=result_str,
                        success=success,
                        error=error,
                        iteration=iteration,
                    ),
                )

                # 记录工具调用结果
                all_tool_results.append(
                    ToolCallResult(
                        tool_name=tool_name,
                        arguments=arguments,
                        result=result_str,
                        success=success,
                        error=error,
                    )
                )

                # 将工具结果回填到对话记忆
                self._memory.add_tool_result(tool_call_id, result_str)

                # 检查连续失败熔断
                if (not breaker_triggered) and consecutive_failures >= max_failures:
                    # 收集触发熔断前的最近失败摘要
                    recent_errors = [
                        f"- {r.tool_name}: {r.error}"
                        for r in all_tool_results[-max_failures:]
                        if not r.success
                    ]
                    breaker_summary = "\n".join(recent_errors)
                    breaker_triggered = True

            if breaker_triggered:
                reply = (
                    f"连续 {max_failures} 次工具调用失败，已终止执行。"
                    f"错误摘要：\n{breaker_summary}"
                )
                self._memory.add_assistant_message(reply)
                logger.warning(
                    "连续 %d 次工具失败，熔断终止", max_failures
                )
                logger.info("最终结果摘要: %s", _summarize_text(reply))
                return reply

        # 达到迭代上限
        reply = f"已达到最大迭代次数（{max_iter}），返回当前结果。请尝试简化任务或分步执行。"
        self._memory.add_assistant_message(reply)
        logger.warning("达到迭代上限 %d，截断返回", max_iter)
        logger.info("最终结果摘要: %s", _summarize_text(reply))
        return reply

    def clear_memory(self) -> None:
        """清除对话历史。"""
        self._memory.clear()
