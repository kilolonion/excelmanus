"""Engine 纯函数与模块级常量 — 从 engine.py 提取的零状态依赖工具函数。"""

from __future__ import annotations

import re as _re
from types import SimpleNamespace
from typing import Any

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

_WINDOW_ADVISOR_RETRY_DELAY_MIN_SECONDS = 0.3
_WINDOW_ADVISOR_RETRY_DELAY_MAX_SECONDS = 0.8
_WINDOW_ADVISOR_RETRY_AFTER_CAP_SECONDS = 1.5
_WINDOW_ADVISOR_RETRY_TIMEOUT_CAP_SECONDS = 8.0
_VALID_WRITE_HINTS = {"may_write", "read_only", "unknown"}
_MID_DISCUSSION_MAX_LEN = 2000  # 中间讨论放行阈值（字符数）
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


def _normalize_write_hint(value: Any) -> str:
    """规范化 write_hint，仅返回 may_write/read_only/unknown。"""
    if not isinstance(value, str):
        return "unknown"
    normalized = value.strip().lower()
    if normalized in _VALID_WRITE_HINTS:
        return normalized
    return "unknown"


def _merge_write_hint(route_hint: Any, fallback_hint: Any) -> str:
    """优先使用路由 write_hint；无效时回退到当前状态。"""
    normalized_route = _normalize_write_hint(route_hint)
    if normalized_route != "unknown":
        return normalized_route
    return _normalize_write_hint(fallback_hint)


def _merge_write_hint_with_override(route_hint: Any, override_hint: Any) -> str:
    """合并 write_hint，但 override_hint == 'may_write' 时强制覆盖 route_hint。

    用于写入工具成功后的场景：当 override_hint == 'may_write' 时
    强制覆盖，不应被原始 route_hint（如 'read_only'）压制。
    """
    normalized_override = _normalize_write_hint(override_hint)
    if normalized_override == "may_write":
        return "may_write"
    return _merge_write_hint(route_hint, override_hint)


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


# ── 澄清检测：判断文本是否为向用户反问/澄清 ──────────────────

_CLARIFICATION_PATTERNS = _re.compile(
    r"(?:"
    # 中文澄清信号
    r"请(?:问|告诉|提供|确认|说明|指定|明确)"
    r"|(?:哪个|哪些|哪一个|哪一些)(?:文件|表格|sheet|工作表|工作簿)"
    r"|需要(?:你|您)(?:提供|确认|说明|指定|补充)"
    r"|(?:你|您)(?:想|希望|需要|打算)(?:对|用|在|把)"
    r"|(?:你|您)(?:指的是|说的是|想要的是)"
    r"|(?:能否|可以|可否|是否能)(?:告诉|说明|提供|确认)"
    r"|以下(?:信息|内容|参数|细节)(?:需要|还需)"
    r"|为了(?:更好地|准确地|正确地)(?:完成|执行|处理)"
    # 英文澄清信号
    r"|(?:which|what|could you|can you|please (?:specify|provide|confirm|clarify))"
    r"|(?:I need (?:to know|more info|clarification))"
    r"|(?:before I (?:proceed|start|begin|continue))"
    r")",
    _re.IGNORECASE,
)

# 问号密度阈值：短文本中问号占比高说明是在提问
_MIN_QUESTION_MARKS_FOR_CLARIFICATION = 1


def _looks_like_clarification(text: str) -> bool:
    """判断文本是否为 agent 向用户的澄清/反问。

    用于在首轮无工具调用时放行澄清性文本回复，
    避免被执行守卫或写入门禁误拦截。
    """
    stripped = (text or "").strip()
    if not stripped:
        return False
    # 条件 1：包含问号（中文或英文）
    question_marks = stripped.count("？") + stripped.count("?")
    if question_marks < _MIN_QUESTION_MARKS_FOR_CLARIFICATION:
        return False
    # 条件 2：匹配澄清模式关键词
    if _CLARIFICATION_PATTERNS.search(stripped):
        return True
    # 条件 3：短文本（< 500 字符）且问号密度高（>= 2 个问号）
    if len(stripped) < 500 and question_marks >= 2:
        return True
    return False


