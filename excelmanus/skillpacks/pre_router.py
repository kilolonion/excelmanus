"""Skillpack 小模型预路由器。

在第一轮 LLM 调用前，用小模型预判最佳 skillpack，
精准注入对应工具集，减少主模型首轮 token 消耗。
"""

from __future__ import annotations

import json
import re
import time
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# 有效技能名称
VALID_SKILL_NAMES: frozenset[str] = frozenset({
    "general_excel", "data_basic", "chart_basic",
    "format_basic", "file_ops", "sheet_ops", "excel_code_runner",
})

# 不需要 skillpack 的场景关键词
_NO_SKILL_PATTERNS = re.compile(
    r"^(你好|hello|hi|嗨|hey|谢谢|感谢|再见|bye|帮助|help|你是谁|你能做什么)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class PreRouteResult:
    """预路由结果。"""
    skill_name: str | None  # None 表示不需要 skillpack（闲聊等）
    confidence: float  # 0.0 ~ 1.0
    reason: str
    latency_ms: float  # 路由耗时（毫秒）
    model_used: str  # 使用的模型名称
    raw_response: str = ""  # 原始响应（调试用）


_SKILL_CATALOG_PROMPT = """可用技能包：
- general_excel: 通用 Excel 助手兜底，覆盖数据读写、统计分析、筛选排序、图表可视化、格式美化等跨领域操作
- data_basic: 数据读取、分析、筛选、转换、排序、统计、分组、汇总
- chart_basic: 图表生成（折线图、柱状图、饼图、雷达图、散点图等）
- format_basic: 格式化与样式（颜色、字体、边框、填充、合并单元格、行列尺寸、条件格式、打印布局）
- file_ops: 文件管理（查看目录、搜索文件、读取文本、复制、重命名、删除）
- sheet_ops: 工作表管理与跨表操作（创建、复制、重命名、删除工作表，跨表数据传输）
- excel_code_runner: 通过 Python 脚本处理大体量 Excel（适用于大文件、批处理、复杂计算）"""

_SYSTEM_PROMPT = (
    "你是技能路由器。根据用户消息选择最匹配的技能包。\n"
    "规则：\n"
    "1. 如果用户消息是闲聊/问候/帮助请求，返回 skill_name 为 null\n"
    "2. 如果涉及多个领域但以某个为主，选主要领域的技能包\n"
    "3. 如果不确定，选 general_excel\n"
    "4. 只输出 JSON，不要输出其他内容\n\n"
    f"{_SKILL_CATALOG_PROMPT}"
)

_USER_PROMPT_TEMPLATE = (
    '用户消息: "{user_message}"\n\n'
    '输出格式: {{"skill_name": "技能名或null", "confidence": 0.0到1.0, "reason": "一句话理由"}}'
)

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def _parse_json_from_text(text: str) -> dict[str, Any] | None:
    """从文本中提取 JSON 对象。"""
    content = (text or "").strip()
    if not content:
        return None

    # 尝试直接解析
    candidates = [content]

    # 尝试 code fence
    for match in _JSON_FENCE_RE.finditer(content):
        body = (match.group(1) or "").strip()
        if body:
            candidates.append(body)

    # 尝试提取 { ... }
    left = content.find("{")
    right = content.rfind("}")
    if left >= 0 and right > left:
        candidates.append(content[left:right + 1].strip())

    for candidate in candidates:
        try:
            data = json.loads(candidate)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            continue
    return None


def _parse_pre_route_response(text: str, model_used: str, latency_ms: float) -> PreRouteResult:
    """解析小模型响应为 PreRouteResult。"""
    parsed = _parse_json_from_text(text)
    if parsed is None:
        return PreRouteResult(
            skill_name="general_excel",
            confidence=0.0,
            reason="解析失败，回退 general_excel",
            latency_ms=latency_ms,
            model_used=model_used,
            raw_response=text,
        )

    raw_skill = parsed.get("skill_name")
    if raw_skill is None or raw_skill == "null" or (isinstance(raw_skill, str) and raw_skill.strip().lower() == "null"):
        skill_name = None
    elif isinstance(raw_skill, str) and raw_skill.strip() in VALID_SKILL_NAMES:
        skill_name = raw_skill.strip()
    else:
        skill_name = "general_excel"

    confidence = 0.5
    raw_conf = parsed.get("confidence")
    if isinstance(raw_conf, (int, float)):
        confidence = max(0.0, min(1.0, float(raw_conf)))

    reason = str(parsed.get("reason", "")).strip()[:200] or "无"

    return PreRouteResult(
        skill_name=skill_name,
        confidence=confidence,
        reason=reason,
        latency_ms=latency_ms,
        model_used=model_used,
        raw_response=text,
    )



def _is_gemini_url(base_url: str) -> bool:
    """判断是否为 Gemini 原生 API URL。"""
    lower = base_url.lower()
    return "v1beta" in lower or ("gemini" in lower and "/v1/" not in lower)


async def _call_gemini_native(
    *,
    user_message: str,
    api_key: str,
    base_url: str,
    model: str,
    timeout_ms: int,
) -> tuple[str, float]:
    """调用 Gemini 原生 API，返回 (response_text, latency_ms)。"""
    url = f"{base_url.rstrip('/')}/models/{model}:generateContent"
    body = {
        "contents": [
            {
                "role": "user",
                "parts": [{"text": _USER_PROMPT_TEMPLATE.format(user_message=user_message[:500])}],
            }
        ],
        "systemInstruction": {
            "parts": [{"text": _SYSTEM_PROMPT}]
        },
        "generationConfig": {
            "temperature": 0.0,
            "maxOutputTokens": 150,
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }

    start = time.monotonic()
    async with httpx.AsyncClient(timeout=timeout_ms / 1000.0) as client:
        resp = await client.post(
            url,
            headers={
                "x-goog-api-key": api_key,
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=body,
        )
        resp.raise_for_status()
        data = resp.json()
    latency_ms = (time.monotonic() - start) * 1000

    # 提取响应文本
    try:
        text = data["candidates"][0]["content"]["parts"][0]["text"] or ""
    except (KeyError, IndexError, TypeError):
        text = ""

    return text, latency_ms


async def _call_openai_compatible(
    *,
    user_message: str,
    api_key: str,
    base_url: str,
    model: str,
    timeout_ms: int,
) -> tuple[str, float]:
    """调用 OpenAI 兼容 API，返回 (response_text, latency_ms)。"""
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": _USER_PROMPT_TEMPLATE.format(user_message=user_message[:500])},
    ]

    start = time.monotonic()
    async with httpx.AsyncClient(timeout=timeout_ms / 1000.0) as client:
        resp = await client.post(
            f"{base_url.rstrip('/')}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": messages,
                "temperature": 0.0,
                "max_tokens": 150,
            },
        )
        resp.raise_for_status()
        data = resp.json()
    latency_ms = (time.monotonic() - start) * 1000

    # 提取响应文本
    try:
        text = data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        text = ""

    return text, latency_ms


async def pre_route_skill(
    user_message: str,
    *,
    api_key: str,
    base_url: str,
    model: str,
    timeout_ms: int = 10000,
) -> PreRouteResult:
    """调用小模型预判最佳 skillpack。

    支持 OpenAI 兼容格式和 Gemini 原生 API 格式。
    通过 base_url 自动判断使用哪种协议。

    Args:
        user_message: 用户输入
        api_key: 小模型 API key
        base_url: 小模型 base URL
        model: 小模型名称
        timeout_ms: 超时毫秒数

    Returns:
        PreRouteResult
    """
    # 快速短路：明显的闲聊场景
    trimmed = user_message.strip()
    if not trimmed or len(trimmed) < 2:
        return PreRouteResult(
            skill_name=None,
            confidence=1.0,
            reason="空消息或过短",
            latency_ms=0.0,
            model_used=model,
        )

    if _NO_SKILL_PATTERNS.match(trimmed):
        return PreRouteResult(
            skill_name=None,
            confidence=0.9,
            reason="闲聊/问候模式匹配",
            latency_ms=0.0,
            model_used=model,
        )

    start = time.monotonic()
    try:
        if _is_gemini_url(base_url):
            text, latency_ms = await _call_gemini_native(
                user_message=trimmed,
                api_key=api_key,
                base_url=base_url,
                model=model,
                timeout_ms=timeout_ms,
            )
        else:
            text, latency_ms = await _call_openai_compatible(
                user_message=trimmed,
                api_key=api_key,
                base_url=base_url,
                model=model,
                timeout_ms=timeout_ms,
            )
    except Exception as exc:
        latency_ms = (time.monotonic() - start) * 1000
        logger.warning("预路由调用失败(%s): %s", model, exc)
        return PreRouteResult(
            skill_name="general_excel",
            confidence=0.0,
            reason=f"API 调用失败: {type(exc).__name__}",
            latency_ms=latency_ms,
            model_used=model,
            raw_response=str(exc),
        )

    return _parse_pre_route_response(text, model_used=model, latency_ms=latency_ms)

