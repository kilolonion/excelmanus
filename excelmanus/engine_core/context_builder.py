"""ContextBuilder — 从 AgentEngine 解耦的系统提示词组装组件。

负责管理：
- 系统提示词组装（_prepare_system_prompts_for_request）
- 各类 notice 构建（access/backup/mcp/window/tool_index）
- 工具名列表、窗口感知提示设置
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from excelmanus.logger import get_logger
from excelmanus.mcp.manager import parse_tool_prefix
from excelmanus.memory import TokenCounter
from excelmanus.task_list import TaskStatus

if TYPE_CHECKING:
    from excelmanus.events import EventCallback
    from excelmanus.skillpacks import SkillMatchResult

_MAX_PLAN_AUTO_CONTINUE = 3  # 计划审批后自动续跑最大次数
_PLAN_CONTEXT_MAX_CHARS = 6000
_MIN_SYSTEM_CONTEXT_CHARS = 256
_SYSTEM_CONTEXT_SHRINK_MARKER = "[上下文已压缩以适配上下文窗口]"

logger = get_logger("context_builder")


class ContextBuilder:
    """系统提示词组装器，从 AgentEngine 搬迁所有 _build_*_notice 和 _prepare_system_prompts。"""

    def __init__(self, engine: Any) -> None:
        self._engine = engine

    def _all_tool_names(self) -> list[str]:
        e = self._engine
        get_tool_names = getattr(e._registry, "get_tool_names", None)
        if callable(get_tool_names):
            return list(get_tool_names())

        get_all_tools = getattr(e._registry, "get_all_tools", None)
        if callable(get_all_tools):
            return [tool.name for tool in get_all_tools()]

        return []

    def _focus_window_refill_reader(
        self,
        *,
        file_path: str,
        sheet_name: str,
        range_ref: str,
    ) -> dict[str, Any]:
        """focus_window 自动补读回调。"""
        e = self._engine
        if not file_path or not sheet_name or not range_ref:
            return {"success": False, "error": "缺少 file_path/sheet_name/range 参数"}

        all_tools = self._all_tool_names()
        read_sheet_tools: list[str] = []
        for tool_name in all_tools:
            if not tool_name.startswith("mcp_"):
                continue
            try:
                _, origin_name = parse_tool_prefix(tool_name)
            except ValueError:
                continue
            if origin_name == "read_sheet":
                read_sheet_tools.append(tool_name)

        for tool_name in read_sheet_tools:
            try:
                arguments = {
                    "file_path": file_path,
                    "sheet_name": sheet_name,
                    "range": range_ref,
                }
                result_text = str(
                    e._registry.call_tool(
                        tool_name,
                        arguments,
                    )
                )
                return {
                    "success": True,
                    "tool_name": tool_name,
                    "arguments": arguments,
                    "result_text": result_text,
                }
            except Exception:
                continue

        if "read_excel" in all_tools:
            arguments: dict[str, Any] = {"file_path": file_path, "sheet_name": sheet_name}
            try:
                from openpyxl.utils.cell import range_boundaries

                _, min_row, _, max_row = range_boundaries(range_ref)
                arguments["max_rows"] = max(1, int(max_row) - int(min_row) + 1)
            except Exception:
                pass
            try:
                result_text = str(
                    e._registry.call_tool(
                        "read_excel",
                        arguments,
                    )
                )
                return {
                    "success": True,
                    "tool_name": "read_excel",
                    "arguments": arguments,
                    "result_text": result_text,
                }
            except Exception as exc:
                return {"success": False, "error": f"补读失败: {exc}"}

        return {"success": False, "error": "未找到可用读取工具（read_sheet/read_excel）"}


    @staticmethod
    def _system_prompts_token_count(system_prompts: Sequence[str]) -> int:
        total = 0
        for prompt in system_prompts:
            total += TokenCounter.count_message({"role": "system", "content": prompt})
        return total

    @staticmethod
    def _shrink_context_text(text: str) -> str:
        normalized = (text or "").strip()
        if not normalized:
            return ""
        if len(normalized) <= _MIN_SYSTEM_CONTEXT_CHARS:
            return ""
        keep_chars = max(_MIN_SYSTEM_CONTEXT_CHARS, len(normalized) // 2)
        shrinked = normalized[:keep_chars].rstrip()
        if _SYSTEM_CONTEXT_SHRINK_MARKER in shrinked:
            return shrinked
        return f"{shrinked}\n{_SYSTEM_CONTEXT_SHRINK_MARKER}"

    @staticmethod
    def _minimize_skill_context(text: str) -> str:
        lines = [line for line in str(text or "").splitlines() if line.strip()]
        if not lines:
            return ""
        head = lines[0]
        second = lines[1] if len(lines) > 1 else ""
        minimal_parts = [head]
        if second:
            minimal_parts.append(second)
        minimal_parts.append("[Skillpack 正文已省略以适配上下文窗口]")
        return "\n".join(minimal_parts)

    def _prepare_system_prompts_for_request(
        self,
        skill_contexts: list[str],
        *,
        route_result: SkillMatchResult | None = None,
    ) -> tuple[list[str], str | None]:
        """构建用于本轮请求的 system prompts，并在必要时压缩上下文。"""
        e = self._engine
        base_prompt = e._memory.system_prompt

        access_notice = e._build_access_notice()
        if access_notice:
            base_prompt = base_prompt + "\n\n" + access_notice

        backup_notice = e._build_backup_notice()
        if backup_notice:
            base_prompt = base_prompt + "\n\n" + backup_notice

        mcp_context = e._build_mcp_context_notice()
        if mcp_context:
            base_prompt = base_prompt + "\n\n" + mcp_context

        # 注入工具索引
        _tool_index = e._build_tool_index_notice(
            compact=False,
        )
        if _tool_index:
            base_prompt = base_prompt + "\n\n" + _tool_index

        # 注入任务策略（PromptComposer strategies）
        if e._prompt_composer is not None and route_result is not None:
            try:
                from excelmanus.prompt_composer import PromptContext as _PCtx
                _p_ctx = _PCtx(
                    write_hint=route_result.write_hint or "unknown",
                    sheet_count=route_result.sheet_count,
                    total_rows=route_result.max_total_rows,
                    task_tags=list(route_result.task_tags),
                )
                _strategy_text = e._prompt_composer.compose_strategies_text(_p_ctx)
                if _strategy_text:
                    base_prompt = base_prompt + "\n\n" + _strategy_text
            except Exception:
                logger.debug("策略注入失败，跳过", exc_info=True)

        if e._transient_hook_contexts:
            hook_context = "\n".join(e._transient_hook_contexts).strip()
            e._transient_hook_contexts.clear()
            if hook_context:
                base_prompt = base_prompt + "\n\n## Hook 上下文\n" + hook_context

        approved_plan_context = self._build_approved_plan_context_notice()
        window_perception_context = self._build_window_perception_notice()
        window_at_tail = e._effective_window_return_mode() != "enriched"
        current_skill_contexts = [
            ctx for ctx in skill_contexts if isinstance(ctx, str) and ctx.strip()
        ]

        def _compose_prompts() -> list[str]:
            mode = e._effective_system_mode()
            if mode == "merge":
                merged_parts = [base_prompt]
                if approved_plan_context:
                    merged_parts.append(approved_plan_context)
                merged_parts.extend(current_skill_contexts)
                if window_perception_context:
                    if window_at_tail:
                        merged_parts.append(window_perception_context)
                    else:
                        merged_parts.insert(2 if approved_plan_context else 1, window_perception_context)
                return ["\n\n".join(merged_parts)]

            prompts = [base_prompt]
            if approved_plan_context:
                prompts.append(approved_plan_context)
            if window_at_tail:
                prompts.extend(current_skill_contexts)
                if window_perception_context:
                    prompts.append(window_perception_context)
            else:
                if window_perception_context:
                    prompts.append(window_perception_context)
                prompts.extend(current_skill_contexts)
            return prompts

        threshold = max(1, int(e._config.max_context_tokens * 0.9))
        prompts = _compose_prompts()
        total_tokens = self._system_prompts_token_count(prompts)
        if total_tokens <= threshold:
            return prompts, None

        if approved_plan_context:
            approved_plan_context = self._shrink_context_text(approved_plan_context)
            prompts = _compose_prompts()
            total_tokens = self._system_prompts_token_count(prompts)
            if total_tokens <= threshold:
                return prompts, None
            approved_plan_context = ""

        if window_perception_context:
            window_perception_context = self._shrink_context_text(window_perception_context)
            prompts = _compose_prompts()
            total_tokens = self._system_prompts_token_count(prompts)
            if total_tokens <= threshold:
                return prompts, None
            window_perception_context = ""

        for idx in range(len(current_skill_contexts) - 1, -1, -1):
            minimized = self._minimize_skill_context(current_skill_contexts[idx])
            if minimized and minimized != current_skill_contexts[idx]:
                current_skill_contexts[idx] = minimized
                prompts = _compose_prompts()
                total_tokens = self._system_prompts_token_count(prompts)
                if total_tokens <= threshold:
                    return prompts, None

        while current_skill_contexts:
            current_skill_contexts.pop()
            prompts = _compose_prompts()
            total_tokens = self._system_prompts_token_count(prompts)
            if total_tokens <= threshold:
                return prompts, None

        if self._system_prompts_token_count(prompts) > threshold:
            return [], (
                "系统上下文过长，已无法在当前上下文窗口内继续执行。"
                "请减少附加上下文或拆分任务后重试。"
            )
        return prompts, None


    def _build_approved_plan_context_notice(self) -> str:
        """注入已批准计划上下文 + 任务清单状态 + 自主执行指令。"""
        e = self._engine
        context = (e._approved_plan_context or "").strip()
        if not context:
            return ""
        if len(context) > _PLAN_CONTEXT_MAX_CHARS:
            truncated = context[:_PLAN_CONTEXT_MAX_CHARS]
            context = (
                f"{truncated}\n"
                f"[计划上下文已截断，原始长度: {len(e._approved_plan_context or '')} 字符]"
            )

        parts = [f"## 已批准计划上下文\n{context}"]

        # 注入任务清单当前状态
        task_status = self._build_task_list_status_notice()
        if task_status:
            parts.append(task_status)

        # 自主执行指令
        parts.append(
            "【自主执行指令】计划已获用户批准，你必须自主连续执行所有子任务直到全部完成。"
            "严禁在中间步骤停下来等待用户发送「继续」或确认。"
            "每完成一个子任务后，立即用 task_update 标记完成，然后继续执行下一个。"
            "仅在遇到需要用户决策的歧义或 accept 门禁时才暂停。"
        )
        return "\n\n".join(parts)

    def _build_task_list_status_notice(self) -> str:
        """构建当前任务清单状态摘要，用于注入 system prompt。"""
        e = self._engine
        task_list = e._task_store.current
        if task_list is None:
            return ""
        lines = [f"### 任务清单状态「{task_list.title}」"]
        for idx, item in enumerate(task_list.items):
            status_icon = {
                TaskStatus.PENDING: "🔵",
                TaskStatus.IN_PROGRESS: "🟡",
                TaskStatus.COMPLETED: "✅",
                TaskStatus.FAILED: "❌",
            }.get(item.status, "⬜")
            lines.append(f"- {status_icon} #{idx} {item.title} ({item.status.value})")
        return "\n".join(lines)

    def _has_incomplete_tasks(self) -> bool:
        """检查任务清单是否存在未完成的子任务。"""
        e = self._engine
        task_list = e._task_store.current
        if task_list is None:
            return False
        return any(
            item.status in (TaskStatus.PENDING, TaskStatus.IN_PROGRESS)
            for item in task_list.items
        )

    async def _auto_continue_task_loop(
        self,
        route_result: "SkillMatchResult",
        on_event: EventCallback | None,
        initial_result: ChatResult,
    ) -> ChatResult:
        """计划审批后自动续跑：若任务清单仍有未完成子任务，自动注入续跑消息。"""
        from excelmanus.engine import ChatResult
        e = self._engine
        result = initial_result
        for attempt in range(_MAX_PLAN_AUTO_CONTINUE):
            if not self._has_incomplete_tasks():
                break
            # 遇到待确认/待回答/待审批时不续跑，交还用户控制
            if e._approval.has_pending():
                break
            if e._question_flow.has_pending():
                break
            if e._pending_plan is not None:
                break

            logger.info(
                "自动续跑 %d/%d：任务清单仍有未完成子任务",
                attempt + 1,
                _MAX_PLAN_AUTO_CONTINUE,
            )
            e._memory.add_user_message(
                "请继续执行剩余的未完成子任务，直到全部完成。"
            )
            e._set_window_perception_turn_hints(
                user_message="继续执行剩余子任务",
                is_new_task=False,
            )
            resumed = await e._tool_calling_loop(route_result, on_event)
            result = ChatResult(
                reply=f"{result.reply}\n\n{resumed.reply}",
                tool_calls=list(result.tool_calls) + list(resumed.tool_calls),
                iterations=result.iterations + resumed.iterations,
                truncated=resumed.truncated,
                prompt_tokens=result.prompt_tokens + resumed.prompt_tokens,
                completion_tokens=result.completion_tokens + resumed.completion_tokens,
                total_tokens=result.total_tokens + resumed.total_tokens,
            )
        return result

    def _redirect_backup_paths(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        """备份模式下重定向工具参数中的文件路径到备份副本。"""
        e = self._engine
        if not e._backup_enabled or e._backup_manager is None:
            return arguments

        from excelmanus.tools.policy import (
            AUDIT_TARGET_ARG_RULES_ALL,
            AUDIT_TARGET_ARG_RULES_FIRST,
            READ_ONLY_SAFE_TOOLS,
        )

        path_fields: list[str] = []
        all_fields = AUDIT_TARGET_ARG_RULES_ALL.get(tool_name)
        if all_fields is not None:
            path_fields.extend(all_fields)
        else:
            first_fields = AUDIT_TARGET_ARG_RULES_FIRST.get(tool_name)
            if first_fields is not None:
                path_fields.extend(first_fields)

        if tool_name in READ_ONLY_SAFE_TOOLS:
            for key in ("file_path", "path", "directory"):
                if key in arguments and key not in path_fields:
                    path_fields.append(key)

        if not path_fields:
            return arguments

        redirected = dict(arguments)
        for field_name in path_fields:
            raw = arguments.get(field_name)
            if raw is None:
                continue
            raw_str = str(raw).strip()
            if not raw_str:
                continue
            try:
                if tool_name in READ_ONLY_SAFE_TOOLS:
                    redirected[field_name] = e._backup_manager.resolve_path(raw_str)
                else:
                    redirected[field_name] = e._backup_manager.ensure_backup(raw_str)
            except ValueError:
                pass  # 工作区外路径，不重定向
        return redirected

    def _build_access_notice(self) -> str:
        """当 fullaccess 关闭时，生成权限限制说明注入 system prompt。"""
        e = self._engine
        if e._full_access_enabled:
            return ""
        restricted = e._restricted_code_skillpacks
        if not restricted:
            return ""
        skill_list = "、".join(sorted(restricted))
        return (
            f"【权限提示】当前 fullaccess 权限处于关闭状态。"
            f"以下技能需要 fullaccess 权限才能激活：{skill_list}。"
            f"注意：run_code 工具已配备代码策略引擎（自动风险分级 + 运行时沙盒），"
            f"安全代码（GREEN/YELLOW 等级）可直接使用，无需 fullaccess 权限。"
            f"仅涉及高风险操作（如 subprocess、exec）的代码需要用户确认。"
        )

    def _build_backup_notice(self) -> str:
        """备份模式启用时，生成提示词注入。"""
        e = self._engine
        if not e._backup_enabled or e._backup_manager is None:
            return ""
        backups = e._backup_manager.list_backups()
        count = len(backups)
        lines = [
            "## ⚠️ 备份沙盒模式已启用",
            "所有文件读写操作已自动重定向到 `outputs/backups/` 下的工作副本。",
            "原始文件不会被修改。操作完成后用户可通过 `/backup apply` 将修改应用到原文件。",
        ]
        if count > 0:
            lines.append(f"当前已管理 {count} 个备份文件。")
        return "\n".join(lines)

    def _build_mcp_context_notice(self) -> str:
        """生成已连接 MCP Server 的概要信息，注入 system prompt。"""
        e = self._engine
        servers = e._mcp_manager.get_server_info()
        if not servers:
            return ""
        lines = ["## MCP 扩展能力"]
        for srv in servers:
            name = srv["name"]
            tool_count = srv.get("tool_count", 0)
            tool_names = srv.get("tools", [])
            tools_str = "、".join(tool_names) if tool_names else "无"
            lines.append(f"- **{name}**（{tool_count} 个工具）：{tools_str}")
        lines.append(
            "以上 MCP 工具已注册，工具名带 `mcp_{server}_` 前缀，可直接调用。"
            "当用户询问你有哪些 MCP 或外部能力时，据此如实回答。"
        )
        return "\n".join(lines)

    def _build_window_perception_notice(self) -> str:
        """渲染窗口感知系统注入文本。"""
        e = self._engine
        requested_mode = e._requested_window_return_mode()
        return e._window_perception.build_system_notice(
            mode=requested_mode,
            model_id=e._active_model,
        )
    def _build_tool_index_notice(
        self,
        *,
        compact: bool = False,
        max_tools_per_category: int = 8,
    ) -> str:
        """生成工具分类索引，注入 system prompt。

        v5.1: 所有工具始终暴露完整 schema，统一按类别展示。
        """
        from excelmanus.tools.policy import TOOL_CATEGORIES, TOOL_SHORT_DESCRIPTIONS

        _CATEGORY_LABELS: dict[str, str] = {
            "data_read": "数据读取",
            "data_write": "数据写入",
            "format": "格式化",
            "advanced_format": "高级格式",
            "chart": "图表",
            "sheet": "工作表操作",
            "file": "文件操作",
            "code": "代码执行",
        }

        limit = max(1, int(max_tools_per_category))
        registered = set(self._all_tool_names())
        category_lines: list[str] = []

        def _format_tool_list(tools: Sequence[str], *, with_desc: bool = False) -> str:
            visible = list(tools[:limit])
            hidden = max(0, len(tools) - len(visible))
            if not visible:
                return ""
            if with_desc:
                parts_list = []
                for t in visible:
                    desc = TOOL_SHORT_DESCRIPTIONS.get(t)
                    parts_list.append(f"{t}({desc})" if desc else t)
                text = ", ".join(parts_list)
            else:
                text = ", ".join(visible)
            if hidden > 0:
                text += f" (+{hidden})"
            return text

        for cat, tools in TOOL_CATEGORIES.items():
            label = _CATEGORY_LABELS.get(cat, cat)
            available = [t for t in tools if t in registered]
            if not available:
                continue
            code_suffix = " [需 fullaccess]" if cat == "code" else ""
            line = _format_tool_list(available, with_desc=True)
            if line:
                category_lines.append(f"- {label}：{line}{code_suffix}")

        if not category_lines:
            return ""

        parts: list[str] = ["## 工具索引"]
        parts.append("可用工具（所有工具参数已完整可见，直接调用）：")
        parts.extend(category_lines)
        parts.append(
            "\n⚠️ 写入类任务（公式、数据、格式）必须调用工具执行，"
            "不得以文本建议替代实际写入操作。"
        )
        return "\n".join(parts)



    def _set_window_perception_turn_hints(self, *, user_message: str, is_new_task: bool) -> None:
        """设置窗口感知层的当前轮提示。"""
        e = self._engine
        clipped_hint = self._clip_window_hint(user_message)
        e._window_perception.set_turn_hints(
            is_new_task=is_new_task,
            user_intent_summary=clipped_hint,
            agent_recent_output=self._clip_window_hint(self._latest_assistant_text()),
            turn_intent_hint=clipped_hint,
        )

    def _latest_assistant_text(self) -> str:
        """提取最近一条 assistant 文本。"""
        e = self._engine
        for item in reversed(e._memory.get_messages()):
            if str(item.get("role", "")).strip() != "assistant":
                continue
            from excelmanus.engine import _message_content_to_text
            text = _message_content_to_text(item.get("content"))
            if text.strip():
                return text.strip()
        return ""

    @staticmethod
    def _clip_window_hint(text: str, *, max_chars: int = 200) -> str:
        normalized = " ".join(str(text or "").split())
        if len(normalized) <= max_chars:
            return normalized
        return normalized[:max_chars]

