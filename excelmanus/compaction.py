"""上下文自动压缩管理器 — 当对话 token 接近上下文窗口限制时，自动总结并替换早期上下文。

设计要点：
- 后台静默执行，对话不中断
- 增强的 ExcelManus 场景化摘要提示词
- 用户可通过 /compact 手动触发
- 可通过配置或命令开关关闭自动压缩
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from excelmanus.engine_utils import _AUX_NO_THINKING_EXTRA_BODY
from excelmanus.logger import get_logger
from excelmanus.memory import ConversationMemory, TokenCounter

if TYPE_CHECKING:
    from excelmanus.config import ExcelManusConfig
    from excelmanus.embedding.client import EmbeddingClient

logger = get_logger("compaction")


# ── 增强的 ExcelManus 场景化摘要提示词 ────────────────────────

COMPACTION_SYSTEM_PROMPT = """\
你是 ExcelManus 对话压缩助手。你的任务是将对话历史压缩为精确的结构化摘要，\
使 agent 能在摘要基础上无缝继续工作。

## 必须保留的信息（按优先级）

1. **文件与工作表状态**
   - 所有涉及的文件完整路径
   - 工作表名称及其结构（列名、数据范围、行数）
   - 文件间的关联关系（如多表合并、跨文件引用）

2. **已完成的操作**
   - 每个操作的工具名称和关键参数
   - 操作结果摘要（成功/失败、影响的行数/单元格）
   - 数据变更记录（写入了什么值、在哪个位置）

3. **进行中的任务**
   - 当前任务清单状态（哪些完成、哪些待做）
   - 用户最近的意图和约束条件
   - 未解决的错误或阻塞点

4. **关键数据点**
   - 精确的数字（计算结果、筛选条件、阈值）
   - 列名、公式、格式规格
   - 用户指定的业务规则

5. **会话状态**
   - 当前激活的 skill 名称
   - 备份模式状态（on/off、scope）
   - fullaccess 权限状态
   - 窗口感知中活跃窗口的文件和工作表

## 输出格式

使用 Markdown 结构化输出，每个类别一个小节。
省略没有相关信息的类别。
总长度控制在 800 字以内。

## 规则

