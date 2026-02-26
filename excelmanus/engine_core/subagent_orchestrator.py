"""SubagentOrchestrator — 从 AgentEngine 解耦的子代理委派组件。

负责管理：
- delegate_to_subagent 元工具的完整执行流程
- parallel_delegate 元工具的并行子代理委派
- 子代理选择、Hook 拦截、结果同步
- 返回结构化 DelegateSubagentOutcome
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from excelmanus.hooks import HookDecision, HookEvent
from excelmanus.logger import get_logger

if TYPE_CHECKING:
    from excelmanus.engine import AgentEngine, DelegateSubagentOutcome
    from excelmanus.events import EventCallback

logger = get_logger("subagent_orchestrator")

# ── 子代理自动选择：关键词规则 ──────────────────────────────
_WRITE_INTENT_KEYWORDS: frozenset[str] = frozenset({
    "写入", "修改", "删除", "创建", "生成", "新增", "插入", "替换", "更新",
    "保存", "导出", "输出", "覆盖", "追加", "合并", "拆分", "格式化",
    "画图", "图表", "公式", "计算列", "vlookup", "运行", "执行",
    "write", "create", "delete", "update", "save", "export", "run",
})
_READ_INTENT_KEYWORDS: frozenset[str] = frozenset({
    "查看", "分析", "读取", "统计", "定位", "检查", "预览", "概况",
    "列出", "搜索", "查找", "对比", "比较", "探索", "浏览", "扫描",
    "有哪些", "多少", "几个", "什么结构", "哪些列", "哪些sheet",
    "read", "analyze", "inspect", "list", "search", "find", "explore",
    "preview", "scan", "count", "structure",
})

_EXPLORER_SKIP_KEYWORDS: frozenset[str] = frozenset({
    "解释", "说明", "总结", "归纳", "翻译", "润色", "改写", "建议",
    "为什么", "怎么", "如何", "区别", "思路", "方案", "what", "why",
    "how", "explain", "summary", "summarize", "difference", "advice",
})
_TRIVIAL_PATTERNS: frozenset[str] = frozenset({
    "你好", "您好", "谢谢", "感谢", "好的", "收到", "明白", "了解",
    "知道了", "好吧", "行", "可以", "没问题", "没事",
    "ok", "thanks", "thank you", "hi", "hello", "got it", "sure",
    "understood", "noted", "fine", "great", "yes", "no",
})
_EXPLORER_REQUIRED_CUES: frozenset[str] = frozenset({
    "sheet", "工作表", "表格", "列名", "行数", "单元格", "范围", "公式", "统计", "读取", "分析",
    "查找", "筛选", "预览", "文件", "路径", "数据", "read", "analyze", "filter",
    "inspect", "search", "excel", "xlsx", "csv", "cell",
})
_EXCEL_FILE_PATTERN = re.compile(r"[\w./\\-]+\.(xlsx|xlsm|xls|csv)\b", re.IGNORECASE)
_CELL_REF_PATTERN = re.compile(r"\b[A-Za-z]{1,3}\d{1,6}(?::[A-Za-z]{1,3}\d{1,6})?\b")


@dataclass
class ParallelDelegateTask:
    """parallel_delegate 中单个子任务的输入描述。"""

    task: str
    agent_name: str | None = None
    file_paths: list[str] = field(default_factory=list)


@dataclass
class ParallelDelegateOutcome:
    """parallel_delegate 的聚合返回。"""

    reply: str
    success: bool
    outcomes: list["DelegateSubagentOutcome"] = field(default_factory=list)
    conflict_error: str | None = None


class SubagentOrchestrator:
    """子代理委派编排器，封装 _delegate_to_subagent 的完整逻辑。

    通过持有 engine 引用来访问必要的基础设施（hook runner、
    subagent executor、window perception 等），但将委派流程
    的控制逻辑集中在此类中。
    """

    def __init__(self, engine: "AgentEngine") -> None:
        self._engine = engine

    async def delegate(
        self,
        *,
        task: str,
        agent_name: str | None = None,
        file_paths: list[Any] | None = None,
        on_event: "EventCallback | None" = None,
    ) -> "DelegateSubagentOutcome":
        """执行 delegate_to_subagent 并返回结构化结果。"""
        from excelmanus.engine import DelegateSubagentOutcome

        engine = self._engine

        if not engine._subagent_enabled:
            return DelegateSubagentOutcome(
                reply="subagent 当前处于关闭状态，请先执行 `/subagent on`。",
                success=False,
            )

        task_text = task.strip()
        if not task_text:
            return DelegateSubagentOutcome(
                reply="工具参数错误: task 必须为非空字符串。",
                success=False,
            )

        normalized_paths = self.normalize_file_paths(file_paths)

        picked_agent = (agent_name or "").strip()
        if not picked_agent:
            picked_agent = await self.auto_select_subagent(
                task=task_text,
                file_paths=normalized_paths,
            )
        picked_agent = engine._normalize_skill_agent_name(picked_agent) or "subagent"

        if picked_agent == "explorer" and self._should_fast_exit_explorer(
            task=task_text,
            file_paths=normalized_paths,
        ):
            return DelegateSubagentOutcome(
                reply=(
                    "任务偏轻量且未提供可探索的数据上下文，已跳过 explorer。"
                    "请主代理直接给出结论或答复。"
                ),
                success=True,
                picked_agent=picked_agent,
                task_text=task_text,
                normalized_paths=normalized_paths,
            )

        # ── Pre-subagent Hook（A1: 无激活技能时跳过，避免冗余 async 开销） ──
        hook_skill = engine._active_skills[-1] if engine._active_skills else None
        if hook_skill is not None:
            pre_hook_raw = engine._run_skill_hook(
                skill=hook_skill,
                event=HookEvent.SUBAGENT_START,
                payload={
                    "task": task_text,
                    "agent_name": picked_agent,
                    "file_paths": normalized_paths,
                },
            )
            pre_hook = await engine._resolve_hook_result(
                event=HookEvent.SUBAGENT_START,
                hook_result=pre_hook_raw,
                on_event=on_event,
            )
            if pre_hook is not None and pre_hook.decision == HookDecision.DENY:
                reason = pre_hook.reason or "Hook 拒绝了子代理执行。"
                return DelegateSubagentOutcome(
                    reply=f"子代理执行已被 Hook 拦截：{reason}",
                    success=False,
                    picked_agent=picked_agent,
                    task_text=task_text,
                    normalized_paths=normalized_paths,
                )

        # ── 执行子代理（带超时保护） ──
        prompt = task_text
        if normalized_paths:
            prompt += f"\n\n相关文件：{', '.join(normalized_paths)}"

        timeout = engine._config.subagent_timeout_seconds
        try:
            result = await asyncio.wait_for(
                engine.run_subagent(
                    agent_name=picked_agent,
                    prompt=prompt,
                    on_event=on_event,
                ),
                timeout=timeout if timeout > 0 else None,
            )
        except asyncio.TimeoutError:
            logger.warning("子代理 %s 执行超时 (%ds)", picked_agent, timeout)
            return DelegateSubagentOutcome(
                reply=f"子代理 {picked_agent} 执行超时（{timeout}s），已终止。",
                success=False,
                picked_agent=picked_agent,
                task_text=task_text,
                normalized_paths=normalized_paths,
            )

        # ── Post-subagent Hook（A1: 无激活技能时跳过） ──
        if hook_skill is not None:
            post_hook_raw = engine._run_skill_hook(
                skill=hook_skill,
                event=HookEvent.SUBAGENT_STOP,
                payload={
                    "task": task_text,
                    "agent_name": picked_agent,
                    "success": result.success,
                    "summary": result.summary,
                },
            )
            post_hook = await engine._resolve_hook_result(
                event=HookEvent.SUBAGENT_STOP,
                hook_result=post_hook_raw,
                on_event=on_event,
            )
            if post_hook is not None and post_hook.decision == HookDecision.DENY:
                reason = post_hook.reason or "Hook 拒绝了子代理结果。"
                return DelegateSubagentOutcome(
                    reply=f"子代理执行结果已被 Hook 拦截：{reason}",
                    success=False,
                    picked_agent=picked_agent,
                    task_text=task_text,
                    normalized_paths=normalized_paths,
                    subagent_result=result,
                )

        if result.success:
            self._sync_subagent_observations(
                picked_agent=picked_agent,
                task_text=task_text,
                normalized_paths=normalized_paths,
                observed_files=result.observed_files,
                structured_changes=result.structured_changes,
            )
            return DelegateSubagentOutcome(
                reply=result.summary,
                success=True,
                picked_agent=picked_agent,
                task_text=task_text,
                normalized_paths=normalized_paths,
                subagent_result=result,
            )

        partial_observed = bool(result.observed_files)
        partial_changes = bool(result.structured_changes)
        if partial_observed or partial_changes:
            self._sync_subagent_observations(
                picked_agent=picked_agent,
                task_text=task_text,
                normalized_paths=normalized_paths,
                observed_files=result.observed_files,
                structured_changes=result.structured_changes,
            )

        partial_hint = ""
        if (partial_observed or partial_changes) and "已完成的工作" not in result.summary:
            partial_hint = (
                "（已保留部分产出"
                f"：发现文件 {len(result.observed_files)} 个"
                f"，结构化变更 {len(result.structured_changes)} 条）"
            )

        return DelegateSubagentOutcome(
            reply=f"子代理执行失败（{picked_agent}）：{result.summary}{partial_hint}",
            success=False,
            picked_agent=picked_agent,
            task_text=task_text,
            normalized_paths=normalized_paths,
            subagent_result=result,
        )

    def _sync_subagent_observations(
        self,
        *,
        picked_agent: str,
        task_text: str,
        normalized_paths: list[str],
        observed_files: list[str],
        structured_changes: list[Any],
    ) -> None:
        """同步子代理产出的上下文与写入线索到主会话。"""
        engine = self._engine
        engine._window_perception.observe_subagent_context(
            candidate_paths=[*normalized_paths, *observed_files],
            subagent_name=picked_agent,
            task=task_text,
        )
        if structured_changes:
            engine._window_perception.observe_subagent_writes(
                structured_changes=structured_changes,
                subagent_name=picked_agent,
                task=task_text,
            )
        engine._context_builder.mark_window_notice_dirty()

    # ── 并行委派 ──────────────────────────────────────────

    async def delegate_parallel(
        self,
        *,
        tasks: list[ParallelDelegateTask],
        on_event: "EventCallback | None" = None,
    ) -> ParallelDelegateOutcome:
        """并行执行多个子代理任务，返回聚合结果。

        前置校验：
        1. subagent 开关
        2. tasks 非空且 <= 5
        3. 文件路径冲突检测（写入子代理不可操作同一文件）
        """
        from excelmanus.engine import DelegateSubagentOutcome

        engine = self._engine

        if not engine._subagent_enabled:
            return ParallelDelegateOutcome(
                reply="subagent 当前处于关闭状态，请先执行 `/subagent on`。",
                success=False,
            )

        if not tasks:
            return ParallelDelegateOutcome(
                reply="工具参数错误: tasks 不能为空。",
                success=False,
            )

        max_parallel = engine._config.parallel_subagent_max
        if len(tasks) > max_parallel:
            return ParallelDelegateOutcome(
                reply=f"工具参数错误: 最多同时并行 {max_parallel} 个子任务。",
                success=False,
            )

        # ── 文件冲突检测 ──
        conflict = self._detect_file_conflicts(tasks)
        if conflict is not None:
            return ParallelDelegateOutcome(
                reply=f"文件冲突：{conflict}",
                success=False,
                conflict_error=conflict,
            )

        # ── 并发执行（Semaphore 限流） ──
        sem = asyncio.Semaphore(max_parallel)

        async def _run_one(t: ParallelDelegateTask) -> DelegateSubagentOutcome:
            async with sem:
                return await self.delegate(
                    task=t.task,
                    agent_name=t.agent_name,
                    file_paths=t.file_paths,
                    on_event=on_event,
                )

        raw_results = await asyncio.gather(
            *[_run_one(t) for t in tasks],
            return_exceptions=True,
        )

        # ── 聚合结果 ──
        outcomes: list[DelegateSubagentOutcome] = []
        all_success = True
        reply_parts: list[str] = []

        for i, r in enumerate(raw_results):
            task_label = tasks[i].task[:60]
            if isinstance(r, BaseException):
                outcome = DelegateSubagentOutcome(
                    reply=f"并行子代理异常: {r}",
                    success=False,
                )
                all_success = False
            else:
                outcome = r
                if not outcome.success:
                    all_success = False
            outcomes.append(outcome)

            status = "✅" if outcome.success else "❌"
            reply_parts.append(
                f"{status} 任务 {i + 1}「{task_label}」：{outcome.reply}"
            )

        # 注意：窗口感知传播（observe_subagent_context / observe_subagent_writes /
        # mark_window_notice_dirty）已在 self.delegate() 内部对每个成功结果执行，
        # 此处无需重复调用。

        summary = "\n\n".join(reply_parts)
        return ParallelDelegateOutcome(
            reply=summary,
            success=all_success,
            outcomes=outcomes,
        )

    @staticmethod
    def normalize_file_paths(file_paths: list[Any] | None) -> list[str]:
        """规范化 subagent 输入文件路径。"""
        if not file_paths:
            return []
        normalized: list[str] = []
        for item in file_paths:
            if not isinstance(item, str):
                continue
            path = item.strip()
            if path:
                normalized.append(path)
        return normalized

    async def auto_select_subagent(
        self,
        *,
        task: str,
        file_paths: list[str],
    ) -> str:
        """基于关键词规则选择子代理（不调用 LLM）。

        规则优先级：
        1. 任务文本含写入意图关键词 → subagent（通用全能力）
        2. 任务文本含只读意图关键词且 explorer 可用 → explorer
        3. 以上均未命中 → subagent（安全回退）
        """
        _, candidates = self._engine._subagent_registry.build_catalog()
        if not candidates:
            return "subagent"

        candidate_set = set(candidates)
        task_lower = task.lower()

        if any(kw in task_lower for kw in _WRITE_INTENT_KEYWORDS):
            return "subagent"

        if any(kw in task_lower for kw in _READ_INTENT_KEYWORDS) and "explorer" in candidate_set:
            return "explorer"

        return "subagent"

    @staticmethod
    def _detect_file_conflicts(
        tasks: list[ParallelDelegateTask],
    ) -> str | None:
        """检测并行任务间的文件路径冲突。

        规则：同一文件不能出现在两个不同任务的 file_paths 中。
        返回冲突描述字符串，无冲突返回 None。
        """
        seen: dict[str, int] = {}  # normalized_path -> task index
        for i, t in enumerate(tasks):
            for raw_path in t.file_paths:
                normalized = raw_path.strip().replace("\\", "/")
                while normalized.startswith("./"):
                    normalized = normalized[2:]
                normalized_lower = normalized.lower()
                if normalized_lower in seen:
                    other = seen[normalized_lower]
                    return (
                        f"任务 {other + 1} 和任务 {i + 1} 都涉及文件 "
                        f"'{normalized}'，不能并行执行。"
                        "请将涉及同一文件的操作合并到一个子代理中。"
                    )
                seen[normalized_lower] = i
        return None

    @staticmethod
    def _should_fast_exit_explorer(*, task: str, file_paths: list[str]) -> bool:
        """判断 explorer 是否可直接快速退出（无需强制探索）。

        三层检测：
        1. 纯对话模式（问候/确认/感谢）→ 无条件退出
        2. 短任务 + 无数据线索 → 退出（不要求必须有 ? 或 skip keywords）
        3. 原有 skip keywords / 问号检测 → 退出
        """
        if file_paths:
            return False

        task_text = task.strip()
        if not task_text:
            return False

        lowered = task_text.lower()

        # Layer 1: 纯对话 — 任务文本完全是问候/确认/感谢，无论长度都退出
        if lowered.rstrip("。！？.!? ") in _TRIVIAL_PATTERNS:
            return True

        # 有明确数据线索时不退出
        if _EXCEL_FILE_PATTERN.search(task_text) or _CELL_REF_PATTERN.search(task_text):
            return False
        if any(cue in lowered for cue in _EXPLORER_REQUIRED_CUES):
            return False

        # Layer 2: 短任务 + 无数据线索 → 直接退出
        if len(task_text) <= 60:
            return True

        if len(task_text) > 120:
            return False

        # Layer 3: 原有逻辑——问号或 skip keywords
        return (
            "?" in task_text
            or "？" in task_text
            or any(kw in lowered for kw in _EXPLORER_SKIP_KEYWORDS)
        )
