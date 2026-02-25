"""ContextBuilder — 从 AgentEngine 解耦的系统提示词组装组件。

负责管理：
- 系统提示词组装（_prepare_system_prompts_for_request）
- 各类 notice 构建（access/backup/mcp/window/tool_index）
- 工具名列表、窗口感知提示设置
"""

from __future__ import annotations

import hashlib as _hashlib
import json as _json
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from excelmanus.logger import get_logger
from excelmanus.mcp.manager import parse_tool_prefix
from excelmanus.memory import TokenCounter
from excelmanus.task_list import TaskStatus

if TYPE_CHECKING:
    from excelmanus.engine import AgentEngine
    from excelmanus.events import EventCallback
    from excelmanus.skillpacks import SkillMatchResult

_MAX_PLAN_AUTO_CONTINUE = 3  # 计划审批后自动续跑最大次数
_PLAN_CONTEXT_MAX_CHARS = 6000
_MIN_SYSTEM_CONTEXT_CHARS = 256
_SYSTEM_CONTEXT_SHRINK_MARKER = "[上下文已压缩以适配上下文窗口]"

logger = get_logger("context_builder")


class ContextBuilder:
    """系统提示词组装器，从 AgentEngine 搬迁所有 _build_*_notice 和 _prepare_system_prompts。"""

    _TOKEN_COUNT_CACHE_MAX = 16  # fingerprint → token_count LRU 上限

    def __init__(self, engine: "AgentEngine") -> None:
        self._engine = engine
        # O3+O4: 基于内容指纹的 token 计数缓存，避免重复 tiktoken 编码
        self._token_count_cache: dict[str, int] = {}

    def _all_tool_names(self) -> list[str]:
        e = self._engine
        get_tool_names = getattr(e.registry, "get_tool_names", None)
        if callable(get_tool_names):
            return list(get_tool_names())

        get_all_tools = getattr(e.registry, "get_all_tools", None)
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
                    e.registry.call_tool(
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
                    e.registry.call_tool(
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

    def _build_rules_notice(self) -> str:
        """组装用户自定义规则文本，注入 system prompt。"""
        e = self._engine
        rm = getattr(e, "_rules_manager", None)
        if rm is None:
            return ""
        session_id = getattr(e, "_session_id", None)
        try:
            return rm.compose_rules_prompt(session_id)
        except Exception:
            logger.debug("规则注入失败", exc_info=True)
            return ""

    def _build_meta_cognition_notice(self) -> str:
        """条件性注入进展反思提示，帮助 agent 在困境中调整策略。

        灵感来源：Metacognition is All You Need 论文。
        仅在特定退化条件下触发（接近迭代上限 / 连续失败 / 执行守卫已触发），
        否则返回空字符串（零 token 开销）。
        """
        e = self._engine
        state = e.state
        max_iter = e.config.max_iterations
        iteration = state.last_iteration_count
        failures = state.last_failure_count
        successes = state.last_success_count

        parts: list[str] = []
        _MAX_WARNINGS = 2

        # 条件 1（优先级最高）：接近迭代上限（已用 >= 60%）
        if max_iter > 0 and iteration >= max_iter * 0.6:
            parts.append(
                f"⚠️ 接近迭代上限（{iteration}/{max_iter}），"
                "请尽快完成任务或调用 ask_user。"
            )

        # 条件 2：连续失败 >= 3
        if len(parts) < _MAX_WARNINGS and failures >= 3 and successes == 0:
            parts.append(
                f"⚠️ 已连续失败 {failures} 次且无成功调用。建议："
                "1) 检查文件路径和 sheet 名是否正确 "
                "2) 简化操作步骤 "
                "3) 调用 ask_user 确认。"
            )

        # 条件 3：执行守卫曾触发（agent 曾给出建议而不执行）
        if len(parts) < _MAX_WARNINGS and state.execution_guard_fired and not state.has_write_tool_call:
            parts.append(
                "⚠️ 此前已触发执行守卫。请通过工具执行操作，不要仅给出文本建议。"
            )

        # 条件 4（优先级最低）：沉默调用
        silent = state.silent_call_count
        reasoned = state.reasoned_call_count
        if len(parts) < _MAX_WARNINGS and silent > 0 and silent >= reasoned:
            parts.append(
                f"⚠️ 本轮已有 {silent} 次工具调用未附带推理文本。"
                "请遵循 Think-Act 协议：工具调用前至少用 1 句话说明意图。"
                "（thinking 模型：推理可在 thinking 块中完成。）"
            )

        if not parts:
            return ""

        return "## 进展反思\n" + "\n".join(parts)

    @staticmethod
    def _compute_reasoning_level_static(route_result: Any) -> str:
        """根据任务上下文计算推荐推理级别。"""
        if route_result is None:
            return "standard"
        wh = getattr(route_result, "write_hint", "unknown") or "unknown"
        tags = set(getattr(route_result, "task_tags", []) or [])
        if wh == "read_only":
            return "lightweight"
        if tags & {"cross_sheet", "large_data"}:
            return "complete"
        if wh == "may_write":
            return "standard"
        return "lightweight"

    def _build_runtime_metadata_line(self) -> str:
        """生成紧凑的运行时元数据行，让 agent 感知自身状态。

        一行即可让 agent 知道自己是什么模型、当前轮次、权限状态等。
        """
        e = self._engine
        parts: list[str] = [
            f"model={e.active_model}",
            f"turn={e._session_turn}/{e.config.max_iterations}",
            f"write_hint={e.state.current_write_hint}",
            f"fullaccess={'on' if e.full_access_enabled else 'off'}",
            f"backup={'on' if e.workspace.transaction_enabled else 'off'}",
            f"mcp={e.mcp_connected_count}",
            f"subagent={'on' if e._subagent_enabled else 'off'}",
            f"vision={'on' if e._is_vision_capable else 'off'}",
            f"chat_mode={getattr(e, '_current_chat_mode', 'write')}",
            f"skills={len(e._active_skills)}",
        ]
        if e.workspace_manifest is not None:
            parts.append(f"files={e.workspace_manifest.total_files}")
        _route = getattr(e, '_last_route_result', None)
        parts.append(f"reasoning={self._compute_reasoning_level_static(_route)}")
        return "Runtime: " + " | ".join(parts)

    def _prepare_system_prompts_for_request(
        self,
        skill_contexts: list[str],
        *,
        route_result: SkillMatchResult | None = None,
    ) -> tuple[list[str], str | None]:
        """构建用于本轮请求的 system prompts，并在必要时压缩上下文。

        Prompt Cache 优化：静态内容（identity prompt、规则、权限等）放在前面，
        动态内容（runtime_metadata、meta_cognition 等）放在末尾，
        确保 Anthropic prompt caching 的前缀稳定性。
        """
        e = self._engine
        base_prompt = e.memory.system_prompt

        # ── 静态/半静态内容（前缀区域，最大化 cache 命中） ──

        rules_notice = self._build_rules_notice()
        if rules_notice:
            base_prompt = base_prompt + "\n\n" + rules_notice

        access_notice = e._build_access_notice()
        if access_notice:
            base_prompt = base_prompt + "\n\n" + access_notice

        backup_notice = e._build_backup_notice()
        if backup_notice:
            base_prompt = base_prompt + "\n\n" + backup_notice

        cow_path_notice = self._build_cow_path_notice()
        if cow_path_notice:
            base_prompt = base_prompt + "\n\n" + cow_path_notice

        mcp_context = e._build_mcp_context_notice()
        if mcp_context:
            base_prompt = base_prompt + "\n\n" + mcp_context

        workspace_manifest_notice = self._build_workspace_manifest_notice()
        if workspace_manifest_notice:
            base_prompt = base_prompt + "\n\n" + workspace_manifest_notice

        # 注入预取上下文（explorer 子代理预取的文件摘要）
        prefetch_context = getattr(e, "_prefetch_context", "") or ""
        if prefetch_context:
            base_prompt = base_prompt + "\n\n" + prefetch_context

        # ── 半静态内容（轮次级稳定，最大化 Provider prompt cache 前缀） ──

        # 注入任务策略（PromptComposer strategies，同一轮次内不变）
        _strategy_text_captured = ""
        if e._prompt_composer is not None and route_result is not None:
            try:
                from excelmanus.prompt_composer import PromptContext as _PCtx
                _p_ctx = _PCtx(
                    chat_mode=getattr(e, "_current_chat_mode", "write"),
                    write_hint=route_result.write_hint or "unknown",
                    sheet_count=route_result.sheet_count,
                    total_rows=route_result.max_total_rows,
                    task_tags=list(route_result.task_tags),
                    full_access=e.full_access_enabled,
                )
                _strategy_text = e._prompt_composer.compose_strategies_text(_p_ctx)
                if _strategy_text:
                    base_prompt = base_prompt + "\n\n" + _strategy_text
                    _strategy_text_captured = _strategy_text
            except Exception:
                logger.debug("策略注入失败，跳过", exc_info=True)

        # ── 动态内容（放在最末尾，Provider cache 前缀到此为止） ──

        _hook_context_captured = ""
        if e._transient_hook_contexts:
            hook_context = "\n".join(e._transient_hook_contexts).strip()
            e._transient_hook_contexts.clear()
            if hook_context:
                base_prompt = base_prompt + "\n\n## Hook 上下文\n" + hook_context
                _hook_context_captured = hook_context

        # 注入运行时元数据（每轮/每迭代变化，放在所有静态内容之后）
        runtime_line = self._build_runtime_metadata_line()
        base_prompt = base_prompt + "\n\n" + runtime_line

        # 条件性注入进展反思（仅在退化条件下触发，正常情况零开销）
        meta_cognition = self._build_meta_cognition_notice()
        if meta_cognition:
            base_prompt = base_prompt + "\n\n" + meta_cognition

        window_perception_context = self._build_window_perception_notice()
        window_at_tail = e._effective_window_return_mode() != "enriched"
        current_skill_contexts = [
            ctx for ctx in skill_contexts if isinstance(ctx, str) and ctx.strip()
        ]

        # ── 采集提示词注入快照 ──
        _snapshot_components: dict[str, str] = {}
        if rules_notice:
            _snapshot_components["user_rules"] = rules_notice
        if access_notice:
            _snapshot_components["access_notice"] = access_notice
        if backup_notice:
            _snapshot_components["backup_notice"] = backup_notice
        if cow_path_notice:
            _snapshot_components["cow_path_notice"] = cow_path_notice
        if mcp_context:
            _snapshot_components["mcp_context"] = mcp_context
        if workspace_manifest_notice:
            _snapshot_components["workspace_manifest"] = workspace_manifest_notice
        if prefetch_context:
            _snapshot_components["prefetch_context"] = prefetch_context
        if runtime_line:
            _snapshot_components["runtime_metadata"] = runtime_line
        if _strategy_text_captured:
            _snapshot_components["prompt_strategies"] = _strategy_text_captured
        if _hook_context_captured:
            _snapshot_components["hook_context"] = _hook_context_captured
        if window_perception_context:
            _snapshot_components["window_perception_context"] = window_perception_context
        for idx, ctx in enumerate(current_skill_contexts):
            _snapshot_components[f"skill_context_{idx}"] = ctx

        _injection_summary: list[dict[str, Any]] = [
            {"name": name, "chars": len(text)}
            for name, text in _snapshot_components.items()
        ]
        _content_fingerprint = _hashlib.md5(
            _json.dumps(
                _snapshot_components, sort_keys=True, ensure_ascii=False,
            ).encode()
        ).hexdigest()[:12]

        _snapshots = e.state.prompt_injection_snapshots
        _last_fp = _snapshots[-1].get("_fingerprint") if _snapshots else None

        if _last_fp != _content_fingerprint:
            _snapshots.append({
                "session_turn": e._session_turn,
                "summary": _injection_summary,
                "total_chars": sum(len(t) for t in _snapshot_components.values()),
                "components": _snapshot_components,
                "_fingerprint": _content_fingerprint,
            })
        else:
            _snapshots.append({
                "session_turn": e._session_turn,
                "_ref": _content_fingerprint,
            })

        def _compose_prompts() -> list[str]:
            mode = e._effective_system_mode()
            if mode == "merge":
                merged_parts = [base_prompt]
                merged_parts.extend(current_skill_contexts)
                if window_perception_context:
                    if window_at_tail:
                        merged_parts.append(window_perception_context)
                    else:
                        merged_parts.insert(1, window_perception_context)
                return ["\n\n".join(merged_parts)]

            prompts = [base_prompt]
            if window_at_tail:
                prompts.extend(current_skill_contexts)
                if window_perception_context:
                    prompts.append(window_perception_context)
            else:
                if window_perception_context:
                    prompts.append(window_perception_context)
                prompts.extend(current_skill_contexts)
            return prompts

        threshold = max(1, int(e.config.max_context_tokens * 0.9))
        prompts = _compose_prompts()

        # O3+O4: 基于内容指纹的 token 计数缓存
        _cached_count = self._token_count_cache.get(_content_fingerprint)
        if _cached_count is not None:
            total_tokens = _cached_count
        else:
            total_tokens = self._system_prompts_token_count(prompts)
            # LRU 淘汰
            if len(self._token_count_cache) >= self._TOKEN_COUNT_CACHE_MAX:
                self._token_count_cache.pop(next(iter(self._token_count_cache)))
            self._token_count_cache[_content_fingerprint] = total_tokens

        if total_tokens <= threshold:
            return prompts, None

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

    def _has_verification_failed_blocking_task(self) -> bool:
        """检查任务序列中是否有带验证条件的失败任务阻断后续步骤。

        仅当失败任务具有 verification_criteria 时视为验证失败阻断；
        无验证条件的操作失败不阻断（保持现有容错行为）。
        """
        e = self._engine
        task_list = e._task_store.current
        if task_list is None:
            return False
        for item in task_list.items:
            if item.status == TaskStatus.FAILED and item.verification_criteria:
                return True
            if item.status in (TaskStatus.PENDING, TaskStatus.IN_PROGRESS):
                break
        return False

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
            # 验证失败阻断：带验证条件的任务失败时停止续跑
            if self._has_verification_failed_blocking_task():
                logger.info("自动续跑停止：检测到带验证条件的任务失败")
                break
            # 遇到待确认/待回答/待审批时不续跑，交还用户控制
            if e.approval.has_pending():
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
            e.memory.add_user_message(
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

    # 对原始文件本身执行破坏性操作的工具。
    # 这些工具绕过备份重定向 — 审批门禁已提供安全保障，
    # 重定向会静默创建一个用户从未打算使用的一次性备份副本。
    _DESTRUCTIVE_NO_REDIRECT_TOOLS = frozenset({"delete_file"})

    def _redirect_backup_paths(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        """备份模式下重定向工具参数中的文件路径到备份副本。"""
        e = self._engine
        tx = e.transaction
        if not e.workspace.transaction_enabled or tx is None:
            return arguments

        if tool_name in self._DESTRUCTIVE_NO_REDIRECT_TOOLS:
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
                    redirected[field_name] = tx.resolve_read(raw_str)
                else:
                    redirected[field_name] = tx.stage_for_write(raw_str)
            except ValueError:
                pass
        return redirected

    def _build_access_notice(self) -> str:
        """当 fullaccess 关闭时，生成权限限制说明注入 system prompt。"""
        e = self._engine
        if e.full_access_enabled:
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
        """备份模式（workspace transaction）启用时，生成提示词注入。

        注意：此文本必须在整个 turn 内保持稳定（不含动态计数等），
        以确保系统提示前缀一致性，最大化 provider prompt cache 命中率。
        """
        e = self._engine
        if not e.workspace.transaction_enabled or e.transaction is None:
            return ""
        lines = [
            "## ⚠️ 工作区事务模式已启用",
            "所有文件读写操作已自动重定向到 `outputs/backups/` 下的工作副本。",
            "原始文件不会被修改。操作完成后用户可通过 `/backup apply` 将修改应用到原文件。",
        ]
        # 统一版本管理器可用时，追加版本追踪信息
        fvm = getattr(e, "_fvm", None)
        if fvm is not None:
            tracked = fvm.list_all_tracked()
            if tracked:
                lines.append(f"当前有 {len(tracked)} 个文件受版本追踪保护，支持精确回滚到原始版本。")
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

    def _build_cow_path_notice(self) -> str:
        """生成 CoW 路径映射清单，注入 system prompt。

        当会话中存在受保护文件的 CoW 副本时，每轮都将映射清单注入系统提示词，
        确保 agent 始终知道应使用副本路径而非原始路径。
        """
        e = self._engine
        registry = e.state.cow_path_registry
        if not registry:
            return ""
        lines = [
            "## ⚠️ 文件保护路径映射（CoW）",
            "以下原始文件受保护，已自动复制到 outputs/ 目录。",
            "**你必须使用副本路径进行所有后续读取和写入操作，严禁访问原始路径。**",
            "",
            "| 原始路径（禁止访问） | 副本路径（请使用） |",
            "|---|---|",
        ]
        for src, dst in registry.items():
            lines.append(f"| `{src}` | `{dst}` |")
        lines.append("")
        lines.append(
            "如果你在工具参数中使用了原始路径，系统会自动重定向到副本，"
            "但请主动记住并使用副本路径以避免混淆。"
        )
        return "\n".join(lines)

    def _build_workspace_manifest_notice(self) -> str:
        """懒加载构建工作区 Manifest 并生成 system prompt 注入文本。

        优先使用后台预热：若尚未完成则不阻塞当前轮次，直接继续对话。
        注入文本根据文件数量自动选择详细度。
        """
        e = self._engine
        if e.workspace_manifest is None:
            e.start_workspace_manifest_prewarm()
        if e.workspace_manifest is None:
            return ""
        return e.workspace_manifest.get_system_prompt_summary()

    def _build_window_perception_notice(self) -> str:
        """渲染窗口感知系统注入文本。"""
        e = self._engine
        requested_mode = e._requested_window_return_mode()
        return e._window_perception.build_system_notice(
            mode=requested_mode,
            model_id=e.active_model,
        )
    def _build_tool_index_notice(
        self,
        *,
        compact: bool = False,
        max_tools_per_category: int = 8,
    ) -> str:
        """生成工具分类索引，注入 system prompt。

        所有工具始终暴露完整 schema，统一按类别展示。
        """
        from excelmanus.tools.policy import TOOL_CATEGORIES, TOOL_SHORT_DESCRIPTIONS

        _CATEGORY_LABELS: dict[str, str] = {
            "data_read": "数据读取",
            "sheet": "工作表操作",
            "file": "文件操作",
            "code": "代码执行",
            "macro": "声明式复合操作",
            "vision": "图片视觉",
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



    def _set_window_perception_turn_hints(
        self,
        *,
        user_message: str,
        is_new_task: bool,
        task_tags: tuple[str, ...] | None = None,
    ) -> None:
        """设置窗口感知层的当前轮提示。"""
        e = self._engine
        clipped_hint = self._clip_window_hint(user_message)
        e._window_perception.set_turn_hints(
            is_new_task=is_new_task,
            user_intent_summary=clipped_hint,
            agent_recent_output=self._clip_window_hint(self._latest_assistant_text()),
            turn_intent_hint=clipped_hint,
            task_tags=task_tags,
        )

    def _latest_assistant_text(self) -> str:
        """提取最近一条 assistant 文本。"""
        e = self._engine
        for item in reversed(e.memory.get_messages()):
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

