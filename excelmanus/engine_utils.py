"""Engine 纯函数与模块级常量 — 从 engine.py 提取的零状态依赖工具函数。"""

from __future__ import annotations

import asyncio as _asyncio
from types import SimpleNamespace
from typing import Any

from excelmanus.logger import get_logger as _get_logger

_ff_logger = _get_logger("fire_and_forget")

from excelmanus.engine_types import _ToolCallBatch
from excelmanus.mentions.parser import ResolvedMention
from excelmanus.message_serialization import to_plain as _to_plain

# ── 常量 ──────────────────────────────────────────────────────

_ALWAYS_AVAILABLE_TOOLS_READONLY = (
    "task_create", "task_update",
    "ask_user",
    "memory_save", "memory_read_topic",
)
_ALWAYS_AVAILABLE_TOOLS_WRITE_ONLY = (
    "write_plan", "edit_text_file",
    "delegate", "delegate_to_subagent", "parallel_delegate",
)
_ALWAYS_AVAILABLE_TOOLS_SET = frozenset(
    _ALWAYS_AVAILABLE_TOOLS_READONLY + _ALWAYS_AVAILABLE_TOOLS_WRITE_ONLY
)
_ALWAYS_AVAILABLE_TOOLS_READONLY_SET = frozenset(_ALWAYS_AVAILABLE_TOOLS_READONLY)
_SYSTEM_Q_SUBAGENT_APPROVAL = "subagent_high_risk_approval"
_SYSTEM_Q_PLAN_EXIT = "plan_exit_approval"
_SUBAGENT_APPROVAL_OPTION_ACCEPT = "立即接受并执行"
_SUBAGENT_APPROVAL_OPTION_FULLACCESS_RETRY = "开启 fullaccess 后重试（推荐）"
_SUBAGENT_APPROVAL_OPTION_REJECT = "拒绝本次操作"

# ── AUX 模型 "禁用思考" 通用 extra_body ─────────────────────
# 覆盖所有已知 provider 的思考模式关闭参数，
# 各自定义 provider 会过滤掉不属于自身的字段。
_AUX_NO_THINKING_EXTRA_BODY: dict[str, Any] = {
    "enable_thinking": False,                   # dashscope / siliconflow / deepseek / volcengine
    "thinking": {"type": "disabled"},           # claude_compat (OpenAI 代理) / GLM
    "reasoning": {"effort": "none"},            # openrouter
}

_TABLE_FILE_EXTENSIONS = (
    ".xlsx",
    ".xlsm",
    ".xls",
    ".xlsb",
    ".csv",
    ".tsv",
    ".txt",
)
_SKILL_AGENT_ALIASES = {
    "explore": "explorer",
    "plan": "subagent",
    "planner": "subagent",
    "general-purpose": "subagent",
    "generalpurpose": "subagent",
    "analyst": "subagent",
}

# 写入语义枚举：工具通过 ToolDef.write_effect 声明副作用类型。
_WRITE_EFFECT_VALUES: frozenset[str] = frozenset(
    {"none", "workspace_write", "external_write", "dynamic", "unknown"}
)

# ── Mention 上下文 XML 组装 ──────────────────────────────

# 各 mention 类型对应的 XML 标签名和属性名
_MENTION_XML_TAG_MAP: dict[str, tuple[str, str]] = {
    "file": ("file", "path"),
    "folder": ("folder", "path"),
    "skill": ("skill", "name"),
    "mcp": ("mcp", "server"),
}


# ── 纯函数 ──────────────────────────────────────────────────


def normalize_path(path: Any) -> str:
    """规范化路径字符串，供 FILES_CHANGED 等路径收集使用。"""
    if not isinstance(path, str):
        return ""
    normalized = path.strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def is_excel_path(path: str) -> bool:
    """判断是否 Excel/CSV 表格文件路径。"""
    lower = (path or "").lower()
    return any(lower.endswith(ext) for ext in _TABLE_FILE_EXTENSIONS)



