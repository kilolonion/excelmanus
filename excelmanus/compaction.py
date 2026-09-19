"""上下文自动压缩管理器 — 当对话 token 接近上下文窗口限制时，自动总结并替换早期上下文。

设计要点：
- 后台静默执行，对话不中断
- 增强的 ExcelManus 场景化摘要提示词
- 用户可通过 /compact 手动触发
- 可通过配置或命令开关关闭自动压缩
- 此为唯一的上下文压缩层
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from excelmanus.engine_utils import _NO_THINKING_EXTRA_BODY
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
   - 最近读取的文件和工作表范围

## 输出格式

使用 Markdown 结构化输出，每个类别一个小节。
省略没有相关信息的类别。
总长度控制在 800 字以内。

## 规则

- 不要编造对话中未出现的信息
- 引用精确的文件路径、列名、单元格地址
- 工具调用结果只保留关键摘要，省略冗长的原始输出
- 如果用户提供了自定义压缩指令，优先遵循用户指令"""


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
    pruned_tool_results: int = 0


def _bump_compaction_generation(memory: ConversationMemory) -> None:
    memory._compaction_generation = int(getattr(memory, "_compaction_generation", 0) or 0) + 1


_SUMMARY_BUDGET_CHARS = 120_000
_SUMMARY_OMITTED_MARKER = "\n…（更早 {} 条消息已省略，不参与摘要）"


