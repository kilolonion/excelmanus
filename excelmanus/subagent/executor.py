"""子代理执行器。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

import openai

from excelmanus.approval import ApprovalManager
from excelmanus.config import ExcelManusConfig
from excelmanus.engine_core.workspace_probe import (
    collect_workspace_mtime_index,
    diff_workspace_mtime_paths,
    has_workspace_mtime_changes,
)
from excelmanus.providers import create_client
from excelmanus.events import EventCallback, EventType, ToolCallEvent
from excelmanus.logger import get_logger
from excelmanus.message_serialization import assistant_message_to_dict
from excelmanus.memory import ConversationMemory
from excelmanus.subagent.models import SubagentConfig, SubagentFileChange, SubagentResult
from excelmanus.subagent.tool_filter import FilteredToolRegistry

_SUMMARY_MAX_CHARS = 4000
_FULL_MODE_SUMMARY_MAX_CHARS = 12000
_SUBAGENT_BLOCKED_META_TOOLS = {"activate_skill", "delegate", "delegate_to_subagent", "parallel_delegate", "list_subagents"}
_FULL_MODE_BLOCKED_META_TOOLS = {"delegate", "delegate_to_subagent", "parallel_delegate", "list_subagents"}
logger = get_logger("subagent.executor")

ToolResultEnricher = Callable[[str, dict[str, Any], str, bool], str]


@dataclass
class _ExecResult:
    success: bool
    result: str
    error: str | None = None
    pending_approval_id: str | None = None
    file_changes: list[str] | None = None
    raw_result: str | None = None


class SubagentExecutor:
    """在独立上下文执行子代理循环。"""

    def __init__(
        self,
        *,
        parent_config: ExcelManusConfig,
        parent_registry: Any,
        approval_manager: ApprovalManager,
    ) -> None:
        self._parent_config = parent_config
        self._registry = parent_registry
        self._approval = approval_manager

    async def run(
        self,
        *,
        config: SubagentConfig,
        prompt: str,
        parent_context: str = "",
        on_event: EventCallback | None = None,
        full_access_enabled: bool = False,
        tool_result_enricher: ToolResultEnricher | None = None,
        enriched_contexts: list[str] | None = None,
    ) -> SubagentResult:
        """执行单次子代理任务。"""
        conversation_id = str(uuid4())
        blocked = (
            _FULL_MODE_BLOCKED_META_TOOLS
            if config.capability_mode == "full"
            else _SUBAGENT_BLOCKED_META_TOOLS
        )
        available_tools = [
            name
            for name in (config.allowed_tools or self._registry.get_tool_names())
            if name not in blocked
        ]
        filtered_registry = FilteredToolRegistry(
            parent=self._registry,
            allowed=available_tools if config.allowed_tools else None,
            disallowed=[*config.disallowed_tools, *blocked],
        )
        tool_scope = filtered_registry.get_tool_names()
        system_prompt = self._build_system_prompt(
            config=config,
            parent_context=parent_context,
            enriched_contexts=enriched_contexts,
        )
        persistent_memory, memory_extractor, memory_client = self._create_memory_components(
            config=config
        )
        if persistent_memory is not None:
            core_memory = persistent_memory.load_core()
            if core_memory:
                system_prompt = f"{system_prompt}\n\n## 子代理持久记忆\n{core_memory}"
        memory = ConversationMemory(self._parent_config)
        memory.add_user_message(prompt)

        self._emit_safe(
            on_event,
            ToolCallEvent(
                event_type=EventType.SUBAGENT_START,
                subagent_name=config.name,
                subagent_reason=prompt,
                subagent_tools=tool_scope,
                subagent_success=True,
                subagent_permission_mode=config.permission_mode,
                subagent_conversation_id=conversation_id,
            ),
        )

        iterations = 0
        tool_calls = 0
        pending_id: str | None = None
        structured_changes: list[SubagentFileChange] = []
        observed_files: set[str] = set()
        consecutive_failures = 0
        last_summary = ""
        success = True
        error: str | None = None

        client = create_client(
            api_key=config.api_key or self._parent_config.api_key,
            base_url=config.base_url or self._parent_config.base_url,
        )
        model = config.model or self._parent_config.aux_model or self._parent_config.model

        try:
            while iterations < config.max_iterations:
                iterations += 1
                self._emit_safe(
                    on_event,
                    ToolCallEvent(
                        event_type=EventType.SUBAGENT_ITERATION,
                        subagent_name=config.name,
                        subagent_conversation_id=conversation_id,
                        subagent_iterations=iterations,
                        subagent_tool_calls=tool_calls,
                        iteration=iterations,
                    ),
                )
                messages = memory.get_messages(system_prompts=[system_prompt])
                response = await client.chat.completions.create(
                    model=model,
                    messages=messages,
                    tools=filtered_registry.get_openai_schemas(mode="chat_completions")
                    if tool_scope
                    else openai.NOT_GIVEN,
                )
                message = response.choices[0].message
                message_tool_calls = getattr(message, "tool_calls", None)
                if not message_tool_calls:
                    last_summary = str(getattr(message, "content", "") or "").strip()
                    if not last_summary:
                        last_summary = "子代理执行完成，但未返回文本摘要。"
                    memory.add_assistant_message(last_summary)
                    break

                memory.add_assistant_tool_message(assistant_message_to_dict(message))

                breaker_skip_msg = (
                    f"工具未执行：连续 {config.max_consecutive_failures} 次工具调用失败，已触发熔断。"
                )
                pending_skip_msg = "工具未执行：子代理命中待确认操作，当前轮次已终止。"

                for index, tc in enumerate(message_tool_calls):
                    tool_calls += 1
                    call_id = getattr(tc, "id", "")
                    tool_name = getattr(getattr(tc, "function", None), "name", "")
                    raw_args = getattr(getattr(tc, "function", None), "arguments", "{}")
                    try:
                        args = json.loads(raw_args or "{}")
                        if not isinstance(args, dict):
                            raise ValueError("工具参数必须为 JSON 对象")
                    except Exception as exc:  # noqa: BLE001
                        parsed_error = f"子代理工具参数解析失败: {exc}"
                        memory.add_tool_result(call_id, parsed_error)
                        consecutive_failures += 1
                        error = parsed_error
                        success = False
                        if consecutive_failures >= config.max_consecutive_failures:
                            last_summary = (
                                f"子代理连续 {config.max_consecutive_failures} 次失败，已终止。"
                            )
                            success = False
                            tool_calls += self._backfill_tool_results_for_remaining_calls(
                                memory=memory,
                                remaining_calls=message_tool_calls[index + 1:],
                                content=breaker_skip_msg,
                            )
                            break
                        continue

                    observed_files.update(self._extract_excel_paths_from_arguments(args))
                    result = await self._execute_tool(
                        config=config,
                        registry=filtered_registry,
                        tool_name=tool_name,
                        arguments=args,
                        tool_scope=tool_scope,
                        full_access_enabled=full_access_enabled,
                        persistent_memory=persistent_memory,
                        tool_result_enricher=tool_result_enricher,
                    )
                    max_chars = (
                        _FULL_MODE_SUMMARY_MAX_CHARS
                        if config.capability_mode == "full"
                        else _SUMMARY_MAX_CHARS
                    )
                    content = result.result[:max_chars]
                    memory.add_tool_result(call_id, content)
                    observed_source = result.raw_result if result.raw_result is not None else result.result
                    observed_files.update(
                        self._extract_excel_paths_from_tool_result(
                            tool_name=tool_name,
                            text=observed_source,
                        )
                    )

                    if result.file_changes:
                        for _fc_path in result.file_changes:
                            structured_changes.append(SubagentFileChange(
                                path=_fc_path,
                                tool_name=tool_name,
                                change_type=self._infer_change_type(tool_name),
                                sheets_affected=self._extract_sheet_names(tool_name, args),
                            ))

                    if result.pending_approval_id:
                        pending_id = result.pending_approval_id
                        success = False
                        error = result.result
                        last_summary = result.result
                        tool_calls += self._backfill_tool_results_for_remaining_calls(
                            memory=memory,
                            remaining_calls=message_tool_calls[index + 1:],
                            content=pending_skip_msg,
                        )
                        break

                    if result.success:
                        consecutive_failures = 0
                    else:
                        success = False
                        error = result.error or result.result
                        consecutive_failures += 1

                    if consecutive_failures >= config.max_consecutive_failures:
                        last_summary = (
                            f"子代理连续 {config.max_consecutive_failures} 次工具调用失败，已终止。"
                        )
                        success = False
                        tool_calls += self._backfill_tool_results_for_remaining_calls(
                            memory=memory,
                            remaining_calls=message_tool_calls[index + 1:],
                            content=breaker_skip_msg,
                        )
                        break

                if pending_id is not None or consecutive_failures >= config.max_consecutive_failures:
                    break

            if not last_summary:
                last_summary = f"子代理达到最大迭代次数（{config.max_iterations}），已终止。"
                success = False
                if error is None:
                    error = last_summary
        except Exception as exc:  # noqa: BLE001
            success = False
            error = str(exc)
            last_summary = f"子代理执行失败：{exc}"
        finally:
            # 确保 HTTP 连接池在任何退出路径下都被释放
            _close = getattr(client, "close", None)
            if callable(_close):
                await _close()
            if memory_client is not None:
                _mc_close = getattr(memory_client, "close", None)
                if callable(_mc_close):
                    await _mc_close()

        summary_limit = (
            _FULL_MODE_SUMMARY_MAX_CHARS
            if config.capability_mode == "full"
            else _SUMMARY_MAX_CHARS
        )
        last_summary = self._truncate(last_summary, max_chars=summary_limit)
        await self._persist_subagent_memory(
            subagent_name=config.name,
            memory=memory,
            system_prompt=system_prompt,
            persistent_memory=persistent_memory,
            memory_extractor=memory_extractor,
        )
        self._emit_safe(
            on_event,
            ToolCallEvent(
                event_type=EventType.SUBAGENT_SUMMARY,
                subagent_name=config.name,
                subagent_reason=prompt,
                subagent_tools=tool_scope,
                subagent_summary=last_summary,
                subagent_success=success,
                subagent_permission_mode=config.permission_mode,
                subagent_conversation_id=conversation_id,
                subagent_iterations=iterations,
                subagent_tool_calls=tool_calls,
            ),
        )
        self._emit_safe(
            on_event,
            ToolCallEvent(
                event_type=EventType.SUBAGENT_END,
                subagent_name=config.name,
                subagent_reason=prompt,
                subagent_tools=tool_scope,
                subagent_success=success,
                subagent_permission_mode=config.permission_mode,
                subagent_conversation_id=conversation_id,
                subagent_iterations=iterations,
                subagent_tool_calls=tool_calls,
            ),
        )

        return SubagentResult(
            success=success,
            summary=last_summary,
            subagent_name=config.name,
            permission_mode=config.permission_mode,
            conversation_id=conversation_id,
            iterations=iterations,
            tool_calls_count=tool_calls,
            error=error,
            pending_approval_id=pending_id,
            structured_changes=structured_changes,
            observed_files=sorted(observed_files),
        )

    @staticmethod
    def _emit_safe(on_event: EventCallback | None, event: ToolCallEvent) -> None:
        if on_event is None:
            return
        try:
            on_event(event)
        except Exception:  # noqa: BLE001
            logger.warning("子代理事件回调异常，已忽略。", exc_info=True)

    def _build_system_prompt(
        self,
        *,
        config: SubagentConfig,
        parent_context: str,
        enriched_contexts: list[str] | None = None,
    ) -> str:
        """构建子代理系统提示。

        优先级：PromptComposer 文件 > config.system_prompt > 默认模板。
        """
        default_prompt = (
            f"你是子代理 `{config.name}`。\n"
            f"职责：{config.description}\n\n"
            "## 工作规范\n"
            "- 优先给出可执行、可核验的结果。\n"
            "- 每次工具调用前简要说明目的。\n"
            "- 完成后输出结果摘要与关键证据。\n"
            "- 不要输出冗长无关内容。"
        )
        # 优先从 PromptComposer 加载子代理提示词
        composer_prompt: str | None = None
        try:
            from excelmanus.prompt_composer import PromptComposer
            from pathlib import Path
            prompts_dir = Path(__file__).resolve().parent.parent / "prompts"
            if prompts_dir.is_dir():
                composer = PromptComposer(prompts_dir)
                composer_prompt = composer.compose_for_subagent(config.name)
        except Exception:
            pass
        parts = [composer_prompt or config.system_prompt.strip() or default_prompt]
        if parent_context.strip():
            parts.append("## 主会话上下文\n" + parent_context.strip())
        # full 模式：注入主代理级别的丰富上下文
        if config.capability_mode == "full" and enriched_contexts:
            for ctx in enriched_contexts:
                stripped = ctx.strip()
                if stripped:
                    parts.append(stripped)
        return "\n\n".join(parts).strip()

    async def _execute_tool(
        self,
        *,
        config: SubagentConfig,
        registry: FilteredToolRegistry,
        tool_name: str,
        arguments: dict[str, Any],
        tool_scope: list[str],
        full_access_enabled: bool,
        persistent_memory: "PersistentMemory | None",
        tool_result_enricher: ToolResultEnricher | None,
    ) -> _ExecResult:
        """执行子代理内单次工具调用（含审批桥接）。"""
        if not registry.is_tool_available(tool_name):
            return _ExecResult(
                success=False,
                result=f"工具 '{tool_name}' 不在子代理授权范围内。",
                error=f"ToolNotAllowed: {tool_name}",
            )

        mode = config.permission_mode
        read_only_safe = self._approval.is_read_only_safe_tool(tool_name)
        confirm_required = self._approval.is_confirm_required_tool(tool_name)
        audit_only = self._approval.is_audit_only_tool(tool_name)
        if mode == "readOnly":
            if not read_only_safe:
                msg = f"只读模式仅允许白名单工具：{tool_name}"
                return _ExecResult(success=False, result=msg, error=msg)

        if confirm_required and mode == "default" and not full_access_enabled:
            try:
                pending = self._approval.create_pending(
                    tool_name=tool_name,
                    arguments=arguments,
                    tool_scope=tool_scope,
                )
            except ValueError:
                block = self._approval.pending_block_message()
                return _ExecResult(success=False, result=block, error=block)
            pending_text = (
                "子代理命中高风险操作，已创建待确认项：\n"
                f"- ID: `{pending.approval_id}`\n"
                f"- 工具: `{tool_name}`\n"
                "请先执行 `/accept <id>` 或 `/reject <id>`。"
            )
            return _ExecResult(
                success=False,
                result=pending_text,
                pending_approval_id=pending.approval_id,
            )

        should_execute_with_audit = audit_only or (
            confirm_required
            and (
                mode in {"acceptEdits", "dontAsk"}
                or (mode == "default" and full_access_enabled)
            )
        )
        if should_execute_with_audit:
            approval_id = self._approval.new_approval_id()
            created_at_utc = self._approval.utc_now()

            # MCP 工具不做本地文件快照审计（由远端系统自行审计）。
            if self._approval.is_mcp_tool(tool_name):
                mcp_probe_before: dict[str, tuple[int, int]] | None = None
                mcp_probe_before_partial = False
                mcp_tool_def = getattr(registry, "get_tool", lambda _: None)(tool_name)
                mcp_effect = (
                    getattr(mcp_tool_def, "write_effect", "unknown")
                    if mcp_tool_def is not None
                    else "unknown"
                )
                if isinstance(mcp_effect, str) and mcp_effect.strip().lower() == "unknown":
                    try:
                        mcp_probe_before, mcp_probe_before_partial = collect_workspace_mtime_index(
                            self._parent_config.workspace_root
                        )
                    except Exception:
                        logger.debug("subagent mcp unknown 探针前置快照失败", exc_info=True)
                try:
                    raw_result = await self._call_tool_with_memory_context(
                        registry=registry,
                        persistent_memory=persistent_memory,
                        tool_name=tool_name,
                        arguments=arguments,
                        tool_scope=tool_scope,
                    )
                except Exception as exc:  # noqa: BLE001
                    error = f"工具执行错误: {exc}"
                    return _ExecResult(success=False, result=error, error=str(exc))
                raw_text = str(raw_result)
                # ── 检测 registry 层返回的结构化错误 JSON ──
                if raw_text.startswith('{"status": "error"'):
                    try:
                        _err = json.loads(raw_text)
                        if isinstance(_err, dict) and _err.get("status") == "error":
                            _msg = _err.get("message") or raw_text
                            return _ExecResult(success=False, result=raw_text, error=_msg)
                    except (json.JSONDecodeError, AttributeError):
                        pass
                probed_changes: list[str] = []
                if mcp_probe_before is not None:
                    try:
                        mcp_probe_after, mcp_probe_after_partial = collect_workspace_mtime_index(
                            self._parent_config.workspace_root
                        )
                        if has_workspace_mtime_changes(mcp_probe_before, mcp_probe_after):
                            probed_changes = diff_workspace_mtime_paths(
                                mcp_probe_before,
                                mcp_probe_after,
                            )
                            logger.info(
                                "subagent mcp unknown 写入探针命中: tool=%s partial_before=%s partial_after=%s changed_paths=%d",
                                tool_name,
                                mcp_probe_before_partial,
                                mcp_probe_after_partial,
                                len(probed_changes),
                            )
                    except Exception:
                        logger.debug("subagent mcp unknown 探针后置快照失败", exc_info=True)
                enriched = self._apply_tool_result_enricher(
                    tool_name=tool_name,
                    arguments=arguments,
                    text=self._truncate_tool_result(
                        registry=registry,
                        tool_name=tool_name,
                        text=raw_text,
                    ),
                    success=True,
                    tool_result_enricher=tool_result_enricher,
                )
                return _ExecResult(
                    success=True,
                    result=enriched,
                    file_changes=probed_changes or None,
                    raw_result=raw_text,
                )

            def _execute(name: str, args: dict[str, Any], scope: list[str]) -> Any:
                from excelmanus.tools import memory_tools

                with memory_tools.bind_memory_context(persistent_memory):
                    return registry.call_tool(name, args, tool_scope=scope)

            try:
                result_text, record = await asyncio.to_thread(
                    self._approval.execute_and_audit,
                    approval_id=approval_id,
                    tool_name=tool_name,
                    arguments=arguments,
                    tool_scope=tool_scope,
                    execute=_execute,
                    undoable=not self._approval.is_read_only_safe_tool(tool_name) and tool_name not in {"run_code", "run_shell"},
                    created_at_utc=created_at_utc,
                )
            except Exception as exc:  # noqa: BLE001
                error = f"工具执行错误: {exc}"
                return _ExecResult(success=False, result=error, error=str(exc))

            changes = [change.path for change in record.changes]
            enriched = self._apply_tool_result_enricher(
                tool_name=tool_name,
                arguments=arguments,
                text=self._truncate_tool_result(
                    registry=registry,
                    tool_name=tool_name,
                    text=result_text,
                ),
                success=True,
                tool_result_enricher=tool_result_enricher,
            )
            return _ExecResult(
                success=True,
                result=enriched,
                file_changes=changes,
                raw_result=result_text,
            )

        probe_before: dict[str, tuple[int, int]] | None = None
        probe_before_partial = False
        tool_def = getattr(registry, "get_tool", lambda _: None)(tool_name)
        write_effect = (
            getattr(tool_def, "write_effect", "unknown")
            if tool_def is not None
            else "unknown"
        )
        if isinstance(write_effect, str) and write_effect.strip().lower() == "unknown":
            try:
                probe_before, probe_before_partial = collect_workspace_mtime_index(
                    self._parent_config.workspace_root
                )
            except Exception:
                logger.debug("subagent unknown 探针前置快照失败", exc_info=True)

        try:
            raw_result = await self._call_tool_with_memory_context(
                registry=registry,
                persistent_memory=persistent_memory,
                tool_name=tool_name,
                arguments=arguments,
                tool_scope=tool_scope,
            )
            raw_text = str(raw_result)
            # ── 检测 registry 层返回的结构化错误 JSON ──
            if raw_text.startswith('{"status": "error"'):
                try:
                    _err = json.loads(raw_text)
                    if isinstance(_err, dict) and _err.get("status") == "error":
                        _msg = _err.get("message") or raw_text
                        return _ExecResult(success=False, result=raw_text, error=_msg)
                except (json.JSONDecodeError, AttributeError):
                    pass
            probed_changes: list[str] = []
            if probe_before is not None:
                try:
                    probe_after, probe_after_partial = collect_workspace_mtime_index(
                        self._parent_config.workspace_root
                    )
                    if has_workspace_mtime_changes(probe_before, probe_after):
                        probed_changes = diff_workspace_mtime_paths(
                            probe_before,
                            probe_after,
                        )
                        logger.info(
                            "subagent unknown 写入探针命中: tool=%s partial_before=%s partial_after=%s changed_paths=%d",
                            tool_name,
                            probe_before_partial,
                            probe_after_partial,
                            len(probed_changes),
                        )
                except Exception:
                    logger.debug("subagent unknown 探针后置快照失败", exc_info=True)
            text = self._truncate_tool_result(
                registry=registry,
                tool_name=tool_name,
                text=raw_text,
            )
            enriched = self._apply_tool_result_enricher(
                tool_name=tool_name,
                arguments=arguments,
                text=text,
                success=True,
                tool_result_enricher=tool_result_enricher,
            )
            return _ExecResult(
                success=True,
                result=enriched,
                file_changes=probed_changes or None,
                raw_result=raw_text,
            )
        except Exception as exc:  # noqa: BLE001
            error = f"工具执行错误: {exc}"
            return _ExecResult(success=False, result=error, error=str(exc))

    async def _call_tool_with_memory_context(
        self,
        *,
        registry: FilteredToolRegistry,
        persistent_memory: "PersistentMemory | None",
        tool_name: str,
        arguments: dict[str, Any],
        tool_scope: list[str],
    ) -> Any:
        """在线程池执行工具时绑定记忆上下文，避免会话间串扰。"""
        from excelmanus.tools import memory_tools

        def _call() -> Any:
            with memory_tools.bind_memory_context(persistent_memory):
                return registry.call_tool(tool_name, arguments, tool_scope=tool_scope)

        return await asyncio.to_thread(_call)

    def _create_memory_components(
        self,
        *,
        config: SubagentConfig,
    ) -> "tuple[PersistentMemory | None, MemoryExtractor | None, openai.AsyncOpenAI | None]":
        """根据 memory_scope 创建子代理持久记忆组件。

        返回 (persistent_memory, memory_extractor, client)，调用方负责关闭 client。
        """
        if config.memory_scope is None:
            return None, None, None
        if not self._parent_config.memory_enabled:
            logger.info(
                "子代理 %s 配置 memory_scope=%s，但全局记忆已禁用，已降级跳过。",
                config.name,
                config.memory_scope,
            )
            return None, None, None

        from excelmanus.memory_extractor import MemoryExtractor
        from excelmanus.persistent_memory import PersistentMemory

        if config.memory_scope == "user":
            memory_dir = (
                Path("~/.excelmanus/agent-memory").expanduser() / config.name
            )
        else:
            memory_dir = (
                Path(self._parent_config.workspace_root).expanduser()
                / ".excelmanus"
                / "agent-memory"
                / config.name
            )

        persistent_memory = PersistentMemory(
            memory_dir=str(memory_dir),
            auto_load_lines=self._parent_config.memory_auto_load_lines,
        )
        model = config.model or self._parent_config.aux_model or self._parent_config.model
        client = create_client(
            api_key=config.api_key or self._parent_config.api_key,
            base_url=config.base_url or self._parent_config.base_url,
        )
        memory_extractor = MemoryExtractor(client=client, model=model)
        return persistent_memory, memory_extractor, client

    async def _persist_subagent_memory(
        self,
        *,
        subagent_name: str,
        memory: ConversationMemory,
        system_prompt: str,
        persistent_memory: "PersistentMemory | None",
        memory_extractor: "MemoryExtractor | None",
    ) -> None:
        if persistent_memory is None or memory_extractor is None:
            return
        try:
            messages = memory.get_messages(system_prompts=[system_prompt])
            entries = await memory_extractor.extract(messages)
            if entries:
                persistent_memory.save_entries(entries)
                logger.info(
                    "子代理 %s 持久记忆提取完成，保存了 %d 条记忆条目。",
                    subagent_name,
                    len(entries),
                )
        except Exception:
            logger.warning("子代理 %s 持久记忆提取失败", subagent_name, exc_info=True)

    @staticmethod
    def _backfill_tool_results_for_remaining_calls(
        *,
        memory: ConversationMemory,
        remaining_calls: list[Any],
        content: str,
    ) -> int:
        backfilled = 0
        for tc in remaining_calls:
            call_id = str(getattr(tc, "id", "") or "")
            if not call_id:
                continue
            memory.add_tool_result(call_id, content)
            backfilled += 1
        return backfilled

    @staticmethod
    def _truncate(text: str, max_chars: int = _SUMMARY_MAX_CHARS) -> str:
        if len(text) <= max_chars:
            return text
        return f"{text[:max_chars]}\n[摘要已截断，原始长度: {len(text)} 字符]"

    def _truncate_tool_result(
        self,
        *,
        registry: FilteredToolRegistry,
        tool_name: str,
        text: str,
    ) -> str:
        tool_def = registry.get_tool(tool_name)
        if tool_def is None:
            return text
        return tool_def.truncate_result(text)

    @staticmethod
    def _apply_tool_result_enricher(
        *,
        tool_name: str,
        arguments: dict[str, Any],
        text: str,
        success: bool,
        tool_result_enricher: ToolResultEnricher | None,
    ) -> str:
        """对工具返回执行外部增强（如窗口感知），失败时回退原文。"""
        if tool_result_enricher is None:
            return text
        try:
            enriched = tool_result_enricher(tool_name, arguments, text, success)
        except Exception:
            logger.warning(
                "子代理工具结果增强失败，已回退原始结果: tool=%s",
                tool_name,
                exc_info=True,
            )
            return text
        return str(enriched) if enriched is not None else text

    @classmethod
    def _extract_excel_paths_from_arguments(cls, arguments: dict[str, Any]) -> list[str]:
        """从工具参数中提取 Excel 文件路径。"""
        keys = (
            "file_path",
            "output_path",
            "source_file",
            "target_file",
            "path",
            "file",
        )
        paths: list[str] = []
        for key in keys:
            value = arguments.get(key)
            if isinstance(value, str):
                normalized = cls._normalize_path(value)
                if cls._is_excel_path(normalized):
                    paths.append(normalized)

        raw_file_paths = arguments.get("file_paths")
        if isinstance(raw_file_paths, list):
            for item in raw_file_paths:
                if not isinstance(item, str):
                    continue
                normalized = cls._normalize_path(item)
                if cls._is_excel_path(normalized):
                    paths.append(normalized)
        return paths

    @classmethod
    def _extract_excel_paths_from_tool_result(
        cls,
        *,
        tool_name: str,
        text: str,
    ) -> list[str]:
        """从工具返回结果中补充提取 Excel 文件路径。"""
        if not text:
            return []
        try:
            payload = json.loads(text)
        except Exception:  # noqa: BLE001
            return []

        paths: list[str] = []
        if isinstance(payload, dict):
            paths.extend(cls._collect_excel_paths_from_mapping(payload))
            if tool_name == "inspect_excel_files":
                files = payload.get("files")
                if isinstance(files, list):
                    for item in files:
                        if not isinstance(item, dict):
                            continue
                        path = item.get("path") or item.get("file")
                        if isinstance(path, str):
                            normalized = cls._normalize_path(path)
                            if cls._is_excel_path(normalized):
                                paths.append(normalized)
        elif isinstance(payload, list):
            for item in payload:
                if isinstance(item, dict):
                    paths.extend(cls._collect_excel_paths_from_mapping(item))
        return paths

    @classmethod
    def _collect_excel_paths_from_mapping(cls, mapping: dict[str, Any]) -> list[str]:
        keys = ("file", "path", "file_path", "output_path")
        paths: list[str] = []
        for key in keys:
            value = mapping.get(key)
            if isinstance(value, str):
                normalized = cls._normalize_path(value)
                if cls._is_excel_path(normalized):
                    paths.append(normalized)
        return paths

    _FORMAT_TOOLS: frozenset[str] = frozenset({
        # 内置格式化工具已全部精简，仅保留 MCP 工具名
        "format_range",
    })
    _DELETE_TOOLS: frozenset[str] = frozenset({"delete_file"})
    _CREATE_TOOLS: frozenset[str] = frozenset()  # create_sheet/create_chart 等已精简
    _CODE_TOOLS: frozenset[str] = frozenset({"run_code", "run_shell"})

    @classmethod
    def _infer_change_type(cls, tool_name: str) -> str:
        """根据工具名推断变更类型。"""
        if tool_name in cls._FORMAT_TOOLS:
            return "format"
        if tool_name in cls._DELETE_TOOLS:
            return "delete"
        if tool_name in cls._CREATE_TOOLS:
            return "create"
        if tool_name in cls._CODE_TOOLS:
            return "code_modified"
        return "write"

    @staticmethod
    def _extract_sheet_names(tool_name: str, arguments: dict[str, Any]) -> tuple[str, ...]:
        """从工具参数中提取受影响的 sheet 名称。"""
        names: list[str] = []
        for key in ("sheet_name", "sheet", "source_sheet", "target_sheet"):
            value = arguments.get(key)
            if isinstance(value, str) and value.strip():
                names.append(value.strip())
        return tuple(dict.fromkeys(names))  # 去重保序

    @staticmethod
    def _normalize_path(path: str) -> str:
        normalized = path.strip().replace("\\", "/")
        while normalized.startswith("./"):
            normalized = normalized[2:]
        return normalized

    @staticmethod
    def _is_excel_path(path: str) -> bool:
        lower = path.lower()
        return lower.endswith(".xlsx") or lower.endswith(".xlsm") or lower.endswith(".xls")
