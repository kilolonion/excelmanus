"""CommandHandler — 从 AgentEngine 解耦的控制命令处理组件。

负责管理：
- /fullaccess, /subagent, /accept, /reject, /undo, /plan, /model, /backup, /compact, /manifest 命令
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from excelmanus.control_commands import (
    NORMALIZED_ALIAS_TO_CANONICAL_CONTROL_COMMAND,
    normalize_control_command,
)
from excelmanus.logger import get_logger

# 延迟导入，运行时使用以避免循环依赖：
# - SkillMatchResult（来自 excelmanus.skillpacks.router）
# - EventType, ToolCallEvent（来自 excelmanus.events）

if TYPE_CHECKING:
    from excelmanus.engine import AgentEngine
    from excelmanus.events import EventCallback

logger = get_logger("command_handler")


class CommandHandler:
    """控制命令处理器，从 AgentEngine 搬迁所有 /command 处理逻辑。"""

    def __init__(self, engine: "AgentEngine") -> None:
        self._engine = engine

    def _emit_mode_changed(
        self,
        on_event: "EventCallback | None",
        mode_name: str,
        enabled: bool,
    ) -> None:
        """发出模式变更事件。"""
        if on_event is None:
            return
        from excelmanus.events import EventType, ToolCallEvent

        on_event(ToolCallEvent(
            event_type=EventType.MODE_CHANGED,
            mode_name=mode_name,
            mode_enabled=enabled,
        ))

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
                self._emit_mode_changed(on_event, "full_access", True)
                msg = "已开启 fullaccess。当前代码技能权限：full_access。"
                # 若当前有 pending approval，自动执行并续上对话
                pending = e.approval.pending
                if pending is not None:
                    accept_result = await self._handle_accept_command(
                        ["/accept", pending.approval_id], on_event=on_event,
                    )
                    return f"{msg}\n\n{accept_result}"
                return msg
            if action == "off" and not too_many_args:
                e._full_access_enabled = False
                # 将受限 skill 从 _active_skills 中驱逐，避免下一轮 scope 泄漏
                blocked = set(e._restricted_code_skillpacks)
                e._active_skills = [
                    s for s in e._active_skills if s.name not in blocked
                ]
                self._emit_mode_changed(on_event, "full_access", False)
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
                return e.handle_list_subagents()
            if action == "run":
                agent_name, task, parse_error = self._parse_subagent_run_command(text)
                if parse_error is not None:
                    return parse_error
                assert task is not None
                outcome = await e.delegate_to_subagent(
                    task=task,
                    agent_name=agent_name,
                    on_event=on_event,
                )
                if (
                    not outcome.success
                    and outcome.subagent_result is not None
                    and outcome.subagent_result.pending_approval_id is not None
                ):
                    pending = e.approval.pending
                    approval_id_value = outcome.subagent_result.pending_approval_id
                    high_risk_tool = (
                        pending.tool_name
                        if pending is not None and pending.approval_id == approval_id_value
                        else "高风险工具"
                    )
                    question = e.enqueue_subagent_approval_question(
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
            return "该命令已废弃。请使用输入框上方的「写入 / 读取 / 计划」模式 Tab 切换。"

        if command == "/model":
            # /model → 显示当前模型
            # /model list → 列出所有可用模型
            # /model <name> → 切换模型
            if not action:
                name_display = e.active_model_name or "default"
                return f"当前模型：{name_display}（{e.active_model}）"
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

        if command == "/manifest":
            return self._handle_manifest_command(parts)

        if command == "/accept":
            return await self._handle_accept_command(parts, on_event=on_event)
        if command == "/reject":
            return self._handle_reject_command(parts, on_event=on_event)
        if command == "/rollback":
            return self._handle_rollback_command(parts)

        if command == "/rules":
            return self._handle_rules_command(parts, text)

        if command == "/memory":
            return self._handle_memory_command(parts)

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
            sys_msgs = e.memory.build_system_messages()
            status = compaction_mgr.get_status(e.memory, sys_msgs)
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
        summary_model = e.config.aux_model or e.active_model
        sys_msgs = e.memory.build_system_messages()

        result = await compaction_mgr.manual_compact(
            memory=e.memory,
            system_msgs=sys_msgs,
            client=e._client,
            summary_model=summary_model,
            custom_instruction=custom_instruction,
        )

        if not result.success:
            return f"压缩未执行: {result.error}"

        pct_before = (
            result.tokens_before / e.config.max_context_tokens * 100
            if e.config.max_context_tokens > 0
            else 0
        )
        pct_after = (
            result.tokens_after / e.config.max_context_tokens * 100
            if e.config.max_context_tokens > 0
            else 0
        )
        return (
            f"✅ 上下文压缩完成。\n"
            f"- 消息: {result.messages_before} → {result.messages_after}\n"
            f"- Token: {result.tokens_before:,} ({pct_before:.1f}%) → "
            f"{result.tokens_after:,} ({pct_after:.1f}%)\n"
            f"- 累计压缩次数: {compaction_mgr.stats.compaction_count}"
        )

    def _handle_manifest_command(self, parts: list[str]) -> str:
        """处理 /manifest 命令。"""
        e = self._engine
        action = parts[1].strip().lower() if len(parts) >= 2 else "status"

        if len(parts) > 2:
            return "无效参数。用法：/manifest [status|build]。"

        if action in {"status", ""}:
            status = e.workspace_manifest_build_status()
            state = status.get("state")
            if state == "ready":
                total_files = int(status.get("total_files") or 0)
                scan_duration_ms = int(status.get("scan_duration_ms") or 0)
                return (
                    "Workspace manifest：已就绪。\n"
                    f"- 文件数: {total_files}\n"
                    f"- 扫描耗时: {scan_duration_ms}ms"
                )
            if state == "building":
                return "Workspace manifest：后台构建中。你可以继续对话，完成后会自动生效。"
            if state == "error":
                error = str(status.get("error") or "unknown")
                return (
                    "Workspace manifest：尚未就绪（最近一次构建失败）。\n"
                    f"- 错误: {error}\n"
                    "可执行 `/manifest build` 重试。"
                )
            return "Workspace manifest：尚未开始。可执行 `/manifest build` 开始后台构建。"

        if action == "build":
            started = e.start_workspace_manifest_prewarm(force=False)
            if started:
                return "已在后台开始构建 Workspace manifest。你可以继续当前对话。"

            status = e.workspace_manifest_build_status()
            state = status.get("state")
            if state == "ready":
                total_files = int(status.get("total_files") or 0)
                return f"Workspace manifest 已就绪（{total_files} 文件），无需重复构建。"
            if state == "building":
                return "Workspace manifest 已在后台构建中，请稍候。"
            if state == "error":
                error = str(status.get("error") or "unknown")
                return (
                    "后台构建启动失败。\n"
                    f"- 最近错误: {error}\n"
                    "请稍后重试。"
                )
            return "当前环境无法启动后台构建，请稍后重试。"

        return "无效参数。用法：/manifest [status|build]。"

    def _handle_backup_command(self, parts: list[str]) -> str:
        """处理 /backup 会话控制命令。"""
        e = self._engine
        action = parts[1].strip().lower() if len(parts) >= 2 else ""
        too_many_args = len(parts) > 3

        if action in {"status", ""} and not too_many_args:
            if not e.workspace.transaction_enabled:
                return "备份沙盒模式：已关闭。"
            tx = e.transaction
            count = len(tx.list_staged()) if tx else 0
            scope = tx.scope if tx else "all"
            return (
                f"备份沙盒模式：已启用（scope={scope}）。\n"
                f"当前管理 {count} 个备份文件。\n"
                f"备份目录：{tx.staging_dir if tx else 'N/A'}"
            )

        if action == "on" and not too_many_args:
            scope = "all"
            if len(parts) == 3 and parts[2].strip().lower() == "--excel-only":
                scope = "excel_only"
            e.workspace.transaction_enabled = True
            e.workspace.transaction_scope = scope
            e.transaction = e.workspace.create_transaction(fvm=e._fvm)
            e.sandbox_env = e.workspace.create_sandbox_env(transaction=e.transaction)
            return f"已开启备份沙盒模式（scope={scope}）。所有文件操作将重定向到副本。"

        if action == "off" and not too_many_args:
            e.workspace.transaction_enabled = False
            e.transaction = None
            e.sandbox_env = e.workspace.create_sandbox_env(transaction=None)
            return "已关闭备份沙盒模式。后续操作将直接修改原始文件。"

        if action == "apply" and not too_many_args:
            tx = e.transaction
            if not e.workspace.transaction_enabled or tx is None:
                return "备份模式未启用，无需 apply。"
            applied = tx.commit_all()
            if not applied:
                return "没有需要应用的备份。"
            lines = [f"已将 {len(applied)} 个备份文件应用到原始位置："]
            for item in applied:
                lines.append(f"  - {item['original']}")
            return "\n".join(lines)

        if action == "list" and not too_many_args:
            tx = e.transaction
            if not e.workspace.transaction_enabled or tx is None:
                return "备份模式未启用。"
            backups = tx.list_staged()
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
        pending = e.approval.pending
        if pending is None:
            return "当前没有待确认操作。"
        if pending.approval_id != approval_id:
            return f"待确认 ID 不匹配。当前待确认 ID 为 `{pending.approval_id}`。"

        # 提前保存并清理状态，确保所有路径（成功/失败）都能正确发射事件
        saved_tool_call_id = e._pending_approval_tool_call_id
        e._pending_approval_tool_call_id = None

        exec_ok, exec_result, record = await e._execute_approved_pending(
            pending, on_event=on_event,
        )

        from excelmanus.events import EventType, ToolCallEvent

        if not exec_ok or record is None:
            route_to_resume = e._pending_approval_route_result
            e._pending_approval_route_result = None
            # ── 失败时也必须发射 APPROVAL_RESOLVED，否则前端卡片永远停在 pending ──
            e.emit(
                on_event,
                ToolCallEvent(
                    event_type=EventType.APPROVAL_RESOLVED,
                    tool_call_id=saved_tool_call_id or "",
                    approval_id=approval_id,
                    approval_tool_name=pending.tool_name,
                    result=exec_result,
                    success=False,
                ),
            )
            return exec_result

        route_to_resume = e._pending_approval_route_result
        e._pending_approval_route_result = None

        # ── 发射 APPROVAL_RESOLVED 事件，携带 tool_call_id 供前端更新卡片 ──
        e.emit(
            on_event,
            ToolCallEvent(
                event_type=EventType.APPROVAL_RESOLVED,
                tool_call_id=saved_tool_call_id or "",
                approval_id=approval_id,
                approval_tool_name=record.tool_name,
                result=exec_result,
                success=exec_ok,
                approval_undoable=record.undoable,
                approval_has_changes=bool(record.changes),
            ),
        )

        # ── 始终更新 memory：用真实结果替换审批提示，使 LLM 知道操作已完成 ──
        if saved_tool_call_id and record.result_preview:
            e.memory.replace_tool_result(saved_tool_call_id, record.result_preview)
        # 移除审批提示对应的 assistant 尾部消息（避免 LLM 重复看到审批文本）
        e.memory.remove_last_assistant_if(lambda c: "待确认队列" in c)

        # ── 恢复主循环，让 LLM 看到工具结果并生成自然语言回复 ──
        if route_to_resume is None:
            return exec_result

        resume_iteration = e._last_iteration_count + 1
        has_tasks = e._has_incomplete_tasks()
        e._set_window_perception_turn_hints(
            user_message="审批已通过，继续执行" + ("剩余子任务" if has_tasks else ""),
            is_new_task=False,
        )
        try:
            resumed = await e._tool_calling_loop(
                route_to_resume,
                on_event,
                start_iteration=resume_iteration,
            )
            return resumed.reply
        except Exception as exc:
            logger.warning("审批后恢复主循环异常: %s", exc, exc_info=True)
            return exec_result

    def _handle_reject_command(
        self,
        parts: list[str],
        *,
        on_event: "EventCallback | None" = None,
    ) -> str:
        """拒绝待确认操作。"""
        e = self._engine
        if len(parts) != 2:
            return "无效参数。用法：/reject <id>。"
        approval_id = parts[1].strip()
        pending = e.approval.pending
        saved_tool_call_id = e._pending_approval_tool_call_id
        tool_name = pending.tool_name if pending else ""
        result = e.approval.reject_pending(approval_id)
        if e.approval.pending is None:
            e._pending_approval_route_result = None
            e._pending_approval_tool_call_id = None

        from excelmanus.events import EventType, ToolCallEvent

        e.emit(
            on_event,
            ToolCallEvent(
                event_type=EventType.APPROVAL_RESOLVED,
                tool_call_id=saved_tool_call_id or "",
                approval_id=approval_id,
                approval_tool_name=tool_name,
                result=result,
                success=False,
            ),
        )
        return result

    def _handle_rollback_command(self, parts: list[str]) -> str:
        """回退对话到指定用户轮次。"""
        e = self._engine
        action = parts[1].strip().lower() if len(parts) >= 2 else ""

        # /rollback 或 /rollback list → 列出用户轮次
        if action in {"", "list"}:
            turns = e.memory.list_user_turns()
            if not turns:
                return "当前没有用户对话记录。"
            lines = ["**用户对话轮次**（最早 → 最近）：\n"]
            for t in turns:
                lines.append(
                    f"  [{t['index']}] {t['content_preview']}"
                )
            lines.append("\n使用 `/rollback <N>` 回退到第 N 轮。")
            return "\n".join(lines)

        # /rollback <N> → 执行回退
        try:
            turn_index = int(action)
        except ValueError:
            return "无效参数。用法：/rollback [list] 或 /rollback <N>（N 为轮次序号）。"

        try:
            result = e.rollback_conversation(turn_index, rollback_files=False)
        except IndexError as exc:
            return str(exc)

        removed = result["removed_messages"]
        return (
            f"已回退到第 {turn_index} 轮用户消息，移除了 {removed} 条后续消息。\n"
            f"提示：如需同时回滚文件变更，请通过 API 传入 rollback_files=true，"
            f"或使用 `/undo` 逐条回滚。"
        )

    def _handle_undo_command(self, parts: list[str]) -> str:
        """回滚已确认操作，或列出可回滚操作。"""
        e = self._engine
        action = parts[1].strip().lower() if len(parts) >= 2 else ""

        # /undo 或 /undo list → 列出可回滚操作
        if action in {"", "list"}:
            records = e.approval.list_applied(limit=20)
            if not records:
                return "没有已执行的操作记录。"
            lines = ["**操作历史**（最近 → 最早）：\n"]
            for rec in records:
                status_icon = "✅" if rec.execution_status == "success" else "❌"
                undo_hint = "🔄 可回滚" if rec.undoable else "—"
                ts = rec.applied_at_utc[:19].replace("T", " ") if rec.applied_at_utc else "?"
                preview = rec.result_preview[:60] if rec.result_preview else ""
                lines.append(
                    f"- {status_icon} `{rec.approval_id}` | "
                    f"**{rec.tool_name}** | {ts} | {undo_hint}"
                )
                if preview:
                    lines.append(f"  _{preview}_")
            lines.append("\n使用 `/undo <id>` 回滚指定操作。")
            return "\n".join(lines)

        # /undo <id> → 执行回滚
        if len(parts) == 2:
            approval_id = parts[1].strip()
            return e.approval.undo(approval_id)

        return "无效参数。用法：/undo [list] 或 /undo <id>。"

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

    # ── /rules 命令处理 ──────────────────────────────────

    def _handle_rules_command(self, parts: list[str], raw_text: str) -> str:
        """处理 /rules 命令。

        用法：
        - /rules                       列出全局规则
        - /rules add <内容>            添加全局规则
        - /rules delete <id>           删除全局规则
        - /rules toggle <id>           切换启用/禁用
        - /rules session               列出会话规则
        - /rules session add <内容>    添加会话规则
        - /rules session delete <id>   删除会话规则
        """
        e = self._engine
        rm = getattr(e, "_rules_manager", None)
        if rm is None:
            return "规则功能未初始化。"

        action = parts[1].strip().lower() if len(parts) >= 2 else ""

        if not action:
            rules = rm.list_global_rules()
            if not rules:
                return "暂无全局规则。使用 `/rules add <内容>` 添加。"
            lines = ["**全局规则：**"]
            for r in rules:
                status = "✅" if r.enabled else "❌"
                lines.append(f"  {status} `{r.id}` {r.content}")
            return "\n".join(lines)

        if action == "add":
            content = raw_text.split(None, 2)[2] if len(parts) >= 3 else ""
            if not content.strip():
                return "用法：/rules add <规则内容>"
            r = rm.add_global_rule(content)
            return f"已添加全局规则 `{r.id}`：{r.content}"

        if action == "delete":
            if len(parts) < 3:
                return "用法：/rules delete <rule_id>"
            rule_id = parts[2].strip()
            ok = rm.delete_global_rule(rule_id)
            return f"已删除规则 `{rule_id}`。" if ok else f"规则 `{rule_id}` 不存在。"

        if action == "toggle":
            if len(parts) < 3:
                return "用法：/rules toggle <rule_id>"
            rule_id = parts[2].strip()
            for r in rm.list_global_rules():
                if r.id == rule_id:
                    updated = rm.update_global_rule(rule_id, enabled=not r.enabled)
                    if updated:
                        status = "启用" if updated.enabled else "禁用"
                        return f"规则 `{rule_id}` 已{status}。"
            return f"规则 `{rule_id}` 不存在。"

        if action == "session":
            session_id = getattr(e, "_session_id", None)
            if not session_id:
                return "当前无活跃会话。"
            sub_action = parts[2].strip().lower() if len(parts) >= 3 else ""

            if not sub_action:
                rules = rm.list_session_rules(session_id)
                if not rules:
                    return "当前会话暂无规则。使用 `/rules session add <内容>` 添加。"
                lines = ["**当前会话规则：**"]
                for r in rules:
                    status = "✅" if r.enabled else "❌"
                    lines.append(f"  {status} `{r.id}` {r.content}")
                return "\n".join(lines)

            if sub_action == "add":
                content = raw_text.split(None, 3)[3] if len(parts) >= 4 else ""
                if not content.strip():
                    return "用法：/rules session add <规则内容>"
                r = rm.add_session_rule(session_id, content)
                if r is None:
                    return "会话级规则需要数据库支持。"
                return f"已添加会话规则 `{r.id}`：{r.content}"

            if sub_action == "delete":
                if len(parts) < 4:
                    return "用法：/rules session delete <rule_id>"
                rule_id = parts[3].strip()
                ok = rm.delete_session_rule(session_id, rule_id)
                return f"已删除会话规则 `{rule_id}`。" if ok else f"规则 `{rule_id}` 不存在。"

            return "无效参数。用法：/rules session [add|delete]"

        return (
            "无效参数。用法：\n"
            "  /rules                — 列出全局规则\n"
            "  /rules add <内容>     — 添加全局规则\n"
            "  /rules delete <id>   — 删除全局规则\n"
            "  /rules toggle <id>   — 切换启用/禁用\n"
            "  /rules session       — 列出会话规则\n"
            "  /rules session add   — 添加会话规则\n"
            "  /rules session delete — 删除会话规则"
        )

    # ── /memory 命令处理 ──────────────────────────────────

    def _handle_memory_command(self, parts: list[str]) -> str:
        """处理 /memory 命令。

        用法：
        - /memory                        列出所有记忆（按类别分组）
        - /memory <category>             列出指定类别
        - /memory delete <id>            删除指定条目
        - /memory clear <category>       清空指定类别
        """
        e = self._engine
        pm = getattr(e, "_persistent_memory", None)
        if pm is None:
            return "持久记忆功能未启用。"

        from excelmanus.memory_models import MemoryCategory

        action = parts[1].strip().lower() if len(parts) >= 2 else ""

        _category_labels = {
            "file_pattern": "文件结构",
            "user_pref": "用户偏好",
            "error_solution": "错误解决方案",
            "general": "通用",
        }

        if not action:
            entries = pm.list_entries()
            if not entries:
                return "暂无持久记忆。Agent 在对话中会自动保存有价值的信息。"
            grouped: dict[str, list] = {}
            for entry in entries:
                cat = entry.category.value
                grouped.setdefault(cat, []).append(entry)
            lines = ["**持久记忆：**"]
            for cat, cat_entries in grouped.items():
                label = _category_labels.get(cat, cat)
                lines.append(f"\n**{label}** ({len(cat_entries)} 条)")
                for entry in cat_entries:
                    preview = entry.content[:60] + ("..." if len(entry.content) > 60 else "")
                    lines.append(f"  `{entry.id}` {preview}")
            return "\n".join(lines)

        if action == "delete":
            if len(parts) < 3:
                return "用法：/memory delete <entry_id>"
            entry_id = parts[2].strip()
            ok = pm.delete_entry(entry_id)
            return f"已删除记忆 `{entry_id}`。" if ok else f"记忆 `{entry_id}` 不存在。"

        if action == "clear":
            if len(parts) < 3:
                return "用法：/memory clear <category>（file_pattern, user_pref, error_solution, general）"
            cat_name = parts[2].strip().lower()
            try:
                cat = MemoryCategory(cat_name)
            except ValueError:
                return f"不支持的类别：{cat_name}。支持：file_pattern, user_pref, error_solution, general"
            entries = pm.list_entries(cat)
            if not entries:
                return f"类别 `{cat_name}` 没有记忆条目。"
            deleted = 0
            for entry in entries:
                if pm.delete_entry(entry.id):
                    deleted += 1
            return f"已清空类别 `{cat_name}`，删除 {deleted} 条记忆。"

        # 尝试作为类别筛选
        try:
            cat = MemoryCategory(action)
        except ValueError:
            return (
                "无效参数。用法：\n"
                "  /memory                  — 列出所有记忆\n"
                "  /memory <category>       — 按类别筛选\n"
                "  /memory delete <id>      — 删除条目\n"
                "  /memory clear <category> — 清空类别"
            )

        entries = pm.list_entries(cat)
        label = _category_labels.get(action, action)
        if not entries:
            return f"类别 `{label}` 暂无记忆。"
        lines = [f"**{label}** ({len(entries)} 条)"]
        for entry in entries:
            preview = entry.content[:80] + ("..." if len(entry.content) > 80 else "")
            lines.append(f"  `{entry.id}` {preview}")
        return "\n".join(lines)
