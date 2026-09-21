"""Single compile entry: surface + route → frozen PreparedRequest."""

from __future__ import annotations

import time
from typing import Any
from uuid import uuid4
from dataclasses import replace

from excelmanus.prompt.envelope import (
    RequestEnvelope,
    assemble_envelope,
    canonical_json,
    canonical_wire_payload,
    digest_text,
    digest_tools,
    seal_envelope,
)
from excelmanus.request.route import resolve_route
from excelmanus.request.series import series_of
from excelmanus.request.types import PreparedRequest, RequestHeader, ResolvedRoute


def create_extra_from_engine(engine: Any) -> dict[str, Any]:
    """Thinking / profile extras. Same snapshot as ResolvedRoute."""
    extra: dict[str, Any] = {}
    caps = getattr(engine, "_model_capabilities", None)
    tc = getattr(engine, "_thinking_config", None)
    profile = getattr(engine, "_active_profile", None)
    api_model = str(
        getattr(engine, "_active_model", None)
        or getattr(getattr(engine, "_config", None), "model", "")
        or ""
    )
    config = getattr(engine, "_config", None)
    protocol_hint = str(getattr(engine, "_active_protocol", "") or "")
    if protocol_hint != "openai_responses":
        try:
            from excelmanus.request.route import resolve_route

            protocol_hint = resolve_route(engine).protocol
        except Exception:
            pass
    if protocol_hint == "openai_responses":
        if bool(getattr(config, "responses_background_enabled", False)):
            extra["_responses_background"] = True
        if bool(getattr(config, "responses_continuation_enabled", False)):
            generation = int(getattr(engine, "_compaction_generation", 0) or 0)
            previous = getattr(engine, "_responses_last_response", None)
            if not isinstance(previous, dict):
                memory = getattr(engine, "_memory", None)
                for message in reversed(list(getattr(memory, "messages", []) or [])):
                    state = message.get("replay_state") if isinstance(message, dict) else None
                    response_id = state.get("response_id") if isinstance(state, dict) else None
                    source = message.get("replay_source") or {} if isinstance(message, dict) else {}
                    if (isinstance(response_id, str) and response_id.strip()
                            and source.get("protocol") == "openai_responses"
                            and source.get("model") == api_model
                            and source.get("compaction_generation", 0) == generation):
                        previous = {
                            "id": response_id.strip(),
                            "protocol": "openai_responses",
                            "model": api_model,
                            "compaction_generation": generation,
                        }
                        break
            protocol = str(getattr(engine, "_active_protocol", "") or "")
            if (
                isinstance(previous, dict)
                and previous.get("protocol") == "openai_responses"
                and protocol == "openai_responses"
                and str(previous.get("model") or "") == api_model
                and previous.get("compaction_generation", 0) == generation
            ):
                extra["_responses_previous_response_id"] = str(previous["id"])
            extra["_responses_store"] = True
    try:
        from excelmanus.auth.providers.registry import strip_managed_prefix

        api_model = strip_managed_prefix(api_model) or api_model
    except Exception:
        pass
    profile_thinking_mode = getattr(profile, "thinking_mode", "auto") if profile else "auto"
    if profile_thinking_mode not in ("auto", ""):
        effective = profile_thinking_mode if profile_thinking_mode != "disabled" else ""
    elif caps and getattr(caps, "supports_thinking", False):
        effective = getattr(caps, "thinking_type", "")
    else:
        effective = ""
    budget = tc.effective_budget() if tc is not None else 0
    disabled = bool(tc is None or getattr(tc, "is_disabled", False))
    if effective == "claude":
        extra["_thinking_enabled"] = not disabled
        extra["_thinking_budget"] = budget if not disabled else 0
        extra["_thinking_effort"] = getattr(tc, "claude_effort", None) if tc is not None else None
    elif not disabled:
        if effective == "claude_compat":
            from excelmanus.providers.claude import uses_adaptive_thinking

            body: dict[str, Any] = dict(extra.get("extra_body") or {})
            if uses_adaptive_thinking(api_model):
                body["thinking"] = {"type": "adaptive"}
                body["output_config"] = {"effort": getattr(tc, "claude_effort", None)}
            else:
                body["thinking"] = {"type": "enabled", "budget_tokens": budget}
            extra["extra_body"] = body
        elif effective == "gemini":
            extra["_thinking_budget"] = budget
        elif effective == "gemini_level":
            extra["_thinking_level"] = getattr(tc, "gemini_level", None)
        elif effective == "openai_reasoning":
            extra["reasoning_effort"] = getattr(tc, "openai_effort", None)
        elif effective == "enable_thinking":
            body = dict(extra.get("extra_body") or {})
            body["enable_thinking"] = True
            body["thinking_budget"] = budget
            extra["extra_body"] = body
        elif effective == "glm_thinking":
            body = dict(extra.get("extra_body") or {})
            body["thinking"] = {"type": "enabled"}
            body["reasoning_effort"] = getattr(tc, "openai_effort", None)
            extra["extra_body"] = body
        elif effective == "openrouter":
            body = dict(extra.get("extra_body") or {})
            body["reasoning"] = {
                "effort": getattr(tc, "openai_effort", None),
                "max_tokens": budget,
            }
            extra["extra_body"] = body
    if profile is not None:
        import json as _json

        raw_body = getattr(profile, "custom_extra_body", None)
        if raw_body:
            try:
                parsed = _json.loads(raw_body)
                if isinstance(parsed, dict):
                    merged = dict(extra.get("extra_body") or {})
                    merged.update(parsed)
                    extra["extra_body"] = merged
            except (ValueError, TypeError):
                pass
        raw_headers = getattr(profile, "custom_extra_headers", None)
        if raw_headers:
            try:
                parsed = _json.loads(raw_headers)
                if isinstance(parsed, dict):
                    extra["extra_headers"] = parsed
            except (ValueError, TypeError):
                pass
    # 订阅凭证解析出的 provider 专属请求头（含刷新后的 token）覆盖静态值
    oauth_headers = getattr(engine, "_oauth_extra_headers", None)
    if isinstance(oauth_headers, dict) and oauth_headers:
        merged_headers = dict(extra.get("extra_headers") or {})
        merged_headers.update(oauth_headers)
        extra["extra_headers"] = merged_headers
    return extra


