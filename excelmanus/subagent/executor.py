"""子代理执行器。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import hashlib
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
        session_turn: int | None = None,
        workspace_context: str = "",
        file_access_guard: Any | None = None,
        sandbox_env: Any | None = None,
        cow_mappings: dict[str, str] | None = None,
        workspace_root: str = "",
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
            workspace_context=workspace_context,
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
        repeated_failure_streak = 0
        similar_failure_streak = 0
        last_failure_signature: str | None = None
        last_category_signature: str | None = None
        _failure_hint_injected_at: int = 0
        last_summary = ""
        success = True
        error: str | None = None
        total_prompt_tokens = 0
        total_completion_tokens = 0

        _sub_protocol = getattr(config, 'protocol', '') or self._parent_config.protocol
        client = create_client(
            api_key=config.api_key or self._parent_config.api_key,
            base_url=config.base_url or self._parent_config.base_url,
            protocol=_sub_protocol,
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
                _usage = getattr(response, "usage", None)
                if _usage is not None:
                    total_prompt_tokens += getattr(_usage, "prompt_tokens", 0) or 0
                    total_completion_tokens += getattr(_usage, "completion_tokens", 0) or 0
                message = response.choices[0].message
                message_tool_calls = getattr(message, "tool_calls", None)
                if not message_tool_calls:
                    last_summary = str(getattr(message, "content", "") or "").strip()
                    if not last_summary:
                        last_summary = "子代理执行完成，但未返回文本摘要。"
                    memory.add_assistant_message(last_summary)
                    break

                memory.add_assistant_tool_message(assistant_message_to_dict(message))

                # ── R7: 只读并行快速路径 ──
                # 当一批 tool_calls 全部可解析且全部是只读安全工具时，
                # 用 asyncio.gather 并行执行以加速 explorer 多工具探索。
                _parallel_handled = await self._try_parallel_readonly_batch(
                    config=config,
                    message_tool_calls=message_tool_calls,
                    registry=filtered_registry,
                    tool_scope=tool_scope,
                    full_access_enabled=full_access_enabled,
                    persistent_memory=persistent_memory,
                    tool_result_enricher=tool_result_enricher,
                    session_turn=session_turn,
                    file_access_guard=file_access_guard,
                    sandbox_env=sandbox_env,
                    cow_mappings=cow_mappings,
                    workspace_root=workspace_root,
                    # mutable accumulators
                    memory=memory,
                    on_event=on_event,
                    conversation_id=conversation_id,
                    observed_files=observed_files,
                    structured_changes=structured_changes,
                )
                if _parallel_handled is not None:
                    _p_tc, _p_success, _p_error = _parallel_handled
                    tool_calls += _p_tc
                    if not _p_success:
                        success = False
                        error = _p_error
                    # 并行路径不触发 breaker/pending，直接进入下一迭代
                    continue

                # ── 串行回退路径（含 breaker/pending/failure tracking） ──
                breaker_skip_msg = (
                    f"工具未执行：同一失败重复 {config.max_consecutive_failures} 次，已触发熔断。"
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
                        current_signature = self._failure_signature(
                            tool_name=tool_name,
                            arguments={"_raw": str(raw_args or "")[:500]},
                            error=parsed_error,
                        )
                        current_category = self._category_signature(
                            tool_name=tool_name, error=parsed_error,
                        )
                        repeated_failure_streak = self._update_failure_streak(
                            signature=current_signature,
                            previous_signature=last_failure_signature,
                            previous_streak=repeated_failure_streak,
                        )
                        similar_failure_streak = self._update_failure_streak(
                            signature=current_category,
                            previous_signature=last_category_signature,
                            previous_streak=similar_failure_streak,
                        )
                        last_failure_signature = current_signature
                        last_category_signature = current_category
                        error = parsed_error
                        success = False
                        if repeated_failure_streak >= config.max_consecutive_failures:
                            last_summary = (
                                f"子代理检测到同一失败重复 {config.max_consecutive_failures} 次，已终止当前策略。"
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
                    self._emit_safe(
                        on_event,
                        ToolCallEvent(
                            event_type=EventType.SUBAGENT_TOOL_START,
                            subagent_name=config.name,
                            subagent_conversation_id=conversation_id,
                            tool_name=tool_name,
                            arguments=self._summarize_args(tool_name, args),
                            subagent_tool_index=tool_calls,
                        ),
                    )
                    _tool_timeout = getattr(config, "tool_timeout", 300)
                    try:
                        result = await asyncio.wait_for(
                            self._execute_tool(
                                config=config,
                                registry=filtered_registry,
                                tool_name=tool_name,
                                arguments=args,
                                tool_scope=tool_scope,
                                full_access_enabled=full_access_enabled,
                                persistent_memory=persistent_memory,
                                tool_result_enricher=tool_result_enricher,
                                session_turn=session_turn,
                                file_access_guard=file_access_guard,
                                sandbox_env=sandbox_env,
                                cow_mappings=cow_mappings,
                                workspace_root=workspace_root,
                            ),
                            timeout=_tool_timeout,
                        )
                    except asyncio.TimeoutError:
                        result = ToolCallResult(
                            tool_name=tool_name,
                            arguments=args,
                            result=f"[错误] 工具 {tool_name} 执行超时（{_tool_timeout}s）",
                            success=False,
                            error=f"timeout after {_tool_timeout}s",
                        )
                    # 工具结果已在 _execute_tool 内经过 ToolDef 级截断，
                    # 此处不再做二次硬截断，避免丢失关键信息。
                    memory.add_tool_result(call_id, result.result)
                    self._emit_safe(
                        on_event,
                        ToolCallEvent(
                            event_type=EventType.SUBAGENT_TOOL_END,
                            subagent_name=config.name,
                            subagent_conversation_id=conversation_id,
                            tool_name=tool_name,
                            success=result.success,
                            result=result.result[:300] if result.result else "",
                            error=result.error[:200] if result.error else None,
                            subagent_tool_index=tool_calls,
                        ),
                    )
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
                        repeated_failure_streak = 0
                        similar_failure_streak = 0
                        last_failure_signature = None
                        last_category_signature = None
                    else:
                        success = False
                        error = result.error or result.result
                        current_signature = self._failure_signature(
                            tool_name=tool_name,
                            arguments=args,
                            error=error,
                        )
                        current_category = self._category_signature(
                            tool_name=tool_name, error=error,
                        )
                        repeated_failure_streak = self._update_failure_streak(
                            signature=current_signature,
                            previous_signature=last_failure_signature,
                            previous_streak=repeated_failure_streak,
                        )
                        similar_failure_streak = self._update_failure_streak(
                            signature=current_category,
                            previous_signature=last_category_signature,
                            previous_streak=similar_failure_streak,
                        )
                        last_failure_signature = current_signature
                        last_category_signature = current_category

                    # ── 渐进降级熔断 ──
                    if repeated_failure_streak >= config.max_consecutive_failures:
                        last_summary = (
                            f"子代理检测到同一失败重复 {config.max_consecutive_failures} 次，已终止当前策略。"
                        )
                        success = False
                        tool_calls += self._backfill_tool_results_for_remaining_calls(
                            memory=memory,
                            remaining_calls=message_tool_calls[index + 1:],
                            content=breaker_skip_msg,
                        )
                        break

                    # 渐进提示：相似失败达到阈值时注入引导，不终止
                    warn_threshold = max(2, (config.max_consecutive_failures + 1) // 2)
                    if (
                        similar_failure_streak >= warn_threshold
                        and _failure_hint_injected_at < similar_failure_streak
                    ):
                        _failure_hint_injected_at = similar_failure_streak
                        hint = self._build_failure_hint(
                            tool_name=tool_name,
                            streak=similar_failure_streak,
                            max_failures=config.max_consecutive_failures,
                            error=error,
                        )
                        memory.add_user_message(hint)

                if pending_id is not None or repeated_failure_streak >= config.max_consecutive_failures:
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
            # ── 异常退出时追加中间产出摘要，确保主代理获得已有信息 ──
            if not success and (iterations > 0 or tool_calls > 0):
                partial = self._build_partial_progress_summary(
                    memory=memory,
                    observed_files=observed_files,
                    structured_changes=structured_changes,
                    iterations=iterations,
                    tool_calls=tool_calls,
                )
                if partial:
                    last_summary = f"{last_summary}\n\n【已完成的工作】\n{partial}"

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
            prompt_tokens=total_prompt_tokens,
            completion_tokens=total_completion_tokens,
            error=error,
            pending_approval_id=pending_id,
            structured_changes=structured_changes,
            observed_files=sorted(observed_files),
        )

    async def _try_parallel_readonly_batch(
        self,
        *,
        config: SubagentConfig,
        message_tool_calls: list[Any],
        registry: FilteredToolRegistry,
        tool_scope: list[str],
        full_access_enabled: bool,
        persistent_memory: Any,
        tool_result_enricher: ToolResultEnricher | None,
        session_turn: int | None,
        file_access_guard: Any | None,
        sandbox_env: Any | None,
        cow_mappings: dict[str, str] | None,
        workspace_root: str,
        # mutable accumulators
        memory: ConversationMemory,
        on_event: EventCallback | None,
        conversation_id: str,
        observed_files: set[str],
        structured_changes: list[SubagentFileChange],
    ) -> tuple[int, bool, str | None] | None:
        """尝试并行执行一批全部为只读的工具调用。

        返回 (tool_call_count, all_success, last_error) 表示已处理；
        返回 None 表示不适用并行，需回退串行路径。

        前置条件（全部满足才走并行）：
        1. 批次 >= 2 个工具调用
        2. 全部参数可解析
        3. 全部是只读安全工具（READ_ONLY_SAFE_TOOLS 或 allowed_tools 显式授权的只读工具）
        """
        if len(message_tool_calls) < 2:
            return None

        # ── 阶段 1: 预解析全部工具调用 ──
        parsed: list[tuple[str, str, dict[str, Any]]] = []  # (call_id, tool_name, args)
        for tc in message_tool_calls:
            call_id = getattr(tc, "id", "")
            tool_name = getattr(getattr(tc, "function", None), "name", "")
            raw_args = getattr(getattr(tc, "function", None), "arguments", "{}")
            try:
                args = json.loads(raw_args or "{}")
                if not isinstance(args, dict):
                    return None  # 解析失败 → 回退串行
            except Exception:
                return None
            if not self._approval.is_read_only_safe_tool(tool_name):
                return None  # 非 READ_ONLY_SAFE 工具 → 回退串行（需要审计/审批流程）
            parsed.append((call_id, tool_name, args))

        # ── 阶段 2: 发射 start 事件 + 并行执行 ──
        _tool_timeout = getattr(config, "tool_timeout", 300)
        _exec_kwargs = dict(
            config=config,
            registry=registry,
            tool_scope=tool_scope,
            full_access_enabled=full_access_enabled,
            persistent_memory=persistent_memory,
            tool_result_enricher=tool_result_enricher,
            session_turn=session_turn,
            file_access_guard=file_access_guard,
            sandbox_env=sandbox_env,
            cow_mappings=cow_mappings,
            workspace_root=workspace_root,
        )

        async def _run_one(call_id: str, tool_name: str, args: dict[str, Any], idx: int) -> _ExecResult:
            self._emit_safe(
                on_event,
                ToolCallEvent(
                    event_type=EventType.SUBAGENT_TOOL_START,
                    subagent_name=config.name,
                    subagent_conversation_id=conversation_id,
                    tool_name=tool_name,
                    arguments=self._summarize_args(tool_name, args),
                    subagent_tool_index=idx,
                ),
            )
            try:
                return await asyncio.wait_for(
                    self._execute_tool(tool_name=tool_name, arguments=args, **_exec_kwargs),
                    timeout=_tool_timeout,
                )
            except asyncio.TimeoutError:
                return _ExecResult(
                    success=False,
                    result=f"[错误] 工具 {tool_name} 执行超时（{_tool_timeout}s）",
                    error=f"timeout after {_tool_timeout}s",
                )

        # 收集路径观察
        for _, tool_name, args in parsed:
            observed_files.update(self._extract_excel_paths_from_arguments(args))

        results = await asyncio.gather(
            *[_run_one(cid, tn, a, i + 1) for i, (cid, tn, a) in enumerate(parsed)],
            return_exceptions=True,
        )

        # ── 阶段 3: 顺序处理结果 ──
        tc_count = len(parsed)
        all_success = True
        last_error: str | None = None

        for i, (call_id, tool_name, args) in enumerate(parsed):
            raw = results[i]
            if isinstance(raw, BaseException):
                result = _ExecResult(success=False, result=str(raw), error=str(raw))
            else:
                result = raw

            memory.add_tool_result(call_id, result.result)
            self._emit_safe(
                on_event,
                ToolCallEvent(
                    event_type=EventType.SUBAGENT_TOOL_END,
                    subagent_name=config.name,
                    subagent_conversation_id=conversation_id,
                    tool_name=tool_name,
                    success=result.success,
                    result=result.result[:300] if result.result else "",
                    error=result.error[:200] if result.error else None,
                    subagent_tool_index=i + 1,
                ),
            )
            observed_source = result.raw_result if result.raw_result is not None else result.result
            observed_files.update(
                self._extract_excel_paths_from_tool_result(tool_name=tool_name, text=observed_source)
            )
            if result.file_changes:
                for _fc_path in result.file_changes:
                    structured_changes.append(SubagentFileChange(
                        path=_fc_path,
                        tool_name=tool_name,
                        change_type=self._infer_change_type(tool_name),
                        sheets_affected=self._extract_sheet_names(tool_name, args),
                    ))
            if not result.success:
                all_success = False
                last_error = result.error or result.result

        return (tc_count, all_success, last_error)

    async def _execute_with_probe(
        self,
        *,
        registry: FilteredToolRegistry,
        persistent_memory: "PersistentMemory | None",
        tool_name: str,
        arguments: dict[str, Any],
        tool_scope: list[str],
        tool_result_enricher: ToolResultEnricher | None,
        log_prefix: str = "subagent unknown",
        file_access_guard: Any | None = None,
        sandbox_env: Any | None = None,
    ) -> _ExecResult:
        """探针→执行→错误检测→探针对比→截断→增强 的通用流水线。

        MCP-audit 路径和无审计路径共用此方法，仅 log_prefix 不同。
        """
        # ── 前置探针（write_effect 未知时收集工作区快照） ──
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
                logger.debug("%s 探针前置快照失败", log_prefix, exc_info=True)

        # ── 执行工具 ──
        try:
            raw_result = await self._call_tool_with_memory_context(
                registry=registry,
                persistent_memory=persistent_memory,
                tool_name=tool_name,
                arguments=arguments,
                tool_scope=tool_scope,
                file_access_guard=file_access_guard,
                sandbox_env=sandbox_env,
            )
        except Exception as exc:  # noqa: BLE001
            error = f"工具执行错误: {exc}"
            return _ExecResult(success=False, result=error, error=str(exc))

        raw_text = str(raw_result)
        struct_err = self._check_structured_error(raw_text)
        if struct_err is not None:
            return struct_err

        # ── 后置探针对比 ──
        probed_changes: list[str] = []
        if probe_before is not None:
            try:
                probe_after, probe_after_partial = collect_workspace_mtime_index(
                    self._parent_config.workspace_root
                )
                if has_workspace_mtime_changes(probe_before, probe_after):
                    probed_changes = diff_workspace_mtime_paths(
                        probe_before, probe_after,
                    )
                    logger.info(
                        "%s 写入探针命中: tool=%s partial_before=%s partial_after=%s changed_paths=%d",
                        log_prefix,
                        tool_name,
                        probe_before_partial,
                        probe_after_partial,
                        len(probed_changes),
                    )
            except Exception:
                logger.debug("%s 探针后置快照失败", log_prefix, exc_info=True)

        # ── 截断 + 增强 ──
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

    @staticmethod
    def _check_structured_error(raw_text: str) -> _ExecResult | None:
        """检测 registry 层返回的结构化错误 JSON，命中时返回失败结果。"""
        if not raw_text.startswith('{"status": "error"'):
            return None
        try:
            err = json.loads(raw_text)
            if isinstance(err, dict) and err.get("status") == "error":
                msg = err.get("message") or raw_text
                return _ExecResult(success=False, result=raw_text, error=msg)
        except (json.JSONDecodeError, AttributeError):
            pass
        return None

    @staticmethod
    def _emit_safe(on_event: EventCallback | None, event: ToolCallEvent) -> None:
        if on_event is None:
            return
        try:
            on_event(event)
        except Exception:  # noqa: BLE001
            logger.warning("子代理事件回调异常，已忽略。", exc_info=True)

    @staticmethod
    def _summarize_args(tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        """为前端展示构建紧凑的参数摘要字典。"""
        summary: dict[str, Any] = {}
        for key in ("sheet", "range", "file_path", "path"):
            if key in args:
                val = args[key]
                if isinstance(val, str) and len(val) > 80:
                    val = val[:77] + "..."
                summary[key] = val
        if tool_name == "run_code" and isinstance(args.get("code"), str):
            first_line = args["code"].split("\n", 1)[0][:60]
            summary["code_preview"] = first_line + ("…" if len(args["code"]) > 60 else "")
        return summary

    def _build_system_prompt(
        self,
        *,
        config: SubagentConfig,
        parent_context: str,
        enriched_contexts: list[str] | None = None,
        workspace_context: str = "",
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
        # S1: 注入文件全景 + CoW 路径映射，让子代理知道工作区有哪些文件
        if workspace_context.strip():
            parts.append(workspace_context.strip())
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
        session_turn: int | None,
        file_access_guard: Any | None = None,
        sandbox_env: Any | None = None,
        cow_mappings: dict[str, str] | None = None,
        workspace_root: str = "",
    ) -> _ExecResult:
        """执行子代理内单次工具调用（含审批桥接）。"""
        # S2: CoW 路径重定向（与主代理 ToolDispatcher 对齐）
        arguments, cow_reminders = self._redirect_cow_paths(
            tool_name=tool_name,
            arguments=arguments,
            cow_mappings=cow_mappings,
            workspace_root=workspace_root,
        )

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
            # allowed_tools 显式白名单优先于 readOnly 策略：
            # 子代理定义者明确授权的工具允许执行（如 explorer 使用 run_code）。
            explicitly_allowed = bool(config.allowed_tools) and tool_name in config.allowed_tools
            if not read_only_safe and not explicitly_allowed:
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

            # MCP 工具不做本地文件快照审计（由远端系统自行审计），
            # 使用通用 probe→execute→enrich 流水线。
            if self._approval.is_mcp_tool(tool_name):
                return await self._execute_with_probe(
                    registry=registry,
                    persistent_memory=persistent_memory,
                    tool_name=tool_name,
                    arguments=arguments,
                    tool_scope=tool_scope,
                    tool_result_enricher=tool_result_enricher,
                    log_prefix="subagent mcp unknown",
                    file_access_guard=file_access_guard,
                    sandbox_env=sandbox_env,
                )

            def _execute(name: str, args: dict[str, Any], scope: list[str]) -> Any:
                from excelmanus.tools import memory_tools

                _tokens = self._set_contextvars(file_access_guard, sandbox_env)
                try:
                    with memory_tools.bind_memory_context(persistent_memory):
                        return registry.call_tool(name, args, tool_scope=scope)
                finally:
                    self._reset_contextvars(_tokens)

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
                    session_turn=session_turn,
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

        # 无审计路径：使用通用 probe→execute→enrich 流水线
        return await self._execute_with_probe(
            registry=registry,
            persistent_memory=persistent_memory,
            tool_name=tool_name,
            arguments=arguments,
            tool_scope=tool_scope,
            tool_result_enricher=tool_result_enricher,
            file_access_guard=file_access_guard,
            sandbox_env=sandbox_env,
        )

    async def _call_tool_with_memory_context(
        self,
        *,
        registry: FilteredToolRegistry,
        persistent_memory: "PersistentMemory | None",
        tool_name: str,
        arguments: dict[str, Any],
        tool_scope: list[str],
        file_access_guard: Any | None = None,
        sandbox_env: Any | None = None,
    ) -> Any:
        """在线程池执行工具时绑定记忆上下文和安全上下文，避免会话间串扰。"""
        from excelmanus.tools import memory_tools

        def _call() -> Any:
            _tokens = self._set_contextvars(file_access_guard, sandbox_env)
            try:
                with memory_tools.bind_memory_context(persistent_memory):
                    return registry.call_tool(tool_name, arguments, tool_scope=tool_scope)
            finally:
                self._reset_contextvars(_tokens)

        return await asyncio.to_thread(_call)

    @staticmethod
    def _set_contextvars(
        file_access_guard: Any | None,
        sandbox_env: Any | None,
    ) -> list[Any]:
        """设置 FileAccessGuard + sandbox env contextvars，返回用于恢复的 token 列表。"""
        tokens: list[Any] = []
        if file_access_guard is not None:
            from excelmanus.tools._guard_ctx import set_guard
            tokens.append(("guard", set_guard(file_access_guard)))
        if sandbox_env is not None:
            from excelmanus.tools.code_tools import set_sandbox_env
            tokens.append(("sandbox", set_sandbox_env(sandbox_env)))
        return tokens

    @staticmethod
    def _reset_contextvars(tokens: list[Any]) -> None:
        """恢复 _set_contextvars 设置的 contextvars。"""
        for kind, token in tokens:
            if kind == "guard":
                from excelmanus.tools._guard_ctx import reset_guard
                reset_guard(token)
            elif kind == "sandbox":
                from excelmanus.tools.code_tools import _current_sandbox_env
                _current_sandbox_env.reset(token)

    @staticmethod
    def _redirect_cow_paths(
        *,
        tool_name: str,
        arguments: dict[str, Any],
        cow_mappings: dict[str, str] | None,
        workspace_root: str,
    ) -> tuple[dict[str, Any], list[str]]:
        """检查工具参数中的文件路径是否命中 CoW 注册表，自动重定向。

        简化版实现，与主代理 ToolDispatcher._redirect_cow_paths 对齐。
        """
        if not cow_mappings:
            return arguments, []

        _PATH_FIELDS = ("file_path", "path", "source_file", "target_file",
                        "output_path", "directory", "source", "destination")
        path_fields = [f for f in _PATH_FIELDS if f in arguments]
        if not path_fields:
            return arguments, []

        redirected = dict(arguments)
        reminders: list[str] = []
        for field_name in path_fields:
            raw = arguments.get(field_name)
            if raw is None:
                continue
            raw_str = str(raw).strip()
            if not raw_str:
                continue
            rel_path = raw_str
            if workspace_root and raw_str.startswith(workspace_root):
                rel_path = raw_str[len(workspace_root):].lstrip("/")
            redirect = cow_mappings.get(rel_path)
            if redirect is not None:
                if raw_str.startswith(workspace_root):
                    new_path = f"{workspace_root}/{redirect}"
                else:
                    new_path = redirect
                redirected[field_name] = new_path
                reminders.append(
                    f"⚠️ 路径 `{raw_str}` 是受保护的原始文件，"
                    f"已自动重定向到副本 `{new_path}`。"
                )
                logger.info(
                    "子代理 CoW 路径拦截: tool=%s field=%s %s → %s",
                    tool_name, field_name, raw_str, new_path,
                )
        return redirected, reminders

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
        _sub_mem_protocol = getattr(config, 'protocol', '') or self._parent_config.protocol
        client = create_client(
            api_key=config.api_key or self._parent_config.api_key,
            base_url=config.base_url or self._parent_config.base_url,
            protocol=_sub_mem_protocol,
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
    def _failure_signature(
        *,
        tool_name: str,
        arguments: dict[str, Any],
        error: str | None,
    ) -> str:
        """构造失败签名：同工具 + 同参数 + 同错误才视为同一失败。"""
        try:
            canonical_args = json.dumps(arguments, sort_keys=True, ensure_ascii=False, default=str)
        except Exception:  # noqa: BLE001
            canonical_args = str(arguments)
        error_text = (error or "").strip()
        if len(error_text) > 240:
            error_text = error_text[:240]
        raw = f"{tool_name}|{canonical_args}|{error_text}"
        return hashlib.md5(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _category_signature(*, tool_name: str, error: str | None) -> str:
        """构造类别签名：同工具 + 相似错误类别即视为同类失败。

        与 _failure_signature 不同，此签名不包含完整参数，
        用于渐进提示（早期预警），而非硬切终止。
        """
        error_text = (error or "").strip().lower()
        # 提取错误类别关键词
        category = "unknown"
        for kw, cat in (
            ("not found", "not_found"), ("不存在", "not_found"), ("找不到", "not_found"),
            ("no such file", "not_found"), ("filenotfound", "not_found"),
            ("permission", "permission"), ("权限", "permission"), ("denied", "permission"),
            ("timeout", "timeout"), ("超时", "timeout"), ("timed out", "timeout"),
            ("parse", "parse_error"), ("解析", "parse_error"), ("invalid", "parse_error"),
            ("json", "parse_error"), ("syntax", "parse_error"),
            ("toolnotallowed", "not_allowed"), ("不在", "not_allowed"),
            ("connection", "network"), ("连接", "network"), ("network", "network"),
        ):
            if kw in error_text:
                category = cat
                break
        raw = f"{tool_name}|{category}"
        return hashlib.md5(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _build_failure_hint(
        *,
        tool_name: str,
        streak: int,
        max_failures: int,
        error: str | None,
    ) -> str:
        """构建渐进降级提示消息，引导子代理换策略。"""
        error_brief = (error or "")[:120]
        remaining = max_failures - streak
        if remaining <= 1:
            urgency = "即将触发终止"
        else:
            urgency = f"还剩 {remaining} 次机会"
        return (
            f"[系统提示] 工具 `{tool_name}` 已连续出现 {streak} 次相似失败"
            f"（{urgency}）。\n"
            f"最近错误: {error_brief}\n"
            "请尝试以下策略之一：\n"
            "1. 换用其他可用工具完成同样目标\n"
            "2. 修改参数（如换用其他路径、范围、sheet名）\n"
            "3. 如果目标确实不可达，直接汇报当前已获取的信息并结束"
        )

    @staticmethod
    def _update_failure_streak(
        *,
        signature: str,
        previous_signature: str | None,
        previous_streak: int,
    ) -> int:
        if previous_signature == signature:
            return previous_streak + 1
        return 1

    @staticmethod
    def _build_partial_progress_summary(
        *,
        memory: ConversationMemory,
        observed_files: set[str],
        structured_changes: list[SubagentFileChange],
        iterations: int,
        tool_calls: int,
    ) -> str:
        """从对话历史提取子代理已完成的有用工作摘要。

        在异常退出时调用，确保主代理不会丢失已获取的中间信息。
        """
        # 收集 assistant 文本消息（排除纯 tool_calls 消息）
        assistant_texts: list[str] = []
        for msg in memory.messages:
            if msg.get("role") != "assistant":
                continue
            content = msg.get("content")
            if isinstance(content, str) and content.strip():
                text = content.strip()
                if len(text) > 200:
                    text = text[:200] + "…"
                assistant_texts.append(text)

        parts: list[str] = []

        # 统计信息
        if iterations > 0 or tool_calls > 0:
            parts.append(f"已执行 {iterations} 轮迭代、{tool_calls} 次工具调用")
        if observed_files:
            file_list = ", ".join(sorted(observed_files)[:5])
            suffix = f" 等共 {len(observed_files)} 个" if len(observed_files) > 5 else ""
            parts.append(f"涉及文件: {file_list}{suffix}")
        if structured_changes:
            parts.append(f"已产生 {len(structured_changes)} 条结构化变更")

        # 最后一条有实质内容的 assistant 分析
        if assistant_texts:
            last_text = assistant_texts[-1]
            parts.append(f"中间分析: {last_text}")

        if not parts:
            return ""
        return "\n".join(parts)

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