def _cap_projected_for_summary(
    projected: list[dict[str, Any]],
    *,
    budget_chars: int = _SUMMARY_BUDGET_CHARS,
) -> list[dict[str, Any]]:
    """给摘要请求的被压区间加体积上限，防止摘要请求自身溢出。

    保留最新（靠近 keep_recent）的部分；超预算的最旧消息整条省略，
    并在头部补一条省略说明。
    """
    sizes: list[int] = []
    for msg in projected:
        content = msg.get("content") if isinstance(msg, dict) else None
        sizes.append(len(content) if isinstance(content, str) else 0)

    total = sum(sizes)
    if total <= budget_chars:
        return projected

    kept_chars = 0
    keep_from = len(projected)
    for idx in range(len(projected) - 1, -1, -1):
        if kept_chars + sizes[idx] > budget_chars and keep_from < len(projected):
            keep_from = idx + 1
            break
        kept_chars += sizes[idx]
        keep_from = idx
    omitted = keep_from
    kept = projected[keep_from:]
    note = {
        "role": "user",
        "content": _SUMMARY_OMITTED_MARKER.format(omitted),
    }
    return [note, *kept]


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
    ) -> None:
        self._config = config
        self._stats = CompactionStats()
        # 会话级动态开关，初始值继承配置
        self._enabled: bool = config.compaction_enabled
        self._token_counter = TokenCounter()
        # 连续空摘要计数：达到 compaction_empty_summary_max_retries 后
        # pre_step 跳过 LLM 摘要直接回落硬截断。
        self._empty_streak: int = 0
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
        tools: list[dict[str, Any]] | None = None,
        vision_capable: bool | None = None,
    ) -> CompactionResult:
        """自动压缩：后台静默执行，对话不中断。"""
        return await self._do_compact(
            memory=memory,
            system_msgs=system_msgs,
            client=client,
            summary_model=summary_model,
            custom_instruction=None,
            source="auto",
            tools=tools,
            vision_capable=vision_capable,
        )

    async def manual_compact(
        self,
        memory: ConversationMemory,
        system_msgs: list[dict] | None,
        *,
        client: object,
        summary_model: str,
        custom_instruction: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        vision_capable: bool | None = None,
    ) -> CompactionResult:
        """手动压缩：由 /compact 命令触发。"""
        return await self._do_compact(
            memory=memory,
            system_msgs=system_msgs,
            client=client,
            summary_model=summary_model,
            custom_instruction=custom_instruction,
            source="manual",
            tools=tools,
            vision_capable=vision_capable,
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

    def _find_split_index(
        self,
        memory: ConversationMemory,
        all_msgs: list[dict],
        user_floor_idx: int,
    ) -> int:
        """token 定价切点 + tool 配对配平。

        从尾部向前累计 token，保留尾段不超过 retain_tokens 预算
        （默认 max_context 的 25%）；``user_floor_idx``（最近 N 个可见
        用户轮的起点）是保留下限——取两者中保留更多者（更小的索引）。
        切点落在连续 tool 结果段时向前越过整段，保证 tool_call/result
        配对整体落入被压区，retained 尾部不出现孤儿 tool result。
        """
        retain_tokens = int(getattr(self._config, "compaction_retain_tokens", 0) or 0)
        if retain_tokens <= 0:
            retain_tokens = int(self.max_context_tokens * 0.25)

        acc = 0
        token_split = 0
        for i in range(len(all_msgs) - 1, -1, -1):
            acc += memory._count_message(all_msgs[i])
            if acc > retain_tokens:
                token_split = i + 1
                break
            token_split = i

        # token_split=0 表示全部历史都在 retain 预算内（如手动 compact）：
        # 回落到用户轮数下限，保证有可压区间。否则取两者中保留更多者。
        split_idx = min(token_split, user_floor_idx) if token_split > 0 else user_floor_idx
        while split_idx < len(all_msgs) and all_msgs[split_idx].get("role") == "tool":
            split_idx += 1
        return split_idx

    @staticmethod
    def _emit_bookkeeping(
        memory: ConversationMemory, kind: str, payload: dict[str, Any],
    ) -> None:
        """压缩审计事件（non-surface 记账，不进模型可见面）。"""
        log = getattr(memory, "_event_log", None)
        if log is None:
            return
        try:
            log.append(kind, payload)
        except Exception:
            logger.warning("compaction 审计事件写入失败: %s", kind, exc_info=True)

    async def _do_compact(
        self,
        memory: ConversationMemory,
        system_msgs: list[dict] | None,
        *,
        client: object,
        summary_model: str,
        custom_instruction: str | None,
        source: str,
        tools: list[dict[str, Any]] | None = None,
        vision_capable: bool | None = None,
    ) -> CompactionResult:
        """执行压缩的核心逻辑。

        vision_capable=None 时保持历史行为（按视觉模型处理被压区间）；
        调用方应传摘要模型的实际视觉能力，避免纯文本模型收到图片 parts。
        """
        messages_before = len(memory.messages)
        tokens_before = memory._total_tokens_with_system_messages(system_msgs)

        if messages_before == 0:
            return CompactionResult(
                success=False,
                error="没有可压缩的对话历史。",
            )

        keep_recent = self._config.compaction_keep_recent_turns

        from excelmanus.memory import is_visible_user_turn

        # 只按可见用户轮次切分，mention/hook 追加不得挤掉 keep_recent。
        user_indices = [
            i for i, m in enumerate(memory.messages)
            if is_visible_user_turn(m)
        ]
        if not user_indices:
            return CompactionResult(
                success=False,
                messages_before=messages_before,
                error="对话轮次不足，无需压缩。",
            )
        # 可见用户轮不足 keep_recent 时（单轮任务也可能堆积大量
        # tool 结果），保留下限收缩为当前用户轮的起点：当前问题
        # 原文不被摘要，此前轮次仍可压缩。
        user_floor_idx = (
            user_indices[-keep_recent]
            if len(user_indices) > keep_recent
            else user_indices[-1]
        )

        # token 定价切点：保留尾段 ≤ retain_tokens，且至少保留到保留下限
        split_idx = self._find_split_index(
            memory, memory.messages, user_floor_idx
        )
        if split_idx <= 0:
            return CompactionResult(
                success=False,
                messages_before=messages_before,
                error="无早期消息可压缩。",
            )
        old_messages = memory.messages[:split_idx]
        shadowed_tokens = sum(memory._count_message(m) for m in old_messages)
        self._emit_bookkeeping(memory, "compaction/start", {
            "source": source,
            "shadowed_count": len(old_messages),
            "shadowed_tokens": shadowed_tokens,
            "source_seqs": [
                m["_seq"] for m in old_messages
                if isinstance(m.get("_seq"), int)
            ],
        })

        from excelmanus.attachments.project import (
            assemble_model_request,
            content_has_image,
            strip_projection_meta,
        )

        instruction = COMPACTION_SYSTEM_PROMPT
        if custom_instruction:
            instruction = f"{instruction}\n\n用户自定义压缩指令：{custom_instruction}"
        # /no_think：qwen 系模板的文本级禁思考指令，对不认该指令的模型是无害文本
        instruction += "\n\n只输出文本摘要，不要输出图片。\n/no_think"

        live_config = self._config
        try:
            from excelmanus.api_app_state import get_config
            current = get_config()
            if current is not None:
                live_config = current
        except Exception:
            pass

        prefix = list(system_msgs or memory.build_system_messages())
        from excelmanus.memory import _sanitize_messages_for_api

        projected_old = assemble_model_request(
            _sanitize_messages_for_api(
                [{k: v for k, v in m.items() if not str(k).startswith("_")} for m in old_messages]
            ),
            vision_capable=bool(vision_capable if vision_capable is not None else True),
            config=live_config,
        )
        projected_old = _cap_projected_for_summary(projected_old)
        compact_messages = strip_projection_meta(
            prefix + projected_old + [{"role": "user", "content": instruction}],
        )

        max_summary_tokens = self._config.compaction_max_summary_tokens

        try:
            create_kwargs: dict[str, Any] = {
                "model": summary_model,
                "messages": compact_messages,
                "max_tokens": max_summary_tokens,
                "temperature": 0.0,
                "extra_body": _NO_THINKING_EXTRA_BODY,
            }
            if tools:
                create_kwargs["tools"] = tools
            response = await client.chat.completions.create(**create_kwargs)
            summary_message = response.choices[0].message
            if content_has_image(getattr(summary_message, "content", None)):
                raise ValueError("compaction summary cannot contain image output")
            summary_text = (getattr(summary_message, "content", None) or "").strip()
        except Exception as exc:
            logger.warning("Compaction 摘要调用失败 (source=%s): %s", source, exc)
            self._emit_bookkeeping(memory, "compaction/end", {
                "source": source, "success": False, "reason": "summary_call_failed",
            })
            return CompactionResult(
                success=False,
                messages_before=messages_before,
                messages_after=messages_before,
                tokens_before=tokens_before,
                tokens_after=tokens_before,
                error=f"摘要失败，未改写历史: {exc}",
            )

        if not summary_text:
            self._empty_streak += 1
            logger.warning(
                "Compaction 摘要为空 (source=%s)，未改写历史（连续 %d 次）",
                source, self._empty_streak,
            )
            self._emit_bookkeeping(memory, "compaction/end", {
                "source": source, "success": False, "reason": "empty_summary",
                "streak": self._empty_streak,
            })
            return CompactionResult(
                success=False,
                messages_before=messages_before,
                messages_after=messages_before,
                tokens_before=tokens_before,
                tokens_after=tokens_before,
                error="摘要为空，未改写历史。",
            )

        # 摘要必须比被压内容小，否则拒绝替换（保留原文）
        summary_tokens = memory._count_message(
            {"role": "assistant", "content": summary_text}
        )
        if summary_tokens >= shadowed_tokens:
            logger.warning(
                "Compaction 摘要未小于被压区间 (source=%s): %d >= %d tokens，未改写历史",
                source, summary_tokens, shadowed_tokens,
            )
            self._emit_bookkeeping(memory, "compaction/end", {
                "source": source, "success": False,
                "reason": "summary_not_smaller",
            })
            return CompactionResult(
                success=False,
                messages_before=messages_before,
                messages_after=messages_before,
                tokens_before=tokens_before,
                tokens_after=tokens_before,
                error="摘要未小于原文，未改写历史。",
            )

        # 用合成消息替换旧历史（事件日志路径：原文保留在 session_events）
        synthetic: list[dict] = [
            {
                "role": "user",
                "content": "[系统] 请基于以下对话摘要继续工作。",
                "_prompt_kind": "compaction",
            },
            {
                "role": "assistant",
                "content": f"[对话摘要]\n{summary_text}",
                "_prompt_kind": "compaction",
                "_source_message_ids": [
                    m.get("message_id") for m in old_messages if m.get("message_id")
                ],
            },
        ]
        memory.apply_compaction_summary(synthetic, split_idx)
        self._empty_streak = 0
        self._emit_bookkeeping(memory, "compaction/summary", {
            "source": source,
            "shadowed_token_count": shadowed_tokens,
            "summary_tokens": summary_tokens,
        })
        self._emit_bookkeeping(memory, "compaction/end", {
            "source": source, "success": True,
        })
        _bump_compaction_generation(memory)

        # 如果替换后仍然超限，硬截断兜底；头部两条合成摘要必须保住
        target_threshold = int(
            self.max_context_tokens
            * (self._config.compaction_threshold_ratio - 0.1)
        )
        if memory._total_tokens_with_system_messages(system_msgs) > target_threshold:
            memory._truncate_history_to_threshold(
                target_threshold, system_msgs, protect_first=len(synthetic)
            )

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
            pruned_tool_results=0,
        )