def header_from_sealed(engine: Any, envelope: Any) -> RequestHeader:
    route = getattr(engine, "_resolved_route", None) or resolve_route(engine)
    wire = list(getattr(envelope, "wire_messages", None) or [])
    transport = str(getattr(envelope, "transport", None) or "inline")
    return _build_header(route, envelope, wire, transport=transport)


_REPLAY_KEYS = frozenset({
    "reasoning_content",
    "thinking",
    "reasoning",
    "replay_state",
    "signature",
})


def _strip_transport_meta(value: Any) -> Any:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if key in {"file_id", "cache_control", "file"}:
                continue
            if key == "image_url" and isinstance(item, dict):
                url = str(item.get("url") or "")
                if url.startswith("data:"):
                    continue
                out[key] = _strip_transport_meta(item)
                continue
            out[key] = _strip_transport_meta(item)
        variant = value.get("_variant_id") or value.get("variant_id")
        attach = value.get("_attachment_id") or value.get("attachment_id")
        if variant:
            out["variant_id"] = variant
        if attach:
            out["attachment_id"] = attach
        return out
    if isinstance(value, list):
        return [_strip_transport_meta(item) for item in value]
    return value


def content_payload(messages: list[dict[str, Any]]) -> str:
    """Canonical content identity: roles + text + variant ids; no file_id / cache_control / data URI."""
    cleaned: list[dict[str, Any]] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        keep = {
            key: _strip_transport_meta(value)
            for key, value in message.items()
            if key in {"role", "content", "tool_calls", "name", "tool_call_id"} or key in _REPLAY_KEYS
        }
        cleaned.append(keep)
    return canonical_wire_payload(cleaned)


def _prompt_cache_key(route: ResolvedRoute, header_material: dict[str, Any]) -> str:
    sid = (route.session_id or "").strip() or "session"
    # Child session IDs can exceed the provider's 64-character key limit.
    if len(sid) > 44:
        sid = digest_text(sid)
    digest = digest_text(canonical_json(header_material))
    return f"em_{sid}-{digest[:16]}"


