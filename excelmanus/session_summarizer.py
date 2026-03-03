"""SessionSummarizer — 会话结束时异步生成结构化摘要。

将对话历史压缩为跨会话可复用的 Episodic Memory：
- task_goal: 用户核心目标（一句话）
- files_involved: 涉及的文件路径列表
- outcome: completed / partial / failed
- unfinished: 未完成项描述
- summary_text: 完整结构化 Markdown 摘要
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from excelmanus.memory import TokenCounter

if TYPE_CHECKING:
    import openai

logger = logging.getLogger(__name__)

_SUMMARIZER_SYSTEM_PROMPT = """\
你是 ExcelManus 会话摘要助手。请分析以下对话历史，生成一份结构化摘要，\
使助手在未来的新会话中能快速了解"上次做了什么"。

## 输出格式

请严格输出以下 JSON 格式，不要包含其他文字：

```json
{
  "task_goal": "用户的核心目标（一句话，如「合并月度销售报表」）",
  "files_involved": ["file1.xlsx", "file2.xlsx"],
  "outcome": "completed|partial|failed",
  "unfinished": "未完成的事项描述（如已完成则为空字符串）",
  "summary": "完整摘要（Markdown，≤300字）"
}
```

## 规则

- task_goal: 精炼概括用户意图，不超过 30 字
- files_involved: 列出所有涉及的 Excel 文件完整路径，无文件则为空数组
- outcome: 三选一
  - completed: 任务已完全完成
  - partial: 部分完成或有遗留问题
  - failed: 任务失败或被中断
- unfinished: 若 outcome 非 completed，描述具体未完成的内容
- summary: 包含关键操作步骤、数据变更、注意事项，≤300 字
- 不要编造对话中未出现的信息
- 引用精确的文件路径、列名、数据值

## 特殊情况

- 对话仅为闲聊/问答，无实质任务：task_goal 设为空字符串，outcome 设为 "completed"
- 多个独立任务：仅总结最近/最主要的任务"""

_MAX_INPUT_TOKENS = 12_000
_MAX_INPUT_CHARS = 48_000
_MAX_MESSAGES = 120


class SessionSummarizer:
    """会话摘要生成器。"""

    def __init__(
        self,
        client: "openai.AsyncOpenAI",
        model: str,
    ) -> None:
        self._client = client
        self._model = model

    async def summarize(
        self,
        messages: list[dict],
        *,
        max_summary_tokens: int = 500,
    ) -> dict[str, Any] | None:
        """从对话消息生成结构化摘要。

        Returns:
            包含 task_goal, files_involved, outcome, unfinished, summary 的字典，
            或 None（输入不足/LLM 调用失败）。
        """
        normalized = self._prepare_messages(messages)
        if not normalized:
            return None

        formatted = self._format_conversation(normalized)
        if not formatted.strip():
            return None

        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": _SUMMARIZER_SYSTEM_PROMPT},
                    {"role": "user", "content": f"请分析以下对话历史并生成结构化摘要：\n\n{formatted}"},
                ],
                max_tokens=max_summary_tokens,
                temperature=0.0,
            )
        except Exception:
            logger.warning("会话摘要 LLM 调用失败", exc_info=True)
            return None

        raw = response.choices[0].message.content
        return self._parse_response(raw)

    @staticmethod
    def _normalize_content(content: Any) -> str:
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict):
                    text = item.get("text") or item.get("content") or ""
                    if isinstance(text, str) and text:
                        parts.append(text)
                elif isinstance(item, str):
                    parts.append(item)
            return "\n".join(p for p in parts if p)
        return str(content)

    def _prepare_messages(
        self, messages: list[dict],
    ) -> list[tuple[str, str]]:
        """清洗消息，仅保留 user/assistant 文本，控制 token 预算。"""
        cleaned: list[tuple[str, str]] = []
        for msg in messages:
            role = str(msg.get("role", "")).strip()
            if role not in {"user", "assistant"}:
                continue
            text = self._normalize_content(msg.get("content")).strip()
            if not text:
                continue
            cleaned.append((role, text))

        if not cleaned:
            return []

        recent = cleaned[-_MAX_MESSAGES:]

        # 从末尾回收，优先保留最近消息
        selected_reversed: list[tuple[str, str]] = []
        total_chars = 0
        total_tokens = 0
        for role, text in reversed(recent):
            if len(text) > _MAX_INPUT_CHARS:
                text = text[-_MAX_INPUT_CHARS:]

            next_chars = len(text)
            next_tokens = TokenCounter.count(text)

            if selected_reversed and (
                total_chars + next_chars > _MAX_INPUT_CHARS
                or total_tokens + next_tokens > _MAX_INPUT_TOKENS
            ):
                break

            selected_reversed.append((role, text))
            total_chars += next_chars
            total_tokens += next_tokens

        return list(reversed(selected_reversed))

    @staticmethod
    def _format_conversation(messages: list[tuple[str, str]]) -> str:
        parts: list[str] = []
        for role, content in messages:
            # 截断过长的单条消息（保留开头，确保文件路径/列名等关键信息不丢失）
            text = content if len(content) <= 800 else content[:400] + "\n...[中间省略]...\n" + content[-300:]
            parts.append(f"[{role}]: {text}")
        return "\n".join(parts)

    @staticmethod
    def _parse_response(raw_text: Any) -> dict[str, Any] | None:
        """解析 LLM 返回的 JSON 摘要。"""
        if raw_text is None:
            return None

        text = str(raw_text).strip()

        # 处理 markdown 代码块包裹
        if text.startswith("```"):
            lines = text.split("\n")
            if len(lines) >= 2:
                start = 1
                end = len(lines) - 1 if lines[-1].strip() == "```" else len(lines)
                text = "\n".join(lines[start:end]).strip()

        if not text:
            return None

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            logger.warning("会话摘要 JSON 解析失败: %s", text[:200])
            return None

        if not isinstance(data, dict):
            return None

        # 校验必要字段
        task_goal = str(data.get("task_goal", "") or "").strip()
        files_raw = data.get("files_involved", [])
        if not isinstance(files_raw, list):
            files_raw = []
        files_involved = [str(f) for f in files_raw if f]

        outcome_raw = str(data.get("outcome", "") or "").strip().lower()
        outcome = outcome_raw if outcome_raw in ("completed", "partial", "failed") else "partial"

        unfinished = str(data.get("unfinished", "") or "").strip()
        summary = str(data.get("summary", "") or "").strip()

        if not summary and not task_goal:
            return None

        return {
            "task_goal": task_goal,
            "files_involved": files_involved,
            "outcome": outcome,
            "unfinished": unfinished,
            "summary": summary,
        }
