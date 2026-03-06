"""统一的 Tool Schema 适配层。

将 OpenAI Chat Completions 格式的 tool 定义规范化并转换为各 provider 原生格式，
消除各 provider adapter 独立实现带来的碎片化和不一致。

Pipeline:
    ToolDef.input_schema (原始 JSON Schema)
        → _normalize_base()              # 通用：移除元数据、default → description
        → _normalize_for_<provider>()    # provider 特定结构变换
        → adapt_tools()                  # 格式封装
"""

from __future__ import annotations

import copy
from typing import Any, Literal

Provider = Literal["claude", "gemini", "openai_responses", "openai_chat"]

# ── helpers ──────────────────────────────────────────────────────

_BASE_STRIP_KEYS = frozenset({"$schema", "$id", "$comment", "examples"})
_GEMINI_STRIP_KEYS = frozenset({"additionalProperties", "title"})


def _format_default(val: Any) -> str:
    if isinstance(val, bool):
        return "true" if val else "false"
    if isinstance(val, str):
        return val if val else "''"
    return str(val)


# ── Phase 1: 通用规范化 ──────────────────────────────────────────


def _normalize_base(schema: dict[str, Any]) -> dict[str, Any]:
    """移除所有 provider 都不需要的元数据字段，将 default 合并到 description。"""
    cleaned = {k: v for k, v in schema.items() if k not in _BASE_STRIP_KEYS}

    if "default" in cleaned:
        default_val = cleaned.pop("default")
        desc = cleaned.get("description", "")
        if desc and "默认" not in desc and "default" not in desc.lower():
            cleaned["description"] = f"{desc}（默认 {_format_default(default_val)}）"

    if "properties" in cleaned and isinstance(cleaned["properties"], dict):
        cleaned["properties"] = {
            k: _normalize_base(v) if isinstance(v, dict) else v
            for k, v in cleaned["properties"].items()
        }
    if "items" in cleaned and isinstance(cleaned["items"], dict):
        cleaned["items"] = _normalize_base(cleaned["items"])
    for combo in ("oneOf", "anyOf", "allOf"):
        if combo in cleaned and isinstance(cleaned[combo], list):
            cleaned[combo] = [
                _normalize_base(item) if isinstance(item, dict) else item
                for item in cleaned[combo]
            ]
    return cleaned


# ── Phase 2: Provider 特定规范化 ─────────────────────────────────


def _normalize_for_gemini(schema: dict[str, Any]) -> dict[str, Any]:
    """Gemini 特定规范化（最严格）。

    - 移除 additionalProperties / title
    - 展平 oneOf / anyOf（优先选 object 变体）
    - 空 items → {"type": "string"}
    - 确保有 properties 的节点拥有 type: "object"
    """
    cleaned = {k: v for k, v in schema.items() if k not in _GEMINI_STRIP_KEYS}

    for combo_key in ("oneOf", "anyOf"):
        variants = cleaned.pop(combo_key, None)
        if not isinstance(variants, list) or not variants:
            continue
        chosen = next(
            (v for v in variants if isinstance(v, dict) and v.get("type") == "object"),
            None,
        )
        if chosen is None:
            chosen = next(
                (v for v in variants if isinstance(v, dict) and "type" in v),
                variants[0] if variants else None,
            )
        if isinstance(chosen, dict):
            for vk, vv in chosen.items():
                if vk not in cleaned:
                    cleaned[vk] = vv

    if "properties" in cleaned and "type" not in cleaned:
        cleaned["type"] = "object"

    if "items" in cleaned:
        if not cleaned["items"] or cleaned["items"] == {}:
            cleaned["items"] = {"type": "string"}
        elif isinstance(cleaned["items"], dict):
            cleaned["items"] = _normalize_for_gemini(cleaned["items"])

    if "properties" in cleaned and isinstance(cleaned["properties"], dict):
        cleaned["properties"] = {
            k: _normalize_for_gemini(v) if isinstance(v, dict) else v
            for k, v in cleaned["properties"].items()
        }

    return cleaned


def _identity(schema: dict[str, Any]) -> dict[str, Any]:
    return schema


_SCHEMA_NORMALIZERS: dict[str, Any] = {
    "claude": _identity,
    "gemini": _normalize_for_gemini,
    "openai_responses": _identity,
    "openai_chat": _identity,
}


def normalize_schema(schema: dict[str, Any], provider: Provider) -> dict[str, Any]:
    """对 JSON Schema 执行两阶段规范化（base → provider 特定）。"""
    result = _normalize_base(copy.deepcopy(schema))
    return _SCHEMA_NORMALIZERS[provider](result)


# ── Tool 格式转换 ────────────────────────────────────────────────


def _extract_function_tools(
    tools: list[dict[str, Any]] | None,
) -> list[tuple[str, str, dict[str, Any] | None]]:
    """从 Chat Completions 格式中提取 (name, description, parameters)。"""
    if not tools:
        return []
    results: list[tuple[str, str, dict[str, Any] | None]] = []
    for tool in tools:
        if tool.get("type") != "function":
            continue
        func = tool.get("function", {})
        results.append((
            func.get("name", ""),
            func.get("description", ""),
            func.get("parameters"),
        ))
    return results


