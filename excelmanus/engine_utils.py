"""Engine 纯函数与模块级常量 — 从 engine.py 提取的零状态依赖工具函数。"""

from __future__ import annotations

import asyncio as _asyncio
import json as _json
import re as _re
import uuid as _uuid
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


# 用户主动请求 VBA 相关帮助的检测模式
_USER_VBA_REQUEST_PATTERN = _re.compile(
    r"(VBA|宏|macro|vbaProject"
    r"|查看.*(?:宏|VBA|macro)|(?:宏|VBA|macro).*(?:代码|源码|内容|逻辑|模块)"
    r"|解[释读析].*(?:宏|VBA|macro)|(?:宏|VBA|macro).*(?:什么|哪些|有没有|是否)"
    r"|inspect.*vba|include.*vba"
    r"|提取.*(?:宏|VBA)|(?:宏|VBA).*提取)",
    _re.IGNORECASE,
)


def _user_requests_vba(text: str) -> bool:
    """检测用户消息是否主动请求 VBA/宏相关帮助（查看、解释、提取等）。"""
    if not text:
        return False
    return bool(_USER_VBA_REQUEST_PATTERN.search(text))


_WRITE_ACTION_VERBS = _re.compile(
    r"(删除|替换|写入|创建|修改|格式化|转置|排序|过滤|合并|计算|填充|插入|移动|复制到|粘贴|更新|设置|调整|添加|生成"
    r"|delete|remove|replace|write|create|modify|format|transpose|merge"
    r"|fill|insert|move|paste|update|generate"
    r"|find\s+and\s+(?:replace|delete)|put\s+in|place\s+in|enter\s+in|apply)",
    _re.IGNORECASE,
)

_FILE_REFERENCE_PATTERN = _re.compile(
    r"(\.\s*xlsx\b|\.\s*xls\b|\.\s*csv\b|[A-Za-z0-9_\-/\\]+\.(?:xlsx|xls|csv))",
    _re.IGNORECASE,
)


def _detect_write_intent(text: str) -> bool:
    """检测用户消息是否同时包含文件引用和写入动作动词。"""
    if not text:
        return False
    has_file = bool(_FILE_REFERENCE_PATTERN.search(text))
    has_action = bool(_WRITE_ACTION_VERBS.search(text))
    return has_file and has_action


def _summarize_text(text: str, max_len: int = 120) -> str:
    """将文本压缩为单行摘要，避免日志过长。"""
    compact = " ".join(text.split())
    if not compact:
        return "(空)"
    if len(compact) <= max_len:
        return compact
    return f"{compact[: max_len - 3]}..."


def _split_tool_call_batches(
    tool_calls: list[Any],
    parallelizable_names: frozenset[str],
) -> list[_ToolCallBatch]:
    """将 tool_calls 拆分为连续的并行/串行批次。

    相邻的可并行工具合并为一个 parallel batch（≥2 个时标记 parallel=True），
    非并行工具各自独立为 sequential batch。
    """
    batches: list[_ToolCallBatch] = []
    current_parallel: list[Any] = []
    for tc in tool_calls:
        name = getattr(getattr(tc, "function", None), "name", "")
        if name in parallelizable_names:
            current_parallel.append(tc)
        else:
            if current_parallel:
                batches.append(_ToolCallBatch(current_parallel, len(current_parallel) > 1))
                current_parallel = []
            batches.append(_ToolCallBatch([tc], False))
    if current_parallel:
        batches.append(_ToolCallBatch(current_parallel, len(current_parallel) > 1))
    return batches


# ── 文本工具调用恢复 ─────────────────────────────────────────
# 部分模型（如 DeepSeek）以纯文本 JSON 输出工具调用而非使用
# API 的 tool_calls 机制。以下函数检测常见格式并恢复为正规调用。

_TOOL_NAME_KEYS = ("command", "name", "tool_name", "function", "tool")
_TOOL_ARGS_KEYS = ("kwargs", "arguments", "parameters", "params", "args", "input")

# ```json ... ``` 或 ``` ... ``` 代码块
_CODE_BLOCK_RE = _re.compile(r'```(?:json)?\s*\n?(.*?)\n?\s*```', _re.DOTALL)
# <tool_call>...</tool_call> XML 标签
_XML_TOOL_CALL_RE = _re.compile(r'<tool_call>\s*(.*?)\s*</tool_call>', _re.DOTALL)
# <function name="...">...</function> XML 标签（部分模型使用此格式）
_XML_FUNCTION_RE = _re.compile(
    r'<function\s+name=["\']([^"\']+)["\']\s*>(.*?)</function>',
    _re.DOTALL,
)
_XML_PARAM_RE = _re.compile(
    r'<parameter\s+name=["\']([^"\']+)["\'](?:\s+type=["\']([^"\']*)["\'])?\s*>(.*?)</parameter>',
    _re.DOTALL,
)


def _try_parse_json_object(text: str) -> dict | None:
    """尝试将文本解析为 JSON object，失败返回 None。"""
    text = text.strip()
    if not text.startswith("{"):
        return None
    try:
        obj = _json.loads(text)
        return obj if isinstance(obj, dict) else None
    except (_json.JSONDecodeError, ValueError):
        return None


