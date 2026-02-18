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
    skill_names: list[str] = field(default_factory=list)  # 兼容复合意图（最多 2 个）
    raw_response: str = ""  # 原始响应（调试用）


# ── 每个 skill 的工具列表（SSOT：与 SKILL.md frontmatter 保持一致） ──
# general_excel 是兜底超集，不列出工具明细以节省 token。

_SKILL_TOOLS: dict[str, tuple[str, list[str]]] = {
    "data_basic": (
        "数据读取、分析、筛选、转换、排序、统计、分组、汇总",
        [
            "read_excel", "write_excel", "analyze_data", "filter_data",
            "transform_data", "group_aggregate", "analyze_sheet_mapping",
            "list_sheets", "inspect_excel_files", "write_cells",
            "insert_rows", "insert_columns",
        ],
    ),
    "chart_basic": (
        "图表生成（折线图、柱状图、饼图、雷达图、散点图等）",
        [
            "create_chart", "create_excel_chart", "read_excel",
            "group_aggregate", "list_sheets",
        ],
    ),
    "format_basic": (
        "格式化与样式（颜色、字体、边框、填充、合并单元格、行列尺寸、条件格式、打印布局）",
        [
            "format_cells", "adjust_column_width", "adjust_row_height",
            "read_cell_styles", "merge_cells", "unmerge_cells",
            "apply_threshold_icon_format", "style_card_blocks",
            "scale_range_unit", "apply_dashboard_dark_theme",
            "add_color_scale", "add_data_bar", "add_conditional_rule",
            "set_print_layout", "set_page_header_footer", "read_excel",
        ],
    ),
    "file_ops": (
        "文件管理（查看目录、搜索文件、读取文本、复制、重命名、删除）",
        [
            "list_directory", "get_file_info", "find_files",
            "read_text_file", "copy_file", "rename_file",
            "delete_file", "read_excel",
        ],
    ),
    "sheet_ops": (
        "工作表管理与跨表操作（创建、复制、重命名、删除工作表，跨表数据传输）",
        [
            "list_sheets", "create_sheet", "copy_sheet", "rename_sheet",
            "delete_sheet", "copy_range_between_sheets",
            "read_excel", "write_excel",
        ],
    ),
    "excel_code_runner": (
        "通过 Python 脚本处理大体量 Excel（适用于大文件、批处理、复杂计算）",
        [
            "write_text_file", "run_code", "read_excel", "analyze_data",
            "filter_data", "transform_data", "write_excel",
            "read_text_file", "find_files", "get_file_info",
            "list_directory",
        ],
    ),
}





_cached_system_prompt: str | None = None


def invalidate_pre_route_cache() -> None:
    """使预路由 system prompt 全局缓存失效。

    在 skillpack 发生 CRUD 变更后调用，确保下次预路由使用最新的 skillpack 信息。
    """
    global _cached_system_prompt
    _cached_system_prompt = None


def _build_skill_catalog(
    runtime_skillpacks: dict[str, Any] | None = None,
) -> str:
    """构建带工具详情的技能目录，供小模型预选使用。

    优先使用运行时动态加载的 skillpack（含自定义 skillpack），
    对于不在 _SKILL_TOOLS 中的 skillpack，从其 allowed_tools 字段读取工具列表。
    """
    from excelmanus.tools.policy import TOOL_SHORT_DESCRIPTIONS

    lines = ["可用技能包（含工具明细）：\n"]

    # general_excel 兜底，只给一句话描述
    lines.append(
        "- general_excel: 通用 Excel 助手兜底，覆盖所有工具。"
        "仅当任务跨越 3 个以上领域或无法归入下列专项技能时选择。"
    )

    # 合并内置 _SKILL_TOOLS 与运行时 skillpack
    # 运行时 skillpack 优先（允许覆盖内置描述）
    merged: dict[str, tuple[str, list[str]]] = dict(_SKILL_TOOLS)
    if runtime_skillpacks:
        for sp_name, sp in runtime_skillpacks.items():
            if sp_name == "general_excel":
                continue
            # Skillpack 对象有 description 和 allowed_tools 属性
            desc = getattr(sp, "description", "") or sp_name
            tools = list(getattr(sp, "allowed_tools", []) or [])
            # 过滤掉通配符选择器，只保留具体工具名
            tools = [t for t in tools if "*" not in t and ":" not in t]
            merged[sp_name] = (desc, tools)

    for skill_name, (description, tools) in merged.items():
        tool_parts = []
        for t in tools:
            desc = TOOL_SHORT_DESCRIPTIONS.get(t)
            if desc:
                tool_parts.append(f"{t}({desc})")
            else:
                tool_parts.append(t)
        lines.append(f"- {skill_name}: {description}")
        if tool_parts:
            lines.append(f"  工具: {', '.join(tool_parts)}")

    return "\n".join(lines)


def _get_system_prompt(
    runtime_skillpacks: dict[str, Any] | None = None,
) -> str:
    """构建预路由 system prompt（含工具级别详情）。

    当传入 runtime_skillpacks 时，动态构建（不使用缓存），
    以确保自定义 skillpack 的工具信息被包含在内。
    无 runtime_skillpacks 时使用全局缓存（向后兼容）。
    """
    global _cached_system_prompt
    if runtime_skillpacks is None:
        if _cached_system_prompt is not None:
            return _cached_system_prompt
        catalog = _build_skill_catalog()
        _cached_system_prompt = _PROMPT_TEMPLATE.format(catalog=catalog)
        return _cached_system_prompt

    catalog = _build_skill_catalog(runtime_skillpacks)
    return _PROMPT_TEMPLATE.format(catalog=catalog)