def _build_claude(extracted: list[tuple[str, str, dict[str, Any] | None]]) -> list[dict[str, Any]] | None:
    tools: list[dict[str, Any]] = []
    for name, desc, params in extracted:
        ct: dict[str, Any] = {"name": name, "description": desc}
        ct["input_schema"] = normalize_schema(params, "claude") if params else {"type": "object", "properties": {}}
        tools.append(ct)
    return tools or None


def _build_gemini(extracted: list[tuple[str, str, dict[str, Any] | None]]) -> list[dict[str, Any]] | None:
    decls: list[dict[str, Any]] = []
    for name, desc, params in extracted:
        decl: dict[str, Any] = {"name": name, "description": desc}
        if params:
            decl["parameters"] = normalize_schema(params, "gemini")
        decls.append(decl)
    return [{"functionDeclarations": decls}] if decls else None


def _build_responses(extracted: list[tuple[str, str, dict[str, Any] | None]]) -> list[dict[str, Any]] | None:
    tools: list[dict[str, Any]] = []
    for name, desc, params in extracted:
        rt: dict[str, Any] = {"type": "function", "name": name, "description": desc}
        if params:
            rt["parameters"] = normalize_schema(params, "openai_responses")
        tools.append(rt)
    return tools or None


def _build_chat(extracted: list[tuple[str, str, dict[str, Any] | None]]) -> list[dict[str, Any]] | None:
    tools: list[dict[str, Any]] = []
    for name, desc, params in extracted:
        func: dict[str, Any] = {"name": name, "description": desc}
        if params:
            func["parameters"] = normalize_schema(params, "openai_chat")
        tools.append({"type": "function", "function": func})
    return tools or None


_TOOL_BUILDERS: dict[str, Any] = {
    "claude": _build_claude,
    "gemini": _build_gemini,
    "openai_responses": _build_responses,
    "openai_chat": _build_chat,
}


def adapt_tools(
    tools: list[dict[str, Any]] | None,
    provider: Provider,
) -> list[dict[str, Any]] | None:
    """将 OpenAI Chat Completions 格式的 tools 转换为目标 provider 格式。

    同时对每个 tool 的 parameters schema 执行两阶段规范化。
    """
    extracted = _extract_function_tools(tools)
    if not extracted:
        return None
    return _TOOL_BUILDERS[provider](extracted)


# ── tool_choice 映射 ─────────────────────────────────────────────


def _extract_tool_choice_name(tc: dict[str, Any]) -> str:
    """从 dict 格式的 tool_choice 中提取目标函数名。"""
    tc_type = str(tc.get("type", "")).strip().lower()
    name = ""
    if tc_type == "function":
        fv = tc.get("function")
        if isinstance(fv, dict):
            name = str(fv.get("name", "")).strip()
        if not name:
            name = str(tc.get("name", "")).strip()
    elif tc_type == "tool":
        name = str(tc.get("name", "")).strip()
    return name


def _tc_claude(tc: Any) -> dict[str, Any] | None:
    if tc is None:
        return None
    if isinstance(tc, str):
        n = tc.strip().lower()
        if n == "auto":
            return {"type": "auto"}
        if n == "required":
            return {"type": "any"}
        if n == "none":
            return {"type": "auto"}
        return None
    if not isinstance(tc, dict):
        return None
    tc_type = str(tc.get("type", "")).strip().lower()
    if tc_type in {"auto", "none", "required"}:
        return _tc_claude(tc_type)
    name = _extract_tool_choice_name(tc)
    return {"type": "tool", "name": name} if name else None


def _tc_gemini(tc: Any) -> dict[str, Any] | None:
    if tc is None:
        return None
    if isinstance(tc, str):
        n = tc.strip().lower()
        mapping = {"auto": "AUTO", "required": "ANY", "none": "NONE"}
        mode = mapping.get(n)
        if mode:
            return {"functionCallingConfig": {"mode": mode}}
        return None
    if not isinstance(tc, dict):
        return None
    tc_type = str(tc.get("type", "")).strip().lower()
    if tc_type in {"auto", "none", "required"}:
        return _tc_gemini(tc_type)
    name = _extract_tool_choice_name(tc)
    if name:
        return {"functionCallingConfig": {"mode": "ANY", "allowedFunctionNames": [name]}}
    return None


def _tc_responses(tc: Any) -> Any:
    if tc is None:
        return None
    if isinstance(tc, str):
        n = tc.strip().lower()
        return n if n in {"auto", "none", "required"} else None
    if not isinstance(tc, dict):
        return None
    tc_type = str(tc.get("type", "")).strip().lower()
    if tc_type == "function":
        fv = tc.get("function")
        if isinstance(fv, dict):
            name = str(fv.get("name", "")).strip()
            if name:
                return {"type": "function", "name": name}
        name = str(tc.get("name", "")).strip()
        if name:
            return {"type": "function", "name": name}
        return None
    if tc_type in {"auto", "none", "required"}:
        return tc_type
    return None


def _tc_chat(tc: Any) -> Any:
    return tc


_TC_ADAPTERS: dict[str, Any] = {
    "claude": _tc_claude,
    "gemini": _tc_gemini,
    "openai_responses": _tc_responses,
    "openai_chat": _tc_chat,
}


def adapt_tool_choice(tool_choice: Any, provider: Provider) -> Any:
    """将 OpenAI Chat Completions tool_choice 映射为目标 provider 格式。"""
    return _TC_ADAPTERS[provider](tool_choice)
