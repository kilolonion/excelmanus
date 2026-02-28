"""会话标题自动生成 — 使用 AUX 模型从首轮对话中生成简洁标题。"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_TITLE_SYSTEM_PROMPT = (
    "你是一个标题生成助手。根据用户与助手的对话，生成一个5-10字的简洁中文标题。\n"
    "要求：\n"
    "- 只输出标题本身，不要任何解释、标点或引号\n"
    "- 概括对话的核心意图\n"
    "- 使用中文"
)


async def generate_session_title(
    user_message: str,
    assistant_reply: str,
    *,
    client: Any,
    model: str,
    max_length: int = 20,
) -> str | None:
    """用 LLM 生成会话标题。

    返回生成的标题字符串，失败或结果为空时返回 None。
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
            max_tokens=30,
            temperature=0.3,
        )
        title = (resp.choices[0].message.content or "").strip().strip('"\'')
        if not title:
            return None
        if len(title) > max_length:
            title = title[:max_length]
        return title
    except Exception:
        logger.warning("会话标题生成失败", exc_info=True)
        return None
