"""SubagentOrchestrator 组件单元测试。"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from excelmanus.config import ExcelManusConfig
from excelmanus.engine import DelegateSubagentOutcome
from excelmanus.engine_core.subagent_orchestrator import SubagentOrchestrator
from excelmanus.memory import ConversationMemory
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
    # orchestrator 通过 _skill_resolver 访问这些方法
    engine_mock._skill_resolver = MagicMock()
    engine_mock._skill_resolver.normalize_skill_agent_name = MagicMock(
        side_effect=lambda x: x or "subagent"
    )
    engine_mock._skill_resolver.run_skill_hook = MagicMock(return_value=None)
    engine_mock._skill_resolver.resolve_hook_result = AsyncMock(return_value=None)
    engine_mock._run_skill_hook = MagicMock(return_value=None)
    engine_mock.run_subagent = AsyncMock()
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


class TestDelegateAgentName:
    """未指定 agent 时使用通用 subagent；显式 explorer 必须真正执行。"""

    async def test_unnamed_delegate_uses_subagent(self):
        orch = _make_orchestrator()
        mock_result = SubagentResult(
            success=True,
            summary="done",
            subagent_name="subagent",
            permission_mode="default",
            conversation_id="c1",
        )
        orch._engine.run_subagent = AsyncMock(return_value=mock_result)

        outcome = await orch.delegate(task="分析这个表格")

        assert outcome.success is True
        assert outcome.picked_agent == "subagent"
        orch._engine.run_subagent.assert_awaited_once()

    async def test_named_explorer_always_runs(self):
        orch = _make_orchestrator()
        mock_result = SubagentResult(
            success=True,
            summary="已分析",
            subagent_name="explorer",
            permission_mode="readOnly",
            conversation_id="explore-1",
        )
        orch._engine.run_subagent = AsyncMock(return_value=mock_result)

        outcome = await orch.delegate(
            task="请解释一下这个思路为什么可行？",
            agent_name="explorer",
        )

        assert outcome.success is True
        assert "已跳过 explorer" not in outcome.reply
        orch._engine.run_subagent.assert_awaited_once()

    async def test_named_explorer_with_files_runs(self):
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
