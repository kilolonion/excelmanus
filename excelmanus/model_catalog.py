"""Versioned capability facts shared by Python and the web client.

The JSON file is the only curated source. Documentation is not an endpoint
probe; legacy name hints are never promoted to verified capabilities.
"""
from __future__ import annotations

import json
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

from excelmanus.model_identity import normalize_model_tokens


@lru_cache(maxsize=1)
def catalog() -> dict:
    return json.loads(Path(__file__).with_name("model_catalog.json").read_text(encoding="utf-8"))


def provider_for(base_url: str = "", model: str = "") -> str:
    host = (urlparse(base_url).hostname or "").lower()
    if model.startswith("antigravity/") or host.endswith("cloudcode-pa.googleapis.com"):
        return "antigravity"
    if model.startswith("workbuddy-") or host in {"copilot.tencent.com", "www.workbuddy.ai"}:
        return "workbuddy"
    if model.startswith("openai-codex/") or host == "chatgpt.com":
        return "openai-codex"
    domains = {
        "openai": ("api.openai.com",), "anthropic": ("api.anthropic.com",),
        "gemini": ("generativelanguage.googleapis.com",), "deepseek": ("api.deepseek.com",),
        "qwen": ("dashscope.aliyuncs.com", "dashscope-intl.aliyuncs.com", "dashscope-us.aliyuncs.com"),
        "glm": ("open.bigmodel.cn", "api.z.ai"), "moonshot": ("api.moonshot.cn", "api.moonshot.ai"),
        "minimax": ("api.minimax.io", "api.minimaxi.com"), "xai": ("api.x.ai",),
        "doubao": ("ark.cn-beijing.volces.com",), "openrouter": ("openrouter.ai",),
        "mimo": ("api.xiaomimimo.com", "token-plan-cn.xiaomimimo.com", "token-plan.xiaomimimo.com"),
    }
    return next((p for p, hosts in domains.items() if host in hosts), "")


def model_spec(model: str, base_url: str = "", *, route_only: bool = False) -> dict | None:
    """Exact normalized identifiers and explicit aliases only; no family wildcard.

    Managed gateway aliases deliberately do not inherit native model facts.
    Codex may display API documentation, but it remains account-unverified.
    """
    provider = provider_for(base_url, model)
    if provider in {"antigravity", "workbuddy"}:
        return None
    name = model.removeprefix("openai-codex/")
    key = normalize_model_tokens(name)
    for entry in catalog()["models"]:
        if key not in {normalize_model_tokens(x) for x in [entry["id"], *entry.get("aliases", [])]}:
            continue
        direct = provider == entry["provider"]
        if route_only and not direct:
            return None
        result = deepcopy(entry)
        result["route_documented"] = direct
        result["match_kind"] = "exact" if normalize_model_tokens(entry["id"]) == key else "alias"
        if entry["provider"] == "qwen" and base_url and "dashscope.aliyuncs.com" != (urlparse(base_url).hostname or ""):
            result["tool_calling"] = False if entry["id"] == "qwen-max" else None
            result["region"] = "international_unverified"
        return result
    # Recognized provider namespaces may identify public model documentation,
    # but never turn that documentation into evidence for another gateway.
    namespace, separator, tail = name.partition("/")
    if separator and namespace in {"openai", "anthropic", "google", "deepseek", "qwen", "minimax"}:
        result = model_spec(tail)
        expected = "gemini" if namespace == "google" else namespace
        if result and result["provider"] == expected:
            result["route_documented"] = provider == expected
            return result if not route_only or result["route_documented"] else None
    return None


def local_context_budget(model: str, base_url: str = "", canonical_model: str = "") -> int:
    spec = model_spec(model, base_url)
    if spec:
        limits = [spec.get(k) for k in ("context_window", "max_input_tokens", "transport_input_limit")]
        limits = [v for v in limits if isinstance(v, int) and v > 0]
        if limits:
            return min(limits)
    return catalog()["default_local_context_budget"]


def capability_metadata(model: str, base_url: str = "") -> dict:
    spec = model_spec(model, base_url)
    app_inputs = catalog()["application_input_modalities"]
    return {
        "source": "official_documentation" if spec else "unknown",
        "verified_at": spec.get("verified_at") if spec else None,
        "source_urls": spec.get("source_urls", []) if spec else [],
        "route_documented": bool(spec and spec["route_documented"]),
        "endpoint_verified": False,
        "status": spec.get("status", "unknown") if spec else "unknown",
        "documented": spec,
        "application_input_modalities": app_inputs,
        "effective_input_modalities": [x for x in (spec or {}).get("input_modalities", ["text"]) if x in app_inputs],
        "local_context_budget": local_context_budget(model, base_url),
        "context_source": "documented_limit" if spec else "local_default",
    }


def recommended_models(base_url: str) -> list[dict]:
    provider = provider_for(base_url)
    return [dict(id=e["id"], capability_metadata=capability_metadata(e["id"], base_url))
            for e in catalog()["models"] if e["provider"] == provider
            and e["status"] == "active" and e["tool_calling"] is True
            and (model_spec(e["id"], base_url) or {}).get("tool_calling") is True]


def model_list_entry(model: str, base_url: str, remote: dict | None = None) -> dict:
    """Preserve remote declarations separately from curated facts."""
    remote = remote or {}
    meta = capability_metadata(model, base_url)
    spec = model_spec(model, base_url, route_only=True)
    methods = remote.get("supportedGenerationMethods")
    specialized = any(token in model.lower() for token in ("embedding", "rerank", "whisper", "tts", "transcribe", "realtime", "moderation", "image-generation", "flash-image", "gpt-image", "dall-e", "native-audio"))
    eligible = not specialized and not (isinstance(methods, list) and "generateContent" not in methods)
    parameters = remote.get("supported_parameters")
    outputs = (remote.get("architecture") or {}).get("output_modalities") if isinstance(remote.get("architecture"), dict) else None
    if isinstance(parameters, list) and "tools" not in parameters:
        eligible = False
    if isinstance(outputs, list) and "text" not in outputs:
        eligible = False
    if spec and (spec.get("tool_calling") is False or spec.get("status") == "retired"):
        eligible = False
    declarations = {k: remote[k] for k in ("context_window", "context_length", "inputTokenLimit", "outputTokenLimit", "supportedGenerationMethods", "architecture", "supported_parameters", "shutdown_date") if k in remote}
    return dict(id=model, owned_by=remote.get("owned_by", ""), agent_eligible=eligible,
                capability_metadata=meta, remote_declarations=declarations,
                availability_source="remote_listing" if remote else "curated_fallback")
