"""记忆提取器：调用 LLM 从对话历史中提取有价值的信息。"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any

import openai
import tiktoken

from excelmanus.engine_utils import _AUX_NO_THINKING_EXTRA_BODY
from excelmanus.memory_models import MemoryCategory, MemoryEntry

if TYPE_CHECKING:
    from excelmanus.embedding.client import EmbeddingClient
    from excelmanus.embedding.semantic_memory import SemanticMemory

logger = logging.getLogger(__name__)

# 系统提示词：指导 LLM 从对话历史中提取值得记住的信息
_EXTRACTION_SYSTEM_PROMPT = """\
你是一个记忆提取助手。请分析以下对话历史，提取值得在未来会话中记住的有价值信息。

**核心原则：只记忆"跨会话可复用"的信息。**

问自己：如果用户下周开一个新会话，这条信息还有用吗？
- 有用 → 提取
- 只对本次会话有用 → 不提取

**严格筛选标准——宁缺毋滥：**
- 只提取对未来会话确实有复用价值的信息。
- 一次性的操作细节、临时数据值、当前任务的中间过程不值得记忆。
- 如果对话只是简单问答或一次性操作，没有产生可复用信息，必须返回空数组 []。
- 不要为了凑数而编造或拉伸信息，质量远比数量重要。
- 不要对用户行为做心理推测（如"用户可能偏好…"）。

值得记忆的类别：
- file_pattern: 用户**反复使用**的 Excel 文件结构特征（列名规律、sheet 布局、数据组织方式）
- user_pref: 用户**明确表达**的偏好（不是推测），如格式要求、命名规则、工作流
- error_solution: 具有**通用复用价值**的错误解决方案（不是单次调试细节）
- general: 其他跨会话确实有价值的信息

**不应提取的反例（遇到类似情况必须跳过）：**
- ✗ "用户需要将回归分析结果写入Excel" — 一次性任务描述
- ✗ "数据包含20行，2列：广告投入和销售额" — 单次会话的具体数据
- ✗ "当前工作区有3个Excel文件" — 临时状态，下次会话已变
- ✗ "用户询问了助手身份" — 一次性交互，不是偏好
- ✗ "成功创建了图表/完成了排序" — 操作记录，不是可复用知识

**应提取的正例：**
- ✓ "用户的月度报表固定包含列：日期、产品、数量、单价、金额" — 反复出现的文件结构
- ✓ "用户要求所有图表使用蓝色系配色，标题14号加粗" — 明确表达的偏好
- ✓ "排序前需将文本型公式恢复为真实公式，否则排序结果异常" — 通用解决方案

请以 JSON 数组格式输出，每条记忆包含 content 和 category 字段。
如果没有值得记住的信息，返回空数组 []。
大多数简单对话应该返回 []。