def build_mention_context_block(
    mention_contexts: list[ResolvedMention],
) -> str:
    """将 ResolvedMention 列表组装为 <mention_context> XML 块。

    规则：
    - 成功解析的 mention 用类型对应的 XML 标签包裹 context_block
    - 解析失败的 mention 用 <error> 标签包裹错误信息
    - img 类型跳过（不生成 context block）
    - 列表为空时返回空字符串
    """
    if not mention_contexts:
        return ""

    parts: list[str] = []
    for rm in mention_contexts:
        # img 类型不生成 context block
        if rm.mention.kind == "img":
            continue

        if rm.error:
            parts.append(
                f'<error ref="{rm.mention.raw}">\n  {rm.error}\n</error>'
            )
        elif rm.context_block:
            tag_info = _MENTION_XML_TAG_MAP.get(rm.mention.kind)
            if tag_info:
                tag, attr = tag_info
                # 为带 range_spec 的文件引用添加 range 属性
                range_attr = ""
                if rm.mention.range_spec:
                    range_attr = f' range="{rm.mention.range_spec}"'
                parts.append(
                    f'<{tag} {attr}="{rm.mention.value}"{range_attr}>\n'
                    f"{rm.context_block}\n"
                    f"</{tag}>"
                )

    if not parts:
        return ""

    inner = "\n".join(parts)
    return f"<mention_context>\n{inner}\n</mention_context>"


def _message_content_to_text(content: Any) -> str:
    """将供应商差异化 content 统一为文本。"""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
            else:
                text = getattr(item, "text", None)
                if isinstance(text, str):
                    parts.append(text)
        if parts:
            return "".join(parts)
    return str(content)


def _normalize_tool_calls(raw_tool_calls: Any) -> list[Any]:
    """兼容 dict/object 两种 tool_call 结构。"""
    if raw_tool_calls is None:
        return []
    if isinstance(raw_tool_calls, tuple):
        raw_tool_calls = list(raw_tool_calls)
    if not isinstance(raw_tool_calls, list):
        return []

    normalized: list[Any] = []
    for item in raw_tool_calls:
        if isinstance(item, dict):
            raw_function = item.get("function")
            if isinstance(raw_function, dict):
                function_obj = SimpleNamespace(
                    name=str(raw_function.get("name", "") or ""),
                    arguments=raw_function.get("arguments"),
                )
            else:
                function_obj = SimpleNamespace(
                    name=str(getattr(raw_function, "name", "") or ""),
                    arguments=getattr(raw_function, "arguments", None),
                )
            normalized.append(
                SimpleNamespace(
                    id=str(item.get("id", "") or ""),
                    type=item.get("type", "function"),
                    function=function_obj,
                )
            )
        else:
            normalized.append(item)
    return normalized


def _coerce_completion_message(message: Any) -> Any:
    """将消息对象标准化为包含 content/tool_calls 的结构。"""
    if message is None:
        return SimpleNamespace(content="", tool_calls=[])
    if isinstance(message, str):
        return SimpleNamespace(content=message, tool_calls=[])
    if isinstance(message, dict):
        return SimpleNamespace(
            content=message.get("content"),
            tool_calls=_normalize_tool_calls(message.get("tool_calls")),
            thinking=message.get("thinking"),
            reasoning=message.get("reasoning"),
            reasoning_content=message.get("reasoning_content"),
        )
    return message


def _extract_completion_message(response: Any) -> tuple[Any, Any]:
    """从 provider 响应中提取首个 message，并兼容字符串响应。"""
    usage = getattr(response, "usage", None)

    if isinstance(response, str):
        return SimpleNamespace(content=response, tool_calls=[]), usage

    choices = getattr(response, "choices", None)
    if isinstance(choices, list) and choices:
        message = getattr(choices[0], "message", None)
        if message is not None:
            return _coerce_completion_message(message), usage

    payload = _to_plain(response)
    if isinstance(payload, dict):
        if usage is None:
            usage = payload.get("usage")
        choices_payload = payload.get("choices")
        if isinstance(choices_payload, list) and choices_payload:
            first = choices_payload[0]
            if isinstance(first, dict):
                message_payload = first.get("message")
            else:
                message_payload = getattr(first, "message", None)
            if message_payload is not None:
                return _coerce_completion_message(message_payload), usage
        for key in ("output_text", "content", "text"):
            candidate = payload.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return SimpleNamespace(content=candidate, tool_calls=[]), usage

    return SimpleNamespace(content=str(response), tool_calls=[]), usage