- 不要编造对话中未出现的信息
- 引用精确的文件路径、列名、单元格地址
- 工具调用结果只保留关键摘要，省略冗长的原始输出
- 如果用户提供了自定义压缩指令，优先遵循用户指令"""


COMPACTION_USER_TEMPLATE = "请压缩以下对话历史：\n\n{formatted_history}"

COMPACTION_USER_TEMPLATE_WITH_INSTRUCTION = (
    "请压缩以下对话历史。\n\n"
    "用户自定义压缩指令：{custom_instruction}\n\n"
    "对话历史：\n\n{formatted_history}"
)


@dataclass
class CompactionStats:
    """Compaction 统计信息。"""

    compaction_count: int = 0
    last_compaction_at: float | None = None
    last_messages_before: int = 0
    last_messages_after: int = 0


@dataclass
class CompactionResult:
    """单次 compaction 操作的结果。"""

    success: bool
    messages_before: int = 0
    messages_after: int = 0
    tokens_before: int = 0
    tokens_after: int = 0
    summary_text: str = ""
    error: str = ""


class CompactionManager:
    """上下文压缩管理器。

    职责：
    - 检测是否需要自动压缩
    - 执行压缩（自动/手动）
    - 跟踪压缩统计
    """

    def __init__(
        self,
        config: "ExcelManusConfig",
        embedding_client: "EmbeddingClient | None" = None,
    ) -> None:
        self._config = config
        self._stats = CompactionStats()
        # 会话级动态开关，初始值继承配置
        self._enabled: bool = config.compaction_enabled
        self._token_counter = TokenCounter()
        # 可选：embedding 客户端，用于语义相关性评分
        self._embedding_client = embedding_client
        # 运行时可变的上下文窗口大小（切换模型时由 engine 更新）
        self._max_context_tokens_override: int = 0

    @property
    def max_context_tokens(self) -> int:
        """当前有效的上下文窗口大小。override > config。"""
        if self._max_context_tokens_override > 0:
            return self._max_context_tokens_override
        return self._config.max_context_tokens

    @max_context_tokens.setter
    def max_context_tokens(self, value: int) -> None:
        """由 engine.switch_model() 调用更新。"""
        self._max_context_tokens_override = max(0, value)

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value

    @property
    def stats(self) -> CompactionStats:
        return self._stats

    def should_compact(
        self,
        memory: ConversationMemory,
        system_msgs: list[dict] | None,
    ) -> bool:
        """检查当前 token 使用率是否超过压缩阈值。"""
        if not self._enabled:
            return False
        current_tokens = memory._total_tokens_with_system_messages(system_msgs)
        threshold = int(
            self.max_context_tokens
            * self._config.compaction_threshold_ratio
        )
        return current_tokens > threshold

    def get_token_usage_ratio(
        self,
        memory: ConversationMemory,
        system_msgs: list[dict] | None,
    ) -> float:
        """返回当前 token 使用率（0.0 ~ 1.0+）。"""
        if self.max_context_tokens <= 0:
            return 0.0
        current_tokens = memory._total_tokens_with_system_messages(system_msgs)
        return current_tokens / self.max_context_tokens

    async def auto_compact(
        self,
        memory: ConversationMemory,
        system_msgs: list[dict] | None,
        *,
        client: object,
        summary_model: str,
    ) -> CompactionResult:
        """自动压缩：后台静默执行，对话不中断。"""
        return await self._do_compact(
            memory=memory,
            system_msgs=system_msgs,
            client=client,
            summary_model=summary_model,
            custom_instruction=None,
            source="auto",
        )

    async def manual_compact(
        self,
        memory: ConversationMemory,
        system_msgs: list[dict] | None,
        *,
        client: object,
        summary_model: str,
        custom_instruction: str | None = None,
    ) -> CompactionResult:
        """手动压缩：由 /compact 命令触发。"""
        return await self._do_compact(
            memory=memory,
            system_msgs=system_msgs,
            client=client,
            summary_model=summary_model,
            custom_instruction=custom_instruction,
            source="manual",
        )

    def get_status(
        self,
        memory: ConversationMemory,
        system_msgs: list[dict] | None,
    ) -> dict[str, Any]:
        """返回 compaction 状态信息，供 /compact status 使用。"""
        current_tokens = memory._total_tokens_with_system_messages(system_msgs)
        max_tokens = self.max_context_tokens
        ratio = current_tokens / max_tokens if max_tokens > 0 else 0.0
        threshold = self._config.compaction_threshold_ratio
        return {
            "enabled": self._enabled,
            "current_tokens": current_tokens,
            "max_tokens": max_tokens,
            "usage_ratio": round(ratio, 3),
            "threshold_ratio": threshold,
            "compaction_count": self._stats.compaction_count,
            "last_compaction_at": self._stats.last_compaction_at,
            "message_count": len(memory.messages),
        }

    async def _score_message_relevance(
        self,
        old_messages: list[dict[str, Any]],
        recent_messages: list[dict[str, Any]],
    ) -> list[float] | None:
        """用 embedding 计算旧消息与最近任务上下文的语义相关性。

        返回每条旧消息的相关性分数 (0.0~1.0)，无 embedding 客户端时返回 None。
        """
        if self._embedding_client is None:
            return None

        # 从最近消息中提取任务上下文（user 消息拼接作为 query）
        recent_user_texts = []
        for msg in recent_messages:
            if msg.get("role") == "user":
                content = msg.get("content", "")
                if isinstance(content, str) and content.strip():
                    recent_user_texts.append(content.strip()[:300])
        if not recent_user_texts:
            return None

        query_text = " ".join(recent_user_texts[-3:])  # 最近 3 条 user 消息

        # 提取旧消息文本（user + assistant 的文本内容）
        old_texts: list[str] = []
        for msg in old_messages:
            content = msg.get("content", "")
            if isinstance(content, str) and content.strip():
                old_texts.append(content.strip()[:300])
            else:
                old_texts.append("")

        if not any(old_texts):
            return None

        try:
            import numpy as np

            # 并行 embed query 和 old_texts
            query_vec, old_vecs = await asyncio.gather(
                self._embedding_client.embed_single(query_text),
                self._embedding_client.embed(old_texts),
            )
            # 计算 cosine similarity
            query_norm = np.linalg.norm(query_vec)
            if query_norm < 1e-9:
                return None
            old_norms = np.linalg.norm(old_vecs, axis=1)
            safe_norms = np.where(old_norms < 1e-9, 1.0, old_norms)
            scores = (old_vecs @ query_vec) / (safe_norms * query_norm)
            return [float(s) for s in scores]
        except Exception:
            logger.debug("Compaction 语义评分失败，跳过相关性标注", exc_info=True)
            return None

    async def _do_compact(
        self,
        memory: ConversationMemory,
        system_msgs: list[dict] | None,
        *,
        client: object,
        summary_model: str,
        custom_instruction: str | None,
        source: str,
    ) -> CompactionResult:
        """执行压缩的核心逻辑。"""
        messages_before = len(memory.messages)
        tokens_before = memory._total_tokens_with_system_messages(system_msgs)

        if messages_before == 0:
            return CompactionResult(
                success=False,
                error="没有可压缩的对话历史。",
            )

        keep_recent = self._config.compaction_keep_recent_turns

        # 找到最近 keep_recent 个 user 消息的起始索引
        user_indices = [
            i for i, m in enumerate(memory.messages)
            if m.get("role") == "user"
        ]
        if len(user_indices) <= keep_recent:
            # 消息太少，不值得压缩
            return CompactionResult(
                success=False,
                messages_before=messages_before,
                error="对话轮次不足，无需压缩。",
            )

        split_idx = user_indices[-keep_recent]
        old_messages = memory.messages[:split_idx]
        recent_messages = memory.messages[split_idx:]

        if not old_messages:
            return CompactionResult(
                success=False,
                messages_before=messages_before,
                error="无早期消息可压缩。",
            )

        # 格式化旧消息供摘要模型消费
        # 如果 embedding 客户端可用，为消息标注语义相关性
        relevance_scores = await self._score_message_relevance(
            old_messages, recent_messages,
        )
        formatted = _format_messages_for_compaction(
            old_messages, relevance_scores=relevance_scores,
        )
        if not formatted.strip():
            return CompactionResult(
                success=False,
                messages_before=messages_before,
                error="旧消息格式化为空，跳过压缩。",
            )

        # 构建摘要请求
        if custom_instruction:
            user_content = COMPACTION_USER_TEMPLATE_WITH_INSTRUCTION.format(
                custom_instruction=custom_instruction,
                formatted_history=formatted,
            )
        else:
            user_content = COMPACTION_USER_TEMPLATE.format(
                formatted_history=formatted,
            )

        max_summary_tokens = self._config.compaction_max_summary_tokens

        try:
            response = await client.chat.completions.create(
                model=summary_model,
                messages=[
                    {"role": "system", "content": COMPACTION_SYSTEM_PROMPT},
                    {"role": "user", "content": user_content},
                ],
                max_tokens=max_summary_tokens,
                temperature=0.0,
                extra_body=_AUX_NO_THINKING_EXTRA_BODY,
            )
            summary_text = (response.choices[0].message.content or "").strip()
        except Exception as exc:
            logger.warning("Compaction 摘要调用失败 (source=%s): %s", source, exc)
            # 降级：规则化极简摘要 + 硬截断
            rule_summary = _extract_rule_based_summary(old_messages)
            if rule_summary:
                synthetic = [
                    {"role": "user", "content": "[系统] 请基于以下对话摘要继续工作。"},
                    {"role": "assistant", "content": f"[对话摘要-规则提取]\n{rule_summary}"},
                ]
                memory._messages = synthetic + recent_messages
            target_threshold = int(
                self.max_context_tokens
                * (self._config.compaction_threshold_ratio - 0.1)
            )
            memory._truncate_history_to_threshold(target_threshold, system_msgs)
            messages_after = len(memory.messages)
            tokens_after = memory._total_tokens_with_system_messages(system_msgs)
            return CompactionResult(
                success=False,
                messages_before=messages_before,
                messages_after=messages_after,
                tokens_before=tokens_before,
                tokens_after=tokens_after,
                error=f"摘要失败，已规则提取+硬截断兜底: {exc}",
            )

        if not summary_text:
            logger.warning("Compaction 摘要为空 (source=%s)，回退到规则提取+硬截断", source)
            rule_summary = _extract_rule_based_summary(old_messages)
            if rule_summary:
                synthetic = [
                    {"role": "user", "content": "[系统] 请基于以下对话摘要继续工作。"},
                    {"role": "assistant", "content": f"[对话摘要-规则提取]\n{rule_summary}"},
                ]
                memory._messages = synthetic + recent_messages
            target_threshold = int(
                self.max_context_tokens
                * (self._config.compaction_threshold_ratio - 0.1)
            )
            memory._truncate_history_to_threshold(target_threshold, system_msgs)
            messages_after = len(memory.messages)
            tokens_after = memory._total_tokens_with_system_messages(system_msgs)
            return CompactionResult(
                success=False,
                messages_before=messages_before,
                messages_after=messages_after,
                tokens_before=tokens_before,
                tokens_after=tokens_after,
                error="摘要为空，已规则提取+硬截断兜底。",
            )

        # 用合成消息替换旧历史
        synthetic: list[dict] = [
            {"role": "user", "content": "[系统] 请基于以下对话摘要继续工作。"},
            {"role": "assistant", "content": f"[对话摘要]\n{summary_text}"},
        ]
        memory._messages = synthetic + recent_messages

        # 如果替换后仍然超限，硬截断兜底
        target_threshold = int(
            self.max_context_tokens
            * (self._config.compaction_threshold_ratio - 0.1)
        )
        if memory._total_tokens_with_system_messages(system_msgs) > target_threshold:
            memory._truncate_history_to_threshold(target_threshold, system_msgs)

        messages_after = len(memory.messages)
        tokens_after = memory._total_tokens_with_system_messages(system_msgs)

        # 更新统计
        self._stats.compaction_count += 1
        self._stats.last_compaction_at = time.time()
        self._stats.last_messages_before = messages_before
        self._stats.last_messages_after = messages_after

        logger.info(
            "Compaction 完成 (source=%s): %d→%d 条消息, %d→%d tokens, 累计 %d 次",
            source,
            messages_before,
            messages_after,
            tokens_before,
            tokens_after,
            self._stats.compaction_count,
        )

        return CompactionResult(
            success=True,
            messages_before=messages_before,
            messages_after=messages_after,
            tokens_before=tokens_before,
            tokens_after=tokens_after,
            summary_text=summary_text,
        )


def _format_messages_for_compaction(
    messages: list[dict[str, Any]],
    *,
    max_content_chars: int = 800,
    max_total_chars: int = 60000,
    relevance_scores: list[float] | None = None,
) -> str:
    """将消息列表格式化为可读文本，供摘要模型消费。

    单条消息上限 800 字符，总量上限 60K，
    以便在大窗口场景下保留更多上下文供摘要。

    工具调用和工具结果会被特殊格式化，保留工具名和关键参数。
    当 relevance_scores 可用时，高相关性消息会获得更大的截断上限，
    低相关性消息会被更积极地截断，并添加相关性标记。
    """
    parts: list[str] = []
    total_chars = 0

    for idx, msg in enumerate(messages):
        # 语义相关性自适应截断：高相关消息保留更多内容
        _score = relevance_scores[idx] if relevance_scores and idx < len(relevance_scores) else -1.0
        if _score >= 0.6:
            _effective_max = min(max_content_chars * 2, 1600)  # 高相关：翻倍上限
            _relevance_tag = "[★高相关] "
        elif _score >= 0.3:
            _effective_max = max_content_chars
            _relevance_tag = ""
        elif _score >= 0:
            _effective_max = max(max_content_chars // 2, 200)  # 低相关：减半上限
            _relevance_tag = "[低相关] "
        else:
            _effective_max = max_content_chars
            _relevance_tag = ""
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        tool_calls = msg.get("tool_calls")

        line = ""

        # 工具调用消息：提取工具名和参数摘要
        if tool_calls and isinstance(tool_calls, list):
            call_parts = []
            for tc in tool_calls:
                func = tc.get("function", {}) if isinstance(tc, dict) else {}
                name = func.get("name", "?") if isinstance(func, dict) else getattr(func, "name", "?")
                args = func.get("arguments", "") if isinstance(func, dict) else getattr(func, "arguments", "")
                if isinstance(args, str) and len(args) > 200:
                    args = args[:200] + "..."
                call_parts.append(f"  → {name}({args})")
            line = f"[{role}] 工具调用:\n" + "\n".join(call_parts)

        # 工具结果消息
        elif role == "tool":
            tool_call_id = msg.get("tool_call_id", "")
            text = str(content).strip() if content else ""
            if len(text) > _effective_max:
                text = text[:_effective_max] + "...[截断]"
            line = f"{_relevance_tag}[tool result:{tool_call_id}] {text}"

        # 普通文本消息
        elif isinstance(content, str) and content.strip():
            text = content.strip()
            if len(text) > _effective_max:
                text = text[:_effective_max] + "...[截断]"
            line = f"{_relevance_tag}[{role}] {text}"

        elif isinstance(content, list):
            # 多模态 content parts
            text_parts = []
            for item in content:
                if isinstance(item, dict):
                    if item.get("type") == "text":
                        text_parts.append(item.get("text", ""))
                    elif item.get("type") == "image_url":
                        text_parts.append("[图片]")
            combined = " ".join(text_parts).strip()
            if combined:
                if len(combined) > _effective_max:
                    combined = combined[:_effective_max] + "...[截断]"
                line = f"{_relevance_tag}[{role}] {combined}"

        if not line:
            continue

        if total_chars + len(line) > max_total_chars:
            parts.append("[..后续消息省略..]")
            break
        parts.append(line)
        total_chars += len(line)

    return "\n".join(parts)


# 写入类工具名集合（用于规则摘要提取写入操作记录）
# 与 policy.MUTATING_ALL_TOOLS 保持语义一致，但硬编码避免循环依赖
_WRITE_TOOLS: frozenset[str] = frozenset({
    "run_shell", "delete_file",
    "write_text_file", "edit_text_file", "rename_file", "copy_file",
    "create_excel_chart", "rebuild_excel_from_spec", "verify_excel_replica",
    "run_code",
})

# 错误关键词模式（用于提取工具执行错误）
_ERROR_KEYWORDS_PATTERN = re.compile(
    r"工具执行错误|Error|Exception|Traceback|失败|FileNotFoundError"
    r"|PermissionError|ValueError|KeyError|IndexError|TypeError",
    re.IGNORECASE,
)

_RULE_SUMMARY_FILE_PATTERN = re.compile(
    r'(?:file_path|path|file|io)["\s:=]+["\']?'
    r'([^\s"\',}\]]+\.(?:xlsx|xls|xlsm|xlsb|csv|tsv|txt|py|json|md))',
    re.IGNORECASE,
)


def _extract_rule_based_summary(
    messages: list[dict[str, Any]],
    *,
    max_total_chars: int = 2000,
) -> str:
    """从消息列表中用纯规则提取结构化摘要（不依赖 LLM）。

    提取维度（按优先级）：
    1. 涉及的文件路径
    2. 已执行的工具调用列表
    3. 写入操作记录（哪些工具修改了哪些文件）
    4. 任务状态（从 task_create/task_update 调用重放）
    5. 用户最近的意图（最后几条 user 消息）
    6. 助手结论（最后几条 assistant 文本回复摘要）
    7. 工具执行错误（最近的未解决错误）

    Args:
        messages: 待提取的消息列表。
        max_total_chars: 摘要总长度软限（字符），超出时截断低优先级维度。

    Returns:
        摘要文本，无可提取内容时返回空字符串。
    """
    import json as _json

    file_paths: set[str] = set()
    tool_calls_summary: list[str] = []
    user_intents: list[str] = []
    # 新维度：写入操作记录
    write_ops: list[str] = []
    # 新维度：任务状态重放
    task_title: str = ""
    task_items: list[dict[str, str]] = []  # [{title, status}]
    # 新维度：助手结论
    assistant_conclusions: list[str] = []
    # 新维度：工具执行错误
    tool_errors: list[str] = []
    # tool_call_id → tool_name 映射（用于关联 tool result 中的错误）
    tc_id_to_name: dict[str, str] = {}

    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")
        tool_calls = msg.get("tool_calls")

        # 提取文件路径
        if isinstance(content, str):
            for m in _RULE_SUMMARY_FILE_PATTERN.finditer(content):
                file_paths.add(m.group(1))

        # 提取工具调用
        if tool_calls and isinstance(tool_calls, list):
            for tc in tool_calls:
                func = tc.get("function", {}) if isinstance(tc, dict) else {}
                name = func.get("name", "?") if isinstance(func, dict) else getattr(func, "name", "?")
                args_str = func.get("arguments", "") if isinstance(func, dict) else getattr(func, "arguments", "")
                tc_id = tc.get("id", "") if isinstance(tc, dict) else getattr(tc, "id", "")
                if tc_id and name:
                    tc_id_to_name[tc_id] = name
                args_dict: dict[str, Any] = {}
                # 从参数中提取文件路径
                if isinstance(args_str, str):
                    for m in _RULE_SUMMARY_FILE_PATTERN.finditer(args_str):
                        file_paths.add(m.group(1))
                    try:
                        args_dict = _json.loads(args_str)
                        fp = args_dict.get("file_path") or args_dict.get("path") or ""
                        if fp:
                            file_paths.add(str(fp))
                    except (ValueError, TypeError):
                        pass
                tool_calls_summary.append(name)

                # ── 写入操作记录 ──
                if name in _WRITE_TOOLS and args_dict:
                    wp = args_dict.get("file_path") or args_dict.get("path") or args_dict.get("destination") or ""
                    sheet = args_dict.get("sheet", "")
                    cell_range = args_dict.get("range", "")
                    desc = name
                    if wp:
                        desc += f" → {wp}"
                    if sheet:
                        desc += f" / {sheet}"
                    if cell_range:
                        desc += f" / {cell_range}"
                    write_ops.append(desc)

                # ── 任务状态重放 ──
                if name == "task_create" and args_dict:
                    task_title = str(args_dict.get("title", ""))
                    subtasks = args_dict.get("subtasks") or args_dict.get("subtask_titles") or []
                    task_items = []
                    for st in subtasks:
                        if isinstance(st, dict):
                            task_items.append({
                                "title": str(st.get("title", "")),
                                "status": "pending",
                            })
                        elif isinstance(st, str):
                            task_items.append({"title": st, "status": "pending"})
                elif name == "task_update" and args_dict:
                    idx = args_dict.get("index")
                    new_status = args_dict.get("new_status") or args_dict.get("status", "")
                    if isinstance(idx, int) and 0 <= idx < len(task_items) and new_status:
                        task_items[idx]["status"] = str(new_status)

        # 提取用户意图
        if role == "user" and isinstance(content, str):
            text = content.strip()
            if text and not text.startswith("[系统]"):
                user_intents.append(text[:200])

        # ── 助手结论 ──
        if role == "assistant" and isinstance(content, str):
            text = content.strip()
            if text and len(text) > 10:
                assistant_conclusions.append(text[:300])

        # ── 工具执行错误 ──
        if role == "tool" and isinstance(content, str):
            text = content.strip()
            if text and _ERROR_KEYWORDS_PATTERN.search(text):
                tc_id = msg.get("tool_call_id", "")
                tool_name = tc_id_to_name.get(tc_id, "unknown")
                err_snippet = text[:150]
                tool_errors.append(f"{tool_name}: {err_snippet}")

    # ── 组装摘要（按优先级排列，高优先级先输出） ──
    parts: list[str] = []
    total_chars = 0

    def _append_if_fits(section: str) -> bool:
        nonlocal total_chars
        if total_chars + len(section) > max_total_chars:
            return False
        parts.append(section)
        total_chars += len(section)
        return True

    # P0: 涉及文件
    if file_paths:
        paths_list = sorted(file_paths)[:20]
        _append_if_fits("**涉及文件**：" + "、".join(paths_list))

    # P0: 已执行工具
    if tool_calls_summary:
        from collections import Counter as _Counter
        counts = _Counter(tool_calls_summary)
        top_tools = counts.most_common(10)
        tool_lines = [f"{name}×{cnt}" for name, cnt in top_tools]
        _append_if_fits("**已执行工具**：" + "、".join(tool_lines))

    # P0: 写入操作记录
    if write_ops:
        unique_ops = list(dict.fromkeys(write_ops))[:10]
        section = "**写入操作**：\n" + "\n".join(f"- {op}" for op in unique_ops)
        _append_if_fits(section)

    # P0: 任务状态
    if task_items:
        status_counts = _Counter(item["status"] for item in task_items)
        progress = "、".join(f"{s}: {c}" for s, c in status_counts.most_common())
        section = f"**任务进度**：「{task_title}」 — {progress}"
        pending = [item["title"] for item in task_items if item["status"] in ("pending", "in_progress")]
        if pending:
            section += "\n待完成: " + "、".join(pending[:5])
        _append_if_fits(section)

    # P0: 用户意图
    if user_intents:
        latest = user_intents[-3:]
        _append_if_fits("**用户意图**：\n" + "\n".join(f"- {i}" for i in latest))

    # P1: 助手结论
    if assistant_conclusions:
        latest = assistant_conclusions[-2:]
        section = "**助手结论**：\n" + "\n".join(f"- {c}" for c in latest)
        _append_if_fits(section)

    # P2: 工具执行错误（仅保留最后 3 条，避免已修复的错误干扰）
    if tool_errors:
        latest = tool_errors[-3:]
        section = "**近期错误**：\n" + "\n".join(f"- {e}" for e in latest)
        _append_if_fits(section)

    return "\n".join(parts)
