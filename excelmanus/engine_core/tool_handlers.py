"""ToolHandler 策略实现 — 从 _dispatch_tool_execution if-elif 提取的独立处理器。

每个 Handler 负责一类工具的执行逻辑，通过 can_handle / handle 接口
与 ToolDispatcher 的策略表对接。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from excelmanus.engine_core.idle_tracker import idle_segment
from excelmanus.logger import get_logger, log_tool_call

if TYPE_CHECKING:
    from pathlib import Path

    from excelmanus.engine import AgentEngine
    from excelmanus.engine_core.tool_dispatcher import ToolDispatcher, _ToolExecOutcome

logger = get_logger("tool_handlers")


def _jev_denied_outcome(tool_name: str, arguments: dict[str, Any], reason: str) -> Any:
    """片 E applied deny：拒绝该调用，不 create_pending。"""
    from excelmanus.engine_core.error_payload import PRE_EXECUTE_DENIED
    from excelmanus.engine_core.tool_dispatcher import _ToolExecOutcome
    from excelmanus.engine_core.tool_result import error_result

    structured = error_result(
        f"工具调用被拒绝：{reason}",
        code=PRE_EXECUTE_DENIED,
        fields={"tool": tool_name},
    )
    log_tool_call(logger, tool_name, arguments, error=PRE_EXECUTE_DENIED)
    return _ToolExecOutcome(
        result_str=structured.model_text,
        success=False,
        error=PRE_EXECUTE_DENIED,
        structured=structured,
    )


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
    """处理 skill / activate_skill 工具调用。"""

    def can_handle(self, tool_name: str, **kwargs: Any) -> bool:
        return tool_name in {"skill", "activate_skill"}

    async def handle(self, tool_name, tool_call_id, arguments, **kwargs):
        from excelmanus.engine_core.tool_dispatcher import _ToolExecOutcome

        e = self._engine
        selected_name = arguments.get("name") or arguments.get("skill_name")
        if not isinstance(selected_name, str) or not selected_name.strip():
            result_str = "工具参数错误: name 必须为非空字符串。"
            log_tool_call(logger, tool_name, arguments, error=result_str)
            return _ToolExecOutcome(result_str=result_str, success=False, error=result_str)

        result_str = await e.handle_activate_skill(selected_name.strip())
        success = result_str.startswith("OK")
        error = None if success else result_str
        log_tool_call(logger, tool_name, arguments, result=result_str if success else None, error=error if not success else None)
        return _ToolExecOutcome(result_str=result_str, success=success, error=error)


# ---------------------------------------------------------------------------
# 技能管理处理器（SkillManagementHandler）
# ---------------------------------------------------------------------------

class SkillManagementHandler(BaseToolHandler):
    """处理 manage_skills 工具调用：安装/卸载/查看技能。"""

    def can_handle(self, tool_name: str, **kwargs: Any) -> bool:
        return tool_name == "manage_skills"

    async def handle(self, tool_name, tool_call_id, arguments, **kwargs):
        from excelmanus.engine_core.tool_dispatcher import _ToolExecOutcome

        action = str(arguments.get("action", "")).strip()
        dispatch = {
            "install": self._handle_install,
            "list": self._handle_list,
            "uninstall": self._handle_uninstall,
        }
        handler_fn = dispatch.get(action)
        if handler_fn is None:
            result_str = f"不支持的操作: {action}（支持 install/list/uninstall）"
            log_tool_call(logger, tool_name, arguments, error=result_str)
            return _ToolExecOutcome(result_str=result_str, success=False, error=result_str)

        return await handler_fn(arguments)

    @staticmethod
    def _resolve_install_source(value: str) -> str | None:
        lowered = value.lower()
        if lowered.startswith("http://") or lowered.startswith("https://"):
            return "github_url"
        if value.endswith((".md", ".MD")) or "/" in value or "\\" in value:
            return "local_path"
        return None

    # ── install ───────────────────────────────────────────

    async def _handle_install(self, arguments: dict[str, Any]):
        from excelmanus.engine_core.tool_dispatcher import _ToolExecOutcome

        slug = str(arguments.get("slug", "")).strip()
        if not slug:
            result_str = "参数错误: install 操作需要提供 slug 参数（GitHub URL 或本地 SKILL.md 路径）。"
            log_tool_call(logger, "manage_skills", arguments, error=result_str)
            return _ToolExecOutcome(result_str=result_str, success=False, error=result_str)

        source = self._resolve_install_source(slug)
        if source is None:
            result_str = "参数错误: install 需要 GitHub URL 或本地 SKILL.md 路径。"
            log_tool_call(logger, "manage_skills", arguments, error=result_str)
            return _ToolExecOutcome(result_str=result_str, success=False, error=result_str)

        e = self._engine
        manager = self._get_manager()
        if manager is None:
            return self._manager_unavailable(arguments)

        overwrite = bool(arguments.get("overwrite", False))

        try:
            result = await manager.import_skillpack_async(
                source=source, value=slug, actor="agent", overwrite=overwrite,
            )
        except Exception as exc:
            exc_str = str(exc)
            if "已存在" in exc_str or "conflict" in exc_str.lower():
                result_str = f"技能已存在: {exc_str}\n如需覆盖安装，请传入 overwrite=true。"
            else:
                result_str = f"安装失败: {exc_str}"
            log_tool_call(logger, "manage_skills", arguments, error=result_str)
            return _ToolExecOutcome(result_str=result_str, success=False, error=result_str)

        e._tools_cache = None

        name = result.get("name", slug)
        desc = result.get("description", "")
        version = result.get("version", "")
        parts = [f"OK 技能 '{name}' 安装成功。"]
        if version:
            parts[0] = f"OK 技能 '{name}' (v{version}) 安装成功。"
        if desc:
            parts.append(f"描述: {desc}")
        parts.append("现在可以通过 skill 加载此技能的完整说明。")
        result_str = "\n".join(parts)
        log_tool_call(logger, "manage_skills", arguments, result=result_str)
        return _ToolExecOutcome(result_str=result_str, success=True)

    # ── list ──────────────────────────────────────────────

    async def _handle_list(self, arguments: dict[str, Any]):
        from excelmanus.engine_core.tool_dispatcher import _ToolExecOutcome

        manager = self._get_manager()
        if manager is None:
            return self._manager_unavailable(arguments)

        skills = manager.list_skillpacks()
        if not skills:
            result_str = "当前没有已安装的技能。"
            log_tool_call(logger, "manage_skills", arguments, result=result_str)
            return _ToolExecOutcome(result_str=result_str, success=True)

        loaded_names: set[str] = set()
        try:
            loaded = self._engine._skill_resolver.get_loaded_skillpacks()
            if loaded:
                loaded_names = set(loaded.keys())
        except Exception:
            pass

        lines = [f"已安装 {len(skills)} 个技能：\n"]
        for s in skills:
            name = s.get("name", "")
            desc = s.get("description", "")
            version = s.get("version", "")
            line = f"  - {name}"
            if version:
                line += f" (v{version})"
            if name in loaded_names:
                line += " [已加载]"
            if desc:
                line += f" — {desc}"
            lines.append(line)
        result_str = "\n".join(lines)
        log_tool_call(logger, "manage_skills", arguments, result=result_str)
        return _ToolExecOutcome(result_str=result_str, success=True)

    # ── uninstall ─────────────────────────────────────────

    async def _handle_uninstall(self, arguments: dict[str, Any]):
        from excelmanus.engine_core.tool_dispatcher import _ToolExecOutcome

        slug = str(arguments.get("slug", "")).strip()
        if not slug:
            result_str = "参数错误: uninstall 操作需要提供 slug 参数（技能名称）。"
            log_tool_call(logger, "manage_skills", arguments, error=result_str)
            return _ToolExecOutcome(result_str=result_str, success=False, error=result_str)

        e = self._engine
        manager = self._get_manager()
        if manager is None:
            return self._manager_unavailable(arguments)

        try:
            result = manager.delete_skillpack(name=slug, actor="agent")
        except Exception as exc:
            result_str = f"卸载失败: {exc}"
            log_tool_call(logger, "manage_skills", arguments, error=result_str)
            return _ToolExecOutcome(result_str=result_str, success=False, error=result_str)

        # 卸载成功 → 清理运行时状态
        e._tools_cache = None
        # 移除已激活的该技能（防止过期引用）
        deleted_name = result.get("name", slug)
        if hasattr(e, "_active_skills"):
            before = len(e._active_skills)
            e._active_skills = [
                s for s in e._active_skills if s.name != deleted_name
            ]
            if len(e._active_skills) != before:
                from excelmanus.request.series import series_of

                series_of(e).note("catalog/change")
        if hasattr(e, "_loaded_skill_names"):
            e._loaded_skill_names.pop(deleted_name, None)

        result_str = f"OK 技能 '{deleted_name}' 已卸载。"
        log_tool_call(logger, "manage_skills", arguments, result=result_str)
        return _ToolExecOutcome(result_str=result_str, success=True)

    # ── helpers ───────────────────────────────────────────

    def _get_manager(self):
        """获取 SkillpackManager，不可用时返回 None。"""
        try:
            return self._engine._require_skillpack_manager()
        except RuntimeError:
            return None

    def _manager_unavailable(self, arguments: dict[str, Any]):
        from excelmanus.engine_core.tool_dispatcher import _ToolExecOutcome

        result_str = "技能管理器不可用。请检查技能系统是否已正确配置。"
        log_tool_call(logger, "manage_skills", arguments, error=result_str)
        return _ToolExecOutcome(result_str=result_str, success=False, error=result_str)


# ---------------------------------------------------------------------------
# 委托处理器（DelegationHandler）
# ---------------------------------------------------------------------------

class DelegationHandler(BaseToolHandler):
    """处理 delegate / delegate_to_subagent（兼容） / list_subagents / parallel_delegate（兼容）。"""

    def can_handle(self, tool_name: str, **kwargs: Any) -> bool:
        return tool_name in ("delegate", "delegate_to_subagent", "list_subagents", "parallel_delegate")

    async def handle(self, tool_name, tool_call_id, arguments, *, tool_scope=None, on_event=None, iteration=0, route_result=None):

        if tool_name == "list_subagents":
            return self._handle_list(arguments)

        if arguments.get("action", "start") != "start" or arguments.get("background"):
            return await self._handle_background(arguments, on_event=on_event)

        # delegate / delegate_to_subagent / parallel_delegate 统一处理
        # 判断是并行还是单任务模式
        tasks_value = arguments.get("tasks")
        if tool_name == "parallel_delegate" or (isinstance(tasks_value, list) and len(tasks_value) >= 2):
            return await self._handle_parallel(arguments, on_event=on_event)
        else:
            return await self._handle_delegate(tool_call_id, arguments, on_event=on_event, iteration=iteration)

    async def _handle_background(self, arguments, *, on_event):
        from excelmanus.engine_core.tool_dispatcher import _ToolExecOutcome
        from excelmanus.engine_core.tool_result import from_payload, ok_result
        from excelmanus.engine_core.error_payload import payload_from_subagent_error
        from excelmanus.subagent.errors import SubagentError
        from excelmanus.subagent.models import SubagentStartRequest

        runtime = self._engine._subagent_runtime
        action = arguments.get("action", "start")
        run_id = arguments.get("run_id", "")
        try:
            if action == "start":
                if arguments.get("tasks"):
                    raise SubagentError("INVALID_ARGS", "后台启动每次传一个 task；tasks 是同步并行入口。")
                task = arguments.get("task", "")
                brief = arguments.get("task_brief")
                if isinstance(brief, dict) and brief.get("title"):
                    task = self._engine.render_task_brief(brief)
                run_id = await runtime.start_background(SubagentStartRequest(
                    task=task, agent_name=arguments.get("agent_name"),
                    file_paths=arguments.get("file_paths") or [], on_event=on_event,
                ))
            elif action == "list":
                payload = {"status": "success", "runs": runtime.list_runs()}
            elif action == "wait":
                row = await runtime.wait(run_id, timeout=float(arguments.get("wait_seconds", 30)))
                payload = {"status": "success", "run": row}
            elif action == "send":
                await runtime.send_message(run_id, arguments.get("message", ""))
            elif action in {"cancel", "pause"}:
                await runtime.interrupt(run_id, pause=action == "pause")
            elif action == "resume":
                run_id = await runtime.resume(run_id, arguments.get("message", ""), on_event=on_event)
            elif action != "status":
                raise SubagentError("INVALID_ARGS", f"不支持的子任务操作: {action}")
            if action not in {"list", "wait"}:
                payload = {"status": "success", "run": runtime.get_run(run_id)}
            structured = ok_result(payload)
        except SubagentError as exc:
            structured = from_payload(payload_from_subagent_error(exc))
        return _ToolExecOutcome(
            result_str=structured.model_text, success=structured.success,
            error=structured.error.code if structured.error else None, structured=structured,
        )

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

        from excelmanus.subagent.result import format_parent_reply

        with idle_segment(e, "delegate"):
            sub_result = await e.delegate_to_subagent(
                task=task_value.strip(),
                agent_name=agent_name_value.strip() if isinstance(agent_name_value, str) else None,
                file_paths=raw_file_paths,
                on_event=on_event,
            )
        result_str = format_parent_reply(sub_result)
        success = sub_result.success
        error = None if success else result_str
        if success and sub_result.structured_changes:
            e.record_workspace_write_action()

        log_tool_call(logger, "delegate", arguments, result=result_str if success else None, error=error if not success else None)
        return _ToolExecOutcome(
            result_str=result_str, success=success, error=error,
            structured=self._text_result(result_str, success),
        )

    @staticmethod
    def _text_result(text: str, success: bool):
        from excelmanus.engine_core.tool_result import ToolResult

        return ToolResult.from_text(text, success=success)

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
            with idle_segment(e, "delegate"):
                pd_outcome = await e.parallel_delegate_to_subagents(tasks=raw_tasks, on_event=on_event)
            result_str = pd_outcome.reply
            success = pd_outcome.success
            error = None if success else result_str

            for sub_result in pd_outcome.results:
                if sub_result.success and sub_result.structured_changes:
                    e.record_workspace_write_action()
        except Exception as exc:
            result_str = f"parallel_delegate 执行异常: {exc}"
            success = False
            error = str(exc)

        log_tool_call(logger, "delegate", arguments, result=result_str if success else None, error=error if not success else None)
        return _ToolExecOutcome(
            result_str=result_str, success=success, error=error,
            structured=self._text_result(result_str, success),
        )


# ---------------------------------------------------------------------------
# 询问用户处理器（AskUserHandler）
# ---------------------------------------------------------------------------

class ShowWorkbookHandler(BaseToolHandler):
    """Present a validated, version-bound range without changing workbook cells."""

    def can_handle(self, tool_name: str, **kwargs: Any) -> bool:
        return tool_name == "show_workbook"

    async def handle(self, tool_name, tool_call_id, arguments, *, tool_scope=None, on_event=None, iteration=0, route_result=None):
        from excelmanus.workbook.interaction import bind_target
        from excelmanus.engine_core.tool_dispatcher import _ToolExecOutcome
        from excelmanus.engine_core.tool_result import ToolResult

        stage = arguments.get("stage")
        if stage not in {"inspect", "planned", "changed"}:
            raise ValueError("stage 必须为 inspect/planned/changed")
        raw = arguments.get("target")
        if stage == "changed" and (not isinstance(raw, dict) or not raw.get("content_version")):
            raise ValueError("展示已修改区域需要写入回执中的 content_version")
        target = await asyncio.to_thread(bind_target, self._engine, raw, require_ranges=True)
        value = {"kind": "workbook_presentation", "target": target, "stage": stage,
                 "summary": str(arguments.get("summary") or "")[:500], "scope_source": "agent"}
        result = ToolResult(success=True, value=value, model_text=json.dumps(value, ensure_ascii=False))
        return _ToolExecOutcome(result_str=result.model_text, success=True, structured=result)


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
        from excelmanus.engine_core.tool_result import coerce_legacy_result, error_result

        structured = coerce_legacy_result(result_str)
        # Completed questions have JSON answers. Timeout text is not an answer.
        if not isinstance(structured.value, (dict, list)):
            structured = error_result(result_str, code="CANCELLED")
        log_tool_call(logger, tool_name, arguments, result=result_str)
        return _ToolExecOutcome(
            result_str=structured.model_text, success=structured.success, structured=structured,
            error=structured.error.code if structured.error else None,
            pending_question=False, question_id=None, defer_tool_result=False,
        )


# ---------------------------------------------------------------------------
# 仅审计处理器（AuditOnlyHandler）
# ---------------------------------------------------------------------------

class AuditOnlyHandler(BaseToolHandler):
    """处理 audit-only 工具（低风险但需审计）。"""

    def can_handle(self, tool_name: str, **kwargs: Any) -> bool:
        arguments = kwargs.get("arguments")
        args = arguments if isinstance(arguments, dict) else None
        approval = self._engine.approval
        # A capability may be both auditable and confirmation-gated (skill
        # install/uninstall). Let HighRiskApprovalHandler own the gate.
        if approval.is_confirm_required_tool(tool_name):
            return False
        return approval.is_audit_only_tool(tool_name, args)

    async def handle(self, tool_name, tool_call_id, arguments, *, tool_scope=None, on_event=None, iteration=0, route_result=None):
        from excelmanus.engine_core.tool_dispatcher import _ToolExecOutcome

        e = self._engine
        result_value, audit_record = await e.execute_tool_with_audit(
            tool_name=tool_name, arguments=arguments, tool_scope=tool_scope,
            approval_id=e.approval.new_approval_id(), created_at_utc=e.approval.utc_now(),
            undoable=e.approval.is_undoable_tool(tool_name, arguments),
        )
        structured = self._dispatcher._coerce_tool_result(result_value)
        result_str = structured.model_text
        raw_result_str = result_str
        tool_def = getattr(e.registry, "get_tool", lambda _: None)(tool_name)
        if tool_def is not None:
            result_str = tool_def.truncate_result(result_str)
            structured = structured.with_model_text(result_str)
        log_tool_call(logger, tool_name, arguments, result=result_str)
        return _ToolExecOutcome(
            result_str=result_str,
            success=structured.success,
            error=structured.error.message if structured.error else None,
            audit_record=audit_record,
            raw_result_str=raw_result_str,
            structured=structured,
        )


# ---------------------------------------------------------------------------
# 高风险审批处理器（HighRiskApprovalHandler）
# ---------------------------------------------------------------------------

class HighRiskApprovalHandler(BaseToolHandler):
    """高风险工具审批。ask 才弹确认；never 自动过（与 full_access 对齐）。

    无人应答不能放行：ask 超时在循环里 reject。MCP 默认不是高风险。
    """

    def can_handle(self, tool_name: str, **kwargs: Any) -> bool:
        arguments = kwargs.get("arguments")
        return self._engine.approval.is_high_risk_tool(tool_name)

    async def handle(self, tool_name, tool_call_id, arguments, *, tool_scope=None, on_event=None, iteration=0, route_result=None, skip_high_risk_approval_by_hook=False):
        from excelmanus.engine_core.tool_dispatcher import _ToolExecOutcome

        e = self._engine
        from excelmanus.security.policy import resolve_approval_policy

        approval = resolve_approval_policy(e)
        if approval == "ask" and not skip_high_risk_approval_by_hook:
            from excelmanus.system_one.host import approval_gate_action, maybe_jev_approval

            decision = await maybe_jev_approval(e, tool_name=tool_name, arguments=arguments)
            action = approval_gate_action(decision)
            if action in {"deny", "auto"}:
                from excelmanus.system_one.trace import record_host_effect

                record_host_effect(
                    e, "approval.tool_call", action=action, changed=True,
                    impact="审批路径已改为拒绝调用" if action == "deny" else "审批路径已改为自动放行，执行结果另行记录",
                    on_event=on_event,
                )
            if action == "deny":
                return _jev_denied_outcome(
                    tool_name, arguments, decision.reason if decision else "deny",
                )
            if action != "auto":
                pending = e.approval.create_pending(tool_name=tool_name, arguments=arguments, tool_scope=tool_scope)
                persist_runtime = getattr(getattr(e, "_driver", None), "_persist_runtime_state", None)
                if callable(persist_runtime):
                    persist_runtime()
                e.emit_pending_approval_event(pending=pending, on_event=on_event, iteration=iteration, tool_call_id=tool_call_id)
                result_str = e.format_pending_prompt(pending)
                log_tool_call(logger, tool_name, arguments, result=result_str)
                return _ToolExecOutcome(
                    result_str=result_str, success=True,
                    pending_approval=True, approval_id=pending.approval_id,
                )
            # applied auto：仍走下面的 registry + 审计，不 create_pending。
        if approval == "never" and e.approval.is_mcp_tool(tool_name):
            probe_before, probe_before_partial = self._dispatcher._capture_unknown_write_probe(tool_name)
            structured = await self._dispatcher.call_registry_tool(
                tool_name=tool_name, arguments=arguments, tool_scope=tool_scope,
            )
            self._dispatcher._apply_unknown_write_probe(tool_name=tool_name, before_snapshot=probe_before, before_partial=probe_before_partial)
            result_str = structured.model_text
            raw_result_str = getattr(self._dispatcher, '_last_call_raw_result', result_str)
            log_tool_call(logger, tool_name, arguments, result=result_str)
            return _ToolExecOutcome(
                result_str=result_str,
                success=structured.success,
                error=structured.error.message if structured.error else None,
                raw_result_str=raw_result_str,
                structured=structured,
            )
        result_value, audit_record = await e.execute_tool_with_audit(
            tool_name=tool_name, arguments=arguments, tool_scope=tool_scope,
            approval_id=e.approval.new_approval_id(), created_at_utc=e.approval.utc_now(),
            undoable=e.approval.is_undoable_tool(tool_name, arguments),
        )
        structured = self._dispatcher._coerce_tool_result(result_value)
        result_str = structured.model_text
        raw_result_str = getattr(self._dispatcher, "_last_call_raw_result", result_str)
        tool_def = getattr(e.registry, "get_tool", lambda _: None)(tool_name)
        if tool_def is not None:
            result_str = tool_def.truncate_result(result_str)
            structured = structured.with_model_text(result_str)
        log_tool_call(logger, tool_name, arguments, result=result_str)
        return _ToolExecOutcome(
            result_str=result_str,
            success=structured.success,
            error=structured.error.message if structured.error else None,
            audit_record=audit_record,
            raw_result_str=raw_result_str,
            structured=structured,
        )


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
        structured = await self._dispatcher.call_registry_tool(
            tool_name=tool_name, arguments=arguments, tool_scope=tool_scope,
        )
        self._dispatcher._apply_unknown_write_probe(tool_name=tool_name, before_snapshot=probe_before, before_partial=probe_before_partial)
        result_str = structured.model_text
        raw_result_str = getattr(self._dispatcher, '_last_call_raw_result', result_str)
        log_tool_call(logger, tool_name, arguments, result=result_str)
        return _ToolExecOutcome(
            result_str=result_str,
            success=structured.success,
            error=structured.error.message if structured.error else None,
            raw_result_str=raw_result_str,
            structured=structured,
        )



# ---------------------------------------------------------------------------
# 代码策略处理器（CodePolicyHandler）
# ---------------------------------------------------------------------------

class CodePolicyHandler(BaseToolHandler):
    """处理 run_code 工具（代码策略引擎路由）。"""

    def can_handle(self, tool_name: str, **kwargs: Any) -> bool:
        return tool_name == "run_code" and self._engine.config.code_policy_enabled

    async def handle(self, tool_name, tool_call_id, arguments, *, tool_scope=None, on_event=None, iteration=0, route_result=None):
        from excelmanus.engine_core.tool_dispatcher import _ToolExecOutcome
        from excelmanus.security.code_policy import (
            CodePolicyEngine,
            CodeRiskTier,
            allows_auto_run,
            strip_exit_calls,
        )

        e = self._engine
        # 模型可能回显结果里的 host 注入字段，真实等级以 analysis 为准。
        arguments.pop("sandbox_tier", None)
        _code_arg = arguments.get("code") or ""
        _cp_engine = CodePolicyEngine(
            extra_safe_modules=e.config.code_policy_extra_safe_modules,
            extra_blocked_modules=e.config.code_policy_extra_blocked_modules,
        )
        _analysis = _cp_engine.analyze(_code_arg)
        _auto = allows_auto_run(
            _analysis,
            green_auto=e.config.code_policy_green_auto_approve,
            yellow_auto=e.config.code_policy_yellow_auto_approve,
        )

        from excelmanus.security.policy import resolve_approval_policy

        if _auto or resolve_approval_policy(e) == "never":
            return await self._execute_code_with_policy(
                code=_code_arg, arguments=arguments, analysis=_analysis,
                tool_name=tool_name, tool_call_id=tool_call_id, tool_scope=tool_scope,
                on_event=on_event, iteration=iteration,
            )

        # 风险等级为 RED 或配置不允许自动执行 → 尝试清洗降级
        _sanitized_code = strip_exit_calls(_code_arg) if _analysis.tier == CodeRiskTier.RED else None
        if _sanitized_code is not None:
            _re_analysis = _cp_engine.analyze(_sanitized_code)
            if allows_auto_run(
                _re_analysis,
                green_auto=e.config.code_policy_green_auto_approve,
                yellow_auto=e.config.code_policy_yellow_auto_approve,
            ):
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

        # 无法降级 → /accept 审批流程；sandbox_tier 落进 pending 参数，
        # 保证获批后的重放与非审批路径用同一沙箱档。
        from excelmanus.system_one.host import approval_gate_action, maybe_jev_approval

        decision = await maybe_jev_approval(
            e,
            tool_name=tool_name,
            arguments=arguments,
            code_tier=getattr(_analysis.tier, "value", None),
        )
        action = approval_gate_action(decision)
        if action in {"deny", "auto"}:
            from excelmanus.system_one.trace import record_host_effect

            record_host_effect(
                e, "approval.tool_call", action=action, changed=True,
                impact="审批路径已改为拒绝调用" if action == "deny" else "审批路径已改为自动放行，执行结果另行记录",
                on_event=on_event,
            )
        if action == "deny":
            return _jev_denied_outcome(
                tool_name, arguments, decision.reason if decision else "deny",
            )
        if action == "auto":
            return await self._execute_code_with_policy(
                code=_code_arg, arguments=arguments, analysis=_analysis,
                tool_name=tool_name, tool_call_id=tool_call_id, tool_scope=tool_scope,
                on_event=on_event, iteration=iteration,
            )
        _caps_detail = ", ".join(sorted(_analysis.capabilities))
        _details_text = "; ".join(_analysis.details[:3])
        pending = e.approval.create_pending(
            tool_name=tool_name,
            arguments={**arguments, "sandbox_tier": _analysis.tier.value},
            tool_scope=tool_scope,
        )
        persist_runtime = getattr(getattr(e, "_driver", None), "_persist_runtime_state", None)
        if callable(persist_runtime):
            persist_runtime()
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
        from excelmanus.engine_core.tool_dispatcher import _ToolExecOutcome

        e = self._engine
        dispatcher = self._dispatcher

        _sandbox_tier = (
            "RED"
            if bool(getattr(e, "_full_access_enabled", False))
            else analysis.tier.value
        )
        _augmented_args = {**arguments, "sandbox_tier": _sandbox_tier}

        # uploads 目录快照，用于检测新建/变更文件
        _uploads_before = dispatcher._snapshot_uploads_dir(e.config.workspace_root)

        result_value, audit_record = await e.execute_tool_with_audit(
            tool_name=tool_name, arguments=_augmented_args, tool_scope=tool_scope,
            approval_id=e.approval.new_approval_id(), created_at_utc=e.approval.utc_now(),
            undoable=False,
        )
        from excelmanus.engine_core.tool_result import coerce_legacy_result

        structured = coerce_legacy_result(result_value)
        result_str = structured.model_text
        raw_result_str = result_str
        tool_def = getattr(e.registry, "get_tool", lambda _: None)(tool_name)
        if tool_def is not None:
            result_str = tool_def.truncate_result(result_str)
            structured = structured.with_model_text(result_str)

        # ── 写入追踪 ──
        _published_paths = ""
        if isinstance(structured.value, dict):
            _published_items = structured.value.get("published") or []
            if isinstance(_published_items, list):
                _published_paths = ", ".join(
                    str(item.get("path"))
                    for item in _published_items
                    if isinstance(item, dict)
                    and item.get("status") == "committed"
                    and item.get("path")
                )
        _has_published = bool(_published_paths)
        if (audit_record is not None and audit_record.changes) or _has_published:
            e.record_write_action()
            _state = getattr(e, "_state", None)
            if _state is not None:
                _observed_paths = ", ".join(
                    str(change.get("file") or "") for change in (getattr(audit_record, "changes", None) or [])
                    if isinstance(change, dict) and change.get("file")
                )
                _file_path = _published_paths or _observed_paths
                _state.record_write_operation(
                    tool_name="run_code",
                    file_path=_file_path,
                    summary=dispatcher._extract_run_code_write_summary(result_str),
                )

        # ── files_changed 事件 ──
        _uploads_after = dispatcher._snapshot_uploads_dir(e.config.workspace_root)
        _uploads_changed = dispatcher._diff_uploads_snapshots(_uploads_before, _uploads_after)
        _extra_changed = list(_uploads_changed or [])
        if isinstance(structured.value, dict):
            for _item in structured.value.get("published") or []:
                if (
                    isinstance(_item, dict)
                    and _item.get("status") == "committed"
                    and _item.get("path")
                ):
                    _extra_changed.append(str(_item["path"]))
        dispatcher._record_files_from_run_code(e, extra_changed_paths=_extra_changed or None)

        logger.info(
            "run_code 策略引擎: tier=%s%s auto_approved=True caps=%s",
            analysis.tier.value, label_suffix, sorted(analysis.capabilities),
        )
        log_tool_call(logger, tool_name, _augmented_args, result=result_str)
        return _ToolExecOutcome(
            result_str=result_str,
            raw_result_str=raw_result_str,
            success=structured.success,
            error=structured.error.message if structured.error else None,
            audit_record=audit_record,
            structured=structured,
        )