# ── Wave D：挂在 pre_step / request-error 上 ─────────────────


class RequestError(RuntimeError):
    """模型请求溢出。仅当 surface 代数推进后才允许重试。"""

    code = "request-error"


def surface_fingerprint(memory: Any) -> tuple[int, int]:
    """会话 surface 代数：条数 + 内容长度。摘要替换或剪枝都会推进。"""
    messages = getattr(memory, "messages", None) or []
    size = 0
    for item in messages:
        content = item.get("content") if isinstance(item, dict) else getattr(item, "content", "")
        size += len(str(content or ""))
    return (len(messages), size)


async def _apply_tool_result_pruning(engine: Any, memory: Any) -> int:
    """L1 机械修剪前可先走片 P。未签字时 maybe_prune 为零。

    返回修剪条数。已 spill/已修剪的内容自动跳过（幂等）。
    只在压缩边界内由 compact_for_pre_step 调用——与摘要共用同一次
    ``surface/compact`` series 重写，一次触发至多一次 cache miss。
    """
    jev_edits = 0
    try:
        from excelmanus.system_one.host import maybe_prune_observations

        jev_edits = await maybe_prune_observations(engine, memory)
    except Exception:
        logger.debug("observation.prune skipped", exc_info=True)
    from excelmanus.compaction_pruner import prune_messages

    msgs = list(getattr(memory, "messages", None) or [])
    edits = prune_messages(msgs)
    for idx, new_content in edits.items():
        msg = msgs[idx]
        msg["content"] = new_content
        emit = getattr(memory, "_emit_replace", None)
        if callable(emit):
            emit(msg, kind="tool/result")
    return jev_edits + len(edits)