def _match_tool_in_dict(
    obj: dict,
    registered_names: set[str] | frozenset[str],
) -> tuple[str, str] | None:
    """从 dict 中提取 (tool_name, arguments_json)。

    识别多种键名格式：command/name/tool_name + kwargs/arguments/parameters。
    工具名必须在 registered_names 中才算有效。
    """
    tool_name: str | None = None
    name_key: str | None = None
    for key in _TOOL_NAME_KEYS:
        val = obj.get(key)
        if isinstance(val, str) and val.strip():
            candidate = val.strip()
            if candidate in registered_names:
                tool_name = candidate
                name_key = key
                break

    if not tool_name or not name_key:
        return None

    # 提取参数
    for key in _TOOL_ARGS_KEYS:
        val = obj.get(key)
        if isinstance(val, dict):
            return tool_name, _json.dumps(val, ensure_ascii=False)

    # 兜底：name_key 以外的所有字段视为参数
    remaining = {k: v for k, v in obj.items() if k != name_key}
    return tool_name, _json.dumps(remaining, ensure_ascii=False)


def _find_balanced_json(text: str, start: int) -> str | None:
    """从 start 位置的 '{' 开始，匹配平衡的 JSON 对象字符串。"""
    if start >= len(text) or text[start] != "{":
        return None
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if escape:
            escape = False
            continue
        if ch == "\\":
            if in_string:
                escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def _extract_text_tool_calls(
    text: str,
    registered_tool_names: set[str] | frozenset[str],
) -> tuple[list[SimpleNamespace], str]:
    """检测并解析 LLM 以纯文本输出的工具调用。

    支持格式:
    - 代码块: ``json {"command": "...", "kwargs": {...}} ``
    - XML 标签: <tool_call>{"name": "...", "arguments": {...}}</tool_call>
    - XML 函数: <function name="..."><parameter name="..." type="...">value</parameter></function>
    - 裸 JSON: {"command": "...", "kwargs": {...}}

    Args:
        text: LLM 回复的文本内容。
        registered_tool_names: 可用工具名集合，用于验证。

    Returns:
        (tool_calls, cleaned_text):
        - tool_calls: 解析出的工具调用列表（SimpleNamespace 格式）。
        - cleaned_text: 去除 JSON 块后的自然语言文本。
        若未检测到有效工具调用，返回 ([], 原文本)。
    """
    if not text or not registered_tool_names:
        return [], text

    parsed: list[SimpleNamespace] = []
    regions: list[tuple[int, int]] = []

    def _make_tc(name: str, args_json: str) -> SimpleNamespace:
        return SimpleNamespace(
            id=f"text_recovery_{_uuid.uuid4().hex[:12]}",
            type="function",
            function=SimpleNamespace(name=name, arguments=args_json),
        )

    # 策略 1: ```json ... ``` 代码块
    for m in _CODE_BLOCK_RE.finditer(text):
        obj = _try_parse_json_object(m.group(1))
        if obj is None:
            continue
        result = _match_tool_in_dict(obj, registered_tool_names)
        if result:
            parsed.append(_make_tc(*result))
            regions.append((m.start(), m.end()))

    # 策略 2: <tool_call>...</tool_call>
    if not parsed:
        for m in _XML_TOOL_CALL_RE.finditer(text):
            obj = _try_parse_json_object(m.group(1))
            if obj is None:
                continue
            result = _match_tool_in_dict(obj, registered_tool_names)
            if result:
                parsed.append(_make_tc(*result))
                regions.append((m.start(), m.end()))

    # 策略 2b: <function name="...">...<parameter>...</parameter>...</function>
    if not parsed:
        for m in _XML_FUNCTION_RE.finditer(text):
            func_name = m.group(1).strip()
            if func_name not in registered_tool_names:
                continue
            params_text = m.group(2)
            args_dict: dict[str, Any] = {}
            for pm in _XML_PARAM_RE.finditer(params_text):
                p_name = pm.group(1).strip()
                p_type = (pm.group(2) or "").strip().lower()
                p_val: Any = pm.group(3).strip()
                if p_type == "boolean":
                    p_val = p_val.lower() in ("true", "1", "yes")
                elif p_type in ("integer", "int"):
                    try:
                        p_val = int(p_val)
                    except ValueError:
                        pass
                elif p_type in ("number", "float"):
                    try:
                        p_val = float(p_val)
                    except ValueError:
                        pass
                args_dict[p_name] = p_val
            parsed.append(_make_tc(func_name, _json.dumps(args_dict, ensure_ascii=False)))
            regions.append((m.start(), m.end()))

    # 策略 3: 裸 JSON — 逐个 '{' 尝试平衡匹配
    if not parsed:
        i = 0
        while i < len(text):
            pos = text.find("{", i)
            if pos < 0:
                break
            json_str = _find_balanced_json(text, pos)
            if json_str is None:
                i = pos + 1
                continue
            obj = _try_parse_json_object(json_str)
            if obj is not None:
                result = _match_tool_in_dict(obj, registered_tool_names)
                if result:
                    parsed.append(_make_tc(*result))
                    regions.append((pos, pos + len(json_str)))
            i = pos + len(json_str) if json_str else pos + 1

    if not parsed:
        return [], text

    # 清理文本：移除 JSON 块，保留自然语言
    cleaned = text
    for start, end in sorted(regions, reverse=True):
        cleaned = cleaned[:start] + cleaned[end:]
    cleaned = _re.sub(r"\n{3,}", "\n\n", cleaned).strip()

    return parsed, cleaned


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
