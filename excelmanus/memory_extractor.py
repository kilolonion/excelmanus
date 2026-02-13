"""记忆提取器：调用 LLM 从对话历史中提取有价值的信息。"""

from __future__ import annotations

import json
import logging
from datetime import datetime

import openai

from excelmanus.memory_models import MemoryCategory, MemoryEntry

logger = logging.getLogger(__name__)

# 系统提示词：指导 LLM 从对话历史中提取值得记住的信息
_EXTRACTION_SYSTEM_PROMPT = """\
你是一个记忆提取助手。请分析以下对话历史，提取值得在未来会话中记住的有价值信息。

请关注以下类别的信息：
- file_pattern: 项目中常用的 Excel 文件结构（列名、数据类型、行数量级）
- user_pref: 用户偏好的图表样式和输出格式
- error_solution: 常见错误的解决方案
- general: 其他有价值的信息

请以 JSON 数组格式输出，每条记忆包含 content 和 category 字段。
如果没有值得记住的信息，返回空数组 []。

输出格式示例：
[
  {"content": "用户的销售数据文件包含列：日期、产品、数量、单价、金额", "category": "file_pattern"},
  {"content": "用户偏好蓝色系柱状图，标题使用14号字体", "category": "user_pref"}
]

只输出 JSON 数组，不要包含其他文字。"""


class MemoryExtractor:
    """记忆提取器：调用 LLM 从对话历史中提取有价值的信息。"""

    def __init__(self, client: openai.AsyncOpenAI, model: str) -> None:
        """初始化记忆提取器。

        Args:
            client: OpenAI 异步客户端实例。
            model: 使用的模型名称。
        """
        self._client = client
        self._model = model

    async def extract(self, messages: list[dict]) -> list[MemoryEntry]:
        """分析对话历史，返回值得记住的 MemoryEntry 列表。

        对话为空或仅含系统消息时返回空列表。
        LLM 调用失败时记录日志并返回空列表。

        Args:
            messages: 对话历史消息列表，每条消息为 dict，包含 role 和 content 字段。

        Returns:
            提取到的 MemoryEntry 列表。
        """
        # 过滤掉系统消息，检查是否有实质性对话内容
        non_system = [m for m in messages if m.get("role") != "system"]
        if not non_system:
            return []

        # 构造 LLM 请求消息
        extraction_messages = [
            {"role": "system", "content": _EXTRACTION_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": "以下是需要分析的对话历史：\n\n"
                + self._format_conversation(messages),
            },
        ]

        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=extraction_messages,
            )
        except Exception:
            logger.error("LLM 记忆提取调用失败", exc_info=True)
            return []

        # 解析 LLM 返回的 JSON
        try:
            raw_text = response.choices[0].message.content or ""
            return self._parse_response(raw_text)
        except Exception:
            logger.error("解析 LLM 记忆提取结果失败", exc_info=True)
            return []

    @staticmethod
    def _format_conversation(messages: list[dict]) -> str:
        """将对话历史格式化为可读文本。"""
        parts: list[str] = []
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            parts.append(f"[{role}]: {content}")
        return "\n".join(parts)

    @staticmethod
    def _parse_response(raw_text: str) -> list[MemoryEntry]:
        """解析 LLM 返回的 JSON 文本为 MemoryEntry 列表。

        尝试从原始文本中提取 JSON 数组，支持 LLM 返回带有 markdown 代码块包裹的情况。
        无效的条目会被跳过并记录警告日志。
        """
        text = raw_text.strip()

        # 处理 markdown 代码块包裹的情况
        if text.startswith("```"):
            # 去除首尾的 ``` 标记
            lines = text.split("\n")
            # 去掉第一行（```json 或 ```）和最后一行（```）
            if len(lines) >= 2:
                start = 1
                end = len(lines) - 1 if lines[-1].strip() == "```" else len(lines)
                text = "\n".join(lines[start:end]).strip()

        if not text:
            return []

        items = json.loads(text)
        if not isinstance(items, list):
            logger.warning("LLM 返回的记忆提取结果不是数组: %s", type(items).__name__)
            return []

        now = datetime.now()
        entries: list[MemoryEntry] = []

        # 构建有效类别值集合，用于快速校验
        valid_categories = {c.value for c in MemoryCategory}

        for item in items:
            if not isinstance(item, dict):
                logger.warning("跳过非字典类型的记忆条目: %s", type(item).__name__)
                continue

            content = item.get("content")
            category_str = item.get("category")

            if not content or not isinstance(content, str):
                logger.warning("跳过缺少 content 字段的记忆条目")
                continue

            if category_str not in valid_categories:
                logger.warning("跳过未知类别的记忆条目: %s", category_str)
                continue

            entries.append(
                MemoryEntry(
                    content=content.strip(),
                    category=MemoryCategory(category_str),
                    timestamp=now,
                )
            )

        return entries
