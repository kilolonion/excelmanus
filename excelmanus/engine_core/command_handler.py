"""CommandHandler — 从 AgentEngine 解耦的控制命令处理组件。

负责管理：
- /fullaccess, /subagent, /accept, /reject, /undo, /plan, /model, /backup, /compact 命令
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any

from excelmanus.control_commands import (
    NORMALIZED_ALIAS_TO_CANONICAL_CONTROL_COMMAND,
    normalize_control_command,
)
from excelmanus.logger import get_logger
from excelmanus.tools.registry import ToolNotAllowedError

# Lazy imports used at runtime to avoid circular dependencies:
# - SkillMatchResult (from excelmanus.skillpacks.router)
# - EventType, ToolCallEvent (from excelmanus.events)
# - BackupManager (from excelmanus.backup)

if TYPE_CHECKING:
    from excelmanus.engine import AgentEngine
    from excelmanus.events import EventCallback

logger = get_logger("command_handler")


class CommandHandler:
    """控制命令处理器，从 AgentEngine 搬迁所有 /command 处理逻辑。"""

    def __init__(self, engine: "AgentEngine") -> None:
        self._engine = engine

    async def handle(
        self,
        user_message: str,
        *,
        on_event: EventCallback | None = None,
    ) -> str | None:
        """处理会话级控制命令。命中时返回回复文本，否则返回 None。"""
        e = self._engine
        text = user_message.strip()
        if not text or not text.startswith("/"):
            return None

        parts = text.split()
        raw_command = parts[0].strip().lower()
        if raw_command in {"/planmode", "/plan_mode"}:
            return "命令已移除，请使用 /plan ..."

        normalized_command = normalize_control_command(raw_command)
        command = NORMALIZED_ALIAS_TO_CANONICAL_CONTROL_COMMAND.get(normalized_command)
        if command is None:
            return None

        from excelmanus.skillpacks.router import SkillMatchResult
        e._last_route_result = SkillMatchResult(
            skills_used=[],
            route_mode="control_command",
            system_contexts=[],
        )

        action = parts[1].strip().lower() if len(parts) >= 2 else ""
        too_many_args = len(parts) > 2

        if command == "/fullaccess":
            if (action in {"on", ""}) and not too_many_args:
                e._full_access_enabled = True
                return "已开启 fullaccess。当前代码技能权限：full_access。"
            if action == "off" and not too_many_args:
                e._full_access_enabled = False
                # 将受限 skill 从 _active_skills 中驱逐，避免下一轮 scope 泄漏
                blocked = set(e._restricted_code_skillpacks)
                e._active_skills = [
                    s for s in e._active_skills if s.name not in blocked
                ]
                return "已关闭 fullaccess。当前代码技能权限：restricted。"
            if action == "status" and not too_many_args:
                status = "full_access" if e._full_access_enabled else "restricted"
                return f"当前代码技能权限：{status}。"
            return "无效参数。用法：/fullaccess [on|off|status]。"

        if command == "/subagent":
            # /subagent 默认行为为查询状态，避免误触启停
            if action in {"status", ""} and len(parts) <= 2:
                status = "enabled" if e._subagent_enabled else "disabled"
                return f"当前 subagent 状态：{status}。"
            if action == "on" and len(parts) == 2:
                e._subagent_enabled = True
                return "已开启 subagent。"
            if action == "off" and len(parts) == 2:
                e._subagent_enabled = False
                return "已关闭 subagent。"
            if action == "list" and len(parts) == 2:
                return e._handle_list_subagents()
            if action == "run":
                agent_name, task, parse_error = self._parse_subagent_run_command(text)
                if parse_error is not None:
                    return parse_error
                assert task is not None
                outcome = await e._delegate_to_subagent(
                    task=task,
                    agent_name=agent_name,
                    on_event=on_event,
                )
                if (
                    not outcome.success
                    and outcome.subagent_result is not None
                    and outcome.subagent_result.pending_approval_id is not None
                ):
                    pending = e._approval.pending
                    approval_id_value = outcome.subagent_result.pending_approval_id
                    high_risk_tool = (
                        pending.tool_name
                        if pending is not None and pending.approval_id == approval_id_value
                        else "高风险工具"
                    )
                    question = e._enqueue_subagent_approval_question(
                        approval_id=approval_id_value,
                        tool_name=high_risk_tool,
                        picked_agent=outcome.picked_agent or "subagent",
                        task_text=outcome.task_text or task,
                        normalized_paths=outcome.normalized_paths,
                        tool_call_id=f"subagent_run_{int(time.time() * 1000)}",
                        on_event=on_event,
                        iteration=0,
                    )
                    return e._question_flow.format_prompt(question)
                return outcome.reply
            return (
                "无效参数。用法：/subagent [on|off|status|list]，"
                "或 /subagent run -- <task>，"
                "或 /subagent run <agent_name> -- <task>。"
            )

        if command == "/plan":
            return await self._handle_plan_command(parts, on_event=on_event)

        if command == "/model":
            # /model → 显示当前模型
            # /model list → 列出所有可用模型
            # /model <name> → 切换模型
            if not action:
                name_display = e._active_model_name or "default"
                return f"当前模型：{name_display}（{e._active_model}）"
            if action == "list":
                rows = e.list_models()
                lines = ["可用模型："]
                for row in rows:
                    marker = " ✦" if row["active"] else ""
                    desc = f"  {row['description']}" if row["description"] else ""
                    lines.append(f"  {row['name']} → {row['model']}{desc}{marker}")
                return "\n".join(lines)
            # 其余视为模型名称，尝试切换
            model_arg = " ".join(parts[1:])
            return e.switch_model(model_arg)

        if command == "/backup":
            return self._handle_backup_command(parts)

        if command == "/compact":
            return await self._handle_compact_command(parts)

        if command == "/accept":
            return await self._handle_accept_command(parts, on_event=on_event)
        if command == "/reject":
            return self._handle_reject_command(parts)
        return self._handle_undo_command(parts)

    async def _handle_compact_command(self, parts: list[str]) -> str:
        """处理 /compact 会话控制命令。

        用法：
        - /compact          — 立即执行压缩
        - /compact status   — 显示 compaction 统计和当前 token 使用率
        - /compact on       — 启用自动压缩
        - /compact off      — 禁用自动压缩
        - /compact <指令>   — 带自定义指令执行压缩
        """
        e = self._engine
        action = parts[1].strip().lower() if len(parts) >= 2 else ""

        compaction_mgr = getattr(e, "_compaction_manager", None)
        if compaction_mgr is None:
            return "Compaction 功能未初始化。"

        # /compact status
        if action == "status":
            sys_msgs = e._memory._build_system_messages()
            status = compaction_mgr.get_status(e._memory, sys_msgs)
            pct = status["usage_ratio"] * 100
            threshold_pct = status["threshold_ratio"] * 100
            lines = [
                f"**上下文压缩状态**",
                f"- 自动压缩: {'启用' if status['enabled'] else '禁用'}",
                f"- 当前 token 使用: {status['current_tokens']:,} / {status['max_tokens']:,} ({pct:.1f}%)",
                f"- 自动压缩阈值: {threshold_pct:.0f}%",
                f"- 对话消息数: {status['message_count']}",
                f"- 累计压缩次数: {status['compaction_count']}",
            ]
            if status["last_compaction_at"]:
                import time as _time
                elapsed = _time.time() - status["last_compaction_at"]
                if elapsed < 60:
                    lines.append(f"- 上次压缩: {elapsed:.0f} 秒前")
                else:
                    lines.append(f"- 上次压缩: {elapsed / 60:.1f} 分钟前")
            return "\n".join(lines)

        # /compact on
        if action == "on" and len(parts) == 2:
            compaction_mgr.enabled = True
            return "已启用自动上下文压缩。"

        # /compact off
        if action == "off" and len(parts) == 2:
            compaction_mgr.enabled = False
            return "已禁用自动上下文压缩。"

        # /compact 或 /compact <自定义指令> — 执行压缩
        custom_instruction = None
        if action and action not in {"on", "off", "status"}:
            custom_instruction = " ".join(parts[1:])

        # 确定摘要模型
        summary_model = e._config.aux_model or e._active_model
        sys_msgs = e._memory._build_system_messages()

        result = await compaction_mgr.manual_compact(
            memory=e._memory,
            system_msgs=sys_msgs,
            client=e._client,
            summary_model=summary_model,
            custom_instruction=custom_instruction,
        )

        if not result.success:
            return f"压缩未执行: {result.error}"

        pct_before = (
            result.tokens_before / e._config.max_context_tokens * 100
            if e._config.max_context_tokens > 0
            else 0
        )
        pct_after = (
            result.tokens_after / e._config.max_context_tokens * 100
            if e._config.max_context_tokens > 0
            else 0
        )
        return (
            f"✅ 上下文压缩完成。\n"
            f"- 消息: {result.messages_before} → {result.messages_after}\n"
            f"- Token: {result.tokens_before:,} ({pct_before:.1f}%) → "
            f"{result.tokens_after:,} ({pct_after:.1f}%)\n"
            f"- 累计压缩次数: {compaction_mgr.stats.compaction_count}"
        )

    def _handle_backup_command(self, parts: list[str]) -> str:
        """处理 /backup 会话控制命令。"""
        e = self._engine
        action = parts[1].strip().lower() if len(parts) >= 2 else ""
        too_many_args = len(parts) > 3

        if action in {"status", ""} and not too_many_args:
            if not e._backup_enabled:
                return "备份沙盒模式：已关闭。"
            mgr = e._backup_manager
            count = len(mgr.list_backups()) if mgr else 0
            scope = mgr.scope if mgr else "all"
            return (
                f"备份沙盒模式：已启用（scope={scope}）。\n"
                f"当前管理 {count} 个备份文件。\n"
                f"备份目录：{mgr.backup_dir if mgr else 'N/A'}"
            )

        if action == "on" and not too_many_args:
            scope = "all"
            if len(parts) == 3 and parts[2].strip().lower() == "--excel-only":
                scope = "excel_only"
            e._backup_enabled = True
            from excelmanus.backup import BackupManager
            e._backup_manager = BackupManager(
                workspace_root=e._config.workspace_root,
                scope=scope,
            )
            return f"已开启备份沙盒模式（scope={scope}）。所有文件操作将重定向到副本。"

        if action == "off" and not too_many_args:
            e._backup_enabled = False
            e._backup_manager = None
            return "已关闭备份沙盒模式。后续操作将直接修改原始文件。"

        if action == "apply" and not too_many_args:
            if not e._backup_enabled or e._backup_manager is None:
                return "备份模式未启用，无需 apply。"
            applied = e._backup_manager.apply_all()
            if not applied:
                return "没有需要应用的备份。"
            lines = [f"已将 {len(applied)} 个备份文件应用到原始位置："]
            for item in applied:
                lines.append(f"  - {item['original']}")
            return "\n".join(lines)

        if action == "list" and not too_many_args:
            if not e._backup_enabled or e._backup_manager is None:
                return "备份模式未启用。"
            backups = e._backup_manager.list_backups()
            if not backups:
                return "当前没有备份文件。"
            lines = [f"当前 {len(backups)} 个备份文件："]
            for item in backups:
                exists = "✓" if item["exists"] == "True" else "✗"
                lines.append(f"  [{exists}] {item['original']} → {item['backup']}")
            return "\n".join(lines)

        return "无效参数。用法：/backup [on|off|status|apply|list]"

    async def _handle_accept_command(
        self,
        parts: list[str],
        *,
        on_event: EventCallback | None = None,
    ) -> str:
        """执行待确认操作。"""
        e = self._engine
        if len(parts) != 2:
            return "无效参数。用法：/accept <id>。"

        approval_id = parts[1].strip()
        pending = e._approval.pending
        if pending is None:
            return "当前没有待确认操作。"
        if pending.approval_id != approval_id:
            return f"待确认 ID 不匹配。当前待确认 ID 为 `{pending.approval_id}`。"

        try:
            _, record = await e._execute_tool_with_audit(
                tool_name=pending.tool_name,
                arguments=pending.arguments,
                tool_scope=None,
                approval_id=pending.approval_id,
                created_at_utc=pending.created_at_utc,
                undoable=pending.tool_name not in {"run_code", "run_shell"},
                force_delete_confirm=True,
            )
        except ToolNotAllowedError:
            e._approval.clear_pending()
            e._pending_approval_route_result = None
            return (
                f"accept 执行失败：工具 `{pending.tool_name}` 当前不在授权范围内。"
            )
        except Exception as exc:  # noqa: BLE001
            e._approval.clear_pending()
            e._pending_approval_route_result = None
            return f"accept 执行失败：{exc}"

        # ── run_code RED 路径 → 写入追踪 ──
        if pending.tool_name == "run_code":
            from excelmanus.security.code_policy import extract_excel_targets
            _rc_code = pending.arguments.get("code") or ""
            _rc_result_json: dict | None = None
            try:
                _rc_result_json = json.loads(record.result_preview or "")
                if not isinstance(_rc_result_json, dict):
                    _rc_result_json = None
            except (json.JSONDecodeError, TypeError):
                pass
            _has_cow = bool(_rc_result_json and _rc_result_json.get("cow_mapping"))
            _has_ast_write = any(
                t.operation == "write"
                for t in extract_excel_targets(_rc_code)
            )
            if record.changes or _has_cow or _has_ast_write:
                e._state.record_write_action()
        # ── run_code RED 路径 → window 感知桥接 ──
        if pending.tool_name == "run_code" and e._window_perception is not None:
            _rc_code = pending.arguments.get("code") or ""
            _rc_stdout = ""
            try:
                _rc_result_json = json.loads(record.result_preview or "")
                _rc_stdout = _rc_result_json.get("stdout_tail", "") if isinstance(_rc_result_json, dict) else ""
            except (json.JSONDecodeError, TypeError):
                pass
            e._window_perception.observe_code_execution(
                code=_rc_code,
                audit_changes=record.changes,
                stdout_tail=_rc_stdout,
                iteration=0,
            )

        # ── 通用 CoW 映射提取（覆盖 run_code RED 及其他高风险工具） ──
        if record.result_preview:
            try:
                _accept_result = json.loads(record.result_preview)
                if isinstance(_accept_result, dict):
                    _accept_cow = _accept_result.get("cow_mapping")
                    if _accept_cow and isinstance(_accept_cow, dict):
                        e._state.register_cow_mappings(_accept_cow)
                        logger.info(
                            "/accept CoW 映射已注册: tool=%s mappings=%s",
                            pending.tool_name, _accept_cow,
                        )
            except (json.JSONDecodeError, TypeError):
                pass

        e._approval.clear_pending()
        route_to_resume = e._pending_approval_route_result
        e._pending_approval_route_result = None
        saved_tool_call_id = e._pending_approval_tool_call_id
        e._pending_approval_tool_call_id = None
        lines = [
            f"已执行待确认操作 `{approval_id}`。",
            f"- 工具: `{record.tool_name}`",
            f"- 审计目录: `{record.audit_dir}`",
            f"- 可回滚: {'是' if record.undoable else '否'}",
        ]
        if record.result_preview:
            lines.append(f"- 结果摘要: {record.result_preview}")
        if record.undoable:
            lines.append(f"- 回滚命令: `/undo {approval_id}`")
        if route_to_resume is None:
            return "\n".join(lines)

        # ── 将实际工具结果注入 memory 替换审批提示，使 LLM 能处理真实输出 ──
        if saved_tool_call_id and record.result_preview:
            e._memory.replace_tool_result(saved_tool_call_id, record.result_preview)
            # 移除审批提示对应的 assistant 尾部消息（避免 LLM 重复看到审批文本）
            msgs = e._memory._messages
            if msgs and msgs[-1].get("role") == "assistant":
                last_content = msgs[-1].get("content", "")
                if isinstance(last_content, str) and "待确认队列" in last_content:
                    msgs.pop()

        resume_iteration = e._last_iteration_count + 1
        e._set_window_perception_turn_hints(
            user_message="审批已通过，继续执行剩余子任务",
            is_new_task=False,
        )
        resumed = await e._tool_calling_loop(
            route_to_resume,
            on_event,
            start_iteration=resume_iteration,
        )
        return "\n".join(lines) + f"\n\n{resumed.reply}"

    def _handle_reject_command(self, parts: list[str]) -> str:
        """拒绝待确认操作。"""
        e = self._engine
        if len(parts) != 2:
            return "无效参数。用法：/reject <id>。"
        approval_id = parts[1].strip()
        result = e._approval.reject_pending(approval_id)
        if e._approval.pending is None:
            e._pending_approval_route_result = None
        return result

    def _handle_undo_command(self, parts: list[str]) -> str:
        """回滚已确认操作。"""
        e = self._engine
        if len(parts) != 2:
            return "无效参数。用法：/undo <id>。"
        approval_id = parts[1].strip()
        return e._approval.undo(approval_id)

    async def _handle_plan_command(
        self,
        parts: list[str],
        *,
        on_event: EventCallback | None,
    ) -> str:
        """处理 /plan 命令。"""
        e = self._engine
        action = parts[1].strip().lower() if len(parts) >= 2 else "status"

        if action in {"status", ""} and len(parts) <= 2:
            mode = "enabled" if e._plan_mode_enabled else "disabled"
            lines = [f"当前 plan mode 状态：{mode}。"]
            if e._pending_plan is not None:
                draft = e._pending_plan.draft
                lines.append(f"- 待审批计划 ID: `{draft.plan_id}`")
                lines.append(f"- 计划文件: `{draft.file_path}`")
                lines.append(f"- 子任务数: {len(draft.subtasks)}")
            return "\n".join(lines)

        if action == "on" and len(parts) == 2:
            e._plan_mode_enabled = True
            e._plan_intercept_task_create = True
            return "已开启 plan mode。后续普通对话将仅生成计划草案。"

        if action == "off" and len(parts) == 2:
            e._plan_mode_enabled = False
            e._plan_intercept_task_create = False
            return "已关闭 plan mode。"

        if action == "approve":
            return await self._handle_plan_approve(parts=parts, on_event=on_event)

        if action == "reject":
            return self._handle_plan_reject(parts=parts)

        return (
            "无效参数。用法：/plan [on|off|status]，"
            "或 /plan approve [plan_id]，"
            "或 /plan reject [plan_id]。"
        )

    async def _handle_plan_approve(
        self,
        *,
        parts: list[str],
        on_event: EventCallback | None,
    ) -> str:
        """批准待审批计划并自动继续执行。"""
        e = self._engine
        if len(parts) > 3:
            return "无效参数。用法：/plan approve [plan_id]。"

        if e._approval.has_pending():
            return (
                "当前存在高风险待确认操作，请先执行 `/accept <id>` 或 `/reject <id>`，"
                "再处理计划审批。"
            )

        pending = e._pending_plan
        if pending is None:
            return "当前没有待审批计划。"

        expected_id = pending.draft.plan_id
        provided_id = parts[2].strip() if len(parts) == 3 else ""
        if provided_id and provided_id != expected_id:
            return f"计划 ID 不匹配。当前待审批计划 ID 为 `{expected_id}`。"

        draft = pending.draft
        task_list = e._task_store.create(
            draft.title,
            draft.subtasks,
            replace_existing=True,
        )
        e._approved_plan_context = (
            f"来源: {draft.file_path}\n"
            f"{draft.markdown.strip()}"
        )
        e._pending_plan = None
        e._plan_mode_enabled = False
        e._plan_intercept_task_create = False

        from excelmanus.events import EventType, ToolCallEvent
        e._emit(
            on_event,
            ToolCallEvent(
                event_type=EventType.TASK_LIST_CREATED,
                task_list_data=task_list.to_dict(),
            ),
        )

        resume_prefix = (
            f"已批准计划 `{draft.plan_id}` 并创建任务清单「{draft.title}」，已切回执行模式。"
        )

        if draft.source == "task_create_hook":
            if pending.tool_call_id:
                e._memory.add_tool_result(
                    pending.tool_call_id,
                    (
                        f"计划 `{draft.plan_id}` 已批准并创建任务清单「{draft.title}」，"
                        f"共 {len(draft.subtasks)} 个子任务。"
                    ),
                )
            route_to_resume = pending.route_to_resume
            if route_to_resume is None:
                return resume_prefix

            e._suspend_task_create_plan_once = True
            try:
                e._set_window_perception_turn_hints(
                    user_message=draft.objective,
                    is_new_task=False,
                )
                resumed = await e._tool_calling_loop(route_to_resume, on_event)
                # 自动续跑：若任务清单仍有未完成子任务，自动继续
                resumed = await e._auto_continue_task_loop(
                    route_to_resume, on_event, resumed
                )
            finally:
                e._suspend_task_create_plan_once = False
            return f"{resume_prefix}\n\n{resumed.reply}"

        e._suspend_task_create_plan_once = True
        try:
            resumed = await e.chat(draft.objective, on_event=on_event)
        finally:
            e._suspend_task_create_plan_once = False
        return f"{resume_prefix}\n\n{resumed.reply}"

    def _handle_plan_reject(self, *, parts: list[str]) -> str:
        """拒绝待审批计划。"""
        e = self._engine
        if len(parts) > 3:
            return "无效参数。用法：/plan reject [plan_id]。"

        if e._approval.has_pending():
            return (
                "当前存在高风险待确认操作，请先执行 `/accept <id>` 或 `/reject <id>`，"
                "再处理计划审批。"
            )

        pending = e._pending_plan
        if pending is None:
            return "当前没有待审批计划。"

        expected_id = pending.draft.plan_id
        provided_id = parts[2].strip() if len(parts) == 3 else ""
        if provided_id and provided_id != expected_id:
            return f"计划 ID 不匹配。当前待审批计划 ID 为 `{expected_id}`。"

        if pending.draft.source == "task_create_hook" and pending.tool_call_id:
            e._memory.add_tool_result(
                pending.tool_call_id,
                f"计划 `{expected_id}` 已拒绝，task_create 已取消执行。",
            )

        e._pending_plan = None
        return f"已拒绝计划 `{expected_id}`。"

    @staticmethod
    def _parse_subagent_run_command(
        text: str,
    ) -> tuple[str | None, str | None, str | None]:
        """解析 `/subagent run` 命令。"""
        raw = text.strip()
        lowered = raw.lower()
        prefix = ""
        for candidate in ("/subagent run", "/sub_agent run"):
            if lowered.startswith(candidate):
                prefix = candidate
                break
        if not prefix:
            return None, None, "无效参数。用法：/subagent run [agent_name] -- <task>。"

        rest = raw[len(prefix):].strip()
        if not rest:
            return None, None, "无效参数。用法：/subagent run [agent_name] -- <task>。"

        if rest.startswith("--"):
            task = rest[2:].strip()
            if not task:
                return None, None, "无效参数。`--` 后必须提供任务描述。"
            return None, task, None

        sep = " -- "
        if sep not in rest:
            return None, None, "无效参数。用法：/subagent run [agent_name] -- <task>。"
        agent_name, task = rest.split(sep, 1)
        agent_name = agent_name.strip()
        task = task.strip()
        if not agent_name or not task:
            return None, None, "无效参数。agent_name 与 task 都不能为空。"
        return agent_name, task, None

