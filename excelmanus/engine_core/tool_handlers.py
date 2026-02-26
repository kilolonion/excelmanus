"""ToolHandler 策略实现 — 从 _dispatch_tool_execution if-elif 提取的独立处理器。

每个 Handler 负责一类工具的执行逻辑，通过 can_handle / handle 接口
与 ToolDispatcher 的策略表对接。
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from excelmanus.logger import get_logger, log_tool_call

if TYPE_CHECKING:
    from excelmanus.engine import AgentEngine
    from excelmanus.engine_core.tool_dispatcher import ToolDispatcher, _ToolExecOutcome
    from excelmanus.events import EventCallback

logger = get_logger("tool_handlers")


# ---------------------------------------------------------------------------
# 基类
# ---------------------------------------------------------------------------

class BaseToolHandler:
    """所有 handler 的基类，持有 engine 和 dispatcher 引用（双轨兼容）。"""

    def __init__(self, engine: AgentEngine, dispatcher: ToolDispatcher) -> None:
        self._engine = engine
        self._dispatcher = dispatcher

    def can_handle(self, tool_name: str, **kwargs: Any) -> bool:
        raise NotImplementedError

    async def handle(
        self,
        tool_name: str,
        tool_call_id: str,
        arguments: dict[str, Any],
        *,
        tool_scope: Sequence[str] | None = None,
        on_event: Any = None,
        iteration: int = 0,
        route_result: Any = None,
    ) -> _ToolExecOutcome:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# 技能激活处理器（SkillActivationHandler）
# ---------------------------------------------------------------------------

class SkillActivationHandler(BaseToolHandler):
    """处理 activate_skill 工具调用。"""

    def can_handle(self, tool_name: str, **kwargs: Any) -> bool:
        return tool_name == "activate_skill"

    async def handle(self, tool_name, tool_call_id, arguments, **kwargs):
        from excelmanus.engine_core.tool_dispatcher import _ToolExecOutcome

        e = self._engine
        selected_name = arguments.get("skill_name")
        if not isinstance(selected_name, str) or not selected_name.strip():
            result_str = "工具参数错误: skill_name 必须为非空字符串。"
            log_tool_call(logger, tool_name, arguments, error=result_str)
            return _ToolExecOutcome(result_str=result_str, success=False, error=result_str)

        result_str = await e.handle_activate_skill(selected_name.strip())
        success = result_str.startswith("OK")
        error = None if success else result_str
        log_tool_call(logger, tool_name, arguments, result=result_str if success else None, error=error if not success else None)
        return _ToolExecOutcome(result_str=result_str, success=success, error=error)


# ---------------------------------------------------------------------------
# 委托处理器（DelegationHandler）
# ---------------------------------------------------------------------------

class DelegationHandler(BaseToolHandler):
    """处理 delegate / delegate_to_subagent（兼容） / list_subagents / parallel_delegate（兼容）。"""

    def can_handle(self, tool_name: str, **kwargs: Any) -> bool:
        return tool_name in ("delegate", "delegate_to_subagent", "list_subagents", "parallel_delegate")

    async def handle(self, tool_name, tool_call_id, arguments, *, tool_scope=None, on_event=None, iteration=0, route_result=None):
        from excelmanus.engine_core.tool_dispatcher import _ToolExecOutcome

        if tool_name == "list_subagents":
            return self._handle_list(arguments)

        # delegate / delegate_to_subagent / parallel_delegate 统一处理
        # 判断是并行还是单任务模式
        tasks_value = arguments.get("tasks")
        if tool_name == "parallel_delegate" or (isinstance(tasks_value, list) and len(tasks_value) >= 2):
            return await self._handle_parallel(arguments, on_event=on_event)
        else:
            return await self._handle_delegate(tool_call_id, arguments, on_event=on_event, iteration=iteration)

    async def _handle_delegate(self, tool_call_id, arguments, *, on_event, iteration):
        from excelmanus.engine_core.tool_dispatcher import _ToolExecOutcome

        e = self._engine
        task_value = arguments.get("task")
        task_brief = arguments.get("task_brief")
        if isinstance(task_brief, dict) and task_brief.get("title"):
            task_value = e.render_task_brief(task_brief)
        if not isinstance(task_value, str) or not task_value.strip():
            result_str = "工具参数错误: task、task_brief 或 tasks 必须提供其一。"
            log_tool_call(logger, "delegate", arguments, error=result_str)
            return _ToolExecOutcome(result_str=result_str, success=False, error=result_str)

        agent_name_value = arguments.get("agent_name")
        if agent_name_value is not None and not isinstance(agent_name_value, str):
            result_str = "工具参数错误: agent_name 必须为字符串。"
            log_tool_call(logger, "delegate", arguments, error=result_str)
            return _ToolExecOutcome(result_str=result_str, success=False, error=result_str)

        raw_file_paths = arguments.get("file_paths")
        if raw_file_paths is not None and not isinstance(raw_file_paths, list):
            result_str = "工具参数错误: file_paths 必须为字符串数组。"
            log_tool_call(logger, "delegate", arguments, error=result_str)
            return _ToolExecOutcome(result_str=result_str, success=False, error=result_str)

        delegate_outcome = await e.delegate_to_subagent(
            task=task_value.strip(),
            agent_name=agent_name_value.strip() if isinstance(agent_name_value, str) else None,
            file_paths=raw_file_paths,
            on_event=on_event,
        )
        result_str = delegate_outcome.reply
        success = delegate_outcome.success
        error = None if success else result_str

        # 写入传播
        sub_result = delegate_outcome.subagent_result
        if success and sub_result is not None and sub_result.structured_changes:
            e.record_write_action()

        # 子代理审批问题：阻塞等待用户决策
        if (
            not success
            and sub_result is not None
            and sub_result.pending_approval_id is not None
        ):
            import asyncio
            import json as _json
            from excelmanus.interaction import DEFAULT_INTERACTION_TIMEOUT

            pending = e.approval.pending
            approval_id_value = sub_result.pending_approval_id
            high_risk_tool = (
                pending.tool_name
                if pending is not None and pending.approval_id == approval_id_value
                else "高风险工具"
            )
            question = e.enqueue_subagent_approval_question(
                approval_id=approval_id_value,
                tool_name=high_risk_tool,
                picked_agent=delegate_outcome.picked_agent or "subagent",
                task_text=delegate_outcome.task_text,
                normalized_paths=delegate_outcome.normalized_paths,
                tool_call_id=tool_call_id,
                on_event=on_event,
                iteration=iteration,
            )
            # 阻塞等待用户回答（支持 question_resolver / InteractionRegistry）
            try:
                payload = await e.await_question_answer(question)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                e._question_flow.pop_current()
                e._interaction_registry.cleanup_done()
                result_str = "子代理审批问题超时/取消。"
                log_tool_call(logger, "delegate", arguments, result=result_str)
                return _ToolExecOutcome(result_str=result_str, success=False, error=result_str)

            e._question_flow.pop_current()
            e._interaction_registry.cleanup_done()

            # 处理子代理审批回答（accept/fullaccess-retry/reject）
            if isinstance(payload, dict):
                result_str, success = await e.process_subagent_approval_inline(
                    payload=payload,
                    approval_id=approval_id_value,
                    picked_agent=delegate_outcome.picked_agent or "subagent",
                    task_text=delegate_outcome.task_text,
                    normalized_paths=delegate_outcome.normalized_paths,
                    on_event=on_event,
                )
                error = None if success else result_str
            else:
                result_str = str(payload)
                success = True
                error = None

        log_tool_call(logger, "delegate", arguments, result=result_str if success else None, error=error if not success else None)
        return _ToolExecOutcome(
            result_str=result_str, success=success, error=error,
        )

    def _handle_list(self, arguments):
        from excelmanus.engine_core.tool_dispatcher import _ToolExecOutcome

        result_str = self._engine.handle_list_subagents()
        log_tool_call(logger, "list_subagents", arguments, result=result_str)
        return _ToolExecOutcome(result_str=result_str, success=True)

    async def _handle_parallel(self, arguments, *, on_event):
        from excelmanus.engine_core.tool_dispatcher import _ToolExecOutcome

        e = self._engine
        raw_tasks = arguments.get("tasks")
        if not isinstance(raw_tasks, list) or len(raw_tasks) < 2:
            result_str = "工具参数错误: tasks 必须为包含至少 2 个子任务的数组。"
            log_tool_call(logger, "delegate", arguments, error=result_str)
            return _ToolExecOutcome(result_str=result_str, success=False, error=result_str)

        try:
            pd_outcome = await e.parallel_delegate_to_subagents(tasks=raw_tasks, on_event=on_event)
            result_str = pd_outcome.reply
            success = pd_outcome.success
            error = None if success else result_str

            for pd_sub_outcome in pd_outcome.outcomes:
                sub_result = pd_sub_outcome.subagent_result
                if pd_sub_outcome.success and sub_result is not None and sub_result.structured_changes:
                    e.record_workspace_write_action()
        except Exception as exc:
            result_str = f"parallel_delegate 执行异常: {exc}"
            success = False
            error = str(exc)

        log_tool_call(logger, "delegate", arguments, result=result_str if success else None, error=error if not success else None)
        return _ToolExecOutcome(result_str=result_str, success=success, error=error)


# ---------------------------------------------------------------------------
# 完成任务处理器（FinishTaskHandler）
# ---------------------------------------------------------------------------

class FinishTaskHandler(BaseToolHandler):
    """处理 finish_task 工具调用。"""

    def can_handle(self, tool_name: str, **kwargs: Any) -> bool:
        return tool_name == "finish_task"

    async def handle(self, tool_name, tool_call_id, arguments, *, tool_scope=None, on_event=None, iteration=0, route_result=None):
        from excelmanus.engine_core.tool_dispatcher import _ToolExecOutcome, _render_finish_task_report

        e = self._engine
        report = arguments.get("report")
        summary = arguments.get("summary", "")
        rendered = _render_finish_task_report(report, summary)
        _has_write = getattr(e, "_has_write_tool_call", False)
        _hint = getattr(e, "_current_write_hint", "unknown")
        _guard_mode = getattr(getattr(e, "_config", None), "guard_mode", "off")
        finish_accepted = False

        if _has_write:
            result_str = f"✅ 任务完成\n\n{rendered}" if rendered else "✓ 任务完成。"
            success = True
            finish_accepted = True
        elif _guard_mode == "off" or _hint in ("read_only", "unknown"):
            _no_write_suffix = "（无写入）" if _hint != "unknown" else ""
            result_str = f"✅ 任务完成{_no_write_suffix}\n\n{rendered}" if rendered else f"✓ 任务完成{_no_write_suffix}。"
            success = True
            finish_accepted = True
        elif getattr(e, "_finish_task_warned", False):
            _no_write_suffix = "（无写入）" if _hint == "read_only" else ""
            result_str = f"✅ 任务完成{_no_write_suffix}\n\n{rendered}" if rendered else f"✓ 任务完成{_no_write_suffix}。"
            success = True
            finish_accepted = True
        else:
            result_str = (
                "⚠️ 未检测到写入类工具的成功调用。"
                "如果确实不需要写入，请再次调用 finish_task 并在 summary 中说明原因。"
                "否则请先执行写入操作。"
            )
            e._finish_task_warned = True
            success = True
            finish_accepted = False

        _report_for_event = report if isinstance(report, dict) else None
        if not _report_for_event:
            top_files = arguments.get("affected_files")
            if top_files and isinstance(top_files, list):
                _report_for_event = {"affected_files": top_files}
        self._dispatcher._emit_files_changed_from_report(e, on_event, tool_call_id, _report_for_event, iteration)
        log_tool_call(logger, tool_name, arguments, result=result_str)
        return _ToolExecOutcome(result_str=result_str, success=success, finish_accepted=finish_accepted)


# ---------------------------------------------------------------------------
# 询问用户处理器（AskUserHandler）
# ---------------------------------------------------------------------------

class AskUserHandler(BaseToolHandler):
    """处理 ask_user 工具调用。

    阻塞模式：await 用户回答（通过 InteractionRegistry Future），
    返回回答内容作为 tool result，循环不中断。
    """

    def can_handle(self, tool_name: str, **kwargs: Any) -> bool:
        return tool_name == "ask_user"

    async def handle(self, tool_name, tool_call_id, arguments, *, tool_scope=None, on_event=None, iteration=0, route_result=None):
        from excelmanus.engine_core.tool_dispatcher import _ToolExecOutcome

        result_str = await self._engine.handle_ask_user_blocking(
            arguments=arguments, tool_call_id=tool_call_id, on_event=on_event, iteration=iteration,
        )
        log_tool_call(logger, tool_name, arguments, result=result_str)
        return _ToolExecOutcome(
            result_str=result_str, success=True,
            pending_question=False, question_id=None, defer_tool_result=False,
        )


# ---------------------------------------------------------------------------
# 建议模式切换处理器（SuggestModeSwitchHandler）
# ---------------------------------------------------------------------------

class SuggestModeSwitchHandler(BaseToolHandler):
    """处理 suggest_mode_switch 工具调用。

    阻塞模式：await 用户选择后返回结果。
    """

    def can_handle(self, tool_name: str, **kwargs: Any) -> bool:
        return tool_name == "suggest_mode_switch"

    async def handle(self, tool_name, tool_call_id, arguments, *, tool_scope=None, on_event=None, iteration=0, route_result=None):
        import asyncio
        import json as _json
        from excelmanus.engine_core.tool_dispatcher import _ToolExecOutcome
        from excelmanus.interaction import DEFAULT_INTERACTION_TIMEOUT

        e = self._engine
        target_mode = str(arguments.get("target_mode", "write")).strip()
        reason = str(arguments.get("reason", "")).strip()
        mode_labels = {"write": "写入", "read": "读取", "plan": "计划"}
        target_label = mode_labels.get(target_mode, target_mode)

        question_payload = {
            "header": "建议切换模式",
            "text": f"{reason}\n\n是否切换到「{target_label}」模式？",
            "options": [
                {"label": f"切换到{target_label}", "description": f"切换到{target_label}模式继续"},
                {"label": "保持当前模式", "description": "不切换，继续当前模式"},
            ],
            "multiSelect": False,
        }

        pending_q = e._question_flow.enqueue(
            question_payload=question_payload,
            tool_call_id=tool_call_id,
        )
        e._emit_user_question_event(
            question=pending_q,
            on_event=on_event,
            iteration=iteration,
        )

        # 阻塞等待用户回答（支持 question_resolver / InteractionRegistry）
        try:
            payload = await e.await_question_answer(pending_q)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            e._question_flow.pop_current()
            e._interaction_registry.cleanup_done()
            result_str = "用户未回答模式切换建议（超时/取消）。"
            log_tool_call(logger, tool_name, arguments, result=result_str)
            return _ToolExecOutcome(result_str=result_str, success=True)

        e._question_flow.pop_current()
        e._interaction_registry.cleanup_done()
        result_str = _json.dumps(payload, ensure_ascii=False) if isinstance(payload, dict) else str(payload)
        log_tool_call(logger, tool_name, arguments, result=result_str)
        return _ToolExecOutcome(result_str=result_str, success=True)


# ---------------------------------------------------------------------------
# 计划拦截处理器（PlanInterceptHandler）
# ---------------------------------------------------------------------------

class PlanInterceptHandler(BaseToolHandler):
    """拦截 task_create 进入 plan 模式。"""

    def can_handle(self, tool_name: str, **kwargs: Any) -> bool:
        if tool_name != "task_create":
            return False
        e = self._engine
        return bool(e._plan_intercept_task_create)

    async def handle(self, tool_name, tool_call_id, arguments, *, tool_scope=None, on_event=None, iteration=0, route_result=None):
        from excelmanus.engine_core.tool_dispatcher import _ToolExecOutcome

        result_str, _plan_id, plan_error = await self._engine.intercept_task_create_with_plan(
            arguments=arguments, route_result=route_result, tool_call_id=tool_call_id, on_event=on_event,
        )
        success = plan_error is None
        log_tool_call(logger, tool_name, arguments, result=result_str if success else None, error=plan_error if not success else None)
        return _ToolExecOutcome(
            result_str=result_str, success=success, error=plan_error,
            defer_tool_result=success,
        )


# ---------------------------------------------------------------------------
# 仅审计处理器（AuditOnlyHandler）
# ---------------------------------------------------------------------------

class AuditOnlyHandler(BaseToolHandler):
    """处理 audit-only 工具（低风险但需审计）。"""

    def can_handle(self, tool_name: str, **kwargs: Any) -> bool:
        return self._engine.approval.is_audit_only_tool(tool_name)

    async def handle(self, tool_name, tool_call_id, arguments, *, tool_scope=None, on_event=None, iteration=0, route_result=None):
        from excelmanus.engine_core.tool_dispatcher import _ToolExecOutcome

        e = self._engine
        result_value, audit_record = await e.execute_tool_with_audit(
            tool_name=tool_name, arguments=arguments, tool_scope=tool_scope,
            approval_id=e.approval.new_approval_id(), created_at_utc=e.approval.utc_now(),
            undoable=not e.approval.is_read_only_safe_tool(tool_name) and tool_name not in {"run_code", "run_shell"},
        )
        result_str = str(result_value)
        tool_def = getattr(e.registry, "get_tool", lambda _: None)(tool_name)
        if tool_def is not None:
            result_str = tool_def.truncate_result(result_str)
        log_tool_call(logger, tool_name, arguments, result=result_str)
        return _ToolExecOutcome(result_str=result_str, success=True, audit_record=audit_record)


# ---------------------------------------------------------------------------
# 高风险审批处理器（HighRiskApprovalHandler）
# ---------------------------------------------------------------------------

class HighRiskApprovalHandler(BaseToolHandler):
    """处理高风险工具（需审批或 fullaccess 直接执行）。"""

    def can_handle(self, tool_name: str, **kwargs: Any) -> bool:
        return self._engine.approval.is_high_risk_tool(tool_name)

    async def handle(self, tool_name, tool_call_id, arguments, *, tool_scope=None, on_event=None, iteration=0, route_result=None, skip_high_risk_approval_by_hook=False):
        from excelmanus.engine_core.tool_dispatcher import _ToolExecOutcome

        e = self._engine
        if not e.full_access_enabled and not skip_high_risk_approval_by_hook:
            pending = e.approval.create_pending(tool_name=tool_name, arguments=arguments, tool_scope=tool_scope)
            e.emit_pending_approval_event(pending=pending, on_event=on_event, iteration=iteration, tool_call_id=tool_call_id)
            result_str = e.format_pending_prompt(pending)
            log_tool_call(logger, tool_name, arguments, result=result_str)
            return _ToolExecOutcome(
                result_str=result_str, success=True,
                pending_approval=True, approval_id=pending.approval_id,
            )
        elif e.approval.is_mcp_tool(tool_name):
            probe_before, probe_before_partial = self._dispatcher._capture_unknown_write_probe(tool_name)
            result_value = await self._dispatcher.call_registry_tool(tool_name=tool_name, arguments=arguments, tool_scope=tool_scope)
            self._dispatcher._apply_unknown_write_probe(tool_name=tool_name, before_snapshot=probe_before, before_partial=probe_before_partial)
            result_str = str(result_value)
            log_tool_call(logger, tool_name, arguments, result=result_str)
            return _ToolExecOutcome(result_str=result_str, success=True)
        else:
            result_value, audit_record = await e.execute_tool_with_audit(
                tool_name=tool_name, arguments=arguments, tool_scope=tool_scope,
                approval_id=e.approval.new_approval_id(), created_at_utc=e.approval.utc_now(),
                undoable=not e.approval.is_read_only_safe_tool(tool_name) and tool_name not in {"run_code", "run_shell"},
            )
            result_str = str(result_value)
            tool_def = getattr(e.registry, "get_tool", lambda _: None)(tool_name)
            if tool_def is not None:
                result_str = tool_def.truncate_result(result_str)
            log_tool_call(logger, tool_name, arguments, result=result_str)
            return _ToolExecOutcome(result_str=result_str, success=True, audit_record=audit_record)


# ---------------------------------------------------------------------------
# 默认工具处理器（DefaultToolHandler）
# ---------------------------------------------------------------------------

class DefaultToolHandler(BaseToolHandler):
    """兜底：普通 registry 工具直接调用。"""

    def can_handle(self, tool_name: str, **kwargs: Any) -> bool:
        return True  # 兜底，总是匹配

    async def handle(self, tool_name, tool_call_id, arguments, *, tool_scope=None, on_event=None, iteration=0, route_result=None):
        from excelmanus.engine_core.tool_dispatcher import _ToolExecOutcome

        probe_before, probe_before_partial = self._dispatcher._capture_unknown_write_probe(tool_name)
        result_value = await self._dispatcher.call_registry_tool(tool_name=tool_name, arguments=arguments, tool_scope=tool_scope)
        self._dispatcher._apply_unknown_write_probe(tool_name=tool_name, before_snapshot=probe_before, before_partial=probe_before_partial)
        result_str = str(result_value)
        log_tool_call(logger, tool_name, arguments, result=result_str)
        return _ToolExecOutcome(result_str=result_str, success=True)


# ---------------------------------------------------------------------------
# 提取表结构处理器（ExtractTableSpecHandler）
# ---------------------------------------------------------------------------

_SUPPORTED_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}
_MAX_IMAGE_SIZE_BYTES = 20_000_000


class ExtractTableSpecHandler(BaseToolHandler):
    """处理 extract_table_spec 工具：VLM 结构化提取 → ReplicaSpec JSON。"""

    def can_handle(self, tool_name: str, **kwargs: Any) -> bool:
        return tool_name == "extract_table_spec"

    async def handle(
        self, tool_name, tool_call_id, arguments, *,
        tool_scope=None, on_event=None, iteration=0, route_result=None,
    ):
        import base64
        from pathlib import Path

        from excelmanus.engine_core.tool_dispatcher import _ToolExecOutcome

        file_path = arguments.get("file_path", "")
        output_path = arguments.get("output_path", "outputs/replica_spec.json")
        skip_style = arguments.get("skip_style", False)
        resume_from_spec = arguments.get("resume_from_spec")  # 断点续跑参数

        # ── 校验文件（基于 workspace_root 解析相对路径） ──
        from excelmanus.security import FileAccessGuard, SecurityViolationError
        workspace_root = self._engine.config.workspace_root
        guard = FileAccessGuard(workspace_root)
        try:
            path = guard.resolve_and_validate(file_path)
        except SecurityViolationError as exc:
            result_str = json.dumps(
                {"status": "error", "message": f"路径校验失败: {exc}"},
                ensure_ascii=False,
            )
            log_tool_call(logger, tool_name, arguments, error=result_str)
            return _ToolExecOutcome(result_str=result_str, success=False, error=result_str)
        if not path.is_file():
            result_str = json.dumps(
                {"status": "error", "message": f"文件不存在: {file_path}"},
                ensure_ascii=False,
            )
            log_tool_call(logger, tool_name, arguments, error=result_str)
            return _ToolExecOutcome(result_str=result_str, success=False, error=result_str)

        if path.suffix.lower() not in _SUPPORTED_IMAGE_EXTENSIONS:
            result_str = json.dumps(
                {"status": "error", "message": f"不支持的图片格式: {path.suffix}"},
                ensure_ascii=False,
            )
            log_tool_call(logger, tool_name, arguments, error=result_str)
            return _ToolExecOutcome(result_str=result_str, success=False, error=result_str)

        size = path.stat().st_size
        if size > _MAX_IMAGE_SIZE_BYTES:
            result_str = json.dumps(
                {"status": "error", "message": f"文件过大: {size} > {_MAX_IMAGE_SIZE_BYTES}"},
                ensure_ascii=False,
            )
            log_tool_call(logger, tool_name, arguments, error=result_str)
            return _ToolExecOutcome(result_str=result_str, success=False, error=result_str)

        # ── 读取图片 ──
        raw = path.read_bytes()
        b64 = base64.b64encode(raw).decode("ascii")
        ext = path.suffix.lower()
        mime_map = {
            ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".gif": "image/gif", ".bmp": "image/bmp", ".webp": "image/webp",
        }
        mime = mime_map.get(ext, "image/png")

        # ── 调用 VLM 提取 ──
        # 解析断点续跑参数
        resume_from_phase = None
        resume_spec_path = None
        if resume_from_spec:
            try:
                rp = guard.resolve_and_validate(resume_from_spec)
                if rp.is_file():
                    # 从文件名推断已完成的阶段号 (e.g. replica_spec_p2.json → phase 2)
                    stem = rp.stem
                    if "_p" in stem:
                        phase_str = stem.rsplit("_p", 1)[-1]
                        if phase_str.isdigit():
                            resume_from_phase = int(phase_str)
                            resume_spec_path = str(rp)
            except Exception:
                pass  # 解析失败则从头开始

        result_str = await self._dispatcher._run_vlm_extract_spec(
            image_b64=b64,
            mime=mime,
            file_path=str(path),
            output_path=output_path,
            skip_style=skip_style,
            resume_from_phase=resume_from_phase,
            resume_spec_path=resume_spec_path,
            on_event=on_event,
        )
        success = '"status": "ok"' in result_str or '"status": "paused"' in result_str
        error = None if success else result_str
        log_tool_call(logger, tool_name, arguments, result=result_str if success else None, error=error)
        if success:
            self._engine.record_write_action()
        return _ToolExecOutcome(result_str=result_str, success=success, error=error)


# ---------------------------------------------------------------------------
# 代码策略处理器（CodePolicyHandler）
# ---------------------------------------------------------------------------

class CodePolicyHandler(BaseToolHandler):
    """处理 run_code 工具（代码策略引擎路由）。"""

    def can_handle(self, tool_name: str, **kwargs: Any) -> bool:
        return tool_name == "run_code" and self._engine.config.code_policy_enabled

    async def handle(self, tool_name, tool_call_id, arguments, *, tool_scope=None, on_event=None, iteration=0, route_result=None):
        from excelmanus.engine_core.tool_dispatcher import _ToolExecOutcome
        from excelmanus.security.code_policy import CodePolicyEngine, CodeRiskTier, strip_exit_calls

        e = self._engine
        _code_arg = arguments.get("code") or ""
        _cp_engine = CodePolicyEngine(
            extra_safe_modules=e.config.code_policy_extra_safe_modules,
            extra_blocked_modules=e.config.code_policy_extra_blocked_modules,
        )
        _analysis = _cp_engine.analyze(_code_arg)
        _auto_green = _analysis.tier == CodeRiskTier.GREEN and e.config.code_policy_green_auto_approve
        _auto_yellow = _analysis.tier == CodeRiskTier.YELLOW and e.config.code_policy_yellow_auto_approve

        if _auto_green or _auto_yellow or e.full_access_enabled:
            return await self._execute_code_with_policy(
                code=_code_arg, arguments=arguments, analysis=_analysis,
                tool_name=tool_name, tool_call_id=tool_call_id, tool_scope=tool_scope,
                on_event=on_event, iteration=iteration,
            )

        # 风险等级为 RED 或配置不允许自动执行 → 尝试清洗降级
        _sanitized_code = strip_exit_calls(_code_arg) if _analysis.tier == CodeRiskTier.RED else None
        if _sanitized_code is not None:
            _re_analysis = _cp_engine.analyze(_sanitized_code)
            _re_auto_green = _re_analysis.tier == CodeRiskTier.GREEN and e.config.code_policy_green_auto_approve
            _re_auto_yellow = _re_analysis.tier == CodeRiskTier.YELLOW and e.config.code_policy_yellow_auto_approve
            if _re_auto_green or _re_auto_yellow:
                logger.info(
                    "run_code 自动清洗: %s → %s (移除退出调用)",
                    _analysis.tier.value, _re_analysis.tier.value,
                )
                _sanitized_args = {**arguments, "code": _sanitized_code}
                return await self._execute_code_with_policy(
                    code=_sanitized_code, arguments=_sanitized_args, analysis=_re_analysis,
                    tool_name=tool_name, tool_call_id=tool_call_id, tool_scope=tool_scope,
                    on_event=on_event, iteration=iteration, label_suffix="(清洗后)",
                )

        # 无法降级 → /accept 审批流程
        _caps_detail = ", ".join(sorted(_analysis.capabilities))
        _details_text = "; ".join(_analysis.details[:3])
        pending = e.approval.create_pending(tool_name=tool_name, arguments=arguments, tool_scope=tool_scope)
        result_str = (
            f"⚠️ 代码包含高风险操作，需要人工确认：\n"
            f"- 风险等级: {_analysis.tier.value}\n"
            f"- 检测到: {_caps_detail}\n"
            f"- 详情: {_details_text}\n"
            f"{e.format_pending_prompt(pending)}"
        )
        e.emit_pending_approval_event(
            pending=pending, on_event=on_event, iteration=iteration, tool_call_id=tool_call_id,
        )
        logger.info("run_code 策略引擎: tier=%s → pending approval %s", _analysis.tier.value, pending.approval_id)
        log_tool_call(logger, tool_name, arguments, result=result_str)
        return _ToolExecOutcome(
            result_str=result_str, success=True,
            pending_approval=True, approval_id=pending.approval_id,
        )

    async def _execute_code_with_policy(
        self,
        *,
        code: str,
        arguments: dict[str, Any],
        analysis: Any,
        tool_name: str,
        tool_call_id: str,
        tool_scope: Sequence[str] | None,
        on_event: Any,
        iteration: int,
        label_suffix: str = "",
    ) -> _ToolExecOutcome:
        """统一的代码策略执行路径（GREEN/YELLOW/降级后均走此方法）。

        消除原先 GREEN/YELLOW 路径与 RED→降级路径的 ~100 行重复代码。
        """
        import json as _json

        from excelmanus.engine_core.tool_dispatcher import _ToolExecOutcome
        from excelmanus.security.code_policy import extract_excel_targets

        e = self._engine
        dispatcher = self._dispatcher

        _sandbox_tier = analysis.tier.value
        _augmented_args = {**arguments, "sandbox_tier": _sandbox_tier}

        # ── run_code 前: 对可能被修改的 Excel 文件做快照 ──
        _excel_targets = [
            t.file_path for t in extract_excel_targets(code)
            if t.operation in ("write", "unknown")
        ]
        _before_snap = dispatcher._snapshot_excel_for_diff(
            _excel_targets, e.config.workspace_root,
        ) if _excel_targets else {}
        # uploads 目录快照，用于检测新建/变更文件
        _uploads_before = dispatcher._snapshot_uploads_dir(e.config.workspace_root)

        result_value, audit_record = await e.execute_tool_with_audit(
            tool_name=tool_name, arguments=_augmented_args, tool_scope=tool_scope,
            approval_id=e.approval.new_approval_id(), created_at_utc=e.approval.utc_now(),
            undoable=False,
        )
        result_str = str(result_value)
        tool_def = getattr(e.registry, "get_tool", lambda _: None)(tool_name)
        if tool_def is not None:
            result_str = tool_def.truncate_result(result_str)

        # ── 写入追踪 ──
        _rc_json: dict | None = None
        try:
            _rc_json = _json.loads(result_str)
            if not isinstance(_rc_json, dict):
                _rc_json = None
        except (_json.JSONDecodeError, TypeError):
            pass
        _has_cow = bool(_rc_json and _rc_json.get("cow_mapping"))
        _has_ast_write = any(t.operation == "write" for t in extract_excel_targets(code))
        if (audit_record is not None and audit_record.changes) or _has_cow or _has_ast_write:
            e.record_write_action()

        # ── window 感知桥接 ──
        _stdout_tail = ""
        if _rc_json is not None:
            _stdout_tail = _rc_json.get("stdout_tail", "")
        if audit_record is not None and e.window_perception is not None:
            e.window_perception.observe_code_execution(
                code=code,
                audit_changes=audit_record.changes if audit_record else None,
                stdout_tail=_stdout_tail,
                iteration=iteration,
            )

        # ── files_changed 事件 ──
        _uploads_after = dispatcher._snapshot_uploads_dir(e.config.workspace_root)
        _uploads_changed = dispatcher._diff_uploads_snapshots(_uploads_before, _uploads_after)
        dispatcher._emit_files_changed_from_audit(
            e, on_event, tool_call_id, code,
            audit_record.changes if audit_record else None,
            iteration,
            extra_changed_paths=_uploads_changed or None,
        )

        # ── Excel diff ──
        if _excel_targets and on_event is not None:
            try:
                _after_snap = dispatcher._snapshot_excel_for_diff(
                    _excel_targets, e.config.workspace_root,
                )
                _diffs = dispatcher._compute_snapshot_diffs(_before_snap, _after_snap)
                from excelmanus.events import EventType, ToolCallEvent
                for _rd in _diffs:
                    e.emit(
                        on_event,
                        ToolCallEvent(
                            event_type=EventType.EXCEL_DIFF,
                            tool_call_id=tool_call_id,
                            excel_file_path=_rd["file_path"],
                            excel_sheet=_rd["sheet"],
                            excel_affected_range=_rd["affected_range"],
                            excel_changes=_rd["changes"],
                        ),
                    )
            except Exception:
                logger.debug("run_code%s Excel diff 计算失败", label_suffix, exc_info=True)

        logger.info(
            "run_code 策略引擎: tier=%s%s auto_approved=True caps=%s",
            analysis.tier.value, label_suffix, sorted(analysis.capabilities),
        )
        log_tool_call(logger, tool_name, _augmented_args, result=result_str)
        return _ToolExecOutcome(result_str=result_str, success=True, audit_record=audit_record)