def _fallback_truncate(
    engine: Any,
    manager: "CompactionManager",
    memory: "ConversationMemory",
    system_msgs: list[dict] | None,
) -> None:
    """连续空摘要的最后手段：硬截断到阈值，牺牲旧上下文换取窗口保证。

    截断经 ``compaction/truncate`` void 事件上账，surface 指纹推进后由
    调用方统一提交 ``surface/compact`` series 重写。头部已存在的合成
    摘要受保护（protect_first），保住压缩连续性。
    """
    # 与 _do_compact 摘要后截断一致：ratio-0.1 留余量，避免下一边界立即重触发
    threshold = int(
        manager.max_context_tokens
        * (getattr(manager._config, "compaction_threshold_ratio", 0.85) - 0.1)
    )
    # 头部合成摘要是 [user 指令, assistant 摘要] 成对出现，需整体保护
    protect = 0
    if memory.messages and memory.messages[0].get("_prompt_kind") == "compaction":
        protect = 1
        if (
            len(memory.messages) > 1
            and memory.messages[1].get("_prompt_kind") == "compaction"
        ):
            protect = 2
    streak = getattr(manager, "_empty_streak", 0)
    CompactionManager._emit_bookkeeping(memory, "compaction/fallback-truncate", {
        "streak": streak, "threshold": threshold,
    })
    memory._truncate_history_to_threshold(
        threshold, system_msgs, protect_first=protect,
    )
    manager._empty_streak = 0
    logger.info(
        "pre_step 连续空摘要 %d 次，回落硬截断（threshold=%d）",
        streak, threshold,
    )