def _build_header(
    route: ResolvedRoute,
    envelope: RequestEnvelope,
    wire: list[dict[str, Any]],
    *,
    transport: str,
) -> RequestHeader:
    # Files may replace an inline image with a remote ID. Compare its original
    # attachment/variant projection, not the replaceable transport reference.
    projected = getattr(envelope, "messages", None)
    payload = content_payload(projected if isinstance(projected, list) and projected else wire)
    tools = list(getattr(envelope, "tools", None) or [])
    identity = getattr(envelope, "identity", None)
    catalog = str(getattr(identity, "catalog_digest", "") or "") if identity is not None else ""
    system = getattr(envelope, "system_head", None) or getattr(envelope, "system", None) or ""
    route_fp = digest_text(canonical_json(route.route_fingerprint_material()))
    cache_policy = digest_text(canonical_json({
        "transport": transport,
        "protocol": route.protocol,
        "files": bool(route.capabilities.get("files")),
    }))
    material = {
        "route": route_fp,
        "tools": digest_tools(tools),
        "catalog": catalog,
        "system": digest_text(str(system)),
        "cache_policy": cache_policy,
    }
    return RequestHeader(
        route_fingerprint=route_fp,
        tools_digest=digest_tools(tools),
        catalog_digest=catalog,
        system_head_digest=digest_text(str(system)),
        content_identity=digest_text(payload),
        content_payload=payload,
        cache_policy_digest=cache_policy,
        transport="file" if transport == "file" else "inline",
        prompt_cache_key=_prompt_cache_key(route, material),
    )


def _omit_degraded_keys(body: dict[str, Any], route: ResolvedRoute) -> dict[str, Any]:
    from excelmanus.engine_core.llm_caller import degraded_params

    skip = set(degraded_params(route.protocol_label(), route.model))
    skip |= set(degraded_params(route.protocol, route.model))
    if not skip:
        return body
    return {key: value for key, value in body.items() if key not in skip}


def _provider_body(
    route: ResolvedRoute,
    envelope: RequestEnvelope,
    wire: list[dict[str, Any]],
    header: RequestHeader,
    extra: dict[str, Any] | None,
) -> dict[str, Any]:
    extra = dict(extra or {})
    if route.protocol == "openai_responses":
        previous = extra.get("_responses_previous_response_id") or extra.get("previous_response_id")
        nested = dict(extra.get("extra_body") or {})
        # Only a response associated with this exact history and credential may
        # omit the preceding messages. Old/unscoped snapshots fall back to full replay.
        valid = False
        projected = envelope.messages
        for index, message in enumerate(projected):
            state = message.get("replay_state") or {}
            source = message.get("replay_source") or {}
            if previous and isinstance(state, dict) and state.get("response_id") == previous:
                valid = (
                    source.get("protocol") == route.protocol and source.get("model") == route.model
                    and source.get("credential_scope") == route.credential_scope
                    and source.get("request_content_identity") == digest_text(content_payload(projected[:index]))
                    and source.get("output_identity") == digest_text(content_payload([message]))
                )
        if not valid or not route.capabilities.get("stored_responses"):
            extra.pop("_responses_previous_response_id", None)
            extra.pop("previous_response_id", None)
            nested.pop("previous_response_id", None)
        if not route.capabilities.get("stored_responses"):
            extra.pop("_responses_background", None)
            extra["_responses_store"] = False
            nested.pop("background", None)
            nested.pop("store", None)
        if nested or "extra_body" in extra:
            extra["extra_body"] = nested
    body: dict[str, Any] = {
        "model": route.model,
        "messages": list(wire),
    }
    tools = list(getattr(envelope, "tools", None) or [])
    if tools:
        body["tools"] = tools
    if route.capabilities.get("prompt_cache_key") and header.prompt_cache_key:
        body["prompt_cache_key"] = header.prompt_cache_key
    if extra:
        for key, value in extra.items():
            if value is not None:
                body[key] = value
    from excelmanus.providers.request_body import compile_provider_body

    native = compile_provider_body(route.protocol, _omit_degraded_keys(body, route))
    from excelmanus.engine_core.llm_caller import degraded_params

    if not route.capabilities.get("prompt_cache_key") or "prompt_cache_key" in (
        degraded_params(route.protocol_label(), route.model) | degraded_params(route.protocol, route.model)
    ):
        native.pop("prompt_cache_key", None)
        if isinstance(native.get("extra_body"), dict):
            native["extra_body"].pop("prompt_cache_key", None)
    return native


