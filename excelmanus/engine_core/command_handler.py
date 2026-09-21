"""CommandHandler — 从 AgentEngine 解耦的控制命令处理组件。

负责管理：
- /fullaccess, /subagent, /accept, /reject, /undo, /plan, /model, /compact, /registry 命令
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

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
        *,
        value: str = "",
    ) -> None:
        """发出模式变更事件。"""
        if on_event is None:
            return
        from excelmanus.events import EventType, ToolCallEvent

        on_event(ToolCallEvent(
            event_type=EventType.MODE_CHANGED,
            mode_name=mode_name,
            mode_enabled=enabled,
            mode_value=value,
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
        if command == "/resume":
            result = await e._driver.resume(text.partition(" ")[2], on_event=on_event)
            return result.reply

        # 改写 durable 历史的命令与在途请求竞争会破坏 series 前缀基线：
        # 压缩/回滚先 start_new，随后在途响应把旧 header accept 进新系列，
        # 导致后续 compile 持续 fail-closed。与 POST /sessions/{id}/compact
        # 的 409 守卫对齐，这里统一拒绝。
        if command in ("/compact", "/rollback", "/clear", "/undo"):
            driver = getattr(e, "_driver", None)
            if driver is not None and getattr(driver, "running", False):
                return "当前会话正在执行，请在步骤结束后再执行该命令。"

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
                e._persist_full_access(True)
                e._auto_approve_enabled = False
                e._persist_auto_approve(False)
                self._emit_mode_changed(on_event, "full_access", True)
                msg = (
                    "已开启完全访问（full_access）。run_code 可访问工作区外文件、联网并启动子进程；"
                    "模型请求的工具与本机 Shell 命令将自动执行。"
                )
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
                e._persist_full_access(False)
                # 将受限 skill 从 _active_skills 中驱逐，避免下一轮 scope 泄漏
                blocked = set(e._restricted_code_skillpacks)
                before = len(e._active_skills)
                e._active_skills = [
                    s for s in e._active_skills if s.name not in blocked
                ]
                if len(e._active_skills) != before:
                    from excelmanus.request.series import series_of

                    series_of(e).note("catalog/change")
                self._emit_mode_changed(on_event, "full_access", False)
                return "已关闭完全访问。当前恢复为受限审批模式。"
            if action == "status" and not too_many_args:
                if e._full_access_enabled:
                    return "当前权限：完全访问（含工作区外文件、联网、子进程和本机命令）。"
                if getattr(e, "_auto_approve_enabled", False):
                    return "当前权限：仅自动审批（不含网络、子进程和工作区外文件）。"
                return "当前权限：询问（受限）。"
            return "无效参数。用法：/fullaccess [on|off|status]。"

        if command == "/autoapprove":
            if (action in {"on", ""}) and not too_many_args:
                e._auto_approve_enabled = True
                e._persist_auto_approve(True)
                e._full_access_enabled = False
                e._persist_full_access(False)
                self._emit_mode_changed(on_event, "auto_approve", True)
                msg = "已开启仅自动审批。代码仍禁止网络、子进程和工作区外文件。"
                pending = e.approval.pending
                if pending is not None:
                    accept_result = await self._handle_accept_command(
                        ["/accept", pending.approval_id], on_event=on_event,
                    )
                    return f"{msg}\n\n{accept_result}"
                return msg
            if action == "off" and not too_many_args:
                e._auto_approve_enabled = False
                e._persist_auto_approve(False)
                self._emit_mode_changed(on_event, "auto_approve", False)
                return "已关闭仅自动审批，当前恢复为询问模式。"
            if action == "status" and not too_many_args:
                if e._full_access_enabled:
                    return "当前权限：完全访问（请用 /fullaccess off 关闭）。"
                status = "开启" if getattr(e, "_auto_approve_enabled", False) else "关闭"
                return f"仅自动审批：**{status}**（不含网络、子进程和工作区外文件）。"
            return "无效参数。用法：/autoapprove [on|off|status]。"

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
                from excelmanus.subagent.result import format_parent_reply

                result = await e.delegate_to_subagent(
                    task=task,
                    agent_name=agent_name,
                    on_event=on_event,
                )
                return format_parent_reply(result)
            return (
                "无效参数。用法：/subagent [on|off|status|list]，"
                "或 /subagent run -- <task>，"
                "或 /subagent run <agent_name> -- <task>。"
            )

        if command == "/plan":
            from excelmanus.plan_mode import handle_plan_command

            return handle_plan_command(e, action, on_event=on_event)

        if command == "/model":
            # /model → 显示当前模型
            # /model list → 列出所有可用模型
            # /model <name> → 切换模型
            if not action:
                name_display = e.active_model_name or e.active_model
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
            result_msg = e.switch_model(model_arg)
            # W2: 异步精确更新上下文预算（通过 API 查询真实窗口大小）
            if result_msg.startswith("已切换"):
                try:
                    new_tokens = await e.context_budget.update_for_model_async(
                        e.active_model, client=e._client,
                        base_url=e._active_base_url,
                    )
                    e._memory.update_context_window(new_tokens)
                    e._compaction_manager.max_context_tokens = new_tokens
                except Exception:
                    logger.debug("异步上下文预算更新失败，保持同步推断值", exc_info=True)
            return result_msg

        if command == "/compact":
            return await self._handle_compact_command(parts)

        if command == "/registry":
            return self._handle_registry_command(parts)

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

        if command == "/tools":
            return self._handle_tools_command(parts, on_event=on_event)

        if command == "/reasoning":
            return self._handle_reasoning_command(parts, on_event=on_event)

        if command == "/context":
            return await self._handle_context_command(parts)

        if command == "/probe":
            return await self._handle_probe_command(parts)

        if command == "/clear":
            await self._engine.shutdown_agents()
            return self._handle_clear_command()

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

        from excelmanus.prompt.envelope import compaction_wire_context

        # /compact status
        if action == "status":
            sys_msgs, _tools = compaction_wire_context(e)
            sys_msgs = sys_msgs or e.memory.build_system_messages()
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
        summary_model = e.active_model
        sys_msgs, compact_tools = compaction_wire_context(e)
        sys_msgs = sys_msgs or e.memory.build_system_messages()

        from excelmanus.compaction import capture_progress, sync_compaction_boundary

        result = await compaction_mgr.manual_compact(
            memory=e.memory,
            system_msgs=sys_msgs,
            client=e._client,
            summary_model=summary_model,
            custom_instruction=custom_instruction,
            tools=compact_tools,
            vision_capable=bool(getattr(e, "_is_vision_capable", True)),
            progress_provider=lambda: capture_progress(e),
        )

        if not result.success:
            return f"压缩未执行: {result.error}"

        recorder = getattr(e, "record_compaction_handoff", None)
        if callable(recorder):
            recorder(getattr(result, "handoff", None))

        sync_compaction_boundary(e)

        pct_before = (
            result.tokens_before / e.max_context_tokens * 100
            if e.max_context_tokens > 0
            else 0
        )
        pct_after = (
            result.tokens_after / e.max_context_tokens * 100
            if e.max_context_tokens > 0
            else 0
        )
        return (
            f"✅ 上下文压缩完成。\n"
            f"- 消息: {result.messages_before} → {result.messages_after}\n"
            f"- Token: {result.tokens_before:,} ({pct_before:.1f}%) → "
            f"{result.tokens_after:,} ({pct_after:.1f}%)\n"
            f"- 累计压缩次数: {compaction_mgr.stats.compaction_count}"
        )

    def _handle_registry_command(self, parts: list[str]) -> str:
        """处理 /registry 命令。"""
        e = self._engine
        action = parts[1].strip().lower() if len(parts) >= 2 else "status"

        if len(parts) > 2:
            return "无效参数。用法：/registry [status|scan]。"

        if action in {"status", ""}:
            status = e.registry_scan_status()
            state = status.get("state")
            if state == "ready":
                total_files = int(status.get("total_files") or 0)
                scan_duration_ms = int(status.get("scan_duration_ms") or 0)
                return (
                    "FileRegistry：已就绪。\n"
                    f"- 文件数: {total_files}\n"
                    f"- 扫描耗时: {scan_duration_ms}ms"
                )
            if state == "building":
                return "FileRegistry：后台扫描中。你可以继续对话，完成后会自动生效。"
            if state == "error":
                error = str(status.get("error") or "unknown")
                return (
                    "FileRegistry：尚未就绪（最近一次扫描失败）。\n"
                    f"- 错误: {error}\n"
                    "可执行 `/registry scan` 重试。"
                )
            return "FileRegistry：尚未开始。可执行 `/registry scan` 开始后台扫描。"

        if action == "scan":
            started = e.start_registry_scan(force=False)
            if started:
                return "已在后台开始 FileRegistry 扫描。你可以继续当前对话。"

            status = e.registry_scan_status()
            state = status.get("state")
            if state == "ready":
                total_files = int(status.get("total_files") or 0)
                return f"FileRegistry 已就绪（{total_files} 文件），无需重复扫描。"
            if state == "building":
                return "FileRegistry 已在后台扫描中，请稍候。"
            if state == "error":
                error = str(status.get("error") or "unknown")
                return (
                    "后台构建启动失败。\n"
                    f"- 最近错误: {error}\n"
                    "请稍后重试。"
                )
            return "当前环境无法启动后台扫描，请稍后重试。"

        return "无效参数。用法：/registry [status|scan]。"

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

        # 子调用内联等待中的审批：决策注入等待通道，禁止走重放路径
        # （重放会在原调用之外产生第二次执行）。
        if approval_id in getattr(e, "_inflight_approval_ids", ()) or e._interaction_registry.has_pending(approval_id):
            from excelmanus.chat_turn import submit_approval
            resolved = submit_approval(e, approval_id, "accept")
            if resolved:
                return f"已批准 `{approval_id}`，等待工具调用继续执行。"
            return "该审批正在由工具内联通道等待决策，无法通过 /accept 重放。"

        # 提前保存并清理状态，确保所有路径（成功/失败）都能正确发射事件
        saved_tool_call_id = e._pending_approval_tool_call_id
        e._pending_approval_tool_call_id = None

        exec_ok, exec_result, record = await e._execute_approved_pending(
            pending, on_event=on_event, tool_call_id=saved_tool_call_id,
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
        if e.memory.remove_last_assistant_if(lambda c: "待确认队列" in c):
            # 唯一一处「改写 durable 却不失效信封」的风险点：补上失效，
            # 否则一旦该消息已上过 wire，下一次 assemble 会 fail-closed 粘死。
            from excelmanus.prompt.envelope import invalidate_envelope

            invalidate_envelope(e)

        # ── 恢复主循环，让 LLM 看到工具结果并生成自然语言回复 ──
        if route_to_resume is None:
            return exec_result

        resume_iteration = e._last_iteration_count + 1
        try:
            from excelmanus.agent.loop import run_tool_loop

            resumed = await run_tool_loop(
                e,
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
        if approval_id in getattr(e, "_inflight_approval_ids", ()) or e._interaction_registry.has_pending(approval_id):
            from excelmanus.chat_turn import submit_approval
            resolved = submit_approval(e, approval_id, "reject")
            if resolved:
                return f"已拒绝 `{approval_id}`。"
            return "该审批正在由工具内联通道等待决策，无法通过 /reject 处理。"
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
            result = e.rollback_conversation(turn_index)
        except IndexError as exc:
            return str(exc)

        removed = result["removed_messages"]
        return (
            f"已回退到第 {turn_index} 轮用户消息，移除了 {removed} 条后续消息。\n"
            "对话回滚不改磁盘。文件回退请用 manage_spreadsheet_versions restore。"
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

        # W9: /undo diff <file> → RevisionStore 时间线
        if action == "diff":
            if len(parts) < 3:
                return "无效参数。用法：/undo diff <文件路径>。"
            file_path = " ".join(parts[2:]).strip()
            from pathlib import Path
            from excelmanus.workspace.identity import IdentityError, resolve_canonical
            from excelmanus.workspace.revisions import RevisionStore
            root = Path(e.config.workspace_root)
            try:
                ident = resolve_canonical(root, file_path)
            except IdentityError as exc:
                return f"路径无效：{exc}"
            records = RevisionStore(root).list(ident.relative)
            if not records:
                return f"未找到文件 {ident.relative!r} 的 revision 记录。"
            lines = [f"**文件版本** `{ident.relative}`\n"]
            for rec in records[-20:]:
                label = rec.label or rec.reason
                lines.append(
                    f"- `{rec.id}` seq={rec.sequence} {label} sha256:{rec.sha256[:12]}…"
                )
            lines.append("\n恢复：manage_spreadsheet_versions restore，或版本面板。")
            return "\n".join(lines)

        # /undo <id> → 执行回滚
        if len(parts) == 2:
            approval_id = parts[1].strip()
            return e.approval.undo(approval_id)

        return "无效参数。用法：/undo [list|diff <文件>] 或 /undo <id>。"

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

    # ── /tools 命令处理 ──────────────────────────────────

    def _handle_tools_command(
        self,
        parts: list[str],
        *,
        on_event: "EventCallback | None" = None,
    ) -> str:
        """处理 /tools 命令：控制工具调用详情展示。

        用法：
        - /tools          — 切换（toggle）
        - /tools on       — 开启
        - /tools off      — 关闭
        - /tools status   — 查看状态
        """
        e = self._engine
        action = parts[1].strip().lower() if len(parts) >= 2 else ""
        too_many_args = len(parts) > 2

        if too_many_args:
            return "无效参数。用法：/tools [on|off|status]。"

        if action == "status":
            status = "开启" if e._show_tool_calls else "关闭"
            return f"工具调用详情展示：{status}。"

        if action == "on":
            e._show_tool_calls = True
            self._emit_mode_changed(on_event, "show_tool_calls", True)
            return "已开启工具调用详情展示。每次工具调用将显示名称和参数摘要。"

        if action == "off":
            e._show_tool_calls = False
            self._emit_mode_changed(on_event, "show_tool_calls", False)
            return "已关闭工具调用详情展示。"

        # 默认 toggle
        e._show_tool_calls = not e._show_tool_calls
        self._emit_mode_changed(on_event, "show_tool_calls", e._show_tool_calls)
        status = "开启" if e._show_tool_calls else "关闭"
        return f"工具调用详情展示已{status}。"

    # ── /reasoning 命令处理 ──────────────────────────────────

    def _handle_reasoning_command(
        self,
        parts: list[str],
        *,
        on_event: "EventCallback | None" = None,
    ) -> str:
        """处理 /reasoning 命令：控制模型推理过程展示。

        用法：
        - /reasoning          — 切换（toggle）
        - /reasoning on       — 开启
        - /reasoning off      — 关闭
        - /reasoning status   — 查看状态
        """
        e = self._engine
        action = parts[1].strip().lower() if len(parts) >= 2 else ""
        too_many_args = len(parts) > 2

        if too_many_args:
            return "无效参数。用法：/reasoning [on|off|status]。"

        if action == "status":
            status = "开启" if e._show_reasoning else "关闭"
            return f"模型推理过程展示：{status}。"

        if action == "on":
            e._show_reasoning = True
            self._emit_mode_changed(on_event, "show_reasoning", True)
            return "已开启模型推理过程展示。每次模型思考将显示推理内容。"

        if action == "off":
            e._show_reasoning = False
            self._emit_mode_changed(on_event, "show_reasoning", False)
            return "已关闭模型推理过程展示。"

        # 默认 toggle
        e._show_reasoning = not e._show_reasoning
        self._emit_mode_changed(on_event, "show_reasoning", e._show_reasoning)
        status = "开启" if e._show_reasoning else "关闭"
        return f"模型推理过程展示已{status}。"

    # ── W3: /context 命令处理 ──────────────────────────────────

    async def _handle_context_command(self, parts: list[str]) -> str:
        """处理 /context 命令：上下文预算管理。

        用法：
        - /context          — 显示当前预算状态
        - /context status   — 同上
        - /context reset    — 清除自适应 override，恢复默认预算
        """
        e = self._engine
        action = parts[1].strip().lower() if len(parts) >= 2 else "status"
        budget = e._context_budget

        if action in {"status", ""}:
            lines = [
                "**上下文预算状态**",
                f"- 当前上限: {budget.max_tokens:,} tokens",
                f"- 模型推断值: {budget._model_tokens:,} tokens",
            ]
            if budget._override_tokens > 0:
                kind = "自适应" if budget._override_is_adaptive else "用户"
                lines.append(f"- Override: {budget._override_tokens:,} tokens（{kind}）")
            if budget._base_tokens > 0:
                lines.append(f"- 用户固定值: {budget._base_tokens:,} tokens")
            return "\n".join(lines)

        if action == "reset":
            old = budget.max_tokens
            budget.clear_override()
            new = budget.max_tokens
            e._memory.update_context_window(new)
            e._compaction_manager.max_context_tokens = new
            if old != new:
                return f"已清除上下文预算 override：{old:,} → {new:,} tokens。"
            return "当前无活跃 override，预算未变更。"

        return "无效参数。用法：/context [status|reset]。"

    # ── W4: /probe 命令处理 ──────────────────────────────────

    async def _handle_probe_command(self, parts: list[str]) -> str:
        """处理 /probe 命令：模型能力探测。

        用法：
        - /probe context  — 探测当前模型的实际上下文窗口大小
        """
        e = self._engine
        action = parts[1].strip().lower() if len(parts) >= 2 else ""

        if action != "context":
            return "无效参数。用法：/probe context。"

        from excelmanus.model_probe import probe_context_window
        result = await probe_context_window(
            client=e._client,
            model=e.active_model,
            base_url=e._active_base_url,
        )
        if result is None:
            return "探测失败：无法确定模型上下文窗口大小。"

        old = e._context_budget.max_tokens
        e._context_budget.set_override(result, adaptive=True)
        e._memory.update_context_window(result)
        e._compaction_manager.max_context_tokens = result
        return (
            f"探测完成：模型 {e.active_model} 上下文窗口 ≈ {result:,} tokens。\n"
            f"已更新预算：{old:,} → {result:,} tokens。"
        )

    # ── W7: /clear 命令处理 ──────────────────────────────────

    def _handle_clear_command(self) -> str:
        """处理 /clear 命令：清除对话历史但保留会话。"""
        e = self._engine
        e.clear_memory()
        e.set_message_snapshot_index(0)
        return "已清除对话历史。"