async def compact_for_pre_step(engine: Any) -> str:
    """pre_step 附件：重放当前信封 leading system + tools + 被压区间。失败也 enter。"""
    manager = getattr(engine, "_compaction_manager", None)
    memory = getattr(engine, "_memory", None) or getattr(engine, "memory", None)
    if manager is None or memory is None:
        return "enter"
    from excelmanus.prompt.envelope import compaction_wire_context

    system_msgs, tools = compaction_wire_context(engine)
    if not system_msgs:
        try:
            from excelmanus.prompt.assemble import build_stable_system_prompt

            stable = build_stable_system_prompt(engine)
            system_msgs = (
                [{"role": "system", "content": stable}] if stable.strip() else []
            )
        except Exception:
            system_msgs = []
    if not manager.should_compact(memory, system_msgs):
        return "enter"
    config = getattr(engine, "_config", None) or getattr(engine, "config", None)
    before = surface_fingerprint(memory)
    # L1：无模型修剪先行——error 载荷瘦身 + head/marker/tail。
    # 修剪后降到阈值下则跳过 LLM 摘要（省一次模型调用）。
    engine._last_pruned_tool_results = 0
    if getattr(config, "compaction_pruner_enabled", True):
        try:
            pruned = await _apply_tool_result_pruning(engine, memory)
            engine._last_pruned_tool_results = pruned
            if pruned:
                logger.info("pre_step 无模型修剪 %d 条 tool 结果", pruned)
                CompactionManager._emit_bookkeeping(memory, "compaction/prune", {
                    "pruned_count": pruned,
                })
        except Exception:
            logger.warning("pre_step 工具结果修剪失败", exc_info=True)
    if not manager.should_compact(memory, system_msgs):
        # 修剪已降到阈值下：只提交 surface 重写，跳过 LLM 摘要
        if surface_fingerprint(memory) != before:
            from excelmanus.prompt.envelope import invalidate_envelope
            from excelmanus.request.series import series_of

            setattr(engine, "_history_snapshot_index", 0)
            engine._compaction_generation = int(getattr(memory, "_compaction_generation", 0) or 0)
            series_of(engine).start_new("surface/compact")
            invalidate_envelope(engine)
        engine._last_compact_failed = False
        return "enter"
    client = getattr(engine, "_client", None)
    if client is None:
        return "enter"
    max_empty = int(getattr(config, "compaction_empty_summary_max_retries", 3) or 0)
    if max_empty > 0 and getattr(manager, "_empty_streak", 0) >= max_empty:
        # 连续空摘要：跳过本次 LLM 摘要调用，直接硬截断释放压力
        _fallback_truncate(engine, manager, memory, system_msgs)
        engine._last_compact_failed = False
    else:
        summary_model = getattr(engine, "_active_model", "") or getattr(config, "model", "") or "dummy"
        try:
            result = await manager.auto_compact(
                memory=memory,
                system_msgs=system_msgs,
                client=client,
                summary_model=str(summary_model),
                tools=tools,
                vision_capable=bool(getattr(engine, "_is_vision_capable", True)),
            )
        except Exception as exc:
            logger.warning("pre_step 压缩失败，不重跑工具: %s", exc)
            engine._last_compact_failed = True
            return "enter"
        if not result.success:
            logger.info("pre_step 压缩未执行: %s", getattr(result, "error", ""))
            if max_empty > 0 and getattr(manager, "_empty_streak", 0) >= max_empty:
                # 本次失败使 streak 达标：同一边界内立即截断，不等下一步
                _fallback_truncate(engine, manager, memory, system_msgs)
        engine._last_compact_failed = not bool(result.success)
    if surface_fingerprint(memory) != before:
        from excelmanus.prompt.envelope import invalidate_envelope
        from excelmanus.request.series import series_of

        setattr(engine, "_history_snapshot_index", 0)
        engine._compaction_generation = int(getattr(memory, "_compaction_generation", 0) or 0)
        series_of(engine).start_new("surface/compact")
        invalidate_envelope(engine)
    return "enter"


async def recover_request_overflow(engine: Any, messages: list[dict[str, Any]] | None) -> Any:
    """溢出走 request-error。只有 surface 推进后才重装信封，禁止静默砍前缀。"""
    _ = messages
    memory = getattr(engine, "_memory", None) or getattr(engine, "memory", None)
    if memory is None:
        return None
    before = surface_fingerprint(memory)
    await compact_for_pre_step(engine)
    if surface_fingerprint(memory) == before:
        logger.warning("request-error：surface 未推进，不重试模型请求")
        return None
    from excelmanus.request.compiler import compile_request

    prepared, error = await compile_request(
        engine,
        tool_access=str(getattr(engine, "_tool_access", None) or "may_write"),
        vision_capable=bool(getattr(engine, "_is_vision_capable", True)),
        extra=getattr(engine, "_compile_extra", None),
    )
    if error is not None or prepared is None:
        logger.warning("request-error：编译失败，不重试: %s", error)
        return None
    return prepared
