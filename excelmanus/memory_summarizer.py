"""对话历史摘要器 — 当消息超阈值时，用轻量模型压缩早期对话。

仿 Claude Agent SDK compaction + LangChain SummarizationMiddleware 设计：
- 系统提示始终完整保留（不压缩）
- 仅压缩对话历史中的早期消息
- 保留最近 N 轮完整上下文
- 用两条合成消息（user 请求摘要 + assistant 摘要）替换旧历史
"""

from __future__ import annotations

from typing import Any

from excelmanus.logger import get_logger

logger = get_logger("memory_summarizer")

SUMMARY_SYSTEM_PROMPT = """\
你是 ExcelManus 对话摘要助手。
将以下对话历史压缩为结构化摘要（≤300字），保留：
• 用户核心需求和约束
• 已操作的文件路径和工作表名称
• 已完成的操作和关键数据点（数字、列名、筛选条件）
• 未完成的待办事项
• 重要的错误和恢复措施
规则：不要编造未出现的信息；引用精确的文件路径和列名。"""


async def summarize_history(
    client: Any,
    model: str,
    messages_to_summarize: list[dict[str, Any]],
    *,
    max_summary_tokens: int = 500,
) -> str:
    """调用轻量模型生成对话摘要。

    Args:
        client: openai.AsyncOpenAI 兼容客户端
        model: 摘要模型名称（建议用 gpt-4o-mini 等轻量模型）
        messages_to_summarize: 需要被摘要的消息列表
        max_summary_tokens: 摘要最大 token 数

    Returns:
        结构化摘要文本
    """
    formatted = _format_messages_for_summary(messages_to_summarize)
    if not formatted.strip():
        return ""

    try:
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
                {"role": "user", "content": f"请摘要以下对话历史：\n\n{formatted}"},
            ],
            max_tokens=max_summary_tokens,
            temperature=0.0,
        )
        content = response.choices[0].message.content
        return (content or "").strip()
    except Exception as exc:
        logger.warning("对话历史摘要失败，跳过: %s", exc)
        return ""


def _format_messages_for_summary(
    messages: list[dict[str, Any]],
    *,
    max_content_chars: int = 500,
    max_total_chars: int = 30000,
) -> str:
    """将消息列表格式化为可读文本，供摘要模型消费。

    对过长的单条消息（如工具结果）进行截断，并限制总长度。
    """
    parts: list[str] = []
    total_chars = 0

    for msg in messages:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")

        if isinstance(content, str) and content.strip():
            text = content.strip()
            if len(text) > max_content_chars:
                text = text[:max_content_chars] + "...[截断]"
            line = f"[{role}] {text}"
            if total_chars + len(line) > max_total_chars:
                parts.append("[..后续消息省略..]")
                break
            parts.append(line)
            total_chars += len(line)

    return "\n".join(parts)
