"""ContextBuilder — 从 AgentEngine 解耦的系统提示词组装组件。

负责管理：
- 系统提示词组装（_prepare_system_prompts_for_request）
- 各类 notice 构建（access/backup/mcp/tool_index）
- 工具名列表
"""

from __future__ import annotations

import hashlib as _hashlib
import json as _json
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from excelmanus.logger import get_logger
from excelmanus.memory import TokenCounter
from excelmanus.task_list import TaskStatus

if TYPE_CHECKING:
    from excelmanus.engine import AgentEngine
    from excelmanus.engine_types import ChatResult
    from excelmanus.events import EventCallback
    from excelmanus.skillpacks import SkillMatchResult

_PLAN_CONTEXT_MAX_CHARS = 6000
_MIN_SYSTEM_CONTEXT_CHARS = 256
_SYSTEM_CONTEXT_SHRINK_MARKER = "[上下文已压缩以适配上下文窗口]"

logger = get_logger("context_builder")


def build_ref_graph_notice(cache: Any) -> str:
    """从 RefCache 构建引用关系图 notice 文本（模块级，方便单元测试）。"""
    all_tier1 = cache.all_tier1() if hasattr(cache, "all_tier1") else {}
    if not all_tier1:
        return ""
    summaries: list[str] = []
    for _fp, index in all_tier1.items():
        text = index.render_summary()
        if text:
            summaries.append(text)
    if not summaries:
        return ""
    return "### 引用关系图\n" + "\n".join(summaries)