def _without_cache_markers(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _without_cache_markers(item) for key, item in value.items() if key != "cache_control"}
    if isinstance(value, list):
        return [_without_cache_markers(item) for item in value]
    return value


def _cache_marker_policies(value: Any) -> set[str]:
    """Marker positions grow with history; TTL/mode changes are policy changes."""
    policies: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "cache_control":
                policies.add(canonical_json(item))
            else:
                policies.update(_cache_marker_policies(item))
    elif isinstance(value, list):
        for item in value:
            policies.update(_cache_marker_policies(item))
    return policies


def _native_header(
    route: ResolvedRoute, header: RequestHeader, body: dict[str, Any], wire: list[dict[str, Any]],
) -> RequestHeader:
    """Measure the compiled provider request, without hashing growing history into its key."""
    from excelmanus.attachments.files_api import collect_wire_file_ids

    message_key = {"openai_responses": "input", "gemini": "contents", "antigravity": "contents"}.get(
        route.protocol, "messages",
    )
    # These fields govern delivery/accounting/sampling, not the rendered prefix.
    non_prefix = {
        message_key, "prompt_cache_key", "prompt_cache_options", "prompt_cache_retention", "cache_control",
        "previous_response_id", "stream", "stream_options", "store", "background", "metadata",
        "max_tokens", "max_output_tokens", "temperature", "top_p", "seed", "service_tier",
        "safety_identifier", "user", "extra_headers",
    }
    effective = dict(body)
    extra_body = effective.pop("extra_body", None)
    if isinstance(extra_body, dict):
        effective.update(extra_body)
    settings = {key: _without_cache_markers(value) for key, value in effective.items() if key not in non_prefix}
    config_digest = digest_text(canonical_json(settings))
    cache_options = {
        key: effective[key] for key in ("cache_control", "prompt_cache_options", "prompt_cache_retention")
        if key in effective
    }
    options = cache_options.get("prompt_cache_options")
    if isinstance(options, dict):
        cache_options["prompt_cache_options"] = {key: value for key, value in options.items()
                                                  if key != "comparison_response_id"}
    policy_digest = digest_text(canonical_json({
        "transport": header.transport,
        **cache_options,
        "marker_policies": sorted(_cache_marker_policies(effective)),
        "key_enabled": "prompt_cache_key" in effective,
    }))
    hashes = []
    for message in effective.get(message_key) or []:
        # Claude/Gemini merge adjacent messages with the same role. Hash their
        # blocks so appending a new block does not rewrite the previous one.
        block_key = "parts" if message_key == "contents" else "content"
        blocks = message.get(block_key) if isinstance(message, dict) else None
        if route.protocol in {"anthropic", "gemini", "antigravity"} and isinstance(blocks, (list, str)):
            if isinstance(blocks, str):
                blocks = [{"type": "text", "text": blocks}]
            for block in blocks:
                item = {key: value for key, value in message.items() if key != block_key}
                item[block_key] = block
                hashes.append(digest_text(canonical_json(_without_cache_markers(item))))
        else:
            hashes.append(digest_text(canonical_json(message)))
    return replace(
        header, provider_config_digest=config_digest, provider_prefix=tuple(hashes),
        cache_policy_digest=policy_digest,
        continuation_id=str(effective.get("previous_response_id") or ""),
        file_ids=tuple(collect_wire_file_ids(wire)),
    )