只输出 JSON 数组，不要包含其他文字。"""

_MAX_MESSAGES = 120
_MAX_TOTAL_CHARS = 48_000
_MAX_TOTAL_TOKENS = 12_000
_MIN_USER_MESSAGES = 3  # 少于此数的对话不值得提取记忆
_TOKEN_ENCODING = tiktoken.get_encoding("cl100k_base")


# 语义去重阈值：cosine similarity 超过此值视为重复记忆
MEMORY_DEDUP_SIMILARITY_THRESHOLD = 0.88


class MemoryExtractor:
    """记忆提取器：调用 LLM 从对话历史中提取有价值的信息。"""

    def __init__(
        self,
        client: openai.AsyncOpenAI,
        model: str,
        embedding_client: "EmbeddingClient | None" = None,
        semantic_memory: "SemanticMemory | None" = None,
    ) -> None:
        self._client = client
        self._model = model
        self._embedding_client = embedding_client
        self._semantic_memory = semantic_memory

    async def extract(self, messages: list[dict]) -> list[MemoryEntry]:
        """分析对话历史，返回值得记住的 MemoryEntry 列表。

        对话轮次不足 _MIN_USER_MESSAGES 时直接跳过（短对话几乎不产生可复用记忆）。
        """
        user_count = sum(1 for m in messages if m.get("role") == "user")
        if user_count < _MIN_USER_MESSAGES:
            logger.debug("对话用户消息数 %d < %d，跳过记忆提取", user_count, _MIN_USER_MESSAGES)
            return []

        normalized = self._prepare_messages(messages)
        if not normalized:
            return []

        extraction_messages = [
            {"role": "system", "content": _EXTRACTION_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": "以下是需要分析的对话历史：\n\n"
                + self._format_conversation(normalized),
            },
        ]

        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=extraction_messages,
                extra_body=_AUX_NO_THINKING_EXTRA_BODY,
            )
        except Exception:
            logger.error("LLM 记忆提取调用失败", exc_info=True)
            return []

        try:
            raw_content = response.choices[0].message.content
            entries = self._parse_response(raw_content)
        except Exception:
            logger.error("解析 LLM 记忆提取结果失败", exc_info=True)
            return []

        # 语义去重：过滤与已有记忆过于相似的新条目
        if entries and self._embedding_client is not None and self._semantic_memory is not None:
            entries = await self._dedup_against_existing(entries)

        return entries

    async def _dedup_against_existing(
        self, entries: list[MemoryEntry],
    ) -> list[MemoryEntry]:
        """用 embedding 对新提取的记忆条目与已有记忆做语义去重。

        cosine similarity > MEMORY_DEDUP_SIMILARITY_THRESHOLD 的条目视为重复，被过滤掉。
        """
        assert self._embedding_client is not None
        assert self._semantic_memory is not None

        new_texts = [e.content for e in entries]
        try:
            new_vecs = await self._embedding_client.embed(new_texts)
        except Exception:
            logger.debug("记忆去重向量化失败，跳过去重", exc_info=True)
            return entries

        # 确保 semantic_memory 索引已同步
        store = self._semantic_memory.store
        if store.size == 0:
            return entries  # 无已有记忆，无需去重

        existing_matrix = store.matrix
        from excelmanus.embedding.search import cosine_top_k

        kept: list[MemoryEntry] = []
        for i, entry in enumerate(entries):
            query_vec = new_vecs[i]
            results = cosine_top_k(
                query_vec, existing_matrix,
                k=1, threshold=MEMORY_DEDUP_SIMILARITY_THRESHOLD,
            )
            if results:
                # 找到高度相似的已有记忆，跳过
                logger.debug(
                    "记忆去重：跳过与已有记忆相似的条目 (score=%.3f): %s",
                    results[0].score, entry.content[:60],
                )
            else:
                kept.append(entry)

        if len(kept) < len(entries):
            logger.info(
                "记忆语义去重：%d → %d 条（过滤 %d 条重复）",
                len(entries), len(kept), len(entries) - len(kept),
            )
        return kept

    @staticmethod
    def _normalize_content(content: Any) -> str:
        """兼容多种 content 形态，提取可读文本。"""
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        if isinstance(content, dict):
            if isinstance(content.get("text"), str):
                return content["text"]
            if isinstance(content.get("content"), str):
                return content["content"]
            try:
                return json.dumps(content, ensure_ascii=False)
            except TypeError:
                return str(content)
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict):
                    if isinstance(item.get("text"), str):
                        parts.append(item["text"])
                        continue
                    if isinstance(item.get("content"), str):
                        parts.append(item["content"])
                        continue
                elif isinstance(item, str):
                    parts.append(item)
            return "\n".join(p for p in parts if p)
        return str(content)

    def _prepare_messages(self, messages: list[dict]) -> list[tuple[str, str]]:
        """仅保留有价值对话文本，并控制消息数与总字符数。"""
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
            if len(text) > _MAX_TOTAL_CHARS:
                text = text[-_MAX_TOTAL_CHARS:]

            next_chars = len(text)
            next_tokens = self._count_tokens(text)

            if selected_reversed and (
                total_chars + next_chars > _MAX_TOTAL_CHARS
                or total_tokens + next_tokens > _MAX_TOTAL_TOKENS
            ):
                break

            if not selected_reversed and next_tokens > _MAX_TOTAL_TOKENS:
                text = self._trim_to_token_budget(text, _MAX_TOTAL_TOKENS)
                next_chars = len(text)
                next_tokens = self._count_tokens(text)

            selected_reversed.append((role, text))
            total_chars += next_chars
            total_tokens += next_tokens

        return list(reversed(selected_reversed))

    @staticmethod
    def _count_tokens(text: str) -> int:
        if not text:
            return 0
        try:
            return len(_TOKEN_ENCODING.encode(text))
        except Exception:
            # tiktoken 异常时回退为字符近似，避免阻断主流程。
            return max(1, len(text) // 3)

    def _trim_to_token_budget(self, text: str, token_budget: int) -> str:
        """在超 token 预算时保留末尾内容并回收到预算内。"""
        if token_budget <= 0:
            return ""
        if self._count_tokens(text) <= token_budget:
            return text

        # 二分查找可保留的最大尾部字符数，O(log n) 次 tiktoken 调用
        left, right = 1, len(text)
        best = ""
        while left <= right:
            mid = (left + right) // 2
            candidate = text[-mid:]
            if self._count_tokens(candidate) <= token_budget:
                best = candidate
                left = mid + 1
            else:
                right = mid - 1
        return best

    @staticmethod
    def _format_conversation(messages: list[tuple[str, str]]) -> str:
        parts: list[str] = []
        for role, content in messages:
            parts.append(f"[{role}]: {content}")
        return "\n".join(parts)

    @staticmethod
    def _parse_response(raw_text: Any) -> list[MemoryEntry]:
        """解析 LLM 返回结果为 MemoryEntry 列表。"""
        if raw_text is None:
            return []

        if isinstance(raw_text, (list, dict)):
            text = json.dumps(raw_text, ensure_ascii=False)
        else:
            text = str(raw_text)
        text = text.strip()

        # 处理 markdown 代码块包裹的情况
        if text.startswith("```"):
            lines = text.split("\n")
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
        valid_categories = {c.value for c in MemoryCategory}
        seen: set[tuple[str, str]] = set()

        for item in items:
            if not isinstance(item, dict):
                logger.warning("跳过非字典类型的记忆条目: %s", type(item).__name__)
                continue

            content = item.get("content")
            category_str = item.get("category")

            if not isinstance(content, str):
                logger.warning("跳过缺少 content 字段的记忆条目")
                continue

            normalized_content = content.strip()
            if not normalized_content:
                logger.warning("跳过空白 content 记忆条目")
                continue

            if category_str not in valid_categories:
                logger.warning("跳过未知类别的记忆条目: %s", category_str)
                continue

            dedupe_key = (str(category_str), " ".join(normalized_content.split()))
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)

            entries.append(
                MemoryEntry(
                    content=normalized_content,
                    category=MemoryCategory(category_str),
                    timestamp=now,
                )
            )

        return entries
