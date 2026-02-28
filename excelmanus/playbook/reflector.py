"""TaskReflector — 执行轨迹反思器。

任务完成后分析执行轨迹，提取可泛化的策略教训（PlaybookDelta）。
使用 aux_model 做单次 LLM 推理，成本极低。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from excelmanus.config import ExcelManusConfig

logger = logging.getLogger(__name__)

# 轨迹截取：最近 N 条消息
_MAX_TRAJECTORY_MESSAGES = 10
# 输出限制
_MAX_OUTPUT_TOKENS = 500


@dataclass
class PlaybookDelta:
    """单条策略教训提取结果。"""

    category: str       # "cross_sheet" | "formatting" | "formula" | "data_cleaning" | "error_recovery" | "general"
    content: str        # 策略描述（≤200 字）
    confidence: float   # 0-1，低于阈值丢弃
    source_summary: str # 来源任务摘要


_VALID_CATEGORIES = frozenset({
    "cross_sheet", "formatting", "formula",
    "data_cleaning", "error_recovery", "general",
})

_REFLECTOR_SYSTEM_PROMPT = """你是一个 Excel 操作策略分析专家。分析以下执行轨迹，提取可泛化的策略教训。

规则：
1. 只提取**可跨任务复用**的策略，不要记录任务特定细节（如具体文件名、具体数据值）
2. 每条策略 ≤ 200 字，聚焦"做什么"而非"为什么"
3. 关注：成功的高效路径、失败后的有效修复、避免的陷阱
4. 分类：cross_sheet / formatting / formula / data_cleaning / error_recovery / general
5. 最多输出 3 条，宁缺毋滥
6. confidence 取值 0-1，仅输出 confidence ≥ 0.5 的条目

输出严格 JSON 数组格式（无其他文本）：
[{"category": "...", "content": "...", "confidence": 0.8, "source_summary": "..."}]

若无可提取的策略，输出空数组 []"""


def _build_reflector_user_prompt(
    trajectory: list[dict[str, Any]],
    task_outcome: str,
    task_tags: tuple[str, ...],
    write_ops_log: list[dict[str, str]],
) -> str:
    """构建反思器的用户提示词。"""
    parts: list[str] = []

    parts.append(f"## 任务结果: {task_outcome}")
    if task_tags:
        parts.append(f"任务标签: {', '.join(task_tags)}")

    if write_ops_log:
        parts.append("\n## 写入操作记录")
        for i, op in enumerate(write_ops_log[:10], 1):
            tool = op.get("tool_name", "unknown")
            fp = op.get("file_path", "")
            sheet = op.get("sheet", "")
            summary = op.get("summary", "")
            parts.append(f"{i}. {tool} → {fp}/{sheet} — {summary}")

    parts.append("\n## 执行轨迹（最近消息）")
    for msg in trajectory[-_MAX_TRAJECTORY_MESSAGES:]:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if isinstance(content, list):
            # 多模态消息，提取文本部分
            texts = [p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"]
            content = " ".join(texts)
        content = str(content)[:300]  # 截取
        if role == "user":
            parts.append(f"[用户] {content}")
        elif role == "assistant":
            # 截取 assistant 消息，跳过工具调用细节
            parts.append(f"[助手] {content}")
        elif role == "tool":
            tool_name = msg.get("name", "")
            parts.append(f"[工具:{tool_name}] {content[:150]}")

    return "\n".join(parts)


def parse_reflector_output(raw_text: str) -> list[PlaybookDelta]:
    """解析反思器 LLM 输出为 PlaybookDelta 列表。

    容错处理：即使输出格式不完美也尽量提取有效条目。
    """
    raw_text = raw_text.strip()

    # 尝试提取 JSON 数组
    start = raw_text.find("[")
    end = raw_text.rfind("]")
    if start == -1 or end == -1 or end <= start:
        logger.debug("反思器输出无有效 JSON 数组: %s", raw_text[:200])
        return []

    json_str = raw_text[start:end + 1]
    try:
        items = json.loads(json_str)
    except json.JSONDecodeError:
        logger.debug("反思器输出 JSON 解析失败: %s", json_str[:200])
        return []

    if not isinstance(items, list):
        return []

    deltas: list[PlaybookDelta] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        category = str(item.get("category", "general")).strip()
        if category not in _VALID_CATEGORIES:
            category = "general"
        content = str(item.get("content", "")).strip()
        if not content:
            continue
        confidence = float(item.get("confidence", 0.0))
        if confidence < 0.5:
            continue
        source_summary = str(item.get("source_summary", "")).strip()

        deltas.append(PlaybookDelta(
            category=category,
            content=content[:200],
            confidence=min(max(confidence, 0.0), 1.0),
            source_summary=source_summary[:100],
        ))

    return deltas[:3]  # 最多 3 条


class TaskReflector:
    """分析执行轨迹，提取可复用的策略教训。"""

    def __init__(self, config: "ExcelManusConfig") -> None:
        self._config = config

    async def reflect(
        self,
        trajectory: list[dict[str, Any]],
        task_outcome: str,
        task_tags: tuple[str, ...] = (),
        write_ops_log: list[dict[str, str]] | None = None,
    ) -> list[PlaybookDelta]:
        """执行反思，返回 0-3 条 PlaybookDelta。

        使用 aux_model 做单次 LLM 调用。
        任何异常均 fail-open（返回空列表）。
        """
        from excelmanus.providers import create_client

        model = self._config.aux_model or self._config.main_model
        if not model:
            logger.debug("无可用模型，跳过反思")
            return []

        user_prompt = _build_reflector_user_prompt(
            trajectory=trajectory,
            task_outcome=task_outcome,
            task_tags=task_tags,
            write_ops_log=write_ops_log or [],
        )

        try:
            client = create_client(
                base_url=self._config.aux_base_url or self._config.main_base_url,
                api_key=self._config.aux_api_key or self._config.main_api_key,
            )
            response = await client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": _REFLECTOR_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=_MAX_OUTPUT_TOKENS,
                temperature=0.3,
            )
            raw_text = (response.choices[0].message.content or "").strip()
            return parse_reflector_output(raw_text)
        except Exception as exc:
            logger.debug("反思器 LLM 调用失败: %s", exc)
            return []
