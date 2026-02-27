"""上下文自动压缩管理器 — 当对话 token 接近上下文窗口限制时，自动总结并替换早期上下文。

设计要点：
- 后台静默执行，对话不中断
- 增强的 ExcelManus 场景化摘要提示词
- 用户可通过 /compact 手动触发
- 可通过配置或命令开关关闭自动压缩
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from excelmanus.logger import get_logger
from excelmanus.memory import ConversationMemory, TokenCounter

if TYPE_CHECKING:
    from excelmanus.config import ExcelManusConfig

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

    def __init__(self, config: "ExcelManusConfig") -> None:
        self._config = config
        self._stats = CompactionStats()
        # 会话级动态开关，初始值继承配置
        self._enabled: bool = config.compaction_enabled
        self._token_counter = TokenCounter()

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
            self._config.max_context_tokens
            * self._config.compaction_threshold_ratio
        )
        return current_tokens > threshold

    def get_token_usage_ratio(
        self,
        memory: ConversationMemory,
        system_msgs: list[dict] | None,
    ) -> float:
        """返回当前 token 使用率（0.0 ~ 1.0+）。"""
        if self._config.max_context_tokens <= 0:
            return 0.0
        current_tokens = memory._total_tokens_with_system_messages(system_msgs)
        return current_tokens / self._config.max_context_tokens

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
        max_tokens = self._config.max_context_tokens
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
        formatted = _format_messages_for_compaction(old_messages)
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
            )
            summary_text = (response.choices[0].message.content or "").strip()
        except Exception as exc:
            logger.warning("Compaction 摘要调用失败 (source=%s): %s", source, exc)
            # 降级到硬截断
            target_threshold = int(
                self._config.max_context_tokens
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
                error=f"摘要失败，已硬截断兜底: {exc}",
            )

        if not summary_text:
            logger.warning("Compaction 摘要为空 (source=%s)，回退到硬截断", source)
            target_threshold = int(
                self._config.max_context_tokens
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
                error="摘要为空，已硬截断兜底。",
            )

        # 用合成消息替换旧历史
        synthetic: list[dict] = [
            {"role": "user", "content": "[系统] 请基于以下对话摘要继续工作。"},
            {"role": "assistant", "content": f"[对话摘要]\n{summary_text}"},
        ]
        memory._messages = synthetic + recent_messages

        # 如果替换后仍然超限，硬截断兜底
        target_threshold = int(
            self._config.max_context_tokens
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
) -> str:
    """将消息列表格式化为可读文本，供摘要模型消费。

    单条消息上限 800 字符，总量上限 60K，
    以便在大窗口场景下保留更多上下文供摘要。

    工具调用和工具结果会被特殊格式化，保留工具名和关键参数。
    """
    parts: list[str] = []
    total_chars = 0

    for msg in messages:
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
            if len(text) > max_content_chars:
                text = text[:max_content_chars] + "...[截断]"
            line = f"[tool result:{tool_call_id}] {text}"

        # 普通文本消息
        elif isinstance(content, str) and content.strip():
            text = content.strip()
            if len(text) > max_content_chars:
                text = text[:max_content_chars] + "...[截断]"
            line = f"[{role}] {text}"

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
                if len(combined) > max_content_chars:
                    combined = combined[:max_content_chars] + "...[截断]"
                line = f"[{role}] {combined}"

        if not line:
            continue

        if total_chars + len(line) > max_total_chars:
            parts.append("[..后续消息省略..]")
            break
        parts.append(line)
        total_chars += len(line)

    return "\n".join(parts)