# ── 等待用户操作检测：agent 正在等待用户上传/提供素材 ────────

_WAITING_FOR_USER_ACTION_PATTERNS = _re.compile(
    r"(?:"
    # 中文：请求用户上传/发送/提供文件/图片
    r"请(?:直接)?(?:上传|发送|提供|附上|拖入|粘贴)(?:.*?(?:图片|文件|截图|附件|素材|源文件|原始文件|照片|图像|表格))"
    r"|(?:上传|发送|提供|附上)(?:到|至|后|完成后|之后)(?:.*?(?:我|就|即可|立刻|马上))"
    r"|(?:等待|等你|等您|待你|待您)(?:上传|提供|发送|附上)"
    r"|(?:需要|还需|缺少)(?:.*?(?:上传|提供|发送))(?:.*?(?:图片|文件|截图|附件|素材|源))"
    r"|(?:尚未|还没有?|未)(?:收到|检测到|发现|看到)(?:.*?(?:图片|文件|截图|附件|上传))"
    # 英文
    r"|please\s+(?:upload|send|provide|attach|drag)\s+(?:the\s+)?(?:image|file|screenshot|attachment)"
    r"|(?:waiting|wait)\s+(?:for\s+)?(?:you|your)\s+(?:upload|file|image|input)"
    r"|(?:once|after)\s+(?:you\s+)?(?:upload|provide|send|attach)"
    r")",
    _re.IGNORECASE,
)


def _looks_like_waiting_for_user_action(text: str) -> bool:
    """检测文本是否表示 agent 正在等待用户执行操作（上传文件等）。

    用于在写入门禁/执行守卫触发前放行，避免 agent 被迫空转。
    """
    stripped = (text or "").strip()
    if not stripped:
        return False
    return bool(_WAITING_FOR_USER_ACTION_PATTERNS.search(stripped))


# ── 执行守卫：检测"仅建议不执行"的回复 ──────────────────────

_FORMULA_ADVICE_PATTERN = _re.compile(
    r"=(?:IF|DATE|VLOOKUP|HLOOKUP|INDEX|MATCH|SUMIF|COUNTIF|CONCATENATE|LEFT|RIGHT|MID|"
    r"AVERAGE|MAX|MIN|SUM|TRIM|LEN|FIND|SEARCH|IFERROR|AND|OR|NOT|TEXT|VALUE|ROUND|"
    r"SUMPRODUCT|OFFSET|INDIRECT|SUBSTITUTE|UPPER|LOWER|PROPER|DATEDIF|YEARFRAC|"
    r"NETWORKDAYS|WORKDAY|EOMONTH|EDATE|DAYS|DATEVALUE|TIMEVALUE|NOW|TODAY|"
    r"LARGE|TEXTJOIN|LET|TEXTSPLIT|XMATCH|VSTACK|SEQUENCE|FILTER|SORT|UNIQUE|"
    r"LAMBDA|CHOOSECOLS|CHOOSEROWS|HSTACK)\s*\(",
    _re.IGNORECASE,
)

_FORMULA_ADVICE_FALLBACK_PATTERN = _re.compile(
    r"(?<![<>=!])=(?![<>=])\s*[A-Z][A-Z0-9_]{2,}\s*\(",
)

_VBA_MACRO_ADVICE_PATTERN = _re.compile(
    r"(```\s*vb|Sub\s+\w+\s*\(|End\s+Sub\b|\.Range\s*\(|\.Cells\s*\("
    r"|Application\.\w+|Dim\s+\w+\s+As\s)",
    _re.IGNORECASE,
)

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


def _contains_formula_advice(text: str, *, vba_exempt: bool = False) -> bool:
    """检测回复文本中是否包含 Excel 公式或 VBA/宏代码建议（而非实际执行）。

    Args:
        text: 回复文本。
        vba_exempt: 若为 True，跳过 VBA 宏模式检测（用户主动请求 VBA 时）。
    """
    if not text:
        return False
    if _FORMULA_ADVICE_PATTERN.search(text) or _FORMULA_ADVICE_FALLBACK_PATTERN.search(text):
        return True
    if not vba_exempt and _VBA_MACRO_ADVICE_PATTERN.search(text):
        return True
    return False


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
