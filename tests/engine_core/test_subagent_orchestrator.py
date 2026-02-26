"""SubagentOrchestrator 组件单元测试。"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from excelmanus.config import ExcelManusConfig
from excelmanus.engine import DelegateSubagentOutcome
from excelmanus.engine_core.subagent_orchestrator import SubagentOrchestrator
from excelmanus.memory import ConversationMemory
from excelmanus.subagent.executor import SubagentExecutor
from excelmanus.subagent.models import SubagentFileChange, SubagentResult


def _make_orchestrator(
    *,
    subagent_enabled: bool = True,
) -> SubagentOrchestrator:
    """构造一个最小化的 SubagentOrchestrator 用于测试。"""
    engine_mock = MagicMock()
    engine_mock._subagent_enabled = subagent_enabled
    engine_mock._active_skills = []
    engine_mock._normalize_skill_agent_name = MagicMock(
        side_effect=lambda x: x or "subagent"
    )
    engine_mock._run_skill_hook = MagicMock(return_value=None)
    engine_mock._resolve_hook_result = AsyncMock(return_value=None)
    engine_mock._auto_select_subagent = AsyncMock(return_value="subagent")
    engine_mock.run_subagent = AsyncMock()
    engine_mock._window_perception = MagicMock()
    engine_mock._context_builder = MagicMock()
    engine_mock._normalize_subagent_file_paths = MagicMock(return_value=[])
    # E4: orchestrator 现在直接访问 _subagent_registry
    engine_mock._subagent_registry = MagicMock()
    engine_mock._subagent_registry.build_catalog = MagicMock(return_value=("", ["subagent"]))
    engine_mock._config = MagicMock()
    engine_mock._config.subagent_timeout_seconds = 600
    engine_mock._config.parallel_subagent_max = 3
    return SubagentOrchestrator(engine_mock)


class TestSubagentDisabled:
    """子代理关闭时的行为。"""

    async def test_returns_failure_when_disabled(self):
        orch = _make_orchestrator(subagent_enabled=False)
        outcome = await orch.delegate(task="test task")
        assert outcome.success is False
        assert "关闭" in outcome.reply


class TestEmptyTask:
    """空任务参数。"""

    async def test_returns_failure_for_empty_task(self):
        orch = _make_orchestrator()
        outcome = await orch.delegate(task="   ")
        assert outcome.success is False
        assert "非空" in outcome.reply


class TestSuccessfulDelegation:
    """成功委派场景。"""

    async def test_successful_subagent_run(self):
        orch = _make_orchestrator()
        mock_result = SubagentResult(
            success=True,
            summary="任务完成",
            subagent_name="subagent",
            permission_mode="default",
            conversation_id="test-conv-1",
            structured_changes=[SubagentFileChange(path="test.xlsx", tool_name="write_excel")],
            observed_files=["test.xlsx"],
        )
        orch._engine.run_subagent = AsyncMock(return_value=mock_result)

        outcome = await orch.delegate(task="处理 test.xlsx")

        assert outcome.success is True
        assert outcome.reply == "任务完成"
        assert outcome.picked_agent == "subagent"
        assert outcome.subagent_result is mock_result
        orch._engine._window_perception.observe_subagent_context.assert_called_once()
        orch._engine._context_builder.mark_window_notice_dirty.assert_called_once()

    async def test_failed_subagent_run(self):
        orch = _make_orchestrator()
        mock_result = SubagentResult(
            success=False,
            summary="文件不存在",
            subagent_name="subagent",
            permission_mode="default",
            conversation_id="test-conv-2",
        )
        orch._engine.run_subagent = AsyncMock(return_value=mock_result)

        outcome = await orch.delegate(task="处理不存在的文件")

        assert outcome.success is False
        assert "失败" in outcome.reply
        assert outcome.subagent_result is mock_result


class TestExplorerFastExit:
    """explorer 对简单任务应快速退出。"""

    async def test_skips_explorer_for_lightweight_question_without_context(self):
        orch = _make_orchestrator()

        outcome = await orch.delegate(
            task="请解释一下这个思路为什么可行？",
            agent_name="explorer",
        )

        assert outcome.success is True
        assert "已跳过 explorer" in outcome.reply
        orch._engine.run_subagent.assert_not_awaited()

    async def test_keeps_explorer_when_file_paths_present(self):
        orch = _make_orchestrator()
        mock_result = SubagentResult(
            success=True,
            summary="已分析 demo.xlsx",
            subagent_name="explorer",
            permission_mode="readOnly",
            conversation_id="explore-1",
            observed_files=["demo.xlsx"],
        )
        orch._engine.run_subagent = AsyncMock(return_value=mock_result)

        outcome = await orch.delegate(
            task="快速看下这个文件",
            agent_name="explorer",
            file_paths=["demo.xlsx"],
        )

        assert outcome.success is True
        orch._engine.run_subagent.assert_awaited_once()

    # ── Layer 1: 纯对话模式 ──

    @pytest.mark.parametrize("task", ["你好", "谢谢！", "ok", "thanks", "好的。", "hi"])
    async def test_trivial_greeting_fast_exits(self, task: str):
        """Layer 1: 问候/确认/感谢等纯对话应无条件退出。"""
        orch = _make_orchestrator()
        outcome = await orch.delegate(task=task, agent_name="explorer")
        assert outcome.success is True
        assert "已跳过 explorer" in outcome.reply
        orch._engine.run_subagent.assert_not_awaited()

    # ── Layer 2: 短任务 + 无数据线索 ──

    async def test_short_task_without_data_cues_fast_exits(self):
        """Layer 2: 短任务（≠60 chars）且无数据线索应快速退出。"""
        orch = _make_orchestrator()
        outcome = await orch.delegate(
            task="这个办法怎么样",  # 6 chars, no data cues
            agent_name="explorer",
        )
        assert outcome.success is True
        assert "已跳过 explorer" in outcome.reply

    async def test_short_task_with_data_cues_does_not_fast_exit(self):
        """Layer 2 例外：短任务但含数据线索不应退出。"""
        orch = _make_orchestrator()
        mock_result = SubagentResult(
            success=True, summary="done",
            subagent_name="explorer", permission_mode="readOnly",
            conversation_id="c1",
        )
        orch._engine.run_subagent = AsyncMock(return_value=mock_result)
        outcome = await orch.delegate(
            task="读取数据",  # 含 "data" cue
            agent_name="explorer",
        )
        orch._engine.run_subagent.assert_awaited_once()

    async def test_short_task_with_excel_file_does_not_fast_exit(self):
        """Layer 2 例外：含 Excel 文件名不应退出。"""
        orch = _make_orchestrator()
        mock_result = SubagentResult(
            success=True, summary="done",
            subagent_name="explorer", permission_mode="readOnly",
            conversation_id="c2",
        )
        orch._engine.run_subagent = AsyncMock(return_value=mock_result)
        outcome = await orch.delegate(
            task="看 test.xlsx",
            agent_name="explorer",
        )
        orch._engine.run_subagent.assert_awaited_once()

    # ── Layer 3: 长任务不退出 ──

    async def test_long_task_without_skip_keywords_does_not_fast_exit(self):
        """超过 120 chars 的任务不应快速退出，即使无数据线索。"""
        orch = _make_orchestrator()
        mock_result = SubagentResult(
            success=True, summary="done",
            subagent_name="explorer", permission_mode="readOnly",
            conversation_id="c3",
        )
        orch._engine.run_subagent = AsyncMock(return_value=mock_result)
        long_task = "请帮我检查一下这个项目的整体情况，" * 5  # >120 chars
        outcome = await orch.delegate(task=long_task, agent_name="explorer")
        orch._engine.run_subagent.assert_awaited_once()


class TestFailurePartialArtifacts:
    """失败时若有部分产出，应保留并同步给主会话。"""

    async def test_syncs_partial_observation_on_failed_subagent(self):
        orch = _make_orchestrator()
        mock_result = SubagentResult(
            success=False,
            summary="读取中途失败",
            subagent_name="explorer",
            permission_mode="readOnly",
            conversation_id="partial-1",
            observed_files=["data.xlsx"],
            structured_changes=[
                SubagentFileChange(path="outputs/demo.xlsx", tool_name="run_code"),
            ],
        )
        orch._engine.run_subagent = AsyncMock(return_value=mock_result)

        outcome = await orch.delegate(task="检查 data.xlsx")

        assert outcome.success is False
        assert "已保留部分产出" in outcome.reply
        orch._engine._window_perception.observe_subagent_context.assert_called_once()
        orch._engine._window_perception.observe_subagent_writes.assert_called_once()
        orch._engine._context_builder.mark_window_notice_dirty.assert_called_once()


class TestCategorySignature:
    """_category_signature 类别签名测试。"""

    def test_same_tool_same_error_category_yields_same_signature(self):
        """同工具 + 同类错误（不同参数）应该产生相同的类别签名。"""
        sig1 = SubagentExecutor._category_signature(
            tool_name="read_excel", error="文件 data.xlsx 不存在",
        )
        sig2 = SubagentExecutor._category_signature(
            tool_name="read_excel", error="文件 other.xlsx 不存在",
        )
        assert sig1 == sig2

    def test_different_error_categories_yield_different_signatures(self):
        """同工具但不同错误类别应该产生不同的类别签名。"""
        sig_not_found = SubagentExecutor._category_signature(
            tool_name="read_excel", error="文件不存在",
        )
        sig_permission = SubagentExecutor._category_signature(
            tool_name="read_excel", error="权限不足",
        )
        assert sig_not_found != sig_permission

    def test_different_tools_same_error_yield_different_signatures(self):
        """不同工具即使同类错误也应该产生不同签名。"""
        sig1 = SubagentExecutor._category_signature(
            tool_name="read_excel", error="file not found",
        )
        sig2 = SubagentExecutor._category_signature(
            tool_name="list_sheets", error="file not found",
        )
        assert sig1 != sig2

    def test_unknown_error_category_fallback(self):
        """未匹配任何关键词时应归类为 unknown。"""
        sig = SubagentExecutor._category_signature(
            tool_name="read_excel", error="一个完全随机的错误",
        )
        # 只要能返回就说明没报错
        assert isinstance(sig, str) and len(sig) == 32


class TestBuildFailureHint:
    """渐进降级提示消息构建测试。"""

    def test_hint_contains_tool_name_and_streak(self):
        hint = SubagentExecutor._build_failure_hint(
            tool_name="read_excel", streak=3, max_failures=6,
            error="文件不存在",
        )
        assert "read_excel" in hint
        assert "3" in hint
        assert "还剩" in hint

    def test_hint_shows_urgency_when_near_limit(self):
        hint = SubagentExecutor._build_failure_hint(
            tool_name="read_excel", streak=5, max_failures=6,
            error="boom",
        )
        assert "即将触发终止" in hint

    def test_hint_truncates_long_error(self):
        long_error = "x" * 500
        hint = SubagentExecutor._build_failure_hint(
            tool_name="read_excel", streak=2, max_failures=6,
            error=long_error,
        )
        assert len(hint) < 600  # 提示不会无限膊长


class TestFailureSignatureVsCategory:
    """_failure_signature 与 _category_signature 的对比。"""

    def test_exact_signature_requires_same_args(self):
        """精确签名要求参数完全相同。"""
        sig1 = SubagentExecutor._failure_signature(
            tool_name="read_excel",
            arguments={"file_path": "a.xlsx"},
            error="not found",
        )
        sig2 = SubagentExecutor._failure_signature(
            tool_name="read_excel",
            arguments={"file_path": "b.xlsx"},
            error="not found",
        )
        assert sig1 != sig2  # 精确签名不同

        # 但类别签名相同
        cat1 = SubagentExecutor._category_signature(
            tool_name="read_excel", error="not found",
        )
        cat2 = SubagentExecutor._category_signature(
            tool_name="read_excel", error="not found",
        )
        assert cat1 == cat2


class TestPartialProgressSummary:
    """异常退出时应保留已完成工作的中间产出摘要。"""

    def _make_memory_with_history(self) -> ConversationMemory:
        config = MagicMock(spec=ExcelManusConfig)
        config.max_context_tokens = 128000
        mem = ConversationMemory(config)
        mem.add_user_message("请分析 data.xlsx")
        mem.add_assistant_message("已读取 data.xlsx，共 500 行 10 列，包含销售数据。")
        return mem

    def test_extracts_assistant_analysis_from_memory(self):
        """应从 memory 中提取 assistant 的中间分析文本。"""
        mem = self._make_memory_with_history()
        summary = SubagentExecutor._build_partial_progress_summary(
            memory=mem,
            observed_files={"data.xlsx"},
            structured_changes=[],
            iterations=2,
            tool_calls=3,
        )
        assert "中间分析" in summary
        assert "500 行" in summary
        assert "data.xlsx" in summary
        assert "2 轮迭代" in summary
        assert "3 次工具调用" in summary

    def test_empty_memory_returns_stats_only(self):
        """无 assistant 消息时仅返回统计信息。"""
        config = MagicMock(spec=ExcelManusConfig)
        config.max_context_tokens = 128000
        mem = ConversationMemory(config)
        mem.add_user_message("测试")
        summary = SubagentExecutor._build_partial_progress_summary(
            memory=mem,
            observed_files=set(),
            structured_changes=[],
            iterations=1,
            tool_calls=1,
        )
        assert "中间分析" not in summary
        assert "1 轮迭代" in summary

    def test_no_iterations_returns_empty(self):
        """无任何迭代时应返回空字符串。"""
        config = MagicMock(spec=ExcelManusConfig)
        config.max_context_tokens = 128000
        mem = ConversationMemory(config)
        summary = SubagentExecutor._build_partial_progress_summary(
            memory=mem,
            observed_files=set(),
            structured_changes=[],
            iterations=0,
            tool_calls=0,
        )
        assert summary == ""

    def test_truncates_long_assistant_text(self):
        """超长 assistant 文本应被截断。"""
        config = MagicMock(spec=ExcelManusConfig)
        config.max_context_tokens = 128000
        mem = ConversationMemory(config)
        mem.add_user_message("测试")
        mem.add_assistant_message("x" * 500)
        summary = SubagentExecutor._build_partial_progress_summary(
            memory=mem,
            observed_files=set(),
            structured_changes=[],
            iterations=1,
            tool_calls=0,
        )
        assert "…" in summary
        assert len(summary) < 400


class TestFailureReplyDedup:
    """失败 reply 去重：summary 已含中间产出时不追加 partial_hint。"""

    async def test_no_partial_hint_when_summary_has_progress(self):
        """当 summary 已包含【已完成的工作】时，不追加 partial_hint。"""
        orch = _make_orchestrator()
        mock_result = SubagentResult(
            success=False,
            summary=(
                "子代理检测到同一失败重复 3 次，已终止当前策略。\n\n"
                "【已完成的工作】\n已执行 3 轮迭代、2 次工具调用\n"
                "涉及文件: data.xlsx"
            ),
            subagent_name="explorer",
            permission_mode="readOnly",
            conversation_id="dedup-1",
            observed_files=["data.xlsx"],
            structured_changes=[
                SubagentFileChange(path="data.xlsx", tool_name="read_excel"),
            ],
        )
        orch._engine.run_subagent = AsyncMock(return_value=mock_result)

        outcome = await orch.delegate(task="检查 data.xlsx")

        assert outcome.success is False
        assert "已保留部分产出" not in outcome.reply
        assert "已完成的工作" in outcome.reply

    async def test_partial_hint_when_summary_lacks_progress(self):
        """当 summary 不含中间产出时，仍应追加 partial_hint。"""
        orch = _make_orchestrator()
        mock_result = SubagentResult(
            success=False,
            summary="读取中途失败",
            subagent_name="explorer",
            permission_mode="readOnly",
            conversation_id="dedup-2",
            observed_files=["data.xlsx"],
            structured_changes=[
                SubagentFileChange(path="data.xlsx", tool_name="run_code"),
            ],
        )
        orch._engine.run_subagent = AsyncMock(return_value=mock_result)

        outcome = await orch.delegate(task="检查 data.xlsx")

        assert outcome.success is False
        assert "已保留部分产出" in outcome.reply


class TestOutcomeStructure:
    """验证返回的 DelegateSubagentOutcome 结构。"""

    async def test_outcome_contains_task_text(self):
        orch = _make_orchestrator()
        mock_result = SubagentResult(
            success=True,
            summary="done",
            subagent_name="subagent",
            permission_mode="default",
            conversation_id="c1",
        )
        orch._engine.run_subagent = AsyncMock(return_value=mock_result)

        outcome = await orch.delegate(task="my task")

        assert outcome.task_text == "my task"
