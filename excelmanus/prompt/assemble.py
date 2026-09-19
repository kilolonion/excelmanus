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
    child_prompt = getattr(engine, "_child_system_prompt", None)
    if isinstance(child_prompt, str) and child_prompt.strip():
        return child_prompt.strip()
    composer = getattr(engine, "_prompt_composer", None)
    if composer is None:
        memory = getattr(engine, "memory", None)
        return str(getattr(memory, "system_prompt", "") or "")
    from excelmanus.tools.catalog import catalog_from_engine
    from excelmanus.tools.runtime import present_as_of

    from excelmanus.prompt.load import PromptComposer

    if isinstance(composer, PromptComposer):
        if composer.reload_if_changed():
            binder = getattr(engine, "_bind_prompt_registry_runtime", None)
            if callable(binder):
                binder()
        composer.validate_runtime()

    chat_mode = getattr(engine, "_current_chat_mode", "write") or "write"
    present_as = present_as_of(engine)
    catalog = catalog_from_engine(engine)
    sdk_section = ""
    if present_as == "code":
        runtime = getattr(engine, "_tool_runtime", None)
        renderer = getattr(runtime, "render_sdk_section", None)
        if callable(renderer):
            sdk_section = renderer() or ""
        if not str(sdk_section).strip():
            raise RuntimeError(
                "Code Mode 请求组装失败：SDK 段为空（present_as=code 必须能生成声明的 SDK）"
            )
    # 导航/能力地图走 L2 执行目录。present_as=code 只坍缩 envelope.tools。
    nav_catalog = catalog
    visible_names: frozenset[str] | None = (
        frozenset(catalog.names()) if catalog is not None else None
    )
    assemble_ctx = AssembleContext(
        plan_active=chat_mode == "plan",
        present_as=present_as,
        variables=prompt_variables(engine),
        chat_mode=chat_mode,
        sdk_section=sdk_section,
        visible_tools=visible_names,
        new_workbook=bool(getattr(engine, "_catalog_new_workbook", True)),
    )
    assembly = composer.registry.assemble(assemble_ctx)
    engine._prompt_tool_snapshot = list(assembly.tools)
    text = composer.registry.render_system(assembly)
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
        transient.clear()
        if hook_context:
            hook_notice = "## Hook 上下文\n" + hook_context

    current_skill_contexts = [
        ctx for ctx in skill_contexts if isinstance(ctx, str) and ctx.strip()
    ]

    snapshot_components: dict[str, str] = {}
    for idx, ctx in enumerate(current_skill_contexts):
        snapshot_components[f"skill_context_{idx}"] = ctx

    content_fingerprint = hashlib.md5(
        json.dumps(snapshot_components, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()[:12]

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
            contexts.extend(current_skill_contexts)
        return system, contexts

    def _compose_prompts() -> list[str]:
        system, contexts = _compose_layers()
        return [*system, *contexts]

    max_tokens = int(getattr(engine, "max_context_tokens", 0) or 0)
    threshold = max(1, int(max_tokens * 0.9)) if max_tokens else 10**9
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

    if total_tokens > threshold and inject_snapshot:
        for idx in range(len(current_skill_contexts) - 1, -1, -1):
            minimized = minimize_skill_context(current_skill_contexts[idx])
            if minimized and minimized != current_skill_contexts[idx]:
                current_skill_contexts[idx] = minimized
                prompts = _compose_prompts()
                total_tokens = system_prompts_token_count(prompts)
                if total_tokens <= threshold:
                    break
        while total_tokens > threshold and current_skill_contexts:
            current_skill_contexts.pop()
            prompts = _compose_prompts()
            total_tokens = system_prompts_token_count(prompts)

    if system_prompts_token_count(prompts) > threshold:
        return [], (
            "系统上下文过长，已无法在当前上下文窗口内继续执行。"
            "请减少附加上下文或拆分任务后重试。"
        )

    if state is not None:
        snapshots = getattr(state, "prompt_injection_snapshots", None)
        if isinstance(snapshots, list):
            if inject_snapshot:
                snapshots.append({
                    "session_turn": getattr(engine, "_session_turn", 0),
                    "summary": [
                        {"name": name, "chars": len(text)}
                        for name, text in snapshot_components.items()
                    ],
                    "total_chars": sum(len(text) for text in snapshot_components.values()),
                    "components": snapshot_components,
                    "_fingerprint": content_fingerprint,
                })
                state.injected_context_fingerprint = content_fingerprint
            else:
                entry: dict[str, Any] = {
                    "session_turn": getattr(engine, "_session_turn", 0),
                    "_ref": content_fingerprint,
                }
                if inject_hooks:
                    entry["hook_context"] = hook_notice
                snapshots.append(entry)

    _system, request_contexts = _compose_layers()
    engine._prompt_user_contexts = request_contexts
    return _system, None
