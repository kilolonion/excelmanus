"""Compile reasoning controls using documented model/endpoint constraints."""
from __future__ import annotations

from typing import Any, Iterable
from excelmanus.model_catalog import model_spec, provider_for

EFFORT_ORDER = ("none", "minimal", "low", "medium", "high", "xhigh", "max")

# 思考等级在协议上无处传递的方言：只有开关，或推理深度完全交给模型决定。
NO_EFFORT_DIALECTS = {"chat_template", "deepseek", "reasoning_content_auto"}


def thinking_controls(model: str, base_url: str, protocol: str, mode: str = "auto", caps: Any = None, requested: str = "medium",
                      declared_efforts: Iterable[str] | None = None) -> dict:
    policy = thinking_policy(model, base_url, protocol, mode, caps, declared_efforts)
    if policy.get("supported") is False:
        kind, allowed = "none", []
    elif policy.get("efforts"):
        kind, allowed = "effort", policy["efforts"]
    elif policy.get("budget_max"):
        kind = "budget"
        allowed = [x for x in EFFORT_ORDER if x != "none" or policy.get("can_disable")]
    elif policy.get("dialect") == "chat_template" and policy.get("can_disable") is not False:
        # vLLM / SGLang 的 chat_template 只承载思考开关，等级发不出去。
        kind, allowed = "toggle", ["none", "high"]
    elif policy.get("supported") is True and policy.get("can_disable"):
        kind, allowed = "toggle", ["none", "high"]
    else:
        kind, allowed = "unknown", []
    effective = effective_effort(policy, requested)
    if kind == "toggle":
        effective = "none" if requested == "none" else "high"
    return {"control_kind": kind, "model_allowed_efforts": allowed, "effective_effort": effective,
            "can_disable": policy.get("can_disable"), "requested_effort": requested,
            "levels_source": policy.get("levels_source")}


def thinking_policy(model: str, base_url: str, protocol: str, mode: str = "auto", caps: Any = None,
                    declared_efforts: Iterable[str] | None = None) -> dict:
    spec = model_spec(model, base_url)
    reasoning = (spec or {}).get("reasoning", {})
    provider = provider_for(base_url, model)
    dialect = mode if mode not in {"", "auto", "disabled"} else ""
    if not dialect and spec and spec.get("route_documented"):
        dialect = reasoning.get("dialect", "")
    if not dialect and getattr(caps, "supports_thinking", None) is True:
        dialect = getattr(caps, "thinking_type", "")
    if not dialect:
        dialect = reasoning.get("dialect", "")
    if provider == "openrouter":
        dialect = "openrouter"
    elif provider == "gemini" and protocol == "openai":
        dialect = "gemini_compat"
    elif dialect in {"gemini", "gemini_level"} and protocol == "openai":
        dialect = "gemini_compat"
    elif dialect == "claude" and protocol == "openai":
        dialect = "claude_compat"
    policy = {**reasoning, "dialect": dialect, "model": model, "spec": spec}
    declared = _declared_levels(reasoning, dialect, mode, declared_efforts)
    if declared:
        policy["efforts"] = declared
        policy["levels_source"] = "user_declared"
    elif policy.get("efforts"):
        policy["levels_source"] = "catalog"
    return policy


def _declared_levels(reasoning: dict, dialect: str, mode: str, declared_efforts: Iterable[str] | None) -> list[str]:
    """用户配置的可用等级可为未入能力目录的模型充当可选等级。

    能力目录的裁决永远优先：条目一旦声明了 efforts（包括"只有开关"的空列表）
    或明确不支持推理，用户的全局配置就不参与。方言未知、思考被强制关闭、或等级
    在该方言的协议上无处传递时同样不声明——选择器不展示发不出去的档位。
    """
    if not declared_efforts or not dialect or mode == "disabled":
        return []
    if "efforts" in reasoning or reasoning.get("supported") is False:
        return []
    if dialect in NO_EFFORT_DIALECTS:
        return []
    selected = set(declared_efforts)
    levels = [e for e in EFFORT_ORDER if e in selected]
    if reasoning.get("can_disable") is False:
        levels = [e for e in levels if e != "none"]
    return levels if any(e != "none" for e in levels) else []


def effective_effort(policy: dict, effort: str) -> str:
    allowed = policy.get("efforts") or []
    if effort in allowed or not allowed:
        return effort
    # Global preferences may survive a model switch. Clamp to a documented
    # level and disclose it in capability metadata; never send an invalid enum.
    index = EFFORT_ORDER.index(effort) if effort in EFFORT_ORDER else 3
    return min(allowed, key=lambda e: (abs(EFFORT_ORDER.index(e)-index), EFFORT_ORDER.index(e)))


