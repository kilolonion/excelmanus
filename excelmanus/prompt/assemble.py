"""请求级 system 组装：稳定前缀 + 技能/hook 快照。

文件列表、MCP 指南、工具索引、TaskList 墙、推理级别都不进入默认路径。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from typing import Any

from excelmanus.prompt.registry import AssembleContext, UnknownPromptVariable

_MIN_SYSTEM_CONTEXT_CHARS = 256
_SYSTEM_CONTEXT_SHRINK_MARKER = "[上下文已压缩以适配上下文窗口]"
_TOKEN_COUNT_CACHE_MAX = 16


def _fingerprint_components(components: dict[str, str]) -> str:
    return hashlib.sha256(
        json.dumps(components, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:16]


def prompt_variables(engine: Any) -> dict[str, str]:
    """组装 ``{{workspace_root}}`` / ``{{model}}``。"""
    raw = getattr(engine, "_runtime_vars", None)
    variables = dict(raw) if isinstance(raw, dict) else {}
    if not variables.get("workspace_root"):
        config = getattr(engine, "config", None)
        root = getattr(config, "workspace_root", None)
        if root:
            from pathlib import Path

            variables["workspace_root"] = str(Path(root).expanduser().resolve())
    if not variables.get("model"):
        model = (
            getattr(engine, "active_model", None)
            or getattr(getattr(engine, "config", None), "model", None)
        )
        if model:
            variables["model"] = str(model)
    return variables


def all_tool_names(engine: Any) -> list[str]:
    from excelmanus.tools.catalog import catalog_from_engine

    catalog = catalog_from_engine(engine)
    if catalog is not None:
        return catalog.names()
    registry = getattr(engine, "registry", None) or getattr(engine, "_registry", None)
    if registry is None:
        return []
    get_tool_names = getattr(registry, "get_tool_names", None)
    if callable(get_tool_names):
        return list(get_tool_names())
    get_all_tools = getattr(registry, "get_all_tools", None)
    if callable(get_all_tools):
        return [tool.name for tool in get_all_tools()]
    return []


def system_prompts_token_count(system_prompts: Sequence[str]) -> int:
    from excelmanus.memory import TokenCounter

    total = 0
    for prompt in system_prompts:
        total += TokenCounter.count_message({"role": "system", "content": prompt})
    return total


def minimize_skill_context(text: str) -> str:
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


def build_stable_system_prompt(engine: Any) -> str:
    """稳定 system 前缀：identity + persona + 按目录模式门控的策略段。"""
    from excelmanus.subagent.models import SubagentConfig

    child_config = getattr(engine, "_subagent_config", None)
    if not isinstance(child_config, SubagentConfig):
        child_config = None
    composer = getattr(engine, "_prompt_composer", None)
    if composer is None:
        if child_config is not None:
            raise ValueError("子代理提示词组装器未初始化")
        memory = getattr(engine, "memory", None)
        text = str(getattr(memory, "system_prompt", "") or "")
        # fallback engine 也不能把模板变量原样发给模型。
        return text.replace("{{workspace_root}}", "当前工作区").replace(
            "{{model}}", "当前模型"
        )
    from excelmanus.tools.catalog import catalog_from_engine

    from excelmanus.prompt.load import PromptComposer

    if isinstance(composer, PromptComposer):
        if composer.reload_if_changed():
            binder = getattr(engine, "_bind_prompt_registry_runtime", None)
            if callable(binder):
                binder()
        composer.validate_runtime()

    chat_mode = getattr(engine, "_current_chat_mode", "write") or "write"
    catalog = catalog_from_engine(engine)
    if child_config is not None:
        chat_mode = engine._fixed_capability.catalog_mode
    # 能力地图走有效执行目录；具体 SDK 签名通过 tool_detail 按需获取。
    nav_catalog = catalog
    visible_names: frozenset[str] | None = (
        frozenset(catalog.names()) if catalog is not None else None
    )
    inherited = getattr(child_config, "inherit_strategies", None) if child_config else None
    if inherited:
        unknown = set(inherited) - {seg.name for seg in composer.strategy_segments}
        if unknown:
            raise ValueError(f"子代理继承了未注册策略: {', '.join(sorted(unknown))}")
    assemble_ctx = AssembleContext(
        plan_active=chat_mode == "plan",
        variables=prompt_variables(engine),
        chat_mode=chat_mode,
        visible_tools=visible_names,
        new_workbook=bool(getattr(engine, "_catalog_new_workbook", True)),
        full_access=bool(getattr(engine, "_full_access_enabled", False)),
        strategy_names=frozenset(inherited) if inherited else None,
    )
    assembly = composer.registry.assemble(assemble_ctx)
    engine._prompt_tool_snapshot = list(assembly.tools)
    text = composer.registry.render_system(assembly)
    if child_config is not None:
        from excelmanus.subagent.child import compose_child_prompt

        text = f"{text}\n\n{compose_child_prompt(engine, child_config)}"
        # catalog 的固定能力优先于父会话/缓存模式；这里只陈述状态，不扩大权限。
        text += f"\n\n当前子代理模式：{chat_mode}；审批策略：{engine.approval_policy}。"
    if nav_catalog is not None:
        nav = nav_catalog.capability_map_text()
        if nav:
            text = f"{text}\n\n{nav}" if text.strip() else nav
        engine._effective_catalog = catalog
    return text


def prepare_system_prompts_for_request(
    engine: Any,
    skill_contexts: list[str] | None = None,
    *,
    consume_dynamic: bool = True,
) -> tuple[list[str], str | None]:
    """构建本步请求的 system prompts。

    稳定前缀始终作为第一条。文件列表、策略段、任务清单、核心记忆、
    MCP 指南不再默认注入。斜杠技能正文与一次性 hook 只在快照变化时追加。
    consume_dynamic=False 只返回稳定前缀，不消费 hook / 技能快照。
    """
    skill_contexts = skill_contexts or []
    try:
        stable_prompt = build_stable_system_prompt(engine)
    except (UnknownPromptVariable, ValueError, OSError, RuntimeError) as exc:
        return [], f"系统提示词组装失败: {exc}"
    if not consume_dynamic:
        return [stable_prompt] if stable_prompt.strip() else [], None

    hook_notice = ""
    transient = getattr(engine, "_transient_hook_contexts", None)
    if isinstance(transient, list) and transient:
        hook_context = "\n".join(transient).strip()
        if hook_context:
            hook_notice = "## Hook 上下文\n" + hook_context

    current_skill_contexts = [
        ctx for ctx in skill_contexts if isinstance(ctx, str) and ctx.strip()
    ]

    # 用户自定义规则属于运行时输入，走 user-role context；不与 stable system 混写。
    rules_manager = getattr(engine, "_rules_manager", None)
    if rules_manager is not None:
        try:
            rules_text = rules_manager.compose_rules_prompt(
                getattr(engine, "_session_id", None)
            )
        except Exception:
            rules_text = ""
        if isinstance(rules_text, str) and rules_text.strip():
            current_skill_contexts.append(rules_text.strip())

    # 先按预算投影，再决定 fingerprint。这样被截断的技能不会把“完整正文”标记
    # 为已注入，重试时也不会丢失或重复追加同一份有效投影。
    max_tokens = int(getattr(engine, "max_context_tokens", 0) or 0)
    threshold = max(1, int(max_tokens * 0.9)) if max_tokens else 10**9

    def _budget_prompts(contexts: list[str], include_contexts: bool) -> tuple[list[str], int]:
        rendered = [stable_prompt]
        if include_contexts:
            rendered.extend(contexts)
        return rendered, system_prompts_token_count(rendered)

    # 预算压缩只作用于动态 user context，不修改 stable system。
    projected_contexts = list(current_skill_contexts)
    initial, total_tokens = _budget_prompts(projected_contexts, True)
    if total_tokens > threshold:
        for idx in range(len(projected_contexts) - 1, -1, -1):
            minimized = minimize_skill_context(projected_contexts[idx])
            if minimized and minimized != projected_contexts[idx]:
                projected_contexts[idx] = minimized
                _, total_tokens = _budget_prompts(projected_contexts, True)
                if total_tokens <= threshold:
                    break
        while total_tokens > threshold and projected_contexts:
            projected_contexts.pop()
            _, total_tokens = _budget_prompts(projected_contexts, True)
    if total_tokens > threshold:
        return [], (
            "系统上下文过长，已无法在当前上下文窗口内继续执行。"
            "请减少附加上下文或拆分任务后重试。"
        )

    snapshot_components: dict[str, str] = {}
    for idx, ctx in enumerate(projected_contexts):
        snapshot_components[f"skill_context_{idx}"] = ctx
    content_fingerprint = _fingerprint_components(snapshot_components)

    state = getattr(engine, "state", None)
    last_fp = getattr(state, "injected_context_fingerprint", None)
    if not isinstance(last_fp, str):
        last_fp = None
    inject_snapshot = last_fp != content_fingerprint
    inject_hooks = bool(hook_notice)
    inject_dynamic = inject_snapshot or inject_hooks
    dynamic_prompt = hook_notice if inject_hooks else ""

    def _compose_layers() -> tuple[list[str], list[str]]:
        """system 前缀 + user-role 快照（技能正文 / hook）。"""
        system = [stable_prompt]
        contexts: list[str] = []
        if not inject_dynamic:
            return system, contexts
        if dynamic_prompt:
            contexts.append(dynamic_prompt)
        if inject_snapshot:
            contexts.extend(projected_contexts)
        return system, contexts

    def _compose_prompts() -> list[str]:
        system, contexts = _compose_layers()
        return [*system, *contexts]

    prompts = _compose_prompts()
    cache_key = f"{content_fingerprint}:{int(inject_snapshot)}:{int(inject_hooks)}"
    token_cache = getattr(engine, "_assemble_token_cache", None)
    if not isinstance(token_cache, dict):
        token_cache = {}
        engine._assemble_token_cache = token_cache
    cached_count = token_cache.get(cache_key)
    if cached_count is not None:
        total_tokens = cached_count
    else:
        total_tokens = system_prompts_token_count(prompts)
        if len(token_cache) >= _TOKEN_COUNT_CACHE_MAX:
            token_cache.pop(next(iter(token_cache)))
        token_cache[cache_key] = total_tokens

    if system_prompts_token_count(prompts) > threshold:
        return [], (
            "系统上下文过长，已无法在当前上下文窗口内继续执行。"
            "请减少附加上下文或拆分任务后重试。"
        )

    _system, request_contexts = _compose_layers()
    engine._prompt_user_contexts = request_contexts
    engine._prompt_dynamic_pending = {
        "fingerprint": content_fingerprint,
        "components": snapshot_components,
        "hook_notice": hook_notice,
        "inject_snapshot": inject_snapshot,
        "inject_hooks": inject_hooks,
        "session_turn": getattr(engine, "_session_turn", 0),
    }
    return _system, None


def commit_prompt_dynamic(engine: Any) -> None:
    """在请求投影成功后提交动态上下文消费与 fingerprint。"""
    pending = getattr(engine, "_prompt_dynamic_pending", None)
    if not isinstance(pending, dict):
        return
    transient = getattr(engine, "_transient_hook_contexts", None)
    if isinstance(transient, list) and pending.get("inject_hooks"):
        transient.clear()
    if getattr(engine, "_mention_pending_digest", None) is not None:
        engine._mention_contexts = []
        engine._mention_flush_digest = engine._mention_pending_digest
        engine._mention_pending_digest = None
    state = getattr(engine, "state", None)
    snapshots = getattr(state, "prompt_injection_snapshots", None) if state is not None else None
    if isinstance(snapshots, list):
        if pending.get("inject_snapshot"):
            components = dict(pending.get("components") or {})
            snapshots.append({
                "session_turn": pending.get("session_turn", 0),
                "summary": [
                    {"name": name, "chars": len(text), "trust": "sourced"}
                    for name, text in components.items()
                ],
                "total_chars": sum(len(text) for text in components.values()),
                "components": components,
                "_fingerprint": pending.get("fingerprint"),
            })
            if state is not None:
                state.injected_context_fingerprint = pending.get("fingerprint")
        else:
            entry: dict[str, Any] = {
                "session_turn": pending.get("session_turn", 0),
                "_ref": pending.get("fingerprint"),
            }
            if pending.get("inject_hooks"):
                entry["hook_context"] = pending.get("hook_notice", "")
            snapshots.append(entry)
    engine._prompt_dynamic_pending = None
    engine._prompt_user_contexts = []
    engine._mention_pending_digest = None
    engine._mention_contexts_pending_restore = None
    engine._prompt_contexts_pending_restore = None
    restored_mentions = getattr(engine, "_mention_contexts_pending_restore", None)
    if isinstance(restored_mentions, list):
        engine._mention_contexts = restored_mentions
    engine._mention_contexts_pending_restore = None
    restored_contexts = getattr(engine, "_prompt_contexts_pending_restore", None)
    if isinstance(restored_contexts, list):
        engine._prompt_user_contexts = restored_contexts
    engine._prompt_contexts_pending_restore = None


def rollback_prompt_dynamic(engine: Any, appended: list[Any] | None = None) -> None:
    """投影失败时撤回本次尚未提交的动态上下文，保留队列与 fingerprint。"""
    memory = getattr(engine, "_memory", None) or getattr(engine, "memory", None)
    if memory is not None and appended:
        messages = getattr(memory, "messages", None)
        if isinstance(messages, list):
            ids = {id(item) for item in appended}
            removed = [item for item in messages if id(item) in ids]
            emit_void = getattr(memory, "_emit_void", None)
            if callable(emit_void):
                for item in removed:
                    emit_void(item, kind="prompt/rollback")
            messages[:] = [item for item in messages if id(item) not in ids]
    engine._prompt_user_contexts = []
    engine._mention_pending_digest = None