def _finalize_cache_key(route: ResolvedRoute, header: RequestHeader, body: dict[str, Any]) -> RequestHeader:
    """The final body owns the sent key, including explicit overrides and degradation."""
    nested = body.get("extra_body")
    key = body.get("prompt_cache_key")
    if isinstance(nested, dict) and "prompt_cache_key" in nested:
        key = nested["prompt_cache_key"]
    # A custom key is preserved; auto-generated keys depend on native settings.
    if key == header.prompt_cache_key and key:
        key = _prompt_cache_key(route, {
            "route": header.route_fingerprint, "config": header.provider_config_digest,
            "cache_policy": header.cache_policy_digest,
        })
        if isinstance(nested, dict) and "prompt_cache_key" in nested:
            nested["prompt_cache_key"] = key
        else:
            body["prompt_cache_key"] = key
    if key is not None and (not isinstance(key, str) or not key.strip() or len(key) > 64):
        raise ValueError("prompt_cache_key 必须是 1–64 字符的非空字符串")
    return replace(header, prompt_cache_key=key or "", provider_digest=digest_text(canonical_json(body)))


async def compile_request(
    engine: Any,
    *,
    tool_access: str = "may_write",
    vision_capable: bool | None = None,
    extra: dict[str, Any] | None = None,
    event: str | None = None,
    envelope: RequestEnvelope | None = None,
) -> tuple[PreparedRequest | None, str | None]:
    """Compile atomically with respect to request state. The session actor owns this call."""
    fields = (
        "_last_envelope", "_prepared_request", "_resolved_route", "_compile_extra",
        "_envelope_system_head", "_envelope_system_effective", "_last_system_msgs",
        "_prompt_tool_snapshot", "_envelope_prefix_snapshot", "_restored_envelope_prefix",
        "_last_vision_capable", "_projection_generation", "_last_image_report",
        "_image_wire_pin_seq", "_files_wire_mode", "_files_inline_until", "_last_wire_messages",
    )
    missing = object()
    before = {key: getattr(engine, key, missing) for key in fields}
    series = series_of(engine)
    series_before = (series.series_id, series.last_accepted, list(series.events), list(series._pending))
    memory = getattr(engine, "_memory", None) or getattr(engine, "memory", None)
    memory_before = {key: getattr(memory, key, missing) for key in (
        "_projection_dirty", "_pending_anchor", "_wire_sent_tool_ids",
    )} if memory is not None else {}
    prepared = None
    try:
        prepared, error = await _compile_request(
            engine, tool_access=tool_access, vision_capable=vision_capable,
            extra=extra, event=event, envelope=envelope,
        )
        return prepared, error
    finally:
        if prepared is None:
            from excelmanus.prompt.assemble import rollback_prompt_dynamic

            rollback_prompt_dynamic(engine, getattr(engine, "_prompt_dynamic_appended_messages", None))
            for target, values in ((engine, before), (memory, memory_before)):
                for key, value in values.items():
                    if value is missing:
                        vars(target).pop(key, None)
                    else:
                        setattr(target, key, value)
            series.series_id, series.last_accepted, series.events, series._pending = series_before