def compile_thinking(model: str, base_url: str, protocol: str, mode: str, config: Any, caps: Any = None,
                     declared_efforts: Iterable[str] | None = None) -> dict:
    policy = thinking_policy(model, base_url, protocol, mode, caps, declared_efforts)
    dialect = policy["dialect"]
    if policy.get("supported") is False:
        return {}
    if not dialect:
        if mode == "disabled":
            raise ValueError(f"尚未核实 {model} 的关闭思考参数，请先选择正确的供应商参数格式")
        return {}
    disabled = mode == "disabled" or bool(config is not None and config.is_disabled)
    if disabled and policy.get("spec") and policy.get("can_disable") is None:
        raise ValueError(f"尚未核实 {model} 是否支持关闭思考，请使用供应商默认模式")
    if disabled and policy.get("can_disable") is False:
        raise ValueError(f"模型 {model} 不支持关闭思考，请选择该模型支持的推理等级")
    effort = effective_effort(policy, getattr(config, "effort", "medium"))
    budget = config.effective_budget() if config is not None else 8192
    if not disabled:
        budget = max(policy.get("budget_min", 1), budget)
        if policy.get("budget_max"):
            budget = min(budget, policy["budget_max"])
    if dialect == "claude":
        return {"_thinking_enabled": not disabled, "_thinking_budget": 0 if disabled else budget,
                "_thinking_effort": effort if policy.get("efforts") else ""}
    if dialect == "claude_compat":
        from excelmanus.providers.claude import uses_adaptive_thinking
        if disabled:
            return {"extra_body": {"thinking": {"type": "disabled"}}}
        if uses_adaptive_thinking(model):
            body = {"thinking": {"type": "adaptive"}}
            if policy.get("efforts"):
                body["output_config"] = {"effort": effort}
            return {"extra_body": body}
        return {"extra_body": {"thinking": {"type": "enabled", "budget_tokens": budget}}}
    if dialect in {"gemini", "gemini_level", "gemini_compat"}:
        is_budget = ((policy.get("spec") or {}).get("reasoning", {}).get("dialect") == "gemini" or policy.get("dialect") == "gemini")
        thinking = {"includeThoughts": not disabled}
        if is_budget:
            thinking["thinkingBudget"] = 0 if disabled else budget
        else:
            thinking["thinkingLevel"] = "minimal" if disabled else {"xhigh":"high", "max":"high"}.get(effort, effort)
        if protocol in {"gemini", "antigravity"}:
            return {"extra_body": {"thinkingConfig": thinking}}
        # Google's compatibility endpoint accepts documented reasoning_effort.
        # For explicit budgets/summaries use its namespaced extra_body.
        mapped = {"include_thoughts": thinking["includeThoughts"]}
        mapped.update({"thinking_budget": thinking["thinkingBudget"]} if "thinkingBudget" in thinking else {"thinking_level": thinking["thinkingLevel"]})
        return {"extra_body": {"extra_body": {"google": {"thinking_config": mapped}}}}
    if dialect == "openai_reasoning":
        if policy.get("spec") and not policy.get("efforts") and not disabled:
            return {}  # documented reasoning, unverified controls: use upstream default
        return {"reasoning_effort": "none" if disabled else effort}
    if dialect == "enable_thinking":
        return {"extra_body": {"enable_thinking": not disabled, **({"thinking_budget": budget} if not disabled else {})}}
    if dialect == "chat_template":
        return {"extra_body": {"chat_template_kwargs": {"enable_thinking": not disabled}}}
    if dialect == "glm_thinking":
        # GLM/Kimi/MiMo/DeepSeek share a switch, not an effort enum.
        body = {"thinking": {"type": "disabled" if disabled else "enabled"}}
        if policy.get("efforts") and not disabled:
            body["reasoning_effort"] = effort
        return {"extra_body": body}
    if dialect == "openrouter":
        reasoning = {"enabled": False} if disabled else ({"max_tokens": budget} if getattr(config, "budget_tokens", 0) else {"effort": effort})
        return {"extra_body": {"reasoning": reasoning}}
    if disabled:
        raise ValueError(f"模型 {model} 的思考关闭方式尚未验证，请配置供应商支持的参数格式")
    return {}


def reject_unsupported_media(messages: list) -> None:
    """No silent content loss until the chat API has audio/video admission."""
    for message in messages:
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") in {"input_audio", "audio_url", "video_url", "input_video", "video", "audio"}:
                    raise ValueError("当前客户端尚未接通原生音视频输入；请使用文本或图片")
