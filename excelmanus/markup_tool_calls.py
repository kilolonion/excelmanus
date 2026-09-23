"""从正文中的 ``<tool_call>`` 标签恢复工具调用。

部分模型（如 MiMo 系）会把工具调用以文本标签形式混进 ``content``：

    <tool_call><function=NAME><parameter=KEY>VALUE</parameter>...</function></tool_call>

上游网关把标签转写成结构化 ``tool_calls`` 时，参数常在数组/对象值处被
截断，``arguments`` 变成非法 JSON，工具派发阶段只能报 INVALID_ARGS。
本模块做两件兜底：

1. 用正文中的完整标签修复截断参数；网关完全没产出结构化调用时直接补建；
2. 把标签块从 ``message.content`` 剥离，避免裸标签泄露给用户和历史记录。

````` 围栏内的 ``<tool_call>`` 视为说明性示例，不解析也不剥离。
"""

from __future__ import annotations

import json
import re
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from excelmanus.engine_utils import _message_content_to_text
from excelmanus.logger import get_logger

logger = get_logger("markup_tool_calls")

_TOOL_CALL_OPEN_RE = re.compile(r"<tool_call\b[^>]*>", re.IGNORECASE)
_TOOL_CALL_CLOSE_RE = re.compile(r"</tool_call\s*>", re.IGNORECASE)
_FUNCTION_HEAD_RE = re.compile(
    r"<function\s*(?:=\s*|name\s*=\s*[\"']?)([^<>\"'\s]+)[\"']?\s*>",
    re.IGNORECASE,
)
_FUNCTION_CLOSE_RE = re.compile(r"</function\s*>", re.IGNORECASE)
_PARAMETER_RE = re.compile(
    r"<parameter\s*(?:=\s*|name\s*=\s*[\"']?)([^<>\"'\s]+)[\"']?\s*>"
    r"(.*?)</parameter\s*>",
    re.IGNORECASE | re.DOTALL,
)
_FENCE_RE = re.compile(r"```")


def _fenced_ranges(text: str) -> list[tuple[int, int]]:
    """``` 成对区间；奇数个时最后一个 ``` 到文末视为未闭合围栏。"""
    marks = [m.start() for m in _FENCE_RE.finditer(text)]
    ranges: list[tuple[int, int]] = []
    it = iter(marks)
    for start in it:
        ranges.append((start, next(it, len(text))))
    return ranges


def _in_ranges(pos: int, ranges: list[tuple[int, int]]) -> bool:
    return any(start <= pos < end for start, end in ranges)


def _block_spans(text: str) -> list[tuple[int, int, str]]:
    """返回 [(start, end, body)]；body 为 <tool_call> 标签内部文本。

    允许末尾未闭合的 <tool_call> 块（流式截断），围栏内的忽略。
    """
    spans: list[tuple[int, int, str]] = []
    fences = _fenced_ranges(text)
    pos = 0
    while True:
        open_match = _TOOL_CALL_OPEN_RE.search(text, pos)
        if open_match is None:
            break
        if _in_ranges(open_match.start(), fences):
            pos = open_match.end()
            continue
        close_match = _TOOL_CALL_CLOSE_RE.search(text, open_match.end())
        end = close_match.end() if close_match else len(text)
        body_end = close_match.start() if close_match else len(text)
        spans.append((open_match.start(), end, text[open_match.end():body_end]))
        pos = end
    return spans


def _coerce_value(raw: str) -> Any:
    """参数值先按 JSON 解析（数组/对象/数字），失败则按原样字符串。"""
    value = raw.strip()
    if not value:
        return ""
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return value


def _parse_function_body(body: str) -> list[dict[str, Any]]:
    """解析 <function=NAME>…<parameter=K>V</parameter>… 段，可含多个 function。"""
    calls: list[dict[str, Any]] = []
    heads = list(_FUNCTION_HEAD_RE.finditer(body))
    for index, head in enumerate(heads):
        name = head.group(1).strip()
        seg_end = heads[index + 1].start() if index + 1 < len(heads) else len(body)
        segment = body[head.end():seg_end]
        close = _FUNCTION_CLOSE_RE.search(segment)
        if close:
            segment = segment[: close.start()]
        args: dict[str, Any] = {}
        for param in _PARAMETER_RE.finditer(segment):
            args[param.group(1).strip()] = _coerce_value(param.group(2))
        if name:
            calls.append({"name": name, "arguments": args})
    return calls


def parse_markup_tool_calls(text: str) -> list[dict[str, Any]]:
    """提取文本中全部 <tool_call> 调用，返回 [{"name", "arguments": dict}]。"""
    if not text or "<tool_call" not in text.lower():
        return []
    calls: list[dict[str, Any]] = []
    for _start, _end, body in _block_spans(text):
        stripped = body.strip()
        if stripped.startswith("{"):
            # 兼容 <tool_call>{"name": ..., "arguments": {...}}</tool_call>
            try:
                payload = json.loads(stripped)
            except (ValueError, TypeError):
                payload = None
            if isinstance(payload, dict) and payload.get("name"):
                args = payload.get("arguments")
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except (ValueError, TypeError):
                        args = None
                calls.append({
                    "name": str(payload["name"]),
                    "arguments": args if isinstance(args, dict) else {},
                })
                continue
        calls.extend(_parse_function_body(body))
    return calls