async def _compile_request(
    engine: Any,
    *,
    tool_access: str = "may_write",
    vision_capable: bool | None = None,
    extra: dict[str, Any] | None = None,
    event: str | None = None,
    envelope: RequestEnvelope | None = None,
) -> tuple[PreparedRequest | None, str | None]:
    """Assemble + seal + header. Does not update last_accepted."""
    from excelmanus.compaction import handoff_from_memory

    _, handoff_error = handoff_from_memory(getattr(engine, "_memory", None))
    if handoff_error:
        return None, handoff_error + "；请恢复有效会话历史后继续。"
    if extra is None:
        extra = create_extra_from_engine(engine)
    engine._compile_extra = extra
    route = resolve_route(engine)
    engine._resolved_route = route
    series = series_of(engine)
    if event:
        if event in {"surface/compact", "route/change", "rollback/edit", "vision/change", "attachment_quota", "restore/migrate"}:
            series.start_new(event)
            from excelmanus.prompt.envelope import invalidate_envelope

            invalidate_envelope(engine)
        else:
            series.note(event)

    if vision_capable is None:
        vision_capable = bool(getattr(engine, "_is_vision_capable", True))

    if envelope is None:
        envelope, error = assemble_envelope(
            engine,
            tool_access=tool_access,
            vision_capable=vision_capable,
            commit_dynamic=False,
        )
        if error is not None or envelope is None:
            return None, error or "系统上下文组装失败"

    report = getattr(engine, "_last_image_report", None) or {}
    if report.get("required_omitted") and "attachment_quota" not in {
        ev.get("type") for ev in series.events[-8:] if isinstance(ev, dict)
    }:
        from excelmanus.prompt.assemble import rollback_prompt_dynamic

        # 第一次投影只用于发现附件配额问题；在重编译前撤回其动态
        # context，避免同一条 hook/mention 在 durable history 中出现两次。
        rollback_prompt_dynamic(
            engine,
            getattr(engine, "_prompt_dynamic_appended_messages", None),
        )
        engine._prompt_dynamic_appended_messages = []
        series.start_new("attachment_quota")
        from excelmanus.prompt.envelope import invalidate_envelope

        invalidate_envelope(engine)
        engine._image_wire_pin_seq = ()
        envelope, error = assemble_envelope(
            engine,
            tool_access=tool_access,
            vision_capable=vision_capable,
            commit_dynamic=False,
        )
        if error is not None or envelope is None:
            return None, error or "附件配额重装失败"

    sealed, seal_error = await seal_envelope(
        engine,
        envelope,
        model=route.model,
        protocol=route.protocol_label(),
        call_config=dict(route.call_config),
        route=route,
    )
    if seal_error is not None or sealed is None:
        from excelmanus.prompt.assemble import rollback_prompt_dynamic

        rollback_prompt_dynamic(
            engine,
            getattr(engine, "_prompt_dynamic_appended_messages", None),
        )
        return None, seal_error or "请求封口失败"

    wire = list(getattr(sealed, "wire_messages", None) or [])
    transport = str(getattr(sealed, "transport", None) or "inline")
    header = _build_header(route, sealed, wire, transport=transport)
    from excelmanus.attachments.files_api import collect_wire_file_ids

    try:
        reserved = {"messages", "tools", "model", "system", "instructions", "input", "contents", "systemInstruction"}
        if reserved.intersection(((extra or {}).get("extra_body") or {})):
            from excelmanus.prompt.assemble import rollback_prompt_dynamic

            rollback_prompt_dynamic(
                engine,
                getattr(engine, "_prompt_dynamic_appended_messages", None),
            )
            return None, "自定义请求体不能覆盖消息、工具、模型或系统策略"
        native_body = _provider_body(route, sealed, wire, header, extra)
        header = _native_header(route, header, native_body, wire)
        header = _finalize_cache_key(route, header, native_body)
    except (TypeError, ValueError) as exc:
        from excelmanus.prompt.assemble import rollback_prompt_dynamic

        rollback_prompt_dynamic(
            engine,
            getattr(engine, "_prompt_dynamic_appended_messages", None),
        )
        return None, f"协议请求编译失败：{exc}"
    prefix_error = series.check_prefix(header)
    if prefix_error:
        from excelmanus.prompt.assemble import rollback_prompt_dynamic

        rollback_prompt_dynamic(engine, getattr(engine, "_prompt_dynamic_appended_messages", None))
        return None, prefix_error
    sealed = replace(sealed, prompt_cache_key=header.prompt_cache_key)
    prepared = PreparedRequest(
        request_id=uuid4().hex,
        series_id=series.series_id,
        attempt=int(getattr(engine, "_request_attempt", 0) or 0) + 1,
        header=header,
        route=route,
        provider_body=native_body,
        file_leases=tuple(collect_wire_file_ids(wire)),
        compiled_at=time.time(),
        transport_headers=(extra or {}).get("extra_headers") or {},
    )
    engine._prepared_request = prepared
    engine._last_envelope = sealed
    from excelmanus.prompt.cache_restore import remember_prefix_snapshot

    remember_prefix_snapshot(engine, sealed)
    from excelmanus.prompt.assemble import commit_prompt_dynamic

    commit_prompt_dynamic(engine)
    engine._prompt_dynamic_appended_messages = []
    return prepared, None
