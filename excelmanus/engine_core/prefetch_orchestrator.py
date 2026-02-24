"""PrefetchOrchestrator — 在主 LLM 调用前预取文件上下文。

在 chat() 路由完成后、_tool_calling_loop 启动前，自动判断是否需要
启动 explorer 子代理预取相关文件的结构和关键数据摘要。

预取结果注入 system prompt（而非对话历史），避免主代理对话中堆积
大量 read_excel / inspect_excel_files 工具调用结果，显著减少 token 消耗。
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from excelmanus.logger import get_logger

if TYPE_CHECKING:
    from excelmanus.engine import AgentEngine
    from excelmanus.events import EventCallback
    from excelmanus.subagent.models import SubagentResult

logger = get_logger("prefetch_orchestrator")

# 匹配用户消息中的 Excel 文件路径
_EXCEL_PATH_RE = re.compile(
    r"((?:[\w./\\-]+/)?"
    r"[\w\u4e00-\u9fff][\w\u4e00-\u9fff.()（）\[\]-]*"
    r"\.(?:xlsx|xlsm|xls))",
    re.IGNORECASE,
)

# 预取摘要最大字符数（避免 system prompt 膨胀）
_PREFETCH_SUMMARY_MAX_CHARS = 6000
# 最多预取文件数
_MAX_PREFETCH_FILES = 8
# 预取超时（秒）
_PREFETCH_TIMEOUT_SECONDS = 30


@dataclass
class PrefetchResult:
    """预取结果。"""

    summary: str
    file_paths: list[str] = field(default_factory=list)
    success: bool = True
    elapsed_seconds: float = 0.0
    skipped_reason: str | None = None


class PrefetchOrchestrator:
    """文件预取编排器。

    在主 LLM 调用前，根据用户消息和工作区状态判断是否需要
    启动 explorer 子代理预取文件上下文。
    """

    def __init__(self, engine: "AgentEngine") -> None:
        self._engine = engine

    async def maybe_prefetch(
        self,
        *,
        user_message: str,
        write_hint: str,
        task_tags: tuple[str, ...],
        on_event: "EventCallback | None" = None,
    ) -> PrefetchResult | None:
        """判断是否需要预取，如需要则执行并返回结果。

        返回 None 表示不需要预取。
        """
        engine = self._engine

        if not engine._config.prefetch_explorer:
            return None

        if not engine._subagent_enabled:
            return None

        # 提取用户消息中提到的文件路径
        mentioned_paths = self._extract_excel_paths(user_message)

        # 如果用户没有提到具体文件，尝试从 workspace manifest 补充
        if not mentioned_paths:
            manifest_paths = self._get_manifest_paths()
            if not manifest_paths:
                return None
            # 用户消息中有文件相关关键词时，预取 manifest 中的文件
            if not self._has_file_intent(user_message, task_tags):
                return None
            mentioned_paths = manifest_paths[:_MAX_PREFETCH_FILES]

        # 限制预取文件数
        if len(mentioned_paths) > _MAX_PREFETCH_FILES:
            mentioned_paths = mentioned_paths[:_MAX_PREFETCH_FILES]

        # 如果只有 1 个文件且是简单查询，不值得预取
        if len(mentioned_paths) == 1 and write_hint == "may_write":
            return None

        logger.info(
            "prefetch 触发: files=%d, paths=%s",
            len(mentioned_paths),
            mentioned_paths,
        )

        return await self._run_prefetch(
            file_paths=mentioned_paths,
            user_message=user_message,
            on_event=on_event,
        )

    async def _run_prefetch(
        self,
        *,
        file_paths: list[str],
        user_message: str,
        on_event: "EventCallback | None" = None,
    ) -> PrefetchResult:
        """执行 explorer 子代理预取。"""
        import asyncio

        engine = self._engine
        paths_str = ", ".join(file_paths)
        prompt = (
            f"用户即将处理以下文件，请预览它们的结构和关键数据：\n"
            f"{paths_str}\n\n"
            f"用户意图参考：{user_message[:200]}\n\n"
            "请为每个文件输出：\n"
            "1. Sheet 列表及各 sheet 的行列数\n"
            "2. 列头（前 1-2 行）\n"
            "3. 数据类型概况（数值列、文本列、日期列等）\n"
            "4. 如有明显的关键数据特征（如汇总行、空行分隔等），简要提及\n\n"
            "输出要求：紧凑、结构化，避免冗余。"
        )

        start = time.monotonic()
        try:
            result: SubagentResult = await asyncio.wait_for(
                engine.run_subagent(
                    agent_name="explorer",
                    prompt=prompt,
                    on_event=on_event,
                ),
                timeout=_PREFETCH_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            elapsed = time.monotonic() - start
            logger.warning("prefetch 超时 (%.1fs)", elapsed)
            return PrefetchResult(
                summary="",
                file_paths=file_paths,
                success=False,
                elapsed_seconds=elapsed,
                skipped_reason="timeout",
            )
        except Exception as exc:  # noqa: BLE001
            elapsed = time.monotonic() - start
            logger.warning("prefetch 异常: %s", exc)
            return PrefetchResult(
                summary="",
                file_paths=file_paths,
                success=False,
                elapsed_seconds=elapsed,
                skipped_reason=f"error: {exc}",
            )

        elapsed = time.monotonic() - start
        summary = (result.summary or "").strip()
        if len(summary) > _PREFETCH_SUMMARY_MAX_CHARS:
            summary = summary[:_PREFETCH_SUMMARY_MAX_CHARS] + "\n[预取摘要已截断]"

        logger.info(
            "prefetch 完成: success=%s, chars=%d, elapsed=%.1fs",
            result.success,
            len(summary),
            elapsed,
        )

        return PrefetchResult(
            summary=summary,
            file_paths=file_paths,
            success=result.success,
            elapsed_seconds=elapsed,
        )

    @classmethod
    def _extract_excel_paths(cls, text: str) -> list[str]:
        """从文本中提取 Excel 文件路径。"""
        matches = _EXCEL_PATH_RE.findall(text)
        # 去重保序
        seen: set[str] = set()
        paths: list[str] = []
        for m in matches:
            normalized = m.strip()
            lower = normalized.lower()
            if lower not in seen:
                seen.add(lower)
                paths.append(normalized)
        return paths

    def _get_manifest_paths(self) -> list[str]:
        """从 workspace manifest 获取已知的 Excel 文件路径。"""
        manifest = self._engine._workspace_manifest
        if manifest is None:
            return []
        files = getattr(manifest, "files", [])
        return [f.path for f in files if hasattr(f, "path")]

    @staticmethod
    def _has_file_intent(
        user_message: str,
        task_tags: tuple[str, ...],
    ) -> bool:
        """判断用户消息是否有文件操作意图。"""
        # task_tags 中有文件相关标签
        file_tags = {"read", "analyze", "format", "merge", "compare", "chart"}
        if any(tag in file_tags for tag in task_tags):
            return True

        # 关键词匹配
        lower = user_message.lower()
        keywords = (
            "文件", "表格", "excel", "xlsx", "工作簿", "sheet",
            "数据", "分析", "报表", "汇总", "合并", "格式化",
            "读取", "查看", "打开", "预览", "检查",
        )
        return any(kw in lower for kw in keywords)

    def build_system_context(self, prefetch: PrefetchResult) -> str:
        """将预取结果格式化为 system prompt 注入文本。"""
        if not prefetch.summary:
            return ""
        paths_str = ", ".join(prefetch.file_paths)
        return (
            f"## 文件预取摘要（explorer 子代理预取，耗时 {prefetch.elapsed_seconds:.1f}s）\n"
            f"涉及文件：{paths_str}\n\n"
            f"{prefetch.summary}\n\n"
            "⚠️ 以上信息已预取，你可以直接基于这些信息进行分析和操作，"
            "无需再次调用 read_excel 或 inspect_excel_files 获取相同内容。"
            "如需更详细的数据（如特定范围的单元格值），仍可按需调用工具。"
        )
