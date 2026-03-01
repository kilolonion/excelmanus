"""记忆维护代理：LLM 驱动的记忆自动清理、合并、改进。

定期审查全量记忆，执行：
- 合并语义重复/互补的条目
- 删除过时或低价值的条目
- 改写模糊条目使其更具体
- 纠正错误的分类

触发条件设计（避免每次都运行）：
1. 总条目 >= memory_maintenance_min_entries（默认 10）
2. 新增条目 >= memory_maintenance_new_threshold（默认 5）
3. 距上次维护 >= memory_maintenance_interval_hours（默认 4h）
强制触发：总条目 >= 30 且距上次 > 24h（无视新增数要求）
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any

import openai

from excelmanus.memory_models import MemoryCategory, MemoryEntry

if TYPE_CHECKING:
    from excelmanus.persistent_memory import PersistentMemory

logger = logging.getLogger(__name__)

_META_KEY_LAST_MAINTENANCE = "last_maintenance_time"
_META_KEY_COUNT_AT_MAINTENANCE = "count_at_last_maintenance"

_MAINTENANCE_SYSTEM_PROMPT = """\
你是一个记忆维护助手。请审查以下所有记忆条目，执行优化维护。

**你的任务：**
1. **合并重复**：将内容高度相似或互补的条目合并为一条更精炼的条目
2. **删除过时**：移除已过期、不再有用、或信息价值极低的条目
3. **改进质量**：改写模糊或过于笼统的条目，使其更具体可用
4. **纠正分类**：如果条目的类别明显不正确，修正到合适的类别

**类别说明：**
- file_pattern: 文件结构特征（列名规律、sheet布局）
- user_pref: 用户偏好（格式、风格、工作流）
- error_solution: 错误解决方案
- general: 其他有价值的信息

**输出格式（严格 JSON）：**
{
  "keep": [
    {"id": "原条目ID", "content": "保留或改写后的内容", "category": "类别", "action": "keep"},
    {"id": "合并源ID1", "content": "合并后的精炼内容", "category": "类别", "action": "merge", "merged_ids": ["源ID1", "源ID2"]}
  ],
  "delete": ["要删除的条目ID1", "要删除的条目ID2"],
  "summary": "本次维护简要说明"
}

