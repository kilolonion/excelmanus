"""ToolHandler 策略实现 — 从 _dispatch_tool_execution if-elif 提取的独立处理器。

每个 Handler 负责一类工具的执行逻辑，通过 can_handle / handle 接口
与 ToolDispatcher 的策略表对接。
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from excelmanus.logger import get_logger, log_tool_call

if TYPE_CHECKING:
    from pathlib import Path

    from excelmanus.engine import AgentEngine
    from excelmanus.engine_core.tool_dispatcher import ToolDispatcher, _ToolExecOutcome

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
    """处理 manage_skills 工具调用：搜索/安装/卸载/查看技能。"""

    _VERSION_CACHE_TTL = 300  # 5 分钟

    def __init__(self, engine: "AgentEngine", dispatcher: "ToolDispatcher") -> None:
        super().__init__(engine, dispatcher)
        self._version_cache: dict[str, tuple[str, float]] = {}  # slug → (version, timestamp)

    def _cache_version(self, slug: str, version: str | None) -> None:
        """缓存 slug→version 映射。"""
        if slug and version:
            self._version_cache[slug] = (version, time.monotonic())

    def _get_cached_version(self, slug: str) -> str | None:
        """获取缓存的版本号，过期返回 None。"""
        entry = self._version_cache.get(slug)
        if entry is None:
            return None
        version, ts = entry
        if time.monotonic() - ts > self._VERSION_CACHE_TTL:
            self._version_cache.pop(slug, None)
            return None
        return version

    def can_handle(self, tool_name: str, **kwargs: Any) -> bool:
        return tool_name == "manage_skills"

    async def handle(self, tool_name, tool_call_id, arguments, **kwargs):
        from excelmanus.engine_core.tool_dispatcher import _ToolExecOutcome

        action = str(arguments.get("action", "")).strip()
        dispatch = {
            "search": self._handle_search,
            "detail": self._handle_detail,
            "install": self._handle_install,
            "list": self._handle_list,
            "uninstall": self._handle_uninstall,
            "update": self._handle_update,
        }
        handler_fn = dispatch.get(action)
        if handler_fn is None:
            result_str = f"不支持的操作: {action}（支持 search/install/detail/list/uninstall/update）"
            log_tool_call(logger, tool_name, arguments, error=result_str)
            return _ToolExecOutcome(result_str=result_str, success=False, error=result_str)

        return await handler_fn(arguments)

    # ── search ────────────────────────────────────────────

    async def _handle_search(self, arguments: dict[str, Any]):
        from excelmanus.engine_core.tool_dispatcher import _ToolExecOutcome

        query = str(arguments.get("query", "")).strip()
        if not query:
            result_str = "参数错误: search 操作需要提供 query 参数。"
            log_tool_call(logger, "manage_skills", arguments, error=result_str)
            return _ToolExecOutcome(result_str=result_str, success=False, error=result_str)

        manager = self._get_manager()
        if manager is None:
            return self._manager_unavailable(arguments)

        try:
            results = await manager.clawhub_search(query, limit=10)
        except Exception as exc:
            result_str = f"ClawHub 搜索失败: {exc}"
            log_tool_call(logger, "manage_skills", arguments, error=result_str)
            return _ToolExecOutcome(result_str=result_str, success=False, error=result_str)

        if not results:
            result_str = f"未找到与 '{query}' 相关的技能。"
            log_tool_call(logger, "manage_skills", arguments, result=result_str)
            return _ToolExecOutcome(result_str=result_str, success=True)

        lines = [f"找到 {len(results)} 个相关技能：\n"]
        for r in results:
            slug = r.get("slug", "")
            display_name = r.get("display_name", slug)
            summary = r.get("summary", "")
            version = r.get("version", "")
            line = f"  - {display_name} (slug={slug}, v{version})"
            if summary:
                line += f" — {summary}"
            lines.append(line)
        # P2: 缓存搜索结果中的版本号，供后续 install 跳过版本解析
        for r in results:
            self._cache_version(r.get("slug", ""), r.get("version"))

        lines.append("\n可使用 action=install, slug=<slug> 安装感兴趣的技能。")
        result_str = "\n".join(lines)
        log_tool_call(logger, "manage_skills", arguments, result=result_str)
        return _ToolExecOutcome(result_str=result_str, success=True)

    # ── detail ────────────────────────────────────────────

    async def _handle_detail(self, arguments: dict[str, Any]):
        from excelmanus.engine_core.tool_dispatcher import _ToolExecOutcome

        slug = str(arguments.get("slug", "")).strip()
        if not slug:
            result_str = "参数错误: detail 操作需要提供 slug 参数。"
            log_tool_call(logger, "manage_skills", arguments, error=result_str)
            return _ToolExecOutcome(result_str=result_str, success=False, error=result_str)

        manager = self._get_manager()
        if manager is None:
            return self._manager_unavailable(arguments)

        try:
            detail = await manager.clawhub_skill_detail(slug)
        except Exception as exc:
            result_str = f"获取技能详情失败: {exc}"
            log_tool_call(logger, "manage_skills", arguments, error=result_str)
            return _ToolExecOutcome(result_str=result_str, success=False, error=result_str)

        parts = [
            f"技能: {detail.get('display_name', slug)}",
            f"标识: {detail.get('slug', slug)}",
            f"版本: {detail.get('latest_version', '未知')}",
        ]
        summary = detail.get("summary", "")
        if summary:
            parts.append(f"简介: {summary}")
        tags = detail.get("tags")
        if tags:
            parts.append(f"标签: {', '.join(tags)}")
        owner = detail.get("owner_display_name") or detail.get("owner_handle")
        if owner:
            parts.append(f"作者: {owner}")
        changelog = detail.get("latest_changelog", "")
        if changelog:
            parts.append(f"更新日志: {changelog}")
        stats = detail.get("stats")
        if isinstance(stats, dict):
            dl = stats.get("downloads")
            if dl is not None:
                parts.append(f"下载量: {dl}")

        result_str = "\n".join(parts)
        log_tool_call(logger, "manage_skills", arguments, result=result_str)
        return _ToolExecOutcome(result_str=result_str, success=True)

    # ── install ───────────────────────────────────────────

    async def _handle_install(self, arguments: dict[str, Any]):
        from excelmanus.engine_core.tool_dispatcher import _ToolExecOutcome

        slug = str(arguments.get("slug", "")).strip()
        if not slug:
            result_str = "参数错误: install 操作需要提供 slug 参数（ClawHub 标识符或 GitHub URL）。"
            log_tool_call(logger, "manage_skills", arguments, error=result_str)
            return _ToolExecOutcome(result_str=result_str, success=False, error=result_str)

        e = self._engine
        manager = self._get_manager()
        if manager is None:
            return self._manager_unavailable(arguments)

        overwrite = bool(arguments.get("overwrite", False))

        # 自动检测来源：GitHub URL vs ClawHub slug
        source = "github_url" if slug.startswith("http") else "clawhub"

        # P2: 从缓存获取版本号，跳过版本解析
        cached_version = self._get_cached_version(slug) if source == "clawhub" else None

        try:
            result = await manager.import_skillpack_async(
                source=source, value=slug, actor="agent", overwrite=overwrite,
                version=cached_version,
            )
        except Exception as exc:
            exc_str = str(exc)
            # 对冲突错误提供更友好的提示
            if "已存在" in exc_str or "conflict" in exc_str.lower():
                result_str = f"技能已存在: {exc_str}\n如需覆盖安装，请传入 overwrite=true。"
            else:
                result_str = f"安装失败: {exc_str}"
            log_tool_call(logger, "manage_skills", arguments, error=result_str)
            return _ToolExecOutcome(result_str=result_str, success=False, error=result_str)

        # 安装成功 → 失效工具缓存，使新技能出现在 skill.name enum 中
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
            e._active_skills = [
                s for s in e._active_skills if s.name != deleted_name
            ]
        if hasattr(e, "_loaded_skill_names"):
            e._loaded_skill_names.pop(deleted_name, None)
        # 清理 ClawHub lockfile 残留条目
        lockfile = getattr(manager, "_clawhub_lockfile", None)
        if lockfile is not None:
            try:
                lockfile.remove(deleted_name)
            except Exception:
                logger.debug("清理 ClawHub lockfile 条目失败: %s", deleted_name, exc_info=True)

        result_str = f"OK 技能 '{deleted_name}' 已卸载。"
        log_tool_call(logger, "manage_skills", arguments, result=result_str)
        return _ToolExecOutcome(result_str=result_str, success=True)

    # ── update ─────────────────────────────────────────────

    async def _handle_update(self, arguments: dict[str, Any]):
        from excelmanus.engine_core.tool_dispatcher import _ToolExecOutcome

        e = self._engine
        manager = self._get_manager()
        if manager is None:
            return self._manager_unavailable(arguments)

        slug = str(arguments.get("slug", "")).strip()

        # 无 slug → 检查可用更新
        if not slug:
            try:
                updates = await manager.clawhub_check_updates()
            except Exception as exc:
                result_str = f"检查更新失败: {exc}"
                log_tool_call(logger, "manage_skills", arguments, error=result_str)
                return _ToolExecOutcome(result_str=result_str, success=False, error=result_str)

            if not updates:
                result_str = "所有已安装的 ClawHub 技能均为最新版本。"
                log_tool_call(logger, "manage_skills", arguments, result=result_str)
                return _ToolExecOutcome(result_str=result_str, success=True)

            available = [u for u in updates if u.get("update_available")]
            if not available:
                result_str = "所有已安装的 ClawHub 技能均为最新版本。"
                log_tool_call(logger, "manage_skills", arguments, result=result_str)
                return _ToolExecOutcome(result_str=result_str, success=True)

            lines = [f"发现 {len(available)} 个可更新技能：\n"]
            for u in available:
                lines.append(
                    f"  - {u.get('slug')} : {u.get('installed_version', '?')} → {u.get('latest_version', '?')}"
                )
            lines.append("\n可使用 action=update, slug=<slug> 更新指定技能。")
            result_str = "\n".join(lines)
            log_tool_call(logger, "manage_skills", arguments, result=result_str)
            return _ToolExecOutcome(result_str=result_str, success=True)

        # 有 slug → 执行更新
        try:
            results = await manager.clawhub_update(slug=slug)
        except Exception as exc:
            result_str = f"更新失败: {exc}"
            log_tool_call(logger, "manage_skills", arguments, error=result_str)
            return _ToolExecOutcome(result_str=result_str, success=False, error=result_str)

        e._tools_cache = None

        if results and results[0].get("success"):
            version = results[0].get("version", "")
            result_str = f"OK 技能 '{slug}' 已更新到 v{version}。"
        else:
            error = results[0].get("error", "未知错误") if results else "未知错误"
            result_str = f"更新失败: {error}"
            log_tool_call(logger, "manage_skills", arguments, error=result_str)
            return _ToolExecOutcome(result_str=result_str, success=False, error=result_str)

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
            e.record_workspace_write_action()

        # 子代理审批问题：阻塞等待用户决策
        if (
            not success
            and sub_result is not None
            and sub_result.pending_approval_id is not None
        ):
            import asyncio

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
            undoable=e.approval.is_undoable_tool(tool_name),
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
        return self._engine.approval.is_high_risk_tool(tool_name)

    async def handle(self, tool_name, tool_call_id, arguments, *, tool_scope=None, on_event=None, iteration=0, route_result=None, skip_high_risk_approval_by_hook=False):
        from excelmanus.engine_core.tool_dispatcher import _ToolExecOutcome

        e = self._engine
        from excelmanus.security.policy import resolve_approval_policy

        approval = resolve_approval_policy(e)
        if approval == "ask" and not skip_high_risk_approval_by_hook:
            pending = e.approval.create_pending(tool_name=tool_name, arguments=arguments, tool_scope=tool_scope)
            e.emit_pending_approval_event(pending=pending, on_event=on_event, iteration=iteration, tool_call_id=tool_call_id)
            result_str = e.format_pending_prompt(pending)
            log_tool_call(logger, tool_name, arguments, result=result_str)
            return _ToolExecOutcome(
                result_str=result_str, success=True,
                pending_approval=True, approval_id=pending.approval_id,
            )
        # never：高危自动过，不存在无人应答却放行。
        elif e.approval.is_mcp_tool(tool_name):
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
        else:
            result_value, audit_record = await e.execute_tool_with_audit(
                tool_name=tool_name, arguments=arguments, tool_scope=tool_scope,
                approval_id=e.approval.new_approval_id(), created_at_utc=e.approval.utc_now(),
                undoable=e.approval.is_undoable_tool(tool_name),
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
        from excelmanus.engine_core.tool_dispatcher import _ToolExecOutcome
        from excelmanus.security.code_policy import extract_excel_targets

        e = self._engine
        dispatcher = self._dispatcher

        _sandbox_tier = analysis.tier.value
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
        _has_ast_write = any(t.operation == "write" for t in extract_excel_targets(code))
        if (audit_record is not None and audit_record.changes) or _has_published or _has_ast_write:
            e.record_write_action()
            _state = getattr(e, "_state", None)
            if _state is not None:
                _ast_paths = ", ".join(
                    t.file_path for t in extract_excel_targets(code)
                    if t.operation == "write" and t.file_path != "<variable>"
                ) if _has_ast_write else ""
                _file_path = _published_paths or _ast_paths
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
            success=structured.success,
            error=structured.error.message if structured.error else None,
            audit_record=audit_record,
            structured=structured,
        )
