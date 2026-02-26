"""SubagentOrchestrator 组件单元测试。"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from excelmanus.engine import DelegateSubagentOutcome
from excelmanus.engine_core.subagent_orchestrator import SubagentOrchestrator
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
    engine_mock._normalize_subagent_file_paths = MagicMock(return_value=[])
    # E4: orchestrator 现在直接访问 _subagent_registry
    engine_mock._subagent_registry = MagicMock()
    engine_mock._subagent_registry.build_catalog = MagicMock(return_value=("", ["subagent"]))
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