_PROMPT_TEMPLATE = (
    "你是技能路由器。根据用户消息选择最匹配的技能包。\n"
    "规则：\n"
    "1. 如果用户消息是闲聊/问候/帮助请求，返回 skill_name 为 null\n"
    "2. 如果涉及多个领域，返回最多 2 个技能名（按主次排序）\n"
    "3. 根据用户需要的具体工具能力选择技能，而非仅凭关键词\n"
    "4. 如果不确定，选 general_excel\n"
    "5. 只输出 JSON，不要输出其他内容\n\n"
    "{catalog}"
)

_USER_PROMPT_TEMPLATE = (
    '用户消息: "{user_message}"\n\n'
    '输出格式: {{"skill_name": "技能名或null", "skill_names": ["最多2个技能名"], "confidence": 0.0到1.0, "reason": "一句话理由"}}'
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


def _parse_pre_route_response(
    text: str,
    model_used: str,
    latency_ms: float,
    valid_skill_names: frozenset[str] | None = None,
) -> PreRouteResult:
    """解析小模型响应为 PreRouteResult。"""
    _valid = valid_skill_names if valid_skill_names is not None else VALID_SKILL_NAMES
    parsed = _parse_json_from_text(text)
    if parsed is None:
        return PreRouteResult(
            skill_name="general_excel",
            skill_names=["general_excel"],
            confidence=0.0,
            reason="解析失败，回退 general_excel",
            latency_ms=latency_ms,
            model_used=model_used,
            raw_response=text,
        )

    skill_names: list[str] = []
    raw_skill_names = parsed.get("skill_names")
    if isinstance(raw_skill_names, list):
        for item in raw_skill_names:
            if not isinstance(item, str):
                continue
            normalized = item.strip()
            if not normalized:
                continue
            if normalized.lower() == "null":
                continue
            if normalized not in _valid:
                continue
            if normalized not in skill_names:
                skill_names.append(normalized)
            if len(skill_names) >= 2:
                break

    raw_skill = parsed.get("skill_name")
    if skill_names:
        skill_name: str | None = skill_names[0]
    elif raw_skill is None or raw_skill == "null" or (
        isinstance(raw_skill, str) and raw_skill.strip().lower() == "null"
    ):
        skill_name = None
    elif isinstance(raw_skill, str) and raw_skill.strip() in _valid:
        skill_name = raw_skill.strip()
        skill_names = [skill_name]
    else:
        skill_name = "general_excel"
        skill_names = ["general_excel"]

    confidence = 0.5
    raw_conf = parsed.get("confidence")
    if isinstance(raw_conf, (int, float)):
        confidence = max(0.0, min(1.0, float(raw_conf)))

    reason = str(parsed.get("reason", "")).strip()[:200] or "无"

    return PreRouteResult(
        skill_name=skill_name,
        skill_names=skill_names,
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
    runtime_skillpacks: dict[str, Any] | None = None,
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
            "parts": [{"text": _get_system_prompt(runtime_skillpacks)}]
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
    runtime_skillpacks: dict[str, Any] | None = None,
) -> tuple[str, float]:
    """调用 OpenAI 兼容 API，返回 (response_text, latency_ms)。"""
    messages = [
        {"role": "system", "content": _get_system_prompt(runtime_skillpacks)},
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
    valid_skill_names: frozenset[str] | None = None,
    runtime_skillpacks: dict[str, Any] | None = None,
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
        valid_skill_names: 运行时已加载的技能名集合；None 时使用内置 VALID_SKILL_NAMES。
            由调用方（engine.py）在运行时传入，以支持用户自定义 Skillpack。
        runtime_skillpacks: 运行时已加载的 Skillpack 对象字典（name -> Skillpack）。
            用于动态构建 skill catalog，使自定义 skillpack 的工具信息对小模型可见。

    Returns:
        PreRouteResult
    """
    # 快速短路：明显的闲聊场景
    trimmed = user_message.strip()
    if not trimmed or len(trimmed) < 2:
        return PreRouteResult(
            skill_name=None,
            skill_names=[],
            confidence=1.0,
            reason="空消息或过短",
            latency_ms=0.0,
            model_used=model,
        )

    if _NO_SKILL_PATTERNS.match(trimmed):
        return PreRouteResult(
            skill_name=None,
            skill_names=[],
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
                runtime_skillpacks=runtime_skillpacks,
            )
        else:
            text, latency_ms = await _call_openai_compatible(
                user_message=trimmed,
                api_key=api_key,
                base_url=base_url,
                model=model,
                timeout_ms=timeout_ms,
                runtime_skillpacks=runtime_skillpacks,
            )
    except Exception as exc:
        latency_ms = (time.monotonic() - start) * 1000
        logger.warning("预路由调用失败(%s): %s", model, exc)
        return PreRouteResult(
            skill_name="general_excel",
            skill_names=["general_excel"],
            confidence=0.0,
            reason=f"API 调用失败: {type(exc).__name__}",
            latency_ms=latency_ms,
            model_used=model,
            raw_response=str(exc),
        )

    return _parse_pre_route_response(text, model_used=model, latency_ms=latency_ms, valid_skill_names=valid_skill_names)