class ContextBuilder:
    """系统提示词组装器，从 AgentEngine 搬迁所有 _build_*_notice 和 _prepare_system_prompts。"""

    _TOKEN_COUNT_CACHE_MAX = 16  # fingerprint → token_count LRU 上限

    def __init__(self, engine: "AgentEngine") -> None:
        self._engine = engine
        # O3+O4: 基于内容指纹的 token 计数缓存，避免重复 tiktoken 编码
        self._token_count_cache: dict[str, int] = {}
        # C2: 轮次级静态 notice 缓存，同一 session_turn 内不重复构建
        self._turn_notice_cache: dict[str, str] = {}
        self._turn_notice_cache_key: int = -1

    def _all_tool_names(self) -> list[str]:
        e = self._engine
        get_tool_names = getattr(e.registry, "get_tool_names", None)
        if callable(get_tool_names):
            return list(get_tool_names())

        get_all_tools = getattr(e.registry, "get_all_tools", None)
        if callable(get_all_tools):
            return [tool.name for tool in get_all_tools()]

        return []


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

    def _build_channel_notice(self) -> str:
        """构建渠道适配提示词。Bot 渠道注入格式/交互指南，Web 返回空。"""
        e = self._engine
        channel = getattr(e, "_channel_context", None)
        if not channel or channel == "web":
            return ""
        try:
            from excelmanus.channels.channel_profile import build_channel_notice
            return build_channel_notice(channel)
        except Exception:
            logger.debug("渠道提示词注入失败", exc_info=True)
            return ""

    @property
    def _channel_cache_key(self) -> str:
        """返回含渠道标识的缓存 key，防止不同渠道间缓存污染。"""
        channel = getattr(self._engine, "_channel_context", None) or "web"
        return f"channel:{channel}"

    # 推理级别数值映射，用于比较和升降级
    _REASONING_LEVEL_ORDER: dict[str, int] = {
        "lightweight": 0,
        "standard": 1,
        "complete": 2,
    }

    # 推理字符数阈值（每个工具调用的平均字符数）：低于此值视为偏简略
    _REASONING_CHARS_THRESHOLDS: dict[str, int] = {
        "lightweight": 5,
        "standard": 30,
        "complete": 60,
    }

    @staticmethod
    def _compute_reasoning_level_static(route_result: Any) -> str:
        """根据任务上下文计算推荐推理级别（静态版本，兼容外部调用）。"""
        if route_result is None:
            return "standard"
        chat_mode = getattr(route_result, "chat_mode", None)
        if chat_mode in ("read", "plan"):
            return "lightweight"
        return "standard" if chat_mode == "write" else "lightweight"

    def _compute_reasoning_level(self, route_result: Any) -> str:
        """根据任务上下文 + 运行时状态动态计算推荐推理级别。

        在静态路由信号基础上，叠加运行时上下文进行升级：
        - 失败后需要更深入分析 → 升级
        - 任务接近尾声需要决策 → 升级
        - 多技能激活暗示复杂度 → 升级
        结果写入 state.recommended_reasoning_level 供闭环检测使用。
        """
        base = self._compute_reasoning_level_static(route_result)
        level = self._REASONING_LEVEL_ORDER.get(base, 1)

        e = self._engine
        state = e.state

        # 升级信号 1：上一迭代有失败 → 至少 standard（需要分析原因）
        if state.last_failure_count > 0 and level < 1:
            level = 1

        # 升级信号 2：连续失败 ≥ 2 → complete（需要深度分析和策略调整）
        if state.last_failure_count >= 2 and state.last_success_count == 0:
            level = 2

        # 升级信号 3：多技能激活暗示复杂任务 → 至少 standard
        if len(e._active_skills) >= 2 and level < 1:
            level = 1

        # 升级信号 4：接近迭代上限（≥50%）→ 至少 standard（需要收敛决策）
        max_iter = e.config.max_iterations
        iteration = state.last_iteration_count
        if max_iter > 0 and iteration >= max_iter * 0.5 and level < 1:
            level = 1

        # 降级信号：不做降级——静态基线是下限，运行时只升不降

        level_names = ["lightweight", "standard", "complete"]
        result = level_names[min(level, 2)]

        # 写入 state 供闭环检测使用
        state.recommended_reasoning_level = result
        return result

    def _build_stable_system_prompt(self) -> str:
        """构建仅含稳定前缀的 system prompt（用于 cache 预热等场景）。

        包含: identity + rules + channel + access + backup。
        MCP 工具出现在 tools 数组，不在系统提示词里教用法。
        """
        e = self._engine
        prompt = e.memory.system_prompt

        _turn = e._session_turn
        if _turn != self._turn_notice_cache_key:
            self._turn_notice_cache.clear()
            self._turn_notice_cache_key = _turn
        _nc = self._turn_notice_cache

        def _cached_notice(key: str, builder: Any) -> str:
            val = _nc.get(key)
            if val is not None:
                return val
            val = builder()
            _nc[key] = val
            return val

        rules_notice = _cached_notice("rules", self._build_rules_notice)
        if rules_notice:
            prompt = prompt + "\n\n" + rules_notice

        channel_notice = _cached_notice(self._channel_cache_key, self._build_channel_notice)
        if channel_notice:
            prompt = prompt + "\n\n" + channel_notice

        access_notice = _cached_notice("access", self._build_access_notice)
        if access_notice:
            prompt = prompt + "\n\n" + access_notice

        backup_notice = _cached_notice("backup", self._build_backup_notice)
        if backup_notice:
            prompt = prompt + "\n\n" + backup_notice

        return prompt

    def _prepare_system_prompts_for_request(
        self,
        skill_contexts: list[str],
        *,
        route_result: SkillMatchResult | None = None,
    ) -> tuple[list[str], str | None]:
        """构建本步请求的 system prompts。

        稳定前缀始终作为第一条 system 消息。薄文件列表、策略段、任务清单
        和技能上下文只在快照指纹变化时追加；一次性 hook 有内容时单独追加。
        """
        e = self._engine

        _turn = e._session_turn
        if _turn != self._turn_notice_cache_key:
            self._turn_notice_cache.clear()
            self._turn_notice_cache_key = _turn

        stable_prompt = self._build_stable_system_prompt()
        self._compute_reasoning_level(
            route_result if route_result is not None else getattr(e, "_last_route_result", None)
        )

        file_registry_notice = self._build_file_registry_notice()
        strategy_text = ""
        if e._prompt_composer is not None and route_result is not None:
            try:
                from excelmanus.prompt_composer import PromptContext as _PCtx
                _chat_mode = getattr(e, "_current_chat_mode", "write")
                strategy_text = e._prompt_composer.compose_strategies_text(
                    _PCtx(
                        chat_mode=_chat_mode,
                        full_access=e.full_access_enabled,
                    ),
                    variables=getattr(e, "_runtime_vars", None),
                ) or ""
            except Exception:
                logger.debug("策略注入失败，跳过", exc_info=True)
                strategy_text = ""

        hook_notice = ""
        if e._transient_hook_contexts:
            hook_context = "\n".join(e._transient_hook_contexts).strip()
            e._transient_hook_contexts.clear()
            if hook_context:
                hook_notice = "## Hook 上下文\n" + hook_context

        task_plan_notice = self._build_task_plan_notice()
        current_skill_contexts = [
            ctx for ctx in skill_contexts if isinstance(ctx, str) and ctx.strip()
        ]

        snapshot_components: dict[str, str] = {}
        if file_registry_notice:
            snapshot_components["file_registry_notice"] = file_registry_notice
        if strategy_text:
            snapshot_components["prompt_strategies"] = strategy_text
        if task_plan_notice:
            snapshot_components["task_plan_notice"] = task_plan_notice
        for idx, ctx in enumerate(current_skill_contexts):
            snapshot_components[f"skill_context_{idx}"] = ctx

        content_fingerprint = _hashlib.md5(
            _json.dumps(snapshot_components, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()[:12]

        last_fp = getattr(e.state, "injected_context_fingerprint", None)
        if not isinstance(last_fp, str):
            last_fp = None
        inject_snapshot = last_fp != content_fingerprint
        inject_hooks = bool(hook_notice)
        inject_dynamic = inject_snapshot or inject_hooks

        durable_parts: list[str] = []
        if inject_snapshot:
            if file_registry_notice:
                durable_parts.append(file_registry_notice)
            if strategy_text:
                durable_parts.append(strategy_text)
            if task_plan_notice:
                durable_parts.append(task_plan_notice)
        if inject_hooks:
            durable_parts.append(hook_notice)
        dynamic_prompt = "\n\n".join(durable_parts)

        def _compose_prompts() -> list[str]:
            if not inject_dynamic:
                return [stable_prompt]
            mode = e._effective_system_mode()
            if mode == "merge":
                merged_parts = [dynamic_prompt] if dynamic_prompt else []
                if inject_snapshot:
                    merged_parts.extend(current_skill_contexts)
                result = [stable_prompt]
                if merged_parts:
                    result.append("\n\n".join(merged_parts))
                return result
            prompts = [stable_prompt]
            if dynamic_prompt:
                prompts.append(dynamic_prompt)
            if inject_snapshot:
                prompts.extend(current_skill_contexts)
            return prompts

        threshold = max(1, int(e.max_context_tokens * 0.9))
        prompts = _compose_prompts()
        cache_key = f"{content_fingerprint}:{int(inject_snapshot)}:{int(inject_hooks)}"
        cached_count = self._token_count_cache.get(cache_key)
        if cached_count is not None:
            total_tokens = cached_count
        else:
            total_tokens = self._system_prompts_token_count(prompts)
            if len(self._token_count_cache) >= self._TOKEN_COUNT_CACHE_MAX:
                self._token_count_cache.pop(next(iter(self._token_count_cache)))
            self._token_count_cache[cache_key] = total_tokens

        if total_tokens > threshold and inject_snapshot:
            for idx in range(len(current_skill_contexts) - 1, -1, -1):
                minimized = self._minimize_skill_context(current_skill_contexts[idx])
                if minimized and minimized != current_skill_contexts[idx]:
                    current_skill_contexts[idx] = minimized
                    prompts = _compose_prompts()
                    total_tokens = self._system_prompts_token_count(prompts)
                    if total_tokens <= threshold:
                        break

            while total_tokens > threshold and current_skill_contexts:
                current_skill_contexts.pop()
                prompts = _compose_prompts()
                total_tokens = self._system_prompts_token_count(prompts)

        if self._system_prompts_token_count(prompts) > threshold:
            return [], (
                "系统上下文过长，已无法在当前上下文窗口内继续执行。"
                "请减少附加上下文或拆分任务后重试。"
            )

        snapshots = e.state.prompt_injection_snapshots
        if inject_snapshot:
            snapshots.append({
                "session_turn": e._session_turn,
                "summary": [
                    {"name": name, "chars": len(text)}
                    for name, text in snapshot_components.items()
                ],
                "total_chars": sum(len(text) for text in snapshot_components.values()),
                "components": snapshot_components,
                "_fingerprint": content_fingerprint,
            })
            e.state.injected_context_fingerprint = content_fingerprint
        else:
            entry: dict[str, Any] = {
                "session_turn": e._session_turn,
                "_ref": content_fingerprint,
            }
            if inject_hooks:
                entry["hook_context"] = hook_notice
            snapshots.append(entry)

        return prompts, None


    def _build_task_plan_notice(self) -> str:
        """构建计划文档引用 + 任务清单状态，注入主 system prompt 动态区域。

        仅当存在活跃 TaskList 时生成（零开销原则）。
        每迭代重建，不缓存（task_update 会改变状态）。
        """
        e = self._engine
        task_list = e._task_store.current
        if task_list is None:
            return ""

        parts: list[str] = ["## 当前计划与任务清单"]

        # 计划文档路径引用
        plan_path = e._task_store.plan_file_path
        if plan_path:
            parts.append(f"📄 计划文档: `{plan_path}`")

        # 任务清单状态（复用 _build_task_list_status_notice 的逻辑）
        parts.append(self._build_task_list_status_notice())

        result = "\n".join(parts)
        if len(result) > _PLAN_CONTEXT_MAX_CHARS:
            result = result[:_PLAN_CONTEXT_MAX_CHARS] + "\n…（计划上下文已截断）"
        return result

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
            "所有文件写入操作已自动重定向到 `outputs/backups/` 下的工作副本，原始文件不会被修改。",
            "",
            "**存储结构**：",
            "- `outputs/backups/` — 当前会话的工作副本（staged files），读写操作透明重定向",
            "- `outputs/.versions/` — 文件版本快照（自动管理，支持精确回滚）",
            "",
            "**用户可用命令**：",
            "- `/backup apply` — 将工作副本应用到原文件",
            "- `/backup rollback` — 丢弃所有修改，恢复原始文件",
            "- `/backup list` — 查看当前暂存的文件列表",
        ]
        # 优先从 FileRegistry 获取版本追踪信息
        _reg = getattr(e, "_file_registry", None)
        if _reg is not None and getattr(_reg, "has_versions", False):
            tracked = _reg.list_all_tracked()
            if tracked:
                lines.append(f"\n当前有 {len(tracked)} 个文件受版本追踪保护。")
        else:
            fvm = getattr(e, "_fvm", None)
            if fvm is not None:
                tracked = fvm.list_all_tracked()
                if tracked:
                    lines.append(f"\n当前有 {len(tracked)} 个文件受版本追踪保护。")
        return "\n".join(lines)

    # 已知 MCP Server 使用场景指引（server_name → 中文描述）
    _MCP_USAGE_HINTS: dict[str, str] = {
        "exa": "通用网页搜索引擎，覆盖面广，适用于搜索新闻、产品、技术资讯、实时信息等任何网络信息查询",
        "tavily": "AI 优化搜索引擎，擅长深度研究、事实核查和结构化信息提取，搜索结果更精准",
        "brave": "隐私友好的网页搜索引擎，适用于通用信息查询",
        "context7": "编程库/框架官方文档查询，仅适用于查找特定编程语言库的 API 文档和代码示例（如 React、Express 等），不适用于通用信息搜索",
        "excel": "Excel 文件读写操作",
    }

    def _build_mcp_context_notice(self) -> str:
        """生成已连接 MCP Server 的概要信息，注入 system prompt。"""
        e = self._engine
        servers = e._mcp_manager.get_server_info()
        if not servers:
            return ""
        ready_servers = [s for s in servers if s.get("status") == "ready"]
        if not ready_servers:
            return ""
        lines = ["## MCP 扩展能力"]
        _has_search = False
        _has_docs = False
        for srv in ready_servers:
            name = srv["name"]
            tool_count = srv.get("tool_count", 0)
            tool_names = srv.get("tools", [])
            tools_str = "、".join(tool_names) if tool_names else "无"
            hint = self._MCP_USAGE_HINTS.get(name, "")
            hint_suffix = f"  — {hint}" if hint else ""
            lines.append(f"- **{name}**（{tool_count} 个工具）：{tools_str}{hint_suffix}")
            _scope = e._mcp_manager.tool_scopes
            if any(v == "search" for k, v in _scope.items() if k.startswith(f"mcp_{name}_") or k.startswith(f"mcp_{name.replace('-', '_')}_")):
                _has_search = True
            if name in ("context7",):
                _has_docs = True
        lines.append(
            "\n以上 MCP 工具已注册，工具名带 `mcp_{server}_` 前缀，可直接调用。"
            "当用户询问你有哪些 MCP 或外部能力时，据此如实回答。\n"
            "**工具优先级**：当内置工具（不带 `mcp_` 前缀）能完成任务时，"
            "优先使用内置工具。MCP 工具仅在内置工具无法覆盖的场景下使用。"
        )
        # 收集已启用的搜索引擎名称（按 ready_servers 顺序）
        _search_engine_names: list[str] = []
        from excelmanus.mcp.builtin import BUILTIN_SEARCH_SERVER_NAMES
        for srv in ready_servers:
            if srv["name"] in BUILTIN_SEARCH_SERVER_NAMES:
                _search_engine_names.append(srv["name"])
        _has_exa = "exa" in _search_engine_names
        _has_parallel_search = _has_exa  # parallel_search 仅在 exa 可用时注册
        # 非 exa 的其他搜索引擎
        _other_engines = [n for n in _search_engine_names if n != "exa"]

        if _has_search and _has_docs:
            guide_lines = ["\n**搜索工具选择指南**："]
            if _has_parallel_search:
                guide_lines.append("- 广泛信息搜索（新闻、产品、概念、最新动态）→ 使用 **parallel_search**（自动多查询并发，覆盖面更广）")
                guide_lines.append("- 精确单条搜索（已知关键词、简单事实核查）→ 直接使用 mcp_exa_* 工具")
            elif _search_engine_names:
                engines_str = "、".join(f"mcp_{n}_*" for n in _search_engine_names)
                guide_lines.append(f"- 通用信息搜索（新闻、产品、概念、最新动态）→ 使用 {engines_str} 搜索工具")
            if _other_engines:
                for eng in _other_engines:
                    hint = self._MCP_USAGE_HINTS.get(eng, "网页搜索")
                    guide_lines.append(f"- 也可使用 **mcp_{eng}_*** 工具（{hint}）")
            guide_lines.append("- 编程库/框架文档查询（API 用法、代码示例）→ 使用 **context7** 文档查询")
            if _has_parallel_search:
                guide_lines.append("- 技术问题可同时调用 parallel_search 和 context7 工具（它们会并行执行）")
                guide_lines.append("- 不确定时优先使用 parallel_search，因为它覆盖范围更广")
            lines.append("\n".join(guide_lines))
        elif _has_search:
            guide_lines = ["\n**搜索指南**："]
            if _has_parallel_search:
                guide_lines.append("- 广泛信息搜索 → 使用 **parallel_search**（自动多查询并发，覆盖面更广）")
                guide_lines.append("- 精确单条搜索 → 直接使用 mcp_exa_* 工具")
            elif _search_engine_names:
                engines_str = "、".join(f"mcp_{n}_*" for n in _search_engine_names)
                guide_lines.append(f"- 搜索信息 → 使用 {engines_str} 搜索工具")
            if _other_engines:
                for eng in _other_engines:
                    hint = self._MCP_USAGE_HINTS.get(eng, "网页搜索")
                    guide_lines.append(f"- 也可使用 **mcp_{eng}_*** 工具（{hint}）")
            if _has_parallel_search:
                guide_lines.append("- 不确定时优先使用 parallel_search")
            lines.append("\n".join(guide_lines))
        return "\n".join(lines)

    def _build_file_registry_notice(self) -> str:
        """薄文件列表：路径、扩展名、已见 content_version。不是全景散文。"""
        from pathlib import Path as _Path

        e = self._engine
        files: dict[str, str] = {}
        seen = getattr(e.state, "file_content_versions", None) or {}
        if isinstance(seen, dict):
            for path, ver in seen.items():
                key = str(path or "").strip()
                if key:
                    files[key] = str(ver or "")

        _reg = getattr(e, "file_registry", None)
        if _reg is not None:
            try:
                for entry in _reg.list_all():
                    path = (
                        getattr(entry, "canonical_path", None)
                        or getattr(entry, "original_name", "")
                        or ""
                    )
                    path = str(path).strip()
                    if not path:
                        continue
                    hash_ = str(getattr(entry, "content_hash", "") or "")
                    ver = files.get(path, "")
                    if not ver and hash_:
                        ver = hash_ if hash_.startswith("sha256:") else f"sha256:{hash_}"
                    files.setdefault(path, ver)
            except Exception:
                logger.debug("thin file list: registry skipped", exc_info=True)

        parts: list[str] = []
        if files:
            lines = ["## 工作区文件"]
            for path in sorted(files)[:80]:
                ext = _Path(path).suffix.lower() or "-"
                ver = files[path]
                if ver:
                    lines.append(f"- `{path}` ({ext}) {ver}")
                else:
                    lines.append(f"- `{path}` ({ext})")
            parts.append("\n".join(lines))

        cow_registry: dict[str, str] = {}
        try:
            if hasattr(e.state, "get_cow_mappings"):
                mappings = e.state.get_cow_mappings()
                if isinstance(mappings, dict):
                    cow_registry = mappings
        except Exception:
            cow_registry = {}
        if cow_registry:
            cow_lines = [
                "## ⚠️ 文件保护路径映射（CoW）",
                "以下原始文件受保护，已自动复制到 outputs/ 目录。",
                "**你必须使用副本路径进行所有后续读取和写入操作，严禁访问原始路径。**",
                "",
                "| 原始路径（禁止访问） | 副本路径（请使用） |",
                "|---|---|",
            ]
            for src, dst in cow_registry.items():
                cow_lines.append(f"| `{src}` | `{dst}` |")
            cow_lines.append("")
            cow_lines.append(
                "如果你在工具参数中使用了原始路径，系统会自动重定向到副本，"
                "但请主动记住并使用副本路径以避免混淆。"
            )
            parts.append("\n".join(cow_lines))

        return "\n\n".join(parts)

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

