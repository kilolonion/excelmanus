"""Verifier advisory 模式单元测试。

覆盖：
- verifier pass → 附加"验证通过"
- verifier fail → 附加"验证发现问题"（advisory，不阻塞 finish）
- verifier 异常 → fail-open（不影响 finish）
- verifier 未注册 → 跳过
- subagent 关闭 → 跳过
- finish_task 有写入时触发 verifier + 结果拼接
"""

from __future__ import annotations

import json
from pathlib import Path
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from excelmanus.config import ExcelManusConfig
from excelmanus.engine import AgentEngine, ToolCallResult
from excelmanus.subagent.models import SubagentResult
from excelmanus.tools.registry import ToolRegistry


def _make_config(**overrides) -> ExcelManusConfig:
    defaults = {
        "api_key": "test-key",
        "base_url": "https://test.example.com/v1",
        "model": "test-model",
        "max_iterations": 20,
        "max_consecutive_failures": 3,
        "workspace_root": str(Path(__file__).resolve().parent),
        "backup_enabled": False,
    }
    defaults.update(overrides)
    return ExcelManusConfig(**defaults)


def _make_engine(**overrides) -> AgentEngine:
    cfg = _make_config(**{k: v for k, v in overrides.items() if k in ExcelManusConfig.__dataclass_fields__})
    registry = ToolRegistry()
    return AgentEngine(config=cfg, registry=registry)


def _make_verifier_result(*, verdict: str, **extra) -> SubagentResult:
    """构造 verifier 子代理的模拟结果。"""
    payload: dict = {"verdict": verdict}
    payload.update(extra)
    return SubagentResult(
        success=True,
        summary=json.dumps(payload, ensure_ascii=False),
        subagent_name="verifier",
        permission_mode="readOnly",
        conversation_id="verifier-test",
    )


class TestVerifierAdvisoryPass:
    @pytest.mark.asyncio
    async def test_verifier_pass_returns_checkmark(self):
        engine = _make_engine()
        engine._subagent_enabled = True
        mock_result = _make_verifier_result(
            verdict="pass",
            checks=["文件存在", "数据行数正确"],
        )
        with patch.object(engine, "run_subagent", new_callable=AsyncMock, return_value=mock_result):
            suffix = await engine._run_finish_verifier_advisory(
                report={"operations": "写入数据", "key_findings": "100行"},
                summary="",
            )
        assert suffix is not None
        assert "验证通过" in suffix
        assert "文件存在" in suffix


class TestVerifierAdvisoryFail:
    @pytest.mark.asyncio
    async def test_verifier_fail_returns_warning(self):
        engine = _make_engine()
        engine._subagent_enabled = True
        mock_result = _make_verifier_result(
            verdict="fail",
            issues=["输出文件不存在"],
            checks=["文件存在性检查"],
        )
        with patch.object(engine, "run_subagent", new_callable=AsyncMock, return_value=mock_result):
            suffix = await engine._run_finish_verifier_advisory(
                report={"operations": "写入数据", "key_findings": "100行"},
                summary="",
            )
        assert suffix is not None
        assert "验证发现问题" in suffix
        assert "advisory" in suffix
        assert "输出文件不存在" in suffix


class TestVerifierAdvisoryUnknown:
    @pytest.mark.asyncio
    async def test_verifier_unknown_returns_uncertain(self):
        engine = _make_engine()
        engine._subagent_enabled = True
        mock_result = SubagentResult(
            success=True,
            summary="我不确定任务是否完成",
            subagent_name="verifier",
            permission_mode="readOnly",
            conversation_id="verifier-test",
        )
        with patch.object(engine, "run_subagent", new_callable=AsyncMock, return_value=mock_result):
            suffix = await engine._run_finish_verifier_advisory(
                report=None,
                summary="done",
            )
        assert suffix is not None
        assert "验证结果不确定" in suffix


class TestVerifierAdvisoryFailOpen:
    @pytest.mark.asyncio
    async def test_verifier_exception_returns_none(self):
        """verifier 抛异常时 fail-open。"""
        engine = _make_engine()
        engine._subagent_enabled = True
        with patch.object(
            engine, "run_subagent",
            new_callable=AsyncMock,
            side_effect=RuntimeError("boom"),
        ):
            suffix = await engine._run_finish_verifier_advisory(
                report={"operations": "test", "key_findings": "test"},
                summary="",
            )
        assert suffix is None

    @pytest.mark.asyncio
    async def test_verifier_subagent_failure_returns_none(self):
        """verifier 子代理执行失败时返回 None。"""
        engine = _make_engine()
        engine._subagent_enabled = True
        mock_result = SubagentResult(
            success=False,
            summary="子代理执行失败",
            error="SubagentError",
            subagent_name="verifier",
            permission_mode="readOnly",
            conversation_id="verifier-test",
        )
        with patch.object(engine, "run_subagent", new_callable=AsyncMock, return_value=mock_result):
            suffix = await engine._run_finish_verifier_advisory(
                report={"operations": "test", "key_findings": "test"},
                summary="",
            )
        assert suffix is None


