"""Pure native request compilation, shared by direct calls and prepared dispatch."""
from __future__ import annotations

from typing import Any


def claude_body(model: str, messages: list, tools: Any = None, *, tool_choice: Any = None,
                thinking_enabled: bool = False, thinking_budget: int = 0, thinking_effort: str = "",
                extra_body: dict | None = None, stream: bool = False, max_tokens: int | None = None) -> dict:
    from excelmanus.providers.claude import (_openai_messages_to_claude, _openai_tools_to_claude,
        _map_openai_tool_choice_to_claude, _apply_thinking_to_body, _strip_non_claude_extra_body,
        _DEFAULT_MAX_TOKENS)
    system, contents = _openai_messages_to_claude(messages)
    body = {"model": model, "messages": contents, "max_tokens": max_tokens or _DEFAULT_MAX_TOKENS}
    _apply_thinking_to_body(body, model, thinking_enabled=thinking_enabled, thinking_budget=thinking_budget,
                            thinking_effort=thinking_effort)
    if system:
        body["system"] = system
    schemas = _openai_tools_to_claude(tools if isinstance(tools, list) else None)
    if schemas:
        body["tools"] = schemas
    choice = _map_openai_tool_choice_to_claude(tool_choice)
    if choice is not None:
        body["tool_choice"] = choice
    if extra_body:
        body.update(_strip_non_claude_extra_body(extra_body))
    if max_tokens is not None and max_tokens > 0:
        # Thinking defaults must not silently enlarge a user-specified output cap.
        body["max_tokens"] = max_tokens
        thinking = body.get("thinking")
        if isinstance(thinking, dict) and thinking.get("type") == "enabled":
            if max_tokens <= 1024:
                body.pop("thinking", None)
            elif isinstance(thinking.get("budget_tokens"), int):
                body["thinking"] = {**thinking, "budget_tokens": min(thinking["budget_tokens"], max_tokens - 1)}
    if stream:
        body["stream"] = True
    return body


def gemini_body(model: str, messages: list, tools: Any = None, *, tool_choice: Any = None,
                thinking_budget: int = 0, thinking_level: str = "", extra_body: dict | None = None) -> dict:
    from excelmanus.providers.gemini import (_openai_messages_to_gemini, _openai_tools_to_gemini,
        _map_openai_tool_choice_to_gemini, _normalize_gemini_thinking_level, _strip_non_gemini_extra_body)
    system, contents = _openai_messages_to_gemini(messages)
    body = {"contents": contents}
    if system:
        body["systemInstruction"] = system
    schemas = _openai_tools_to_gemini(tools if isinstance(tools, list) else None)
    if schemas:
        body["tools"] = schemas
    choice = _map_openai_tool_choice_to_gemini(tool_choice)
    if choice is not None:
        body["toolConfig"] = choice
    config = {}
    if thinking_level:
        config["thinkingConfig"] = {"thinkingLevel": _normalize_gemini_thinking_level(model, thinking_level)}
    elif thinking_budget > 0:
        config["thinkingConfig"] = {"thinkingBudget": thinking_budget}
    if extra_body:
        config.update(_strip_non_gemini_extra_body(extra_body))
    if config:
        body["generationConfig"] = config
    return body


def responses_body(model: str, messages: list, tools: Any = None, *, tool_choice: Any = None,
                   extra_kwargs: dict | None = None) -> dict:
    from excelmanus.providers.openai_responses import (_chat_messages_to_responses_input,
        _chat_tools_to_responses_tools, _map_chat_tool_choice_to_responses, _apply_chat_kwargs_to_responses_body)
    extras = dict(extra_kwargs or {})
    previous_response_id = (
        extras.get("_responses_previous_response_id")
        or extras.get("previous_response_id")
    )
    source_messages = messages
    if isinstance(previous_response_id, str) and previous_response_id.strip():
        # A stored Responses response already contains its preceding output.
        # Send only system instructions plus messages after that response.
        cut_at: int | None = None
        for index, message in enumerate(messages):
            if not isinstance(message, dict):
                continue
            state = message.get("replay_state")
            if isinstance(state, dict) and state.get("response_id") == previous_response_id:
                cut_at = index + 1
        if cut_at is not None:
            system_messages = [
                item for item in messages
                if isinstance(item, dict) and item.get("role") == "system"
            ]
            source_messages = [*system_messages, *messages[cut_at:]]
        else:
            # Never combine a stored response with its complete replay: it
            # duplicates history on the server. Direct steer uses another path.
            extras.pop("_responses_previous_response_id", None)
            extras.pop("previous_response_id", None)
    instructions, contents = _chat_messages_to_responses_input(source_messages)
    body: dict[str, Any] = {
        "model": model,
        "input": contents,
        "stream": True,
        "store": bool(extras.get("_responses_store", False)),
    }
    if instructions:
        body["instructions"] = instructions
    schemas = _chat_tools_to_responses_tools(tools if isinstance(tools, list) else None)
    if schemas:
        body["tools"] = schemas
    choice = _map_chat_tool_choice_to_responses(tool_choice)
    if choice is not None:
        body["tool_choice"] = choice
    _apply_chat_kwargs_to_responses_body(body, extras)
    return body


def compile_provider_body(protocol: str, chat: dict) -> dict:
    from copy import deepcopy
    args = deepcopy(chat)
    from excelmanus.providers.thinking import reject_unsupported_media
    reject_unsupported_media(args.get("messages", []))
    messages = args.get("messages", [])
    model = args["model"]
    # Replay blobs belong to a provider/model. Never replay them on another route.
    for message in messages:
        source = message.pop("replay_source", None)
        if not source or source.get("protocol") != protocol or source.get("model") != model:
            message.pop("replay_state", None)
        if protocol == "openai":
            for key in ("replay_state", "signature", "thinking", "reasoning", "thinking_text"):
                message.pop(key, None)
    if protocol == "anthropic":
        return claude_body(model, messages, args.get("tools"), tool_choice=args.get("tool_choice"),
            thinking_enabled=args.get("_thinking_enabled", False), thinking_budget=args.get("_thinking_budget", 0),
            thinking_effort=args.get("_thinking_effort") or "", extra_body=args.get("extra_body"), max_tokens=args.get("max_tokens"))
    if protocol in ("gemini", "antigravity"):
        # Antigravity 复用 Gemini 载荷编译；v1internal 信封由客户端包装。
        return gemini_body(model, messages, args.get("tools"), tool_choice=args.get("tool_choice"),
            thinking_budget=args.get("_thinking_budget", 0), thinking_level=args.get("_thinking_level") or "",
            extra_body=args.get("extra_body"))
    if protocol == "openai_responses":
        return responses_body(model, messages, args.get("tools"), tool_choice=args.get("tool_choice"), extra_kwargs=args)
    return {key: value for key, value in args.items() if not key.startswith("_thinking") and key != "extra_headers"}
