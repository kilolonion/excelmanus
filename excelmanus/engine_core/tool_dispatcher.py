"""ToolDispatcher — 从 AgentEngine 解耦的工具调度组件。

负责管理：
- 工具参数解析（JSON string / dict / None）
- 普通工具的 registry 调用（含线程池执行）
- 单个工具调用的完整执行流程（execute）
- 工具结果截断
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from excelmanus.hooks import HookDecision, HookEvent
from excelmanus.logger import get_logger, log_tool_call
from excelmanus.tools.registry import ToolNotAllowedError

if TYPE_CHECKING:
    from excelmanus.events import EventCallback

logger = get_logger("tool_dispatcher")


class ToolDispatcher:
    """工具调度器：参数解析、分支路由、执行、审计。"""

    def __init__(self, engine: Any) -> None:
        self._engine = engine

    # ── 向后兼容：供测试直接 ToolDispatcher(registry=xxx) ──

    @property
    def _registry(self) -> Any:
        return self._engine._registry

    @property
    def _persistent_memory(self) -> Any:
        return self._engine._persistent_memory

    def parse_arguments(self, raw_args: Any) -> tuple[dict[str, Any], str | None]:
        """解析工具调用参数，返回 (arguments, error)。

        error 为 None 表示解析成功。
        """
        if raw_args is None or raw_args == "":
            return {}, None
        if isinstance(raw_args, dict):
            return raw_args, None
        if isinstance(raw_args, str):
            try:
                parsed = json.loads(raw_args)
                if not isinstance(parsed, dict):
                    return {}, f"参数必须为 JSON 对象，当前类型: {type(parsed).__name__}"
                return parsed, None
            except (json.JSONDecodeError, TypeError) as exc:
                return {}, f"JSON 解析失败: {exc}"
        return {}, f"参数类型无效: {type(raw_args).__name__}"

    async def call_registry_tool(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        tool_scope: Sequence[str] | None = None,
    ) -> str:
        """在线程池中调用工具，返回截断后的结果字符串。"""
        from excelmanus.tools import memory_tools

        registry = self._registry
        persistent_memory = self._persistent_memory

        def _call() -> Any:
            with memory_tools.bind_memory_context(persistent_memory):
                return registry.call_tool(
                    tool_name,
                    arguments,
                    tool_scope=tool_scope,
                )

        result_value = await asyncio.to_thread(_call)
        result_str = str(result_value)

        # 工具结果截断
        tool_def = getattr(registry, "get_tool", lambda _: None)(tool_name)
        if tool_def is not None:
            result_str = tool_def.truncate_result(result_str)

        return result_str

    # ── 核心执行方法：从 AgentEngine._execute_tool_call 搬迁 ──

    async def execute(
        self,
        tc: Any,
        tool_scope: Sequence[str] | None,
        on_event: "EventCallback | None",
        iteration: int,
        route_result: Any | None = None,
    ) -> Any:
        """单个工具调用：参数解析 → 执行 → 事件发射 → 返回结果。

        从 AgentEngine._execute_tool_call 整体搬迁，通过 self._engine
        引用回调 AgentEngine 上的基础设施方法。
        """
        from excelmanus.engine import ToolCallResult, _AuditedExecutionError
        from excelmanus.events import EventType, ToolCallEvent

        e = self._engine  # 引擎快捷引用

        function = getattr(tc, "function", None)
        tool_name = getattr(function, "name", "")
        raw_args = getattr(function, "arguments", None)
        tool_call_id = getattr(tc, "id", "") or f"call_{int(time.time() * 1000)}"

        # 参数解析
        arguments, parse_error = self.parse_arguments(raw_args)

        # 发射 TOOL_CALL_START 事件
        e._emit(
            on_event,
            ToolCallEvent(
                event_type=EventType.TOOL_CALL_START,
                tool_name=tool_name,
                arguments=arguments,
                iteration=iteration,
            ),
        )

        pending_approval = False
        approval_id: str | None = None
        audit_record = None
        pending_question = False
        question_id: str | None = None
        pending_plan = False
        plan_id: str | None = None
        defer_tool_result = False
        finish_accepted = False

        # 执行工具调用
        hook_skill = e._pick_route_skill(route_result)
        if parse_error is not None:
            result_str = f"工具参数解析错误: {parse_error}"
            success = False
            error = result_str
            log_tool_call(
                logger,
                tool_name,
                {"_raw_arguments": raw_args},
                error=error,
            )
        else:
            # ── 备份沙盒模式：重定向文件路径 ──
            arguments = e._redirect_backup_paths(tool_name, arguments)

            pre_hook_raw = e._run_skill_hook(
                skill=hook_skill,
                event=HookEvent.PRE_TOOL_USE,
                payload={
                    "tool_name": tool_name,
                    "arguments": dict(arguments),
                    "iteration": iteration,
                },
                tool_name=tool_name,
            )
            pre_hook = await e._resolve_hook_result(
                event=HookEvent.PRE_TOOL_USE,
                hook_result=pre_hook_raw,
                on_event=on_event,
            )
            if pre_hook is not None and isinstance(pre_hook.updated_input, dict):
                arguments = dict(pre_hook.updated_input)
            skip_high_risk_approval_by_hook = (
                pre_hook is not None and pre_hook.decision == HookDecision.ALLOW
            )
            if skip_high_risk_approval_by_hook:
                logger.info(
                    "Hook ALLOW 已生效，跳过确认门禁：tool=%s iteration=%s",
                    tool_name,
                    iteration,
                )

            if pre_hook is not None and pre_hook.decision == HookDecision.DENY:
                reason = pre_hook.reason or "Hook 拒绝执行该工具。"
                result_str = f"工具调用被 Hook 拒绝：{reason}"
                success = False
                error = result_str
                log_tool_call(logger, tool_name, arguments, error=error)
            elif pre_hook is not None and pre_hook.decision == HookDecision.ASK:
                try:
                    pending = e._approval.create_pending(
                        tool_name=tool_name,
                        arguments=arguments,
                        tool_scope=tool_scope,
                    )
                    pending_approval = True
                    approval_id = pending.approval_id
                    result_str = e._format_pending_prompt(pending)
                    success = True
                    error = None
                    e._emit_pending_approval_event(
                        pending=pending, on_event=on_event, iteration=iteration,
                    )
                    log_tool_call(logger, tool_name, arguments, result=result_str)
                except ValueError:
                    result_str = e._approval.pending_block_message()
                    success = False
                    error = result_str
                    log_tool_call(logger, tool_name, arguments, error=error)
            else:
                try:
                    skip_plan_once_for_task_create = False
                    if tool_name == "task_create" and e._suspend_task_create_plan_once:
                        skip_plan_once_for_task_create = True
                        e._suspend_task_create_plan_once = False

                    if tool_name == "activate_skill":
                        selected_name = arguments.get("skill_name")
                        if not isinstance(selected_name, str) or not selected_name.strip():
                            result_str = "工具参数错误: skill_name 必须为非空字符串。"
                            success = False
                            error = result_str
                        else:
                            result_str = await e._handle_activate_skill(
                                selected_name.strip(),
                            )
                            success = result_str.startswith("OK")
                            error = None if success else result_str
                        log_tool_call(
                            logger,
                            tool_name,
                            arguments,
                            result=result_str if success else None,
                            error=error if not success else None,
                        )
                    elif tool_name == "delegate_to_subagent":
                        task_value = arguments.get("task")
                        task_brief = arguments.get("task_brief")
                        # task_brief 优先：渲染为结构化 Markdown
                        if isinstance(task_brief, dict) and task_brief.get("title"):
                            task_value = e._render_task_brief(task_brief)
                        if not isinstance(task_value, str) or not task_value.strip():
                            result_str = "工具参数错误: task 或 task_brief 必须提供其一。"
                            success = False
                            error = result_str
                        else:
                            agent_name_value = arguments.get("agent_name")
                            if agent_name_value is not None and not isinstance(agent_name_value, str):
                                result_str = "工具参数错误: agent_name 必须为字符串。"
                                success = False
                                error = result_str
                            else:
                                raw_file_paths = arguments.get("file_paths")
                                if raw_file_paths is not None and not isinstance(raw_file_paths, list):
                                    result_str = "工具参数错误: file_paths 必须为字符串数组。"
                                    success = False
                                    error = result_str
                                else:
                                    delegate_outcome = await e._delegate_to_subagent(
                                        task=task_value.strip(),
                                        agent_name=agent_name_value.strip() if isinstance(agent_name_value, str) else None,
                                        file_paths=raw_file_paths,
                                        on_event=on_event,
                                    )
                                    result_str = delegate_outcome.reply
                                    success = delegate_outcome.success
                                    error = None if success else result_str

                                    # ── 写入传播：subagent 有文件变更时视为主 agent 写入 ──
                                    sub_result = delegate_outcome.subagent_result
                                    if (
                                        success
                                        and sub_result is not None
                                        and sub_result.structured_changes
                                    ):
                                        e._has_write_tool_call = True
                                        e._current_write_hint = "may_write"
                                        logger.info(
                                            "delegate_to_subagent 写入传播: structured_changes=%d, paths=%s",
                                            len(sub_result.structured_changes),
                                            sub_result.file_changes,
                                        )
                                    if (
                                        not success
                                        and sub_result is not None
                                        and sub_result.pending_approval_id is not None
                                    ):
                                        pending = e._approval.pending
                                        approval_id_value = sub_result.pending_approval_id
                                        high_risk_tool = (
                                            pending.tool_name
                                            if pending is not None and pending.approval_id == approval_id_value
                                            else "高风险工具"
                                        )
                                        question = e._enqueue_subagent_approval_question(
                                            approval_id=approval_id_value,
                                            tool_name=high_risk_tool,
                                            picked_agent=delegate_outcome.picked_agent or "subagent",
                                            task_text=delegate_outcome.task_text,
                                            normalized_paths=delegate_outcome.normalized_paths,
                                            tool_call_id=tool_call_id,
                                            on_event=on_event,
                                            iteration=iteration,
                                        )
                                        result_str = f"已创建待回答问题 `{question.question_id}`。"
                                        question_id = question.question_id
                                        pending_question = True
                                        defer_tool_result = True
                                        success = True
                                        error = None
                        log_tool_call(
                            logger,
                            tool_name,
                            arguments,
                            result=result_str if success else None,
                            error=error if not success else None,
                        )
                    elif tool_name == "list_subagents":
                        result_str = e._handle_list_subagents()
                        success = True
                        error = None
                        log_tool_call(
                            logger,
                            tool_name,
                            arguments,
                            result=result_str,
                        )
                    elif tool_name == "finish_task":
                        summary = arguments.get("summary", "")
                        _has_write = getattr(e, "_has_write_tool_call", False)
                        _hint = getattr(e, "_current_write_hint", "unknown")
                        if _has_write:
                            result_str = f"✓ 任务完成。{summary}"
                            success = True
                            error = None
                            finish_accepted = True
                        elif getattr(e, "_finish_task_warned", False):
                            if _hint == "may_write":
                                # write_hint 为 may_write 但无实际写入，不允许绕过
                                result_str = (
                                    "⚠️ 当前任务被判定为需要写入（write_hint=may_write），"
                                    "但未检测到任何写入类工具的成功调用。"
                                    "请先执行写入操作，再调用 finish_task。"
                                )
                                success = True
                                error = None
                                finish_accepted = False
                            else:
                                result_str = f"✓ 任务完成（无写入）。{summary}"
                                success = True
                                error = None
                                finish_accepted = True
                        else:
                            if _hint == "may_write":
                                result_str = (
                                    "⚠️ 当前任务被判定为需要写入（write_hint=may_write），"
                                    "但未检测到任何写入类工具的成功调用。"
                                    "请先调用写入工具完成实际操作，再调用 finish_task。"
                                    "不接受无写入的完成声明。"
                                )
                            else:
                                result_str = (
                                    "⚠️ 未检测到写入类工具的成功调用。"
                                    "如果经分析确实不需要写入，请再次调用 finish_task 并在 summary 中说明原因。"
                                    "否则请先执行写入操作。"
                                )
                                e._finish_task_warned = True
                            success = True
                            error = None
                            finish_accepted = False
                        log_tool_call(
                            logger,
                            tool_name,
                            arguments,
                            result=result_str,
                        )
                    elif tool_name == "ask_user":
                        result_str, question_id = e._handle_ask_user(
                            arguments=arguments,
                            tool_call_id=tool_call_id,
                            on_event=on_event,
                            iteration=iteration,
                        )
                        success = True
                        error = None
                        pending_question = True
                        defer_tool_result = True
                        log_tool_call(
                            logger,
                            tool_name,
                            arguments,
                            result=result_str,
                        )
                    elif (
                        tool_name == "task_create"
                        and e._plan_intercept_task_create
                        and not skip_plan_once_for_task_create
                    ):
                        result_str, plan_id, plan_error = await e._intercept_task_create_with_plan(
                            arguments=arguments,
                            route_result=route_result,
                            tool_call_id=tool_call_id,
                            on_event=on_event,
                        )
                        success = plan_error is None
                        error = plan_error
                        pending_plan = success
                        defer_tool_result = success
                        log_tool_call(
                            logger,
                            tool_name,
                            arguments,
                            result=result_str if success else None,
                            error=error if not success else None,
                        )
                    elif tool_name == "run_code" and e._config.code_policy_enabled:
                        # ── 动态代码策略引擎路由 ──
                        from excelmanus.security.code_policy import CodePolicyEngine, CodeRiskTier
                        _code_arg = arguments.get("code") or ""
                        _cp_engine = CodePolicyEngine(
                            extra_safe_modules=e._config.code_policy_extra_safe_modules,
                            extra_blocked_modules=e._config.code_policy_extra_blocked_modules,
                        )
                        _analysis = _cp_engine.analyze(_code_arg)
                        _auto_green = (
                            _analysis.tier == CodeRiskTier.GREEN
                            and e._config.code_policy_green_auto_approve
                        )
                        _auto_yellow = (
                            _analysis.tier == CodeRiskTier.YELLOW
                            and e._config.code_policy_yellow_auto_approve
                        )
                        if _auto_green or _auto_yellow or e._full_access_enabled:
                            _sandbox_tier = _analysis.tier.value
                            _augmented_args = {**arguments, "sandbox_tier": _sandbox_tier}
                            result_value, audit_record = await e._execute_tool_with_audit(
                                tool_name=tool_name,
                                arguments=_augmented_args,
                                tool_scope=tool_scope,
                                approval_id=e._approval.new_approval_id(),
                                created_at_utc=e._approval.utc_now(),
                                undoable=False,
                            )
                            result_str = str(result_value)
                            tool_def = getattr(e._registry, "get_tool", lambda _: None)(tool_name)
                            if tool_def is not None:
                                result_str = tool_def.truncate_result(result_str)
                            success = True
                            error = None
                            # ── run_code → window 感知桥接 ──
                            if audit_record is not None and e._window_perception is not None:
                                _stdout_tail = ""
                                try:
                                    _rc_json = json.loads(result_str)
                                    _stdout_tail = _rc_json.get("stdout_tail", "") if isinstance(_rc_json, dict) else ""
                                except (json.JSONDecodeError, TypeError):
                                    pass
                                e._window_perception.observe_code_execution(
                                    code=_code_arg,
                                    audit_changes=audit_record.changes if audit_record else None,
                                    stdout_tail=_stdout_tail,
                                    iteration=iteration,
                                )
                            logger.info(
                                "run_code 策略引擎: tier=%s auto_approved=True caps=%s",
                                _analysis.tier.value,
                                sorted(_analysis.capabilities),
                            )
                            log_tool_call(logger, tool_name, arguments, result=result_str)
                        else:
                            # RED 或配置不允许自动执行 → /accept 流程
                            _caps_detail = ", ".join(sorted(_analysis.capabilities))
                            _details_text = "; ".join(_analysis.details[:3])
                            pending = e._approval.create_pending(
                                tool_name=tool_name,
                                arguments=arguments,
                                tool_scope=tool_scope,
                            )
                            pending_approval = True
                            approval_id = pending.approval_id
                            result_str = (
                                f"⚠️ 代码包含高风险操作，需要人工确认：\n"
                                f"- 风险等级: {_analysis.tier.value}\n"
                                f"- 检测到: {_caps_detail}\n"
                                f"- 详情: {_details_text}\n"
                                f"{e._format_pending_prompt(pending)}"
                            )
                            success = True
                            error = None
                            e._emit_pending_approval_event(
                                pending=pending, on_event=on_event, iteration=iteration,
                            )
                            logger.info(
                                "run_code 策略引擎: tier=%s → pending approval %s",
                                _analysis.tier.value,
                                pending.approval_id,
                            )
                            log_tool_call(logger, tool_name, arguments, result=result_str)
                    elif e._approval.is_audit_only_tool(tool_name):
                        result_value, audit_record = await e._execute_tool_with_audit(
                            tool_name=tool_name,
                            arguments=arguments,
                            tool_scope=tool_scope,
                            approval_id=e._approval.new_approval_id(),
                            created_at_utc=e._approval.utc_now(),
                            undoable=tool_name not in {"run_code", "run_shell"},
                        )
                        result_str = str(result_value)
                        tool_def = getattr(e._registry, "get_tool", lambda _: None)(tool_name)
                        if tool_def is not None:
                            result_str = tool_def.truncate_result(result_str)
                        success = True
                        error = None
                        log_tool_call(logger, tool_name, arguments, result=result_str)
                    elif e._approval.is_high_risk_tool(tool_name):
                        if not e._full_access_enabled and not skip_high_risk_approval_by_hook:
                            pending = e._approval.create_pending(
                                tool_name=tool_name,
                                arguments=arguments,
                                tool_scope=tool_scope,
                            )
                            pending_approval = True
                            approval_id = pending.approval_id
                            result_str = e._format_pending_prompt(pending)
                            success = True
                            error = None
                            e._emit_pending_approval_event(
                                pending=pending, on_event=on_event, iteration=iteration,
                            )
                            log_tool_call(logger, tool_name, arguments, result=result_str)
                        elif e._approval.is_mcp_tool(tool_name):
                            # 非白名单 MCP 工具在 fullaccess 下可直接执行（不做文件审计）。
                            result_value = await self.call_registry_tool(
                                tool_name=tool_name,
                                arguments=arguments,
                                tool_scope=tool_scope,
                            )
                            result_str = str(result_value)
                            success = True
                            error = None
                            log_tool_call(logger, tool_name, arguments, result=result_str)
                        else:
                            result_value, audit_record = await e._execute_tool_with_audit(
                                tool_name=tool_name,
                                arguments=arguments,
                                tool_scope=tool_scope,
                                approval_id=e._approval.new_approval_id(),
                                created_at_utc=e._approval.utc_now(),
                                undoable=tool_name not in {"run_code", "run_shell"},
                            )
                            result_str = str(result_value)
                            tool_def = getattr(e._registry, "get_tool", lambda _: None)(tool_name)
                            if tool_def is not None:
                                result_str = tool_def.truncate_result(result_str)
                            success = True
                            error = None
                            log_tool_call(logger, tool_name, arguments, result=result_str)
                    else:
                        result_value = await self.call_registry_tool(
                            tool_name=tool_name,
                            arguments=arguments,
                            tool_scope=tool_scope,
                        )
                        result_str = str(result_value)
                        success = True
                        error = None
                        log_tool_call(logger, tool_name, arguments, result=result_str)
                except ValueError as exc:
                    result_str = str(exc)
                    success = False
                    error = result_str
                    log_tool_call(logger, tool_name, arguments, error=error)
                except ToolNotAllowedError:
                    permission_error = {
                        "error_code": "TOOL_NOT_ALLOWED",
                        "tool": tool_name,
                        "message": f"工具 '{tool_name}' 不在当前授权范围内。",
                    }
                    result_str = json.dumps(permission_error, ensure_ascii=False)
                    success = False
                    error = result_str
                    log_tool_call(logger, tool_name, arguments, error=error)
                except Exception as exc:
                    root_exc: Exception = exc
                    if isinstance(exc, _AuditedExecutionError):
                        audit_record = exc.record
                        root_exc = exc.cause
                    result_str = f"工具执行错误: {root_exc}"
                    success = False
                    error = str(root_exc)
                    log_tool_call(logger, tool_name, arguments, error=error)

            # ── 检测 registry 层返回的结构化错误 JSON ──
            if success and e._registry.is_error_result(result_str):
                success = False
                try:
                    _err = json.loads(result_str)
                    error = _err.get("message") or result_str
                except Exception:
                    error = result_str

            post_hook_event = HookEvent.POST_TOOL_USE if success else HookEvent.POST_TOOL_USE_FAILURE
            post_hook_raw = e._run_skill_hook(
                skill=hook_skill,
                event=post_hook_event,
                payload={
                    "tool_name": tool_name,
                    "arguments": dict(arguments),
                    "success": success,
                    "result": result_str,
                    "error": error,
                    "iteration": iteration,
                },
                tool_name=tool_name,
            )
            post_hook = await e._resolve_hook_result(
                event=post_hook_event,
                hook_result=post_hook_raw,
                on_event=on_event,
            )
            if post_hook is not None:
                if post_hook.additional_context:
                    result_str = f"{result_str}\n[Hook] {post_hook.additional_context}"
                if post_hook.decision == HookDecision.DENY:
                    reason = post_hook.reason or "post hook 拒绝"
                    success = False
                    error = reason
                    result_str = f"{result_str}\n[Hook 拒绝] {reason}"

        result_str = e._enrich_tool_result_with_window_perception(
            tool_name=tool_name,
            arguments=arguments,
            result_text=result_str,
            success=success,
        )
        result_str = e._apply_tool_result_hard_cap(result_str)
        if error:
            error = e._apply_tool_result_hard_cap(str(error))

        # 发射 TOOL_CALL_END 事件
        e._emit(
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

        # 任务清单事件：成功执行 task_create/task_update 后发射对应事件
        if success and tool_name == "task_create" and not pending_plan:
            task_list = e._task_store.current
            if task_list is not None:
                e._emit(
                    on_event,
                    ToolCallEvent(
                        event_type=EventType.TASK_LIST_CREATED,
                        task_list_data=task_list.to_dict(),
                    ),
                )
        elif success and tool_name == "task_update":
            task_list = e._task_store.current
            if task_list is not None:
                e._emit(
                    on_event,
                    ToolCallEvent(
                        event_type=EventType.TASK_ITEM_UPDATED,
                        task_index=arguments.get("task_index"),
                        task_status=arguments.get("status", ""),
                        task_result=arguments.get("result"),
                        task_list_data=task_list.to_dict(),
                    ),
                )

        return ToolCallResult(
            tool_name=tool_name,
            arguments=arguments,
            result=result_str,
            success=success,
            error=error,
            pending_approval=pending_approval,
            approval_id=approval_id,
            audit_record=audit_record,
            pending_question=pending_question,
            question_id=question_id,
            pending_plan=pending_plan,
            plan_id=plan_id,
            defer_tool_result=defer_tool_result,
            finish_accepted=finish_accepted,
        )