class TestVerifierAdvisorySkip:
    @pytest.mark.asyncio
    async def test_skip_when_subagent_disabled(self):
        engine = _make_engine()
        engine._subagent_enabled = False
        suffix = await engine._run_finish_verifier_advisory(
            report={"operations": "test", "key_findings": "test"},
            summary="",
        )
        assert suffix is None

    @pytest.mark.asyncio
    async def test_skip_when_no_report_and_no_summary(self):
        engine = _make_engine()
        engine._subagent_enabled = True
        suffix = await engine._run_finish_verifier_advisory(
            report=None,
            summary="",
        )
        assert suffix is None


class TestFinishTaskWithVerifierAdvisory:
    """finish_task 有写入时应触发 verifier 并拼接结果。"""

    @staticmethod
    def _finish_task_call(report: dict | None = None, summary: str = "done") -> types.SimpleNamespace:
        args: dict = {}
        if report is not None:
            args["report"] = report
        else:
            args["summary"] = summary
        return types.SimpleNamespace(
            id="call_finish",
            function=types.SimpleNamespace(
                name="finish_task",
                arguments=json.dumps(args, ensure_ascii=False),
            ),
        )

    @pytest.mark.asyncio
    async def test_finish_with_write_triggers_verifier_and_appends_result(self):
        engine = _make_engine()
        engine._has_write_tool_call = True
        engine._current_write_hint = "may_write"

        mock_result = _make_verifier_result(verdict="pass", checks=["数据正确"])
        with patch.object(engine, "run_subagent", new_callable=AsyncMock, return_value=mock_result):
            result = await engine._execute_tool_call(
                self._finish_task_call(
                    report={"operations": "写入数据", "key_findings": "100行"},
                ),
                tool_scope=None,
                on_event=None,
                iteration=1,
            )

        assert result.finish_accepted is True
        assert "任务完成" in result.result
        assert "验证通过" in result.result

    @pytest.mark.asyncio
    async def test_finish_with_write_verifier_fail_still_accepted(self):
        """verifier fail 时 finish 仍应 accepted（advisory 模式）。"""
        engine = _make_engine()
        engine._has_write_tool_call = True
        engine._current_write_hint = "may_write"

        mock_result = _make_verifier_result(verdict="fail", issues=["文件缺失"])
        with patch.object(engine, "run_subagent", new_callable=AsyncMock, return_value=mock_result):
            result = await engine._execute_tool_call(
                self._finish_task_call(
                    report={"operations": "写入数据", "key_findings": "100行"},
                ),
                tool_scope=None,
                on_event=None,
                iteration=1,
            )

        assert result.finish_accepted is True
        assert "验证发现问题" in result.result
        assert "文件缺失" in result.result

    @pytest.mark.asyncio
    async def test_finish_with_write_verifier_exception_still_accepted(self):
        """verifier 异常时 finish 仍应 accepted（fail-open）。"""
        engine = _make_engine()
        engine._has_write_tool_call = True
        engine._current_write_hint = "may_write"

        with patch.object(
            engine, "run_subagent",
            new_callable=AsyncMock,
            side_effect=RuntimeError("verifier boom"),
        ):
            result = await engine._execute_tool_call(
                self._finish_task_call(summary="done"),
                tool_scope=None,
                on_event=None,
                iteration=1,
            )

        assert result.finish_accepted is True
        assert "任务完成" in result.result

    @pytest.mark.asyncio
    async def test_finish_without_write_does_not_trigger_verifier(self):
        """无写入时不触发 verifier。"""
        engine = _make_engine()
        engine._has_write_tool_call = False
        engine._current_write_hint = "may_write"

        run_subagent_mock = AsyncMock()
        with patch.object(engine, "run_subagent", run_subagent_mock):
            result = await engine._execute_tool_call(
                self._finish_task_call(summary="只是查询"),
                tool_scope=None,
                on_event=None,
                iteration=1,
            )

        assert result.finish_accepted is False
        run_subagent_mock.assert_not_awaited()
