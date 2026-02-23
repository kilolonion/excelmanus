"""模型能力探测：启动时或切换模型后自动检测 tool_calling / vision / thinking 支持。

通过向 LLM 发送最小化请求来探测实际能力，避免硬编码模型名。
探测结果缓存到 SQLite，相同 (model, base_url) 不重复探测。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

import openai

from excelmanus.providers import (
    ClaudeClient,
    GeminiClient,
    OpenAIResponsesClient,
)

logger = logging.getLogger(__name__)

# 1x1 white pixel PNG — 最小合法图片
_TINY_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4"
    "2mP8/5+hHgAHggJ/PchI7wAAAABJRU5ErkJggg=="
)


def _cap_key(model: str, base_url: str) -> str:
    raw = f"{model.strip().lower()}|{base_url.strip().rstrip('/')}"
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


@dataclass
class ModelCapabilities:
    """模型能力探测结果。"""

    model: str = ""
    base_url: str = ""
    healthy: bool | None = None
    health_error: str = ""
    supports_tool_calling: bool | None = None
    supports_vision: bool | None = None
    supports_thinking: bool | None = None
    thinking_type: str = ""  # "claude"|"gemini"|"deepseek"|"enable_thinking"|"glm_thinking"|"openrouter"|""
    detected_at: str = ""
    probe_errors: dict[str, str] = field(default_factory=dict)
    manual_override: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ModelCapabilities:
        known = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in d.items() if k in known})


# ── 探测实现 ──────────────────────────────────────────────────


async def probe_health(
    client: Any,
    model: str,
    timeout: float = 15.0,
) -> tuple[bool, str]:
    """最小化健康检查：发一条 Hi 看模型是否可达、鉴权是否正常。"""
    messages = [{"role": "user", "content": "Hi"}]
    try:
        if isinstance(client, (GeminiClient, ClaudeClient)):
            await asyncio.wait_for(
                client.chat.completions.create(model=model, messages=messages),
                timeout=timeout,
            )
        else:
            await asyncio.wait_for(
                client.chat.completions.create(
                    model=model, messages=messages, max_tokens=5,
                ),
                timeout=timeout,
            )
        return True, ""
    except Exception as exc:
        return False, str(exc)[:200]


async def probe_tool_calling(
    client: Any,
    model: str,
    timeout: float = 30.0,
) -> tuple[bool, str]:
    """探测模型是否支持 tool calling，返回 (supported, error_msg)。"""
    tool_def = {
        "type": "function",
        "function": {
            "name": "test_add",
            "description": "Add two numbers",
            "parameters": {
                "type": "object",
                "properties": {
                    "a": {"type": "number"},
                    "b": {"type": "number"},
                },
                "required": ["a", "b"],
            },
        },
    }
    messages = [{"role": "user", "content": "What is 2+3? Use the tool."}]

    try:
        if isinstance(client, (GeminiClient, ClaudeClient)):
            resp = await asyncio.wait_for(
                client.chat.completions.create(
                    model=model,
                    messages=messages,
                    tools=[tool_def],
                ),
                timeout=timeout,
            )
        else:
            resp = await asyncio.wait_for(
                client.chat.completions.create(
                    model=model,
                    messages=messages,
                    tools=[tool_def],
                    max_tokens=100,
                ),
                timeout=timeout,
            )

        # 有些模型即使不 call tool 也不报错，只要不报错就算支持
        msg = _extract_message(resp)
        tc = getattr(msg, "tool_calls", None)
        return True, ""
    except Exception as exc:
        err = str(exc)[:200]
        if _is_param_unsupported_error(err):
            return False, err
        logger.debug("tool_calling 探测异常: %s", err)
        return None, err


async def probe_vision(
    client: Any,
    model: str,
    timeout: float = 30.0,
) -> tuple[bool, str]:
    """探测模型是否支持图片输入。"""
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Describe this image in one word."},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/png;base64,{_TINY_PNG_B64}",
                    },
                },
            ],
        }
    ]

    try:
        if isinstance(client, GeminiClient):
            resp = await asyncio.wait_for(
                client.chat.completions.create(model=model, messages=messages),
                timeout=timeout,
            )
        elif isinstance(client, ClaudeClient):
            resp = await asyncio.wait_for(
                client.chat.completions.create(model=model, messages=messages),
                timeout=timeout,
            )
        else:
            resp = await asyncio.wait_for(
                client.chat.completions.create(
                    model=model, messages=messages, max_tokens=20,
                ),
                timeout=timeout,
            )
        return True, ""
    except Exception as exc:
        err = str(exc)[:200]
        if _is_param_unsupported_error(err):
            return False, err
        logger.debug("vision 探测异常: %s", err)
        return None, err


async def probe_thinking(
    client: Any,
    model: str,
    base_url: str,
    timeout: float = 45.0,
) -> tuple[bool, str, str]:
    """探测模型是否支持输出思考过程。

    返回 (supported, error_msg, thinking_type)。
    thinking_type: "claude"|"gemini"|"deepseek"|"enable_thinking"|"glm_thinking"|"openrouter"|""
    """
    messages = [{"role": "user", "content": "What is 17*23? Think step by step."}]

    # ── Claude: 尝试 extended thinking ──
    if isinstance(client, ClaudeClient):
        return await _probe_claude_thinking(client, model, messages, timeout)

    # ── Gemini: 尝试 thinkingConfig ──
    if isinstance(client, GeminiClient):
        return await _probe_gemini_thinking(client, model, messages, timeout)

    # ── OpenAI 兼容: 多策略探测 reasoning_content / reasoning 字段 ──
    return await _probe_openai_thinking(client, model, messages, timeout, base_url)


async def _probe_claude_thinking(
    client: ClaudeClient,
    model: str,
    messages: list[dict],
    timeout: float,
) -> tuple[bool, str, str]:
    """Claude extended thinking 探测。"""
    from excelmanus.providers.claude import StreamDelta

    try:
        stream = await asyncio.wait_for(
            client.chat.completions.create(
                model=model,
                messages=messages,
                stream=True,
                _thinking_enabled=True,
                _thinking_budget=2048,
            ),
            timeout=timeout,
        )
        found_thinking = False
        async for chunk in stream:
            if isinstance(chunk, StreamDelta) and chunk.thinking_delta:
                found_thinking = True
                break
            content = getattr(chunk, "content_delta", "") if isinstance(chunk, StreamDelta) else ""
            if content:
                break
        return found_thinking, "", "claude" if found_thinking else ""
    except Exception as exc:
        err = str(exc)[:200]
        logger.debug("Claude thinking 探测异常: %s", err)
        if _is_fatal_probe_error(err):
            return None, err, ""
        return False, err, ""


async def _probe_gemini_thinking(
    client: GeminiClient,
    model: str,
    messages: list[dict],
    timeout: float,
) -> tuple[bool, str, str]:
    """Gemini thinking 探测。"""
    from excelmanus.providers.stream_types import StreamDelta

    try:
        stream = await asyncio.wait_for(
            client.chat.completions.create(
                model=model,
                messages=messages,
                stream=True,
                _thinking_budget=2048,
            ),
            timeout=timeout,
        )
        found_thinking = False
        async for chunk in stream:
            if isinstance(chunk, StreamDelta) and chunk.thinking_delta:
                found_thinking = True
                break
        return found_thinking, "", "gemini" if found_thinking else ""
    except Exception as exc:
        err = str(exc)[:200]
        logger.debug("Gemini thinking 探测异常: %s", err)
        if _is_fatal_probe_error(err):
            return None, err, ""
        return False, err, ""


async def _probe_openai_thinking(
    client: Any,
    model: str,
    messages: list[dict],
    timeout: float,
    base_url: str = "",
) -> tuple[bool, str, str]:
    """OpenAI 兼容 API 多策略思考探测。

    按 provider 特征依次尝试不同的 thinking 启用参数，
    流式检查 delta 中是否出现 reasoning_content/reasoning/thinking 字段。
    """
    provider = _detect_openai_provider(base_url)
    strategies = _get_thinking_strategies(provider, model)

    last_err = ""
    hit_fatal = False
    for strategy_name, extra_kwargs, thinking_type in strategies:
        logger.debug("思考探测策略: %s (provider=%s, model=%s)", strategy_name, provider, model)
        ok, err = await _try_thinking_stream(client, model, messages, timeout, extra_kwargs)
        if ok:
            logger.info("思考探测成功: strategy=%s → thinking_type=%s", strategy_name, thinking_type)
            return True, "", thinking_type
        if err:
            last_err = err
            if _is_param_unsupported_error(err):
                continue
            if _is_fatal_probe_error(err):
                hit_fatal = True
                break

    if last_err:
        logger.debug("所有思考探测策略均失败 (provider=%s): %s", provider, last_err)
    if hit_fatal:
        return None, last_err, ""
    return False, last_err, ""


async def run_full_probe(
    client: Any,
    model: str,
    base_url: str,
    skip_if_cached: bool = True,
    db: Any = None,
) -> ModelCapabilities:
    """运行完整的三项能力探测，返回 ModelCapabilities。

    如果 db 中有缓存且 skip_if_cached=True，直接返回缓存。
    """
    cap_id = _cap_key(model, base_url)

    if skip_if_cached and db is not None:
        cached = load_capabilities(db, model, base_url)
        if cached is not None:
            logger.info(
                "模型 %s 能力探测已缓存（tool=%s, vision=%s, thinking=%s）",
                model,
                cached.supports_tool_calling,
                cached.supports_vision,
                cached.supports_thinking,
            )
            return cached

    logger.info("开始探测模型 %s 的能力...", model)
    caps = ModelCapabilities(
        model=model,
        base_url=base_url,
        detected_at=datetime.now(tz=timezone.utc).isoformat(),
    )

    # ── 先做健康检查：模型不可达则跳过能力探测 ──
    health_ok, health_err = await probe_health(client, model)
    caps.healthy = health_ok
    caps.health_error = health_err

    if not health_ok:
        caps.probe_errors["health"] = health_err
        logger.warning("模型 %s 健康检查失败，跳过能力探测: %s", model, health_err)
        if db is not None:
            save_capabilities(db, caps)
        return caps

    # ── 健康检查通过，并发探测三项能力 ──
    tool_task = asyncio.create_task(probe_tool_calling(client, model))
    vision_task = asyncio.create_task(probe_vision(client, model))
    thinking_task = asyncio.create_task(probe_thinking(client, model, base_url))

    tool_ok, tool_err = await tool_task
    vision_ok, vision_err = await vision_task
    thinking_ok, thinking_err, thinking_type = await thinking_task

    caps.supports_tool_calling = tool_ok
    caps.supports_vision = vision_ok
    caps.supports_thinking = thinking_ok
    caps.thinking_type = thinking_type

    if tool_err:
        caps.probe_errors["tool_calling"] = tool_err
    if vision_err:
        caps.probe_errors["vision"] = vision_err
    if thinking_err:
        caps.probe_errors["thinking"] = thinking_err

    logger.info(
        "模型 %s 探测完成: tool_calling=%s, vision=%s, thinking=%s (type=%s)",
        model,
        caps.supports_tool_calling,
        caps.supports_vision,
        caps.supports_thinking,
        caps.thinking_type,
    )

    if db is not None:
        save_capabilities(db, caps)

    return caps


# ── DB 持久化 ────────────────────────────────────────────────


def save_capabilities(db: Any, caps: ModelCapabilities) -> None:
    """将能力探测结果存入 config_kv 表。"""
    key = f"model_caps:{_cap_key(caps.model, caps.base_url)}"
    try:
        db.conn.execute(
            "INSERT OR REPLACE INTO config_kv (key, value, updated_at) VALUES (?, ?, ?)",
            (key, json.dumps(caps.to_dict(), ensure_ascii=False), caps.detected_at or datetime.now(tz=timezone.utc).isoformat()),
        )
        db.conn.commit()
    except Exception:
        logger.warning("保存模型能力探测结果失败", exc_info=True)


def load_capabilities(db: Any, model: str, base_url: str) -> ModelCapabilities | None:
    """从 config_kv 表加载缓存的能力探测结果。"""
    key = f"model_caps:{_cap_key(model, base_url)}"
    try:
        row = db.conn.execute(
            "SELECT value FROM config_kv WHERE key = ?", (key,)
        ).fetchone()
        if row:
            return ModelCapabilities.from_dict(json.loads(row[0]))
    except Exception:
        logger.debug("加载模型能力探测缓存失败", exc_info=True)
    return None


def delete_capabilities(db: Any, model: str, base_url: str) -> None:
    """删除指定模型的能力缓存，强制下次重新探测。"""
    key = f"model_caps:{_cap_key(model, base_url)}"
    try:
        db.conn.execute("DELETE FROM config_kv WHERE key = ?", (key,))
        db.conn.commit()
    except Exception:
        logger.debug("删除模型能力缓存失败", exc_info=True)


def update_capabilities_override(
    db: Any,
    model: str,
    base_url: str,
    overrides: dict[str, Any],
) -> ModelCapabilities | None:
    """手动覆盖特定能力标记（前端设置用）。"""
    caps = load_capabilities(db, model, base_url)
    if caps is None:
        caps = ModelCapabilities(model=model, base_url=base_url)

    for k, v in overrides.items():
        if hasattr(caps, k):
            setattr(caps, k, v)
    caps.manual_override = True
    caps.detected_at = datetime.now(tz=timezone.utc).isoformat()
    save_capabilities(db, caps)
    return caps


# ── 工具函数 ──────────────────────────────────────────────────


def _extract_message(resp: Any) -> Any:
    choices = getattr(resp, "choices", None)
    if choices:
        return getattr(choices[0], "message", None)
    return None


def _is_param_unsupported_error(err: str) -> bool:
    lowered = err.lower()
    keywords = [
        "tools",
        "tool_choice",
        "not support",
        "unsupported",
        "unknown parameter",
        "unknown variant",
        "unrecognized",
        "invalid parameter",
        "does not support",
        "not available",
        "not allowed",
        "image_url",
    ]
    return any(kw in lowered for kw in keywords)


def _is_fatal_probe_error(err: str) -> bool:
    """认证/鉴权/余额不足/限流等错误，继续尝试其他策略也无意义。"""
    lowered = err.lower()
    keywords = [
        "unauthorized", "401", "402", "403", "forbidden",
        "invalid api", "authentication", "rate limit", "429",
        "payment_required", "insufficient quota", "quota exceeded",
        "billing", "balance",
    ]
    return any(kw in lowered for kw in keywords)


# ── Provider 检测与 Thinking 策略 ─────────────────────────────────


_PROVIDER_URL_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("dashscope", re.compile(r"dashscope", re.IGNORECASE)),
    ("deepseek", re.compile(r"deepseek", re.IGNORECASE)),
    ("glm", re.compile(r"z\.ai|zhipuai|bigmodel|chatglm", re.IGNORECASE)),
    ("siliconflow", re.compile(r"siliconflow", re.IGNORECASE)),
    ("openrouter", re.compile(r"openrouter\.ai", re.IGNORECASE)),
    ("xai", re.compile(r"api\.x\.ai", re.IGNORECASE)),
    ("together", re.compile(r"together", re.IGNORECASE)),
    ("groq", re.compile(r"groq\.com", re.IGNORECASE)),
]


def _detect_openai_provider(base_url: str) -> str:
    """从 base_url 推断 OpenAI 兼容 provider 类型。"""
    for name, pattern in _PROVIDER_URL_PATTERNS:
        if pattern.search(base_url):
            return name
    return "generic"


def _get_thinking_strategies(
    provider: str, model: str,
) -> list[tuple[str, dict[str, Any], str]]:
    """返回 (策略名, extra_create_kwargs, thinking_type) 列表。

    按优先级排序：最可能的策略在前。
    thinking_type 会存入 ModelCapabilities，engine 据此注入请求参数。
    """
    strategies: list[tuple[str, dict[str, Any], str]] = []
    model_lower = model.lower()

    # ── 按 provider 添加专属策略 ────────────────────────────
    if provider == "dashscope":
        # 阿里百炼 / Qwen: extra_body.enable_thinking
        strategies.append((
            "dashscope_enable",
            {"extra_body": {"enable_thinking": True}},
            "enable_thinking",
        ))
    elif provider == "glm":
        # 智谱 GLM: extra_body.thinking.type
        strategies.append((
            "glm_thinking",
            {"extra_body": {"thinking": {"type": "enabled"}}},
            "glm_thinking",
        ))
    elif provider == "siliconflow":
        # 硅基流动: enable_thinking + thinking_budget
        strategies.append((
            "sf_enable",
            {"extra_body": {"enable_thinking": True, "thinking_budget": 2048}},
            "enable_thinking",
        ))
    elif provider == "deepseek":
        # DeepSeek: reasoner 自动输出, V3+ 可用 enable_thinking
        strategies.append(("plain", {}, "deepseek"))
        strategies.append((
            "ds_enable",
            {"extra_body": {"enable_thinking": True}},
            "enable_thinking",
        ))
    elif provider == "openrouter":
        # OpenRouter 统一 reasoning 参数
        strategies.append((
            "or_reasoning",
            {"extra_body": {"reasoning": {"max_tokens": 2048}}},
            "openrouter",
        ))
        strategies.append(("plain", {}, "deepseek"))
    elif provider == "xai":
        # xAI Grok: 仅 grok-3-mini 返回 reasoning_content
        if "mini" in model_lower:
            strategies.append(("plain", {}, "deepseek"))

    # ── 通用兜底策略 ──────────────────────────────────────
    # 1) 纯流式检查（捕获自动输出推理的模型，如 DeepSeek-R1、QwQ）
    if not any(s[0] == "plain" for s in strategies):
        strategies.append(("plain", {}, "deepseek"))

    # 2) 如果还没试过 enable_thinking，再试一次（很多中转平台支持）
    if not any("enable" in s[0] for s in strategies):
        strategies.append((
            "enable_fallback",
            {"extra_body": {"enable_thinking": True}},
            "enable_thinking",
        ))

    return strategies


async def _try_thinking_stream(
    client: Any,
    model: str,
    messages: list[dict],
    timeout: float,
    extra_kwargs: dict[str, Any],
) -> tuple[bool, str]:
    """尝试一种 thinking 策略：发起流式请求，检查 delta 是否含推理字段。"""
    try:
        stream = await asyncio.wait_for(
            client.chat.completions.create(
                model=model,
                messages=messages,
                stream=True,
                max_tokens=300,
                **extra_kwargs,
            ),
            timeout=timeout,
        )

        found = False
        deadline = time.monotonic() + timeout
        async for chunk in stream:
            if time.monotonic() > deadline:
                break

            choices = getattr(chunk, "choices", None)
            if not choices:
                continue
            delta = getattr(choices[0], "delta", None)
            if delta is None:
                continue

            for key in ("reasoning_content", "reasoning", "thinking"):
                val = getattr(delta, key, None)
                if val:
                    found = True
                    break

            if found:
                break

            content = getattr(delta, "content", None)
            if content:
                break

        return found, ""
    except Exception as exc:
        return False, str(exc)[:200]
