"""会话标题自动生成 — 用独立客户端从首轮对话生成短标题，不占用主循环。"""

from __future__ import annotations

import logging
import re
from typing import Any

from excelmanus.engine_utils import _AUX_NO_THINKING_EXTRA_BODY

logger = logging.getLogger(__name__)

_UPLOAD_NOTICE_RE = re.compile(r"\[已上传(?:文件|图片): [^\]]*\]\s*")
_WHITESPACE_RE = re.compile(r"\s+")

_TITLE_SYSTEM_PROMPT = (
    "为一段对话生成简短标题（5-10 字）。\n"
    "风格要求：\n"
    "- 像文件夹名或浏览器标签页标题，名词短语优先\n"
    "- 突出主题而非动作，例如「销售报表汇总」而非「帮我汇总销售报表」\n"
    "- 若涉及具体文件/表格，可包含关键词，例如「Q3营收分析」\n"
    "- 不加标点、引号、书名号，不加「关于」「请求」等冗余词\n"
    "- 若对话为英文则用英文标题，中文对话用中文\n"
    "直接输出标题，不要任何解释。"
)


def clip_title(title: str, max_length: int | None) -> str:
    """超过 max_length 时截断并在末尾加省略号；max_length 为 None 时原样返回。"""
    if max_length is None or max_length < 1 or len(title) <= max_length:
        return title
    if max_length == 1:
        return "…"
    return title[: max_length - 1].rstrip() + "…"


def instant_session_title(user_message: str) -> str | None:
    """从用户消息得到即时标题：去掉上传前缀、取首行，不按显示宽度截断。"""
    if not user_message or not str(user_message).strip():
        return None
    text = str(user_message)
    cleaned = _UPLOAD_NOTICE_RE.sub("", text).strip()
    source = cleaned or text.strip()
    for line in source.splitlines():
        collapsed = _WHITESPACE_RE.sub(" ", line).strip()
        if collapsed:
            return collapsed
    return None


def title_from_messages(messages: list) -> str:
    """从可见用户消息派生即时标题，不按显示宽度截断。"""
    from excelmanus.memory import is_visible_user_turn, plain_user_text

    for msg in messages:
        if not isinstance(msg, dict) or not is_visible_user_turn(msg):
            continue
        title = instant_session_title(plain_user_text(msg.get("content", "")))
        if title:
            return title
    return ""


async def generate_session_title(
    user_message: str,
    assistant_reply: str,
    *,
    client: Any,
    model: str,
    max_length: int | None = None,
) -> str | None:
    """用独立 LLM 客户端生成会话标题。

    返回生成的标题字符串，失败或结果为空时返回 None。
    默认不按字符数截断；仅当调用方传入 max_length 时才截断并加省略号。
    该函数捕获所有异常，保证不会中断调用方流程。
    """
    try:
        user_excerpt = user_message[:300]
        reply_excerpt = assistant_reply[:200]

        resp = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _TITLE_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": f"用户: {user_excerpt}\n助手: {reply_excerpt}",
                },
            ],
            max_tokens=48,
            temperature=0.3,
            extra_body=_AUX_NO_THINKING_EXTRA_BODY,
        )
        raw = (resp.choices[0].message.content or "").strip().strip("\"'")
        if not raw:
            return None
        title = instant_session_title(raw) or raw.splitlines()[0].strip()
        if not title:
            return None
        return clip_title(title, max_length)
    except Exception:
        logger.warning("会话标题生成失败", exc_info=True)
        return None
