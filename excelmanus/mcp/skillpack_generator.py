"""MCP Skillpack 自动生成器。

通过 LLM 从 MCP Server 工具元数据生成富 Skillpack，并提供本地缓存机制：
- 首次安装：同步调用 LLM 生成 → 缓存结果
- LLM 失败：回退到程序化生成（MCPManager.generate_skillpacks）
- 后续启动：从缓存加载 → 异步后台静默再生成
- MCP 更新：工具指纹变化时触发重新生成
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from pathlib import Path
from typing import Any, TYPE_CHECKING

from excelmanus.skillpacks.models import Skillpack

if TYPE_CHECKING:
    import openai

logger = logging.getLogger(__name__)

# 缓存文件位置
_CACHE_DIR = Path.home() / ".excelmanus"
_CACHE_FILE = _CACHE_DIR / "mcp_skillpack_cache.json"

# 缓存格式版本，变更时强制重建
_CACHE_VERSION = 2

# 后台刷新冷却时间（秒）：上次 LLM 生成后 6 小时内不再后台刷新
_REFRESH_COOLDOWN_SECONDS = 6 * 3600

# 每个会话内后台静默重试的最大轮次
_MAX_SILENT_REFRESH_ATTEMPTS_PER_SESSION = 2

# LLM 调用超时（秒）
_LLM_STEP1_TIMEOUT = 45.0   # Step1（description + triggers）内容较短
_LLM_STEP2_TIMEOUT = 90.0   # Step2（instructions）内容较长，需更多时间

# 生成质量诊断计数键
_DIAG_KEYS = ("step1_fail", "step2_fail", "repair_success", "fallback_used")

# Step1/Step2 提示词合同：强约束输出结构，降低自由发挥导致的解析失败
_STEP1_CONTRACT = (
    "输出合同（必须严格遵守）：\n"
    "1. 仅输出一个 JSON 对象，禁止 markdown 代码块、禁止解释文字、禁止前后缀。\n"
    "2. 仅允许字段：description, triggers。\n"
    "3. description 必须是 10-50 字中文句子。\n"
    "4. triggers 必须是字符串数组，元素应短小、可检索。\n"
    "反例（禁止）：\n"
    "- ```json ... ```\n"
    "- 好的，以下是结果：{...}\n"
    "- 多个 JSON 对象或夹带自然语言说明。"
)

_STEP2_CONTRACT = (
    "输出合同（必须严格遵守）：\n"
    "1. 仅输出一个 JSON 对象，禁止 markdown 代码块、禁止解释文字、禁止前后缀。\n"
    "2. 仅允许字段：instructions。\n"
    "3. instructions 必须包含：工具前缀、推荐调用顺序、错误处理建议。\n"
    "反例（禁止）：\n"
    "- `请按需调用工具。`\n"
    "- 只列工具名但无顺序与错误处理。\n"
    "- 附带额外说明段落或非 JSON 文本。"
)

# instructions 可执行性关键词（至少命中三类中的两类）
_ACTIONABILITY_TOOL_PREFIX_HINTS = (
    "工具前缀",
    "前缀",
    "mcp_",
    "mcp:",
    "tool prefix",
)
_ACTIONABILITY_ORDER_HINTS = (
    "顺序",
    "步骤",
    "流程",
    "先",
    "然后",
    "最后",
)
_ACTIONABILITY_ERROR_HINTS = (
    "错误",
    "失败",
    "异常",
    "重试",
    "回退",
    "降级",
    "校验",
    "检查",
)


def _new_diagnostics() -> dict[str, int]:
    """创建生成质量诊断计数字典。"""
    return {key: 0 for key in _DIAG_KEYS}


def _merge_diagnostics(target: dict[str, int], source: dict[str, int]) -> None:
    """将 source 计数累加到 target。"""
    for key in _DIAG_KEYS:
        target[key] = int(target.get(key, 0)) + int(source.get(key, 0))


def _inc_diag(diag: dict[str, int] | None, key: str) -> None:
    if diag is None:
        return
    if key not in _DIAG_KEYS:
        return
    diag[key] = int(diag.get(key, 0)) + 1


def _is_hard_llm_failure(diag: dict[str, int]) -> bool:
    """判断是否为调用层硬失败（非内容质量失败）。"""
    return all(int(diag.get(key, 0)) == 0 for key in _DIAG_KEYS)

# ---------------------------------------------------------------------------
# 工具指纹
# ---------------------------------------------------------------------------


def compute_fingerprint(tools: list[Any]) -> str:
    """根据工具名称和描述计算指纹哈希。

    指纹变化意味着 MCP Server 的工具集发生了增减或描述变更，
    需要重新生成 Skillpack。

    Args:
        tools: MCP 工具对象列表（duck typing，需具有 name/description 属性）。

    Returns:
        16 位十六进制哈希字符串。
    """
    parts: list[str] = []
    for t in sorted(tools, key=lambda x: getattr(x, "name", "")):
        name = getattr(t, "name", "")
        desc = getattr(t, "description", "") or ""
        parts.append(f"{name}::{desc}")
    content = "\n".join(parts)
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# 缓存读写
# ---------------------------------------------------------------------------


def load_cache() -> dict[str, Any]:
    """加载本地缓存。

    Returns:
        缓存字典，格式为 ``{"version": int, "servers": {server_name: {...}}``。
        文件不存在或版本不匹配时返回空结构。
    """
    if not _CACHE_FILE.exists():
        return {"version": _CACHE_VERSION, "servers": {}}
    try:
        data = json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("version") != _CACHE_VERSION:
            logger.info("MCP Skillpack 缓存版本不匹配，将重建")
            return {"version": _CACHE_VERSION, "servers": {}}
        return data
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("读取 MCP Skillpack 缓存失败: %s", exc)
        return {"version": _CACHE_VERSION, "servers": {}}


def save_cache(cache: dict[str, Any]) -> None:
    """将缓存写入本地文件。"""
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _CACHE_FILE.write_text(
            json.dumps(cache, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.debug("MCP Skillpack 缓存已保存: %s", _CACHE_FILE)
    except OSError as exc:
        logger.warning("写入 MCP Skillpack 缓存失败: %s", exc)


def _skillpack_to_cache_dict(sp: Skillpack) -> dict[str, Any]:
    """将 Skillpack 序列化为可缓存的字典。"""
    return {
        "name": sp.name,
        "description": sp.description,
        "allowed_tools": list(sp.allowed_tools),
        "triggers": list(sp.triggers),
        "instructions": sp.instructions,
        "priority": sp.priority,
        "version": sp.version,
    }


def _cache_dict_to_skillpack(data: dict[str, Any]) -> Skillpack:
    """从缓存字典反序列化为 Skillpack。"""
    return Skillpack(
        name=data["name"],
        description=data["description"],
        allowed_tools=data.get("allowed_tools", []),
        triggers=data.get("triggers", []),
        instructions=data.get("instructions", ""),
        source="system",
        root_dir="",
        priority=data.get("priority", 3),
        version=data.get("version", "1.0.0"),
        disable_model_invocation=False,
        user_invocable=True,
    )


# ---------------------------------------------------------------------------
# LLM 输出解析与校验
# ---------------------------------------------------------------------------

# 从 LLM 输出中提取 JSON 对象的正则（兼容 markdown 包裹、前后废话等）
_JSON_OBJECT_RE = re.compile(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", re.DOTALL)


def _extract_json(raw: str) -> dict[str, Any] | None:
    """从 LLM 原始输出中尽力提取第一个合法 JSON 对象。

    依次尝试：
    1. 去除 markdown 代码块后直接 json.loads
    2. 修复 JSON 字符串内的未转义换行符后重试
    3. 正则匹配第一个 ``{...}`` 结构
    4. 均失败返回 None
    """
    text = raw.strip()

    # 去除 markdown 代码块包裹
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(
            line for line in lines if not line.strip().startswith("```")
        ).strip()

    # 尝试 1：直接解析
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except (json.JSONDecodeError, ValueError):
        pass

    # 尝试 2：修复 LLM 常见问题——字符串值内含未转义换行符
    # 将字面换行替换为空格后重试（已转义的 \\n 不受影响）
    fixed = " ".join(text.splitlines())
    try:
        obj = json.loads(fixed)
        if isinstance(obj, dict):
            return obj
    except (json.JSONDecodeError, ValueError):
        pass

    # 尝试 3：正则提取第一个 JSON 对象（同样修复换行）
    for candidate in (text, fixed):
        match = _JSON_OBJECT_RE.search(candidate)
        if match:
            try:
                obj = json.loads(match.group())
                if isinstance(obj, dict):
                    return obj
            except (json.JSONDecodeError, ValueError):
                pass

    return None


def _parse_json_payload(raw: str | None) -> tuple[dict[str, Any] | None, str, str]:
    """解析 LLM 文本为 JSON 对象，并返回结构化失败原因。"""
    if raw is None:
        return None, "llm_no_response", "LLM 未返回可解析内容"
    data = _extract_json(raw)
    if data is None:
        return None, "json_parse_failed", "输出不是合法 JSON 对象"
    return data, "ok", ""


def _instruction_actionability_categories(text: str) -> tuple[bool, bool, bool]:
    """检查 instructions 是否覆盖工具前缀/调用顺序/错误处理三类信息。"""
    raw = text.strip()
    lower = raw.lower()
    tool_prefix_hit = any(
        hint.lower() in lower if "mcp" in hint or "tool prefix" in hint else hint in raw
        for hint in _ACTIONABILITY_TOOL_PREFIX_HINTS
    )
    order_hit = any(hint in raw for hint in _ACTIONABILITY_ORDER_HINTS)
    error_hit = any(hint in raw for hint in _ACTIONABILITY_ERROR_HINTS)
    return tool_prefix_hit, order_hit, error_hit


def _validate_skillpack_fields(
    data: dict[str, Any],
    *,
    require_description: bool = True,
    require_instructions: bool = True,
    enforce_instruction_actionability: bool = True,
    server_name: str = "",
    silent: bool = False,
) -> tuple[dict[str, Any] | None, str, str]:
    """统一字段校验与标准化。

    Returns:
        (normalized_data, reason_code, reason_text)
    """
    normalized: dict[str, Any] = {}
    truncated_fields: list[str] = []

    # ── description ──
    description = data.get("description")
    if require_description:
        if not isinstance(description, str) or not description.strip():
            return None, "description_missing", "description 缺失或为空"
        normalized_desc = description.strip()
        if len(normalized_desc) > 80:
            normalized_desc = normalized_desc[:77] + "..."
            truncated_fields.append("description")
        normalized["description"] = normalized_desc
    elif isinstance(description, str) and description.strip():
        normalized_desc = description.strip()
        if len(normalized_desc) > 80:
            normalized_desc = normalized_desc[:77] + "..."
            truncated_fields.append("description")
        normalized["description"] = normalized_desc

    # ── triggers ──
    raw_triggers = data.get("triggers", [])
    triggers: list[str] = []
    seen: set[str] = set()
    if isinstance(raw_triggers, list):
        for item in raw_triggers:
            if not isinstance(item, str):
                continue
            trigger = item.strip()
            if not trigger:
                continue
            if len(trigger) > 10:
                continue
            if trigger in seen:
                continue
            seen.add(trigger)
            triggers.append(trigger)
    normalized["triggers"] = triggers[:15]

    # ── instructions ──
    instructions = data.get("instructions")
    if require_instructions:
        if not isinstance(instructions, str) or not instructions.strip():
            return None, "instructions_missing", "instructions 缺失或为空"
        normalized_inst = instructions.strip()
        if len(normalized_inst) > 2000:
            normalized_inst = normalized_inst[:1997] + "..."
            truncated_fields.append("instructions")
        if enforce_instruction_actionability:
            tool_prefix_hit, order_hit, error_hit = _instruction_actionability_categories(
                normalized_inst
            )
            hit_count = sum((tool_prefix_hit, order_hit, error_hit))
            if hit_count < 2:
                missing: list[str] = []
                if not tool_prefix_hit:
                    missing.append("工具前缀")
                if not order_hit:
                    missing.append("调用顺序")
                if not error_hit:
                    missing.append("错误处理")
                return (
                    None,
                    "instructions_not_actionable",
                    "instructions 可执行性不足，缺失: " + "、".join(missing),
                )
        normalized["instructions"] = normalized_inst
    elif isinstance(instructions, str) and instructions.strip():
        normalized_inst = instructions.strip()
        if len(normalized_inst) > 2000:
            normalized_inst = normalized_inst[:1997] + "..."
            truncated_fields.append("instructions")
        normalized["instructions"] = normalized_inst

    if truncated_fields and not silent:
        logger.debug(
            "LLM 生成 '%s' 字段超长已截断: %s",
            server_name,
            ",".join(truncated_fields),
        )
    return normalized, "ok", ""


def _build_skillpack_from_validated(
    normalized_data: dict[str, Any],
    server_name: str,
    normalized_name: str,
) -> Skillpack:
    """从已校验数据构建 Skillpack。"""
    skill_name = f"mcp_{normalized_name}"
    return Skillpack(
        name=skill_name,
        description=str(normalized_data.get("description", "")),
        allowed_tools=[f"mcp:{server_name}:*"],
        triggers=list(normalized_data.get("triggers", [])),
        instructions=str(normalized_data.get("instructions", "")),
        source="system",
        root_dir="",
        priority=3,
        version="1.0.0",
        disable_model_invocation=False,
        user_invocable=True,
    )


def _validate_and_build(
    data: dict[str, Any],
    server_name: str,
    normalized_name: str,
    *,
    silent: bool = False,
) -> Skillpack | None:
    """兼容旧入口：校验后构建 Skillpack。"""
    normalized_data, reason_code, reason_text = _validate_skillpack_fields(
        data,
        require_description=True,
        require_instructions=True,
        enforce_instruction_actionability=True,
        server_name=server_name,
        silent=silent,
    )
    if normalized_data is None:
        if not silent:
            logger.warning(
                "LLM 生成 '%s' Skillpack 校验失败: %s - %s",
                server_name,
                reason_code,
                reason_text,
            )
        return None
    return _build_skillpack_from_validated(
        normalized_data,
        server_name,
        normalized_name,
    )


# ---------------------------------------------------------------------------
# LLM 生成
# ---------------------------------------------------------------------------

_LLM_SYSTEM_PROMPT = "你是 JSON 生成器。只输出 JSON，不要输出任何其他内容。"


async def _llm_call(
    client: "openai.AsyncOpenAI",
    model: str,
    messages: list[dict[str, str]],
    *,
    timeout: float = 30.0,
    max_tokens: int = 600,
    server_name: str = "",
    silent: bool = False,
    expect_json: bool = False,
) -> str | None:
    """发送 LLM 请求并返回文本内容，自动逐级降级不兼容参数。

    优先链：
    - expect_json=True: 先试 ``response_format=json_object``，再逐步放宽参数
    - expect_json=False: 先试 ``max_tokens``，再放宽
    遇到参数错误或空内容时自动降级重试。
    """
    # 逐级降级的参数组合
    if expect_json:
        param_chain = [
            {"response_format": {"type": "json_object"}, "max_tokens": max_tokens},
            {"response_format": {"type": "json_object"}},
            {"max_tokens": max_tokens},
            {},  # 最小化：仅 model + messages
        ]
    else:
        param_chain = [
            {"max_tokens": max_tokens},
            {},  # 最小化
        ]
    for i, kwargs in enumerate(param_chain):
        try:
            response = await asyncio.wait_for(
                client.chat.completions.create(
                    model=model,
                    messages=messages,
                    **kwargs,
                ),
                timeout=timeout,
            )
            content = response.choices[0].message.content
            text_out = ""
            if content is None:
                text_out = ""
            if isinstance(content, str):
                text_out = content
            elif isinstance(content, list):
                chunks: list[str] = []
                for item in content:
                    text = None
                    if isinstance(item, dict):
                        text = item.get("text")
                    else:
                        text = getattr(item, "text", None)
                    if isinstance(text, str):
                        chunks.append(text)
                text_out = "".join(chunks)
            elif content is not None:
                text_out = str(content)

            # 某些模型在特定参数组合下会返回空 content（仅 reasoning_content）。
            # 空内容统一视为无效响应，继续走降级链。
            if not text_out.strip() and i < len(param_chain) - 1:
                if not silent:
                    logger.debug(
                        "LLM 返回空内容，继续降级参数重试 '%s'",
                        server_name,
                    )
                continue
            if not text_out.strip():
                return None
            return text_out
        except asyncio.TimeoutError:
            if not silent:
                logger.warning("LLM 调用 '%s' 超时 (%.0fs)", server_name, timeout)
            return None
        except Exception as exc:
            err_text = str(exc).lower()
            is_param_error = any(
                kw in err_text
                for kw in (
                    "invalid param",
                    "temperature",
                    "max_tokens",
                    "response_format",
                    "json_object",
                    "param_error",
                    "unsupported_parameter",
                )
            )
            if is_param_error and i < len(param_chain) - 1:
                dropped = set(kwargs.keys()) - set(param_chain[i + 1].keys())
                if not silent:
                    logger.debug(
                        "LLM 参数不兼容，去掉 %s 后重试 '%s'",
                        dropped or "额外参数",
                        server_name,
                    )
                continue
            if not silent:
                logger.warning("LLM 调用 '%s' 异常: %s", server_name, exc)
            return None
    return None


def _build_tool_list_text(tools: list[Any]) -> str:
    """构建简洁的编号工具列表文本。"""
    lines: list[str] = []
    for i, t in enumerate(tools, 1):
        t_name = getattr(t, "name", str(t))
        t_desc = getattr(t, "description", "") or ""
        lines.append(f"{i}. {t_name} - {t_desc[:60]}")
    return "\n".join(lines)


async def _llm_generate_and_validate(
    client: "openai.AsyncOpenAI",
    model: str,
    sys_msg: dict[str, str],
    user_prompt: str,
    *,
    timeout: float,
    max_tokens: int,
    server_name: str,
    silent: bool,
    validate_kwargs: dict[str, Any],
    diag: dict[str, int],
    fail_key: str,
) -> tuple[dict[str, Any] | None, str, str]:
    """调用 LLM 生成 JSON 并校验，首次校验失败自动纠错重试一次。

    Returns:
        (normalized_data | None, reason_code, reason_text)
    """
    raw = await _llm_call(
        client, model,
        [sys_msg, {"role": "user", "content": user_prompt}],
        timeout=timeout,
        max_tokens=max_tokens,
        server_name=server_name,
        silent=silent,
        expect_json=True,
    )
    if raw is None:
        return None, "llm_call_failed", "LLM 调用失败或超时"

    # 解析 + 校验
    data_raw, reason_code, reason_text = _parse_json_payload(raw)
    normalized: dict[str, Any] | None = None
    if data_raw is not None:
        normalized, reason_code, reason_text = _validate_skillpack_fields(
            data_raw, server_name=server_name, silent=silent, **validate_kwargs,
        )
    if normalized is not None:
        return normalized, "ok", ""

    # ── 首次失败，纠错重试 ──
    _inc_diag(diag, fail_key)
    retry_prompt = (
        user_prompt
        + "\n\n上次输出不合法。"
        + f"\n失败代码: {reason_code}"
        + f"\n失败原因: {reason_text}"
        + "\n请仅输出合法 JSON，并严格满足合同。"
    )
    raw_retry = await _llm_call(
        client, model,
        [sys_msg, {"role": "user", "content": retry_prompt}],
        timeout=timeout,
        max_tokens=max_tokens,
        server_name=server_name,
        silent=silent,
        expect_json=True,
    )
    if raw_retry is None:
        return None, reason_code, reason_text

    retry_data_raw, reason_code, reason_text = _parse_json_payload(raw_retry)
    if retry_data_raw is not None:
        normalized, reason_code, reason_text = _validate_skillpack_fields(
            retry_data_raw, server_name=server_name, silent=silent, **validate_kwargs,
        )
    if normalized is not None:
        _inc_diag(diag, "repair_success")
        return normalized, "ok", ""

    return None, reason_code, reason_text


def _build_fallback_instructions(
    server_name: str,
    normalized_name: str,
    tools: list[Any],
) -> str:
    """构建无需 LLM 的稳定兜底指引。"""
    top_tools = tools[:8]
    tool_names = ", ".join(getattr(t, "name", str(t)) for t in top_tools) or "无"
    lines: list[str] = [
        f"{server_name} 工具集使用建议：",
        f"调用前缀：mcp_{normalized_name}_",
        "推荐调用顺序：先用读取类工具确认当前状态，再执行写入/变更工具，最后用查询类工具复核结果。",
        "常用工具摘要：" + tool_names,
    ]
    for t in top_tools:
        t_name = getattr(t, "name", str(t))
        t_desc = (getattr(t, "description", "") or "").strip()
        lines.append(f"- {t_name}：{t_desc or '请查看工具说明后再调用。'}")
    lines.append("错误处理建议：出现错误时先检查参数类型、字段名和目标对象是否存在，再按原顺序重试；连续失败则回退到只读校验。")
    return "\n".join(lines)


async def generate_skillpack_with_llm(
    client: "openai.AsyncOpenAI",
    model: str,
    server_name: str,
    normalized_name: str,
    tools: list[Any],
    *,
    silent: bool = False,
    diagnostics: dict[str, int] | None = None,
) -> Skillpack | None:
    """通过两步 LLM 调用生成富 Skillpack。

    Step 1: 生成 description + triggers（概要信息，超时 _LLM_STEP1_TIMEOUT）
    Step 2: 基于 Step 1 结果，生成 instructions（详细指引，超时 _LLM_STEP2_TIMEOUT）

    拆分为两步可降低单次上下文压力，提升多工具 server 的生成质量。
    每步失败自动纠错重试一次；Step 2 最终失败走模板兜底。
    """
    run_diag = _new_diagnostics()
    tool_list_text = _build_tool_list_text(tools)
    sys_msg = {"role": "system", "content": _LLM_SYSTEM_PROMPT}

    def _diag_text() -> str:
        return ", ".join(f"{k}={run_diag[k]}" for k in _DIAG_KEYS)

    skillpack: Skillpack | None = None
    try:
        # ── Step 1: description + triggers ──
        step1_prompt = (
            f"为以下 MCP 工具集生成描述和关键词：\n\n"
            f"服务器: {server_name}\n"
            f"工具（共 {len(tools)} 个）:\n{tool_list_text}\n\n"
            f"要求：description 要概括所有工具能力，triggers 覆盖主要功能领域。\n\n"
            f"{_STEP1_CONTRACT}\n\n"
            f'目标格式:\n{{"description":"一句话中文描述(10-50字)","triggers":["关键词1","关键词2"]}}'
        )

        step1_result, reason_code, reason_text = await _llm_generate_and_validate(
            client, model, sys_msg, step1_prompt,
            timeout=_LLM_STEP1_TIMEOUT,
            max_tokens=300,
            server_name=server_name,
            silent=silent,
            validate_kwargs={
                "require_description": True,
                "require_instructions": False,
                "enforce_instruction_actionability": False,
            },
            diag=run_diag,
            fail_key="step1_fail",
        )
        if step1_result is None:
            if not silent:
                logger.warning(
                    "LLM Step1 '%s' 失败: %s - %s",
                    server_name, reason_code, reason_text,
                )
            return None

        description = step1_result.get("description", "")
        triggers = step1_result.get("triggers", [])

        # ── Step 2: instructions ──
        step2_prompt = (
            f"为以下 MCP 工具集生成详细中文使用指引：\n\n"
            f"服务器: {server_name}\n"
            f"工具前缀: mcp_{normalized_name}_\n"
            f"工具:\n{tool_list_text}\n\n"
            f"Step1 输出：description={description}；triggers={triggers}\n\n"
            f"{_STEP2_CONTRACT}\n\n"
            f'目标格式:\n{{"instructions":"详细中文使用指引"}}'
        )

        step2_result, reason_code, reason_text = await _llm_generate_and_validate(
            client, model, sys_msg, step2_prompt,
            timeout=_LLM_STEP2_TIMEOUT,
            max_tokens=800,
            server_name=server_name,
            silent=silent,
            validate_kwargs={
                "require_description": False,
                "require_instructions": True,
                "enforce_instruction_actionability": True,
            },
            diag=run_diag,
            fail_key="step2_fail",
        )
        if step2_result is not None:
            instructions = str(step2_result.get("instructions", ""))
        else:
            _inc_diag(run_diag, "fallback_used")
            instructions = _build_fallback_instructions(
                server_name, normalized_name, tools,
            )

        # ── 最终校验 ──
        normalized_final, reason_code, reason_text = _validate_skillpack_fields(
            {"description": description, "triggers": triggers, "instructions": instructions},
            require_description=True,
            require_instructions=True,
            enforce_instruction_actionability=True,
            server_name=server_name,
            silent=silent,
        )
        if normalized_final is None:
            if not silent:
                logger.warning(
                    "LLM 生成 '%s' 最终校验失败: %s - %s",
                    server_name, reason_code, reason_text,
                )
            return None

        skillpack = _build_skillpack_from_validated(
            normalized_final, server_name, normalized_name,
        )
        return skillpack
    finally:
        if diagnostics is not None:
            _merge_diagnostics(diagnostics, run_diag)
        if skillpack is not None:
            if silent:
                logger.info(
                    "MCP Skillpack 静默生成成功 '%s'（%s）",
                    server_name, _diag_text(),
                )
            else:
                logger.info(
                    "MCP Skillpack 生成完成 '%s'（%s）",
                    server_name, _diag_text(),
                )
        elif not silent:
            logger.debug(
                "MCP Skillpack 生成失败 '%s'（%s）",
                server_name, _diag_text(),
            )


# ---------------------------------------------------------------------------
# 编排器
# ---------------------------------------------------------------------------


class MCPSkillpackGenerator:
    """MCP Skillpack 生成编排器。

    协调缓存检查、LLM 生成、程序化回退和异步更新。

    典型用法::

        generator = MCPSkillpackGenerator(mcp_manager, llm_client, model)
        # 首次/缓存命中 → 同步获取
        skillpacks = await generator.generate()
        # 后台异步更新（非阻塞）
        generator.schedule_background_refresh()
    """

    def __init__(
        self,
        mcp_manager: Any,
        llm_client: "openai.AsyncOpenAI",
        model: str,
    ) -> None:
        self._mcp_manager = mcp_manager
        self._llm_client = llm_client
        self._model = model
        self._background_task: asyncio.Task | None = None
        # 本次会话中已由 LLM 成功生成的 server，后台刷新跳过这些
        self._llm_generated_this_session: set[str] = set()
        # 会话级静默刷新轮次计数：达到上限后本次会话不再重试
        self._silent_refresh_attempts_this_session = 0
        # 会话级生成质量计数（仅内部观测）
        self._diagnostics = _new_diagnostics()

    async def generate(self) -> list[Skillpack]:
        """根据缓存状态生成 Skillpack 列表。

        流程：
        1. 缓存命中且指纹匹配 → 直接加载缓存
        2. 缓存未命中或指纹变化 → 同步调 LLM 生成
        3. LLM 失败 → 回退到程序化生成
        4. 写入缓存（含 generated_by 和 timestamp 元数据）

        Returns:
            Skillpack 列表。
        """
        server_infos = self._collect_server_infos()
        if not server_infos:
            return []

        cache = load_cache()
        cached_servers = cache.get("servers", {})

        result: list[Skillpack] = []
        cache_dirty = False
        basic_skillpacks: dict[str, Skillpack] | None = None  # 懒加载，LLM 回退时用
        llm_available = True  # 熔断器：首个 server 失败后跳过剩余
        run_diag = _new_diagnostics()

        for server_name, info in server_infos.items():
            tools = info["tools"]
            fingerprint = info["fingerprint"]
            normalized = info["normalized"]

            # 检查缓存
            cached = cached_servers.get(server_name)
            if (
                cached is not None
                and cached.get("fingerprint") == fingerprint
                and cached.get("skillpack")
            ):
                # 缓存命中，指纹匹配
                try:
                    sp = _cache_dict_to_skillpack(cached["skillpack"])
                    result.append(sp)
                    logger.debug("从缓存加载 MCP Skillpack '%s'", sp.name)
                    continue
                except (KeyError, TypeError) as exc:
                    logger.warning("缓存反序列化失败 '%s': %s", server_name, exc)

            sp = None
            generated_by = "basic"

            # 熔断器未触发时才调 LLM
            if llm_available:
                logger.info(
                    "为 MCP Server '%s' 生成 Skillpack（%s）...",
                    server_name,
                    "首次生成" if cached is None else "工具已更新",
                )
                server_diag = _new_diagnostics()
                try:
                    sp = await generate_skillpack_with_llm(
                        client=self._llm_client,
                        model=self._model,
                        server_name=server_name,
                        normalized_name=normalized,
                        tools=tools,
                        diagnostics=server_diag,
                    )
                except Exception:
                    logger.warning(
                        "LLM 生成 '%s' 发生未预期异常，转程序化回退",
                        server_name,
                        exc_info=True,
                    )
                    sp = None
                _merge_diagnostics(run_diag, server_diag)
                if sp is not None:
                    generated_by = "llm"
                    self._llm_generated_this_session.add(server_name)
                else:
                    # 仅在调用层硬失败时熔断；内容不合规仅回退当前 server
                    if _is_hard_llm_failure(server_diag):
                        llm_available = False
                        logger.info(
                            "LLM 可能不可用，剩余 MCP Server 将直接使用程序化生成"
                        )
                    else:
                        logger.info(
                            "LLM 输出未通过合同校验，当前 server 回退程序化生成，继续处理其他 server"
                        )

            if sp is None:
                # 程序化回退
                _inc_diag(run_diag, "fallback_used")
                if basic_skillpacks is None:
                    basic_skillpacks = {
                        s.name: s
                        for s in self._mcp_manager.generate_skillpacks()
                    }
                sp = basic_skillpacks.get(f"mcp_{normalized}")

            if sp is not None:
                result.append(sp)
                now = time.time()
                cached_servers[server_name] = {
                    "fingerprint": fingerprint,
                    "skillpack": _skillpack_to_cache_dict(sp),
                    "generated_by": generated_by,
                    "timestamp": now,
                }
                cache_dirty = True

        if cache_dirty:
            cache["servers"] = cached_servers
            save_cache(cache)

        _merge_diagnostics(self._diagnostics, run_diag)
        logger.debug(
            "MCP Skillpack 本轮生成计数: %s",
            ", ".join(f"{k}={run_diag[k]}" for k in _DIAG_KEYS),
        )
        return result

    def schedule_background_refresh(self) -> None:
        """启动后台异步任务，静默重新生成所有 MCP Skillpack。

        用于后续启动时在加载缓存后异步更新内容质量。
        不阻塞主流程，失败时静默忽略。
        """
        if (
            self._silent_refresh_attempts_this_session
            >= _MAX_SILENT_REFRESH_ATTEMPTS_PER_SESSION
        ):
            return

        if self._background_task is not None and not self._background_task.done():
            return

        self._background_task = asyncio.create_task(
            self._background_refresh(),
            name="mcp-skillpack-refresh",
        )

    async def _background_refresh(self) -> None:
        """后台静默重新生成并更新缓存。

        仅对需要刷新的 server 调用 LLM，跳过以下情况：
        - 本次会话 generate() 已用 LLM 成功生成的 server
        - 缓存中已由 LLM 生成且在冷却期内（6 小时）的 server
        - 指纹匹配且 generated_by == "llm" 的 server
        """
        try:
            server_infos = self._collect_server_infos()
            if not server_infos:
                return

            pending_servers = set(server_infos.keys())
            while (
                pending_servers
                and self._silent_refresh_attempts_this_session
                < _MAX_SILENT_REFRESH_ATTEMPTS_PER_SESSION
            ):
                cache = load_cache()
                cached_servers = cache.get("servers", {})
                now = time.time()
                attempted_this_round = False
                failed_servers: set[str] = set()
                updated = False
                success_count = 0
                run_diag = _new_diagnostics()

                for server_name in pending_servers:
                    info = server_infos.get(server_name)
                    if info is None:
                        continue

                    tools = info["tools"]
                    fingerprint = info["fingerprint"]
                    normalized = info["normalized"]

                    # 跳过本次会话已由 LLM 成功生成的 server
                    if server_name in self._llm_generated_this_session:
                        continue

                    # 检查缓存冷却
                    cached = cached_servers.get(server_name)
                    if cached is not None:
                        cached_fp = cached.get("fingerprint")
                        cached_by = cached.get("generated_by", "")
                        cached_ts = cached.get("timestamp", 0)

                        # 手动编写的永不覆盖
                        if cached_fp == fingerprint and cached_by == "manual":
                            continue

                        # 指纹匹配 + LLM 生成 + 在冷却期内 → 跳过
                        if (
                            cached_fp == fingerprint
                            and cached_by == "llm"
                            and (now - cached_ts) < _REFRESH_COOLDOWN_SECONDS
                        ):
                            continue

                    attempted_this_round = True
                    try:
                        sp = await generate_skillpack_with_llm(
                            client=self._llm_client,
                            model=self._model,
                            server_name=server_name,
                            normalized_name=normalized,
                            tools=tools,
                            silent=True,
                            diagnostics=run_diag,
                        )
                    except Exception:
                        sp = None
                    if sp is None:
                        failed_servers.add(server_name)
                        continue

                    self._llm_generated_this_session.add(server_name)
                    cached_servers[server_name] = {
                        "fingerprint": fingerprint,
                        "skillpack": _skillpack_to_cache_dict(sp),
                        "generated_by": "llm",
                        "timestamp": now,
                    }
                    updated = True
                    success_count += 1

                # 本轮没有实际 LLM 调用，说明无需刷新，直接结束
                if not attempted_this_round:
                    return

                self._silent_refresh_attempts_this_session += 1
                _merge_diagnostics(self._diagnostics, run_diag)

                if updated:
                    cache["servers"] = cached_servers
                    save_cache(cache)
                    logger.info(
                        "MCP Skillpack 后台刷新成功：更新 %d 个 server（%s）",
                        success_count,
                        ", ".join(f"{k}={run_diag[k]}" for k in _DIAG_KEYS),
                    )

                if not failed_servers:
                    return

                # 失败项继续静默重试，最多 2 轮，超限后留到下次启动
                pending_servers = failed_servers

        except Exception:
            return

    def _collect_server_infos(self) -> dict[str, dict[str, Any]]:
        """收集所有已连接 MCP Server 的工具信息和指纹。"""
        from excelmanus.mcp.manager import _normalize_server_name

        infos: dict[str, dict[str, Any]] = {}
        clients = getattr(self._mcp_manager, "_clients", {})

        for server_name, client in clients.items():
            tools = getattr(client, "_tools", [])
            if not tools:
                continue
            normalized = _normalize_server_name(server_name)
            fingerprint = compute_fingerprint(tools)
            infos[server_name] = {
                "tools": tools,
                "fingerprint": fingerprint,
                "normalized": normalized,
            }

        return infos