**重要原则：**
- **保守优先**：宁可保留也不要误删有价值的信息
- 合并时保留最完整的信息，不丢失关键细节
- action 为 "keep" 时 content 可改写优化，也可保持原样
- action 为 "merge" 时必须提供 merged_ids 列出被合并的所有源条目 ID
- 被合并的源条目 ID 应出现在 delete 列表中
- 如果所有条目都很好无需维护，delete 返回空数组，keep 包含所有原条目
- 只输出 JSON，不要包含其他文字"""


class MemoryMaintainer:
    """LLM 驱动的记忆维护代理。"""

    def __init__(self, client: openai.AsyncOpenAI, model: str) -> None:
        self._client = client
        self._model = model

    # ── 触发判断 ──────────────────────────────────────────────

    @staticmethod
    def should_run(
        pm: "PersistentMemory",
        *,
        min_entries: int = 10,
        new_threshold: int = 5,
        interval_hours: float = 4.0,
        force_interval_hours: float = 24.0,
        force_total: int = 30,
    ) -> bool:
        """判断是否应运行记忆维护。

        触发条件（ALL true）：
        1. 总条目 >= min_entries
        2. 新增条目 >= new_threshold（相比上次维护时的条目数）
        3. 距上次维护 >= interval_hours

        强制触发（overrides 条件 2）：
        - 总条目 >= force_total 且距上次 > force_interval_hours
        """
        try:
            total = pm.count()
        except Exception:
            return False

        if total < min_entries:
            return False

        # 检查时间间隔
        last_time_str = pm.get_meta(_META_KEY_LAST_MAINTENANCE)
        hours_since: float = float("inf")
        if last_time_str:
            try:
                last_time = datetime.fromisoformat(last_time_str)
                hours_since = (datetime.now() - last_time).total_seconds() / 3600
            except (ValueError, TypeError):
                pass  # 元数据损坏，视为从未维护

        if hours_since < interval_hours:
            return False

        # 检查新增条目数
        last_count_str = pm.get_meta(_META_KEY_COUNT_AT_MAINTENANCE)
        last_count = 0
        if last_count_str:
            try:
                last_count = int(last_count_str)
            except (ValueError, TypeError):
                pass

        new_entries = total - last_count

        # 正常触发
        if new_entries >= new_threshold:
            return True

        # 强制触发：大量条目 + 长时间未维护
        if total >= force_total and hours_since >= force_interval_hours:
            return True

        return False

    # ── 执行维护 ──────────────────────────────────────────────

    async def maintain(self, pm: "PersistentMemory") -> MaintenanceResult:
        """执行一次记忆维护，返回维护结果。"""
        entries = pm.list_entries()
        if not entries:
            return MaintenanceResult(kept=0, deleted=0, rewritten=0, merged=0, summary="无条目")

        # 构建 LLM 输入
        entries_text = self._format_entries_for_llm(entries)
        messages = [
            {"role": "system", "content": _MAINTENANCE_SYSTEM_PROMPT},
            {"role": "user", "content": f"以下是当前所有记忆条目（共 {len(entries)} 条）：\n\n{entries_text}"},
        ]

        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=messages,
            )
        except Exception:
            logger.error("记忆维护 LLM 调用失败", exc_info=True)
            return MaintenanceResult(kept=len(entries), deleted=0, rewritten=0, merged=0, summary="LLM 调用失败")

        raw = response.choices[0].message.content
        result = self._parse_and_apply(pm, entries, raw)

        # 更新维护状态
        try:
            pm.set_meta(_META_KEY_LAST_MAINTENANCE, datetime.now().isoformat())
            pm.set_meta(_META_KEY_COUNT_AT_MAINTENANCE, str(pm.count()))
        except Exception:
            logger.debug("更新维护元数据失败", exc_info=True)

        return result

    @staticmethod
    def _format_entries_for_llm(entries: list[MemoryEntry]) -> str:
        """将条目格式化为 LLM 可读的文本。"""
        parts: list[str] = []
        for e in entries:
            ts = e.timestamp.strftime("%Y-%m-%d %H:%M") if e.timestamp else "unknown"
            parts.append(f"- ID: {e.id} | 类别: {e.category.value} | 时间: {ts}\n  内容: {e.content}")
        return "\n".join(parts)

    @staticmethod
    def _parse_and_apply(
        pm: "PersistentMemory",
        original_entries: list[MemoryEntry],
        raw_text: Any,
    ) -> "MaintenanceResult":
        """解析 LLM 返回并执行维护操作。"""
        if raw_text is None:
            return MaintenanceResult(kept=len(original_entries), deleted=0, rewritten=0, merged=0, summary="LLM 返回空")

        text = str(raw_text).strip()
        # 处理 markdown 代码块
        if text.startswith("```"):
            lines = text.split("\n")
            start = 1
            end = len(lines) - 1 if lines[-1].strip() == "```" else len(lines)
            text = "\n".join(lines[start:end]).strip()

        try:
            result_data = json.loads(text)
        except json.JSONDecodeError:
            logger.warning("记忆维护 LLM 返回非法 JSON: %s", text[:200])
            return MaintenanceResult(kept=len(original_entries), deleted=0, rewritten=0, merged=0, summary="JSON 解析失败")

        if not isinstance(result_data, dict):
            return MaintenanceResult(kept=len(original_entries), deleted=0, rewritten=0, merged=0, summary="返回格式异常")

        # 构建 ID → Entry 映射
        entry_map = {e.id: e for e in original_entries}
        valid_categories = {c.value for c in MemoryCategory}

        # 1. 处理删除
        delete_ids = result_data.get("delete", [])
        if not isinstance(delete_ids, list):
            delete_ids = []
        deleted_count = 0
        for did in delete_ids:
            if not isinstance(did, str):
                continue
            if did in entry_map:
                try:
                    if pm.delete_entry(did):
                        deleted_count += 1
                except Exception:
                    logger.debug("删除记忆条目 %s 失败", did, exc_info=True)

        # 2. 处理 keep（包含 rewrite 和 merge）
        keep_items = result_data.get("keep", [])
        if not isinstance(keep_items, list):
            keep_items = []

        rewritten_count = 0
        merged_count = 0
        new_entries: list[MemoryEntry] = []

        for item in keep_items:
            if not isinstance(item, dict):
                continue
            action = item.get("action", "keep")
            entry_id = item.get("id", "")
            content = item.get("content", "")
            category_str = item.get("category", "")

            if not content or not isinstance(content, str):
                continue

            content = content.strip()
            if not content:
                continue

            if category_str not in valid_categories:
                category_str = "general"

            if action == "merge":
                # 合并操作：创建新条目
                merged_count += 1
                new_entries.append(MemoryEntry(
                    content=content,
                    category=MemoryCategory(category_str),
                    timestamp=datetime.now(),
                ))
            elif action in ("rewrite", "keep") and entry_id in entry_map:
                original = entry_map[entry_id]
                # 检查内容或类别是否变化
                if content != original.content or category_str != original.category.value:
                    rewritten_count += 1
                    # 删除旧条目，写入新条目
                    try:
                        pm.delete_entry(entry_id)
                    except Exception:
                        logger.debug("删除旧条目 %s 失败", entry_id, exc_info=True)
                    new_entries.append(MemoryEntry(
                        content=content,
                        category=MemoryCategory(category_str),
                        timestamp=original.timestamp,
                    ))

        # 批量保存新/改写的条目
        if new_entries:
            try:
                pm.save_entries(new_entries)
            except Exception:
                logger.warning("保存维护后的记忆条目失败", exc_info=True)

        summary = result_data.get("summary", "")
        kept = len(original_entries) - deleted_count
        logger.info(
            "记忆维护完成：保留 %d / 删除 %d / 改写 %d / 合并 %d — %s",
            kept, deleted_count, rewritten_count, merged_count, summary,
        )

        return MaintenanceResult(
            kept=kept,
            deleted=deleted_count,
            rewritten=rewritten_count,
            merged=merged_count,
            summary=summary,
        )


class MaintenanceResult:
    """记忆维护操作结果。"""

    __slots__ = ("kept", "deleted", "rewritten", "merged", "summary")

    def __init__(
        self,
        kept: int = 0,
        deleted: int = 0,
        rewritten: int = 0,
        merged: int = 0,
        summary: str = "",
    ) -> None:
        self.kept = kept
        self.deleted = deleted
        self.rewritten = rewritten
        self.merged = merged
        self.summary = summary

    def __repr__(self) -> str:
        return (
            f"MaintenanceResult(kept={self.kept}, deleted={self.deleted}, "
            f"rewritten={self.rewritten}, merged={self.merged}, summary={self.summary!r})"
        )