def strip_markup_tool_calls(text: str) -> str:
    """移除文本中的 <tool_call> 块（围栏内除外），保留其余文本。"""
    if not text or "<tool_call" not in text.lower():
        return text
    spans = _block_spans(text)
    if not spans:
        return text
    parts: list[str] = []
    cursor = 0
    for start, end, _body in spans:
        parts.append(text[cursor:start])
        cursor = end
    parts.append(text[cursor:])
    return "".join(parts)


def _arguments_state(raw: Any) -> str:
    """结构化 arguments 状态：valid（非空合法）/ empty / broken。"""
    if raw is None:
        return "empty"
    if isinstance(raw, dict):
        return "valid" if raw else "empty"
    if isinstance(raw, str):
        if not raw.strip():
            return "empty"
        try:
            return "valid" if isinstance(json.loads(raw), dict) else "broken"
        except (ValueError, TypeError):
            return "broken"
    return "broken"


def _function_of(tc: Any) -> Any:
    function = getattr(tc, "function", None)
    if function is None and isinstance(tc, dict):
        function = tc.get("function")
    return function


def _fn_name(function: Any) -> str:
    if isinstance(function, dict):
        return str(function.get("name") or "")
    return str(getattr(function, "name", "") or "")


def _fn_arguments(function: Any) -> Any:
    if isinstance(function, dict):
        return function.get("arguments")
    return getattr(function, "arguments", None)


def _tc_id(tc: Any) -> str:
    if isinstance(tc, dict):
        return str(tc.get("id") or "")
    return str(getattr(tc, "id", "") or "")


def _make_call(markup: dict[str, Any]) -> Any:
    return SimpleNamespace(
        id=f"call_markup_{uuid4().hex[:20]}",
        type="function",
        function=SimpleNamespace(
            name=markup["name"],
            arguments=json.dumps(markup["arguments"], ensure_ascii=False),
        ),
    )


def recover_tool_calls_from_markup(
    message: Any, tool_calls: list[Any] | None,
) -> tuple[list[Any], str | None]:
    """用 message.content 中的 <tool_call> 标签修复/补全 tool_calls。

    - 结构化调用 arguments 为非法 JSON → 用同名（或同序）标签参数重建；
    - 完全没有结构化调用 → 由标签创建新调用（合成 call id）；
    - 网关已坏（发生过修复）时，追加标签里异名的遗漏调用；
    - message.content 中的标签块被剥离。

    Returns:
        (tool_calls, stripped_text)：stripped_text 为剥离后的正文；
        正文未变化时为 None。
    """
    calls = list(tool_calls or [])
    text = _message_content_to_text(getattr(message, "content", None))
    if not text or "<tool_call" not in text.lower():
        return calls, None

    spans = _block_spans(text)
    if not spans:
        return calls, None
    parsed = parse_markup_tool_calls(text)

    cleaned = strip_markup_tool_calls(text)
    stripped_text = cleaned if cleaned != text else None
    if stripped_text is not None:
        try:
            if isinstance(message, dict):
                message["content"] = cleaned
            else:
                message.content = cleaned
        except Exception:
            logger.debug("message.content 标签剥离回写失败", exc_info=True)

    used: set[int] = set()
    repaired = 0
    for index, tc in enumerate(calls):
        function = _function_of(tc)
        if function is None:
            continue
        name = _fn_name(function)
        state = _arguments_state(_fn_arguments(function))
        if state == "valid":
            continue
        pick = next(
            (i for i, m in enumerate(parsed) if i not in used and m["name"] == name),
            None,
        )
        # 参数完全缺失时只允许同名匹配，避免给无参工具塞错参数；
        # 参数截断（broken）时允许按顺序兜底，坏 JSON 本来就无法执行。
        if pick is None and (state == "broken" or not name):
            pick = next((i for i in range(len(parsed)) if i not in used), None)
        if pick is None:
            continue
        used.add(pick)
        new_name = name or parsed[pick]["name"]
        new_args = json.dumps(parsed[pick]["arguments"], ensure_ascii=False)
        try:
            if isinstance(function, dict):
                function["name"] = new_name
                function["arguments"] = new_args
            else:
                function.name = new_name
                function.arguments = new_args
        except Exception:
            calls[index] = SimpleNamespace(
                id=_tc_id(tc) or f"call_markup_{uuid4().hex[:20]}",
                type="function",
                function=SimpleNamespace(name=new_name, arguments=new_args),
            )
        repaired += 1
        logger.info("已从正文 <tool_call> 标签修复工具调用参数: %s", new_name)

    if not calls:
        for markup in parsed:
            calls.append(_make_call(markup))
            logger.info("已从正文 <tool_call> 标签补建工具调用: %s", markup["name"])
    elif repaired:
        existing_names = {_fn_name(_function_of(tc)) for tc in calls}
        for i, markup in enumerate(parsed):
            if i in used or markup["name"] in existing_names:
                continue
            calls.append(_make_call(markup))
            existing_names.add(markup["name"])
            logger.info("已补建网关遗漏的工具调用: %s", markup["name"])

    return calls, stripped_text


__all__ = [
    "parse_markup_tool_calls",
    "recover_tool_calls_from_markup",
    "strip_markup_tool_calls",
]