def _usage_token(usage: Any, key: str) -> int:
    """读取 usage 中 token 计数，兼容 dict/object。"""
    if usage is None:
        return 0
    value = usage.get(key) if isinstance(usage, dict) else getattr(usage, key, 0)
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _extract_cached_tokens(usage: Any) -> int:
    """从 usage.prompt_tokens_details.cached_tokens 提取缓存命中 token 数。

    兼容 OpenAI SDK 对象和 dict 两种格式。非 OpenAI provider 无此字段时返回 0。
    """
    if usage is None:
        return 0
    details = (
        usage.get("prompt_tokens_details")
        if isinstance(usage, dict)
        else getattr(usage, "prompt_tokens_details", None)
    )
    if details is None:
        return 0
    raw = (
        details.get("cached_tokens")
        if isinstance(details, dict)
        else getattr(details, "cached_tokens", 0)
    )
    try:
        return int(raw or 0)
    except (TypeError, ValueError):
        return 0


def _extract_anthropic_cache_tokens(usage: Any) -> tuple[int, int]:
    """从 Anthropic usage 提取 cache_creation_input_tokens 和 cache_read_input_tokens。

    返回 (cache_creation, cache_read)。非 Anthropic provider 返回 (0, 0)。
    """
    if usage is None:
        return 0, 0
    if isinstance(usage, dict):
        creation = usage.get("cache_creation_input_tokens", 0)
        read = usage.get("cache_read_input_tokens", 0)
    else:
        creation = getattr(usage, "cache_creation_input_tokens", 0)
        read = getattr(usage, "cache_read_input_tokens", 0)
    try:
        return int(creation or 0), int(read or 0)
    except (TypeError, ValueError):
        return 0, 0


def _extract_ttft_ms(usage: Any) -> float:
    """从 usage 提取 TTFT（由 _consume_stream 附加）。"""
    if usage is None:
        return 0.0
    if isinstance(usage, dict):
        return float(usage.get("_ttft_ms", 0.0))
    return float(getattr(usage, "_ttft_ms", 0.0))


def _looks_like_html_document(text: str) -> bool:
    """判断文本是否像整页 HTML 文档（常见于 base_url 配置错误）。"""
    stripped = text.lstrip()
    if not stripped:
        return False
    lowered = stripped.lower()
    if lowered.startswith("<!doctype html") or lowered.startswith("<html"):
        return True
    return "<html" in lowered and "</html>" in lowered and "<head" in lowered


def _summarize_text(text: str, max_len: int = 120) -> str:
    """将文本压缩为单行摘要，避免日志过长。"""
    compact = " ".join(text.split())
    if not compact:
        return "(空)"
    if len(compact) <= max_len:
        return compact
    return f"{compact[: max_len - 3]}..."


# ── 异步辅助 ──────────────────────────────────────────────────

def fire_and_forget(coro: Any, *, name: str = "background") -> None:
    """安全地 fire-and-forget 一个协程，捕获异常避免 'Task exception was never retrieved' 警告。

    可从任何模块调用，替代裸 ``asyncio.create_task()``。
    """
    try:
        task = _asyncio.get_running_loop().create_task(coro, name=name)
    except RuntimeError:
        coro.close()
        return

    def _on_done(t: _asyncio.Task[Any]) -> None:
        if t.cancelled():
            return
        exc = t.exception()
        if exc is not None:
            _ff_logger.debug("fire-and-forget task %r failed: %s", name, exc)

    task.add_done_callback(_on_done)
