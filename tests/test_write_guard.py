"""写入完成门禁（finish_task）单元测试。"""

from __future__ import annotations

import types
from dataclasses import dataclass, field
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from excelmanus.config import ExcelManusConfig
from excelmanus.engine import AgentEngine, ChatResult, ToolCallResult, _WRITE_TOOL_NAMES
from excelmanus.skillpacks.models import SkillMatchResult
from excelmanus.tools.policy import MUTATING_ALL_TOOLS
from excelmanus.tools.registry import ToolRegistry


# ── helpers ──────────────────────────────────────────────────

def _make_config(**overrides) -> ExcelManusConfig:
    defaults = {
        "api_key": "test-key",
        "base_url": "https://test.example.com/v1",
        "model": "test-model",
        "max_iterations": 20,
        "max_consecutive_failures": 3,
        "workspace_root": ".",
        "backup_enabled": False,
    }
    defaults.update(overrides)
    return ExcelManusConfig(**defaults)


def _make_engine(**overrides) -> AgentEngine:
    """构建最小化 AgentEngine 实例用于单元测试。"""
    cfg = _make_config(**{k: v for k, v in overrides.items() if k in ExcelManusConfig.__dataclass_fields__})
    registry = ToolRegistry()
    engine = AgentEngine(config=cfg, registry=registry)
    return engine


def _make_route_result(write_hint: str = "unknown", **kwargs) -> SkillMatchResult:
    defaults = dict(
        skills_used=[],
        route_mode="all_tools",
        system_contexts=[],
    )
    defaults.update(kwargs)
    defaults["write_hint"] = write_hint
    return SkillMatchResult(**defaults)


# ── SkillMatchResult.write_hint 字段测试 ──

class TestWriteHintField:
    def test_default_is_unknown(self):
        result = SkillMatchResult(
            skills_used=[], route_mode="test",
        )
        assert result.write_hint == "unknown"

    def test_explicit_may_write(self):
        result = SkillMatchResult(
            skills_used=[], route_mode="test",
            write_hint="may_write",
        )
        assert result.write_hint == "may_write"

    def test_explicit_read_only(self):
        result = SkillMatchResult(
            skills_used=[], route_mode="test",
            write_hint="read_only",
        )
        assert result.write_hint == "read_only"


# ── ChatResult.write_guard_triggered 字段测试 ──

class TestChatResultWriteGuard:
    def test_default_is_false(self):
        result = ChatResult(reply="ok")
        assert result.write_guard_triggered is False

    def test_explicit_true(self):
        result = ChatResult(reply="ok", write_guard_triggered=True)
        assert result.write_guard_triggered is True


class TestDelegateSubagentWritePropagation:
    """delegate_to_subagent 成功且 subagent 有 file_changes 时应传播写入状态。

    修复回归测试：conversation_20260220T104533 中 subagent 成功写入但
    主 agent 的 write_guard 不认可，导致 finish_task 被拒、任务被重复执行 3 次。
    """

    @staticmethod
    def _delegate_tc(task: str = "test", agent: str = "writer") -> types.SimpleNamespace:
        return types.SimpleNamespace(
            id="call_delegate",
            function=types.SimpleNamespace(
                name="delegate_to_subagent",
                arguments=f'{{"task":"{task}","agent_name":"{agent}"}}',
            ),
        )

    @staticmethod
    def _make_outcome(*, success: bool, file_changes: list[str]) -> "DelegateSubagentOutcome":
        from excelmanus.engine import DelegateSubagentOutcome
        from excelmanus.subagent.models import SubagentResult

        sub = SubagentResult(
            success=success,
            summary="test summary",
            subagent_name="writer",
            permission_mode="default",
            conversation_id="conv_test",
            file_changes=file_changes,
        )
        return DelegateSubagentOutcome(
            reply="test reply",
            success=success,
            picked_agent="writer",
            task_text="test task",
            subagent_result=sub,
        )

    @pytest.mark.asyncio
    async def test_subagent_with_file_changes_propagates_write_state(self):
        """subagent 成功返回且有 file_changes → has_write_tool_call=True。"""
        engine = _make_engine()
        engine._current_write_hint = "may_write"
        engine._has_write_tool_call = False

        outcome = self._make_outcome(success=True, file_changes=["outputs/backups/test.xlsx"])
        with patch.object(engine, "_delegate_to_subagent", return_value=outcome):
            result = await engine._execute_tool_call(
                self._delegate_tc(), tool_scope=None, on_event=None, iteration=1,
            )

        assert result.success is True
        assert engine._has_write_tool_call is True
        assert engine._current_write_hint == "may_write"

    @pytest.mark.asyncio
    async def test_subagent_with_file_changes_then_finish_task_accepted(self):
        """subagent 写入传播后，finish_task 应被接受。"""
        engine = _make_engine()
        engine._current_write_hint = "may_write"
        engine._has_write_tool_call = False

        outcome = self._make_outcome(success=True, file_changes=["outputs/backups/test.xlsx"])
        with patch.object(engine, "_delegate_to_subagent", return_value=outcome):
            await engine._execute_tool_call(
                self._delegate_tc(), tool_scope=None, on_event=None, iteration=1,
            )

        ft = types.SimpleNamespace(
            id="call_finish",
            function=types.SimpleNamespace(
                name="finish_task",
                arguments='{"summary":"done via subagent"}',
            ),
        )
        finish_result = await engine._execute_tool_call(
            ft, tool_scope=None, on_event=None, iteration=2,
        )
        assert finish_result.finish_accepted is True
        assert "✓ 任务完成。" in finish_result.result

    @pytest.mark.asyncio
    async def test_subagent_without_file_changes_does_not_propagate(self):
        """subagent 成功但无 file_changes → has_write_tool_call 不变。"""
        engine = _make_engine()
        engine._current_write_hint = "may_write"
        engine._has_write_tool_call = False

        outcome = self._make_outcome(success=True, file_changes=[])
        with patch.object(engine, "_delegate_to_subagent", return_value=outcome):
            await engine._execute_tool_call(
                self._delegate_tc(agent="analyst"), tool_scope=None, on_event=None, iteration=1,
            )

        assert engine._has_write_tool_call is False

    @pytest.mark.asyncio
    async def test_failed_subagent_with_file_changes_does_not_propagate(self):
        """subagent 失败 → 即使有 file_changes 也不传播写入。"""
        engine = _make_engine()
        engine._current_write_hint = "may_write"
        engine._has_write_tool_call = False

        outcome = self._make_outcome(success=False, file_changes=["outputs/backups/partial.xlsx"])
        with patch.object(engine, "_delegate_to_subagent", return_value=outcome):
            await engine._execute_tool_call(
                self._delegate_tc(), tool_scope=None, on_event=None, iteration=1,
            )

        assert engine._has_write_tool_call is False

    @pytest.mark.asyncio
    async def test_write_hint_upgraded_when_not_may_write(self):
        """write_hint 非 may_write 时，subagent 写入应升级 hint。"""
        engine = _make_engine()
        engine._current_write_hint = "read_only"
        engine._has_write_tool_call = False

        outcome = self._make_outcome(success=True, file_changes=["outputs/test.xlsx"])
        with patch.object(engine, "_delegate_to_subagent", return_value=outcome):
            await engine._execute_tool_call(
                self._delegate_tc(), tool_scope=None, on_event=None, iteration=1,
            )

        assert engine._has_write_tool_call is True
        assert engine._current_write_hint == "may_write"


class TestWriteToolNamesSourceOfTruth:
    def test_write_tool_names_match_policy_mutating_all_tools(self):
        assert _WRITE_TOOL_NAMES == MUTATING_ALL_TOOLS


# ── _build_meta_tools finish_task 注入测试 ──

class TestFinishTaskInjection:
    def test_current_write_hint_initialized_as_unknown(self):
        engine = _make_engine()
        assert engine._current_write_hint == "unknown"

    def test_no_finish_task_when_unknown(self):
        engine = _make_engine()
        engine._current_write_hint = "unknown"
        engine._skill_router = None
        tools = engine._build_meta_tools()
        names = [t["function"]["name"] for t in tools]
        assert "finish_task" not in names

    def test_no_finish_task_when_read_only(self):
        engine = _make_engine()
        engine._current_write_hint = "read_only"
        engine._skill_router = None
        tools = engine._build_meta_tools()
        names = [t["function"]["name"] for t in tools]
        assert "finish_task" not in names

    def test_finish_task_injected_when_may_write(self):
        engine = _make_engine()
        engine._current_write_hint = "may_write"
        engine._skill_router = None
        tools = engine._build_meta_tools()
        names = [t["function"]["name"] for t in tools]
        assert "finish_task" in names

    def test_finish_task_schema(self):
        engine = _make_engine()
        engine._current_write_hint = "may_write"
        engine._skill_router = None
        tools = engine._build_meta_tools()
        ft = [t for t in tools if t["function"]["name"] == "finish_task"][0]
        params = ft["function"]["parameters"]
        assert "summary" in params["properties"]
        assert params["required"] == ["summary"]

    def test_finish_task_reaches_model_tools_when_may_write(self):
        engine = _make_engine()
        engine._current_write_hint = "may_write"
        tools = engine._build_v5_tools()
        names = [t["function"]["name"] for t in tools]
        assert "finish_task" in names

    def test_route_write_hint_blank_falls_back_to_current_state(self):
        engine = _make_engine()
        engine._current_write_hint = "may_write"
        tools = engine._build_v5_tools()
        names = [t["function"]["name"] for t in tools]
        assert "finish_task" in names


class TestWriteGuardPrompt:
    @pytest.mark.asyncio
    async def test_write_guard_prompt_requires_activate_skill_then_execute(self):
        engine = _make_engine(max_iterations=2)
        route_result = _make_route_result(write_hint="may_write")
        engine._route_skills = AsyncMock(return_value=route_result)
        engine._client.chat.completions.create = AsyncMock(
            side_effect=[
                types.SimpleNamespace(
                    choices=[types.SimpleNamespace(message=types.SimpleNamespace(content="先确认一下", tool_calls=None))]
                ),
                types.SimpleNamespace(
                    choices=[types.SimpleNamespace(message=types.SimpleNamespace(content="仍然不执行", tool_calls=None))]
                ),
            ]
        )

        _ = await engine.chat("先画图再美化")

        user_messages = [
            str(m.get("content", ""))
            for m in engine.memory.get_messages()
            if m.get("role") == "user"
        ]
        assert any("请立即调用" in msg for msg in user_messages)


class TestExecutionGuardState:
    @pytest.mark.asyncio
    async def test_execution_guard_should_not_repeat_across_loop_resumes(self):
        engine = _make_engine(max_iterations=3)
        route_result = _make_route_result(write_hint="unknown")

        formula_text = "请用公式 =SUM(A1:A2)"
        engine._client.chat.completions.create = AsyncMock(
            side_effect=[
                types.SimpleNamespace(
                    choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=formula_text, tool_calls=None))]
                ),
                types.SimpleNamespace(
                    choices=[types.SimpleNamespace(message=types.SimpleNamespace(content="loop-1-end", tool_calls=None))]
                ),
                types.SimpleNamespace(
                    choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=formula_text, tool_calls=None))]
                ),
                types.SimpleNamespace(
                    choices=[types.SimpleNamespace(message=types.SimpleNamespace(content="loop-2-end", tool_calls=None))]
                ),
            ]
        )

        _ = await engine._tool_calling_loop(route_result, on_event=None)
        second = await engine._tool_calling_loop(route_result, on_event=None)

        guard_msg = "⚠️ 你刚才在文本中给出了公式或代码建议，但没有实际写入文件。"
        user_messages = [
            str(m.get("content", ""))
            for m in engine.memory.get_messages()
            if m.get("role") == "user"
        ]
        guard_count = sum(guard_msg in msg for msg in user_messages)
        assert guard_count == 1
        assert second.reply == formula_text

    @pytest.mark.asyncio
    async def test_execution_guard_resets_for_new_chat_tasks(self):
        engine = _make_engine(max_iterations=3)
        route_result = _make_route_result(write_hint="unknown")
        engine._route_skills = AsyncMock(return_value=route_result)

        formula_text = "请用公式 =SUM(A1:A2)"
        engine._client.chat.completions.create = AsyncMock(
            side_effect=[
                types.SimpleNamespace(
                    choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=formula_text, tool_calls=None))]
                ),
                types.SimpleNamespace(
                    choices=[types.SimpleNamespace(message=types.SimpleNamespace(content="task-1-end", tool_calls=None))]
                ),
                types.SimpleNamespace(
                    choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=formula_text, tool_calls=None))]
                ),
                types.SimpleNamespace(
                    choices=[types.SimpleNamespace(message=types.SimpleNamespace(content="task-2-end", tool_calls=None))]
                ),
            ]
        )

        _ = await engine.chat("任务一")
        _ = await engine.chat("任务二")

        guard_msg = "⚠️ 你刚才在文本中给出了公式或代码建议，但没有实际写入文件。"
        user_messages = [
            str(m.get("content", ""))
            for m in engine.memory.get_messages()
            if m.get("role") == "user"
        ]
        guard_count = sum(guard_msg in msg for msg in user_messages)
        assert guard_count == 2


class TestWriteHintSyncOnWriteCall:
    @pytest.mark.asyncio
    async def test_write_hint_upgrades_after_successful_write_tool_call(self):
        engine = _make_engine(max_iterations=2)
        route_result = _make_route_result(write_hint="read_only")
        engine._current_write_hint = "read_only"

        first = types.SimpleNamespace(
            choices=[
                types.SimpleNamespace(
                    message=types.SimpleNamespace(
                        content="",
                        tool_calls=[
                            {
                                "id": "call_write_1",
                                "function": {
                                    "name": "write_excel",
                                    "arguments": '{"file_path":"demo.xlsx","sheet_name":"Sheet1","rows":[]}',
                                },
                            }
                        ],
                    )
                )
            ]
        )
        second = types.SimpleNamespace(
            choices=[
                types.SimpleNamespace(
                    message=types.SimpleNamespace(content="done", tool_calls=None)
                )
            ]
        )
        engine._client.chat.completions.create = AsyncMock(side_effect=[first, second])
        engine._execute_tool_call = AsyncMock(
            return_value=ToolCallResult(
                tool_name="write_excel",
                arguments={"file_path": "demo.xlsx"},
                result="ok",
                success=True,
            )
        )

        result = await engine._tool_calling_loop(route_result, on_event=None)

        assert result.reply == "done"
        assert engine._current_write_hint == "may_write"


class TestFinishTaskAcceptance:
    @pytest.mark.asyncio
    async def test_finish_task_warning_should_not_exit_when_result_contains_checkmark(self):
        engine = _make_engine(max_iterations=2)
        route_result = _make_route_result(
            write_hint="unknown",
            tool_scope=["finish_task"],
        )

        first = types.SimpleNamespace(
            choices=[
                types.SimpleNamespace(
                    message=types.SimpleNamespace(
                        content="",
                        tool_calls=[
                            {
                                "id": "call_1",
                                "function": {
                                    "name": "finish_task",
                                    "arguments": '{"summary":"首轮只是解释原因 ✓"}',
                                },
                            }
                        ],
                    )
                )
            ]
        )
        second = types.SimpleNamespace(
            choices=[
                types.SimpleNamespace(
                    message=types.SimpleNamespace(content="继续执行", tool_calls=None)
                )
            ]
        )
        engine._client.chat.completions.create = AsyncMock(side_effect=[first, second])

        with patch.object(
            engine,
            "_enrich_tool_result_with_window_perception",
            side_effect=lambda **kwargs: f"{kwargs['result_text']}\n外部附加符号 ✓",
        ):
            result = await engine._tool_calling_loop(route_result, on_event=None)

        assert result.reply == "继续执行"

    @staticmethod
    def _finish_task_call(summary: str = "done") -> types.SimpleNamespace:
        return types.SimpleNamespace(
            id="call_finish",
            function=types.SimpleNamespace(
                name="finish_task",
                arguments=f'{{"summary":"{summary}"}}',
            ),
        )

    @pytest.mark.asyncio
    async def test_finish_accepted_flag_tracks_real_acceptance(self):
        engine = _make_engine()

        first = await engine._execute_tool_call(
            self._finish_task_call("first"),
            tool_scope=["finish_task"],
            on_event=None,
            iteration=1,
        )
        assert first.success is True
        assert first.finish_accepted is False
        assert first.result.startswith("⚠️ 未检测到写入类工具")

        second = await engine._execute_tool_call(
            self._finish_task_call("second"),
            tool_scope=["finish_task"],
            on_event=None,
            iteration=2,
        )
        assert second.success is True
        assert second.finish_accepted is True
        assert second.result.startswith("✓ 任务完成（无写入）。")

        engine._has_write_tool_call = True
        with_write = await engine._execute_tool_call(
            self._finish_task_call("third"),
            tool_scope=["finish_task"],
            on_event=None,
            iteration=3,
        )
        assert with_write.success is True
        assert with_write.finish_accepted is True
        assert with_write.result.startswith("✓ 任务完成。")

    @pytest.mark.asyncio
    async def test_finish_task_blocked_when_may_write_without_actual_write(self):
        """write_hint=may_write 时，任意次调用 finish_task 均应被强拒，不邀请重试。"""
        engine = _make_engine()
        engine._current_write_hint = "may_write"

        # 第一次调用：立即强拒，不设 _finish_task_warned，不邀请重试
        first = await engine._execute_tool_call(
            self._finish_task_call("first"),
            tool_scope=["finish_task"],
            on_event=None,
            iteration=1,
        )
        assert first.success is True
        assert first.finish_accepted is False
        assert "write_hint=may_write" in first.result
        assert "不接受无写入的完成声明" in first.result
        assert not getattr(engine, "_finish_task_warned", False)

        # 第二次调用：走同一路径，继续强拒（不因 warned 而放行）
        second = await engine._execute_tool_call(
            self._finish_task_call("second"),
            tool_scope=["finish_task"],
            on_event=None,
            iteration=2,
        )
        assert second.success is True
        assert second.finish_accepted is False
        assert "write_hint=may_write" in second.result

    @pytest.mark.asyncio
    async def test_finish_task_accepted_when_may_write_with_actual_write(self):
        """write_hint=may_write 且有实际写入时，finish_task 应被接受。"""
        engine = _make_engine()
        engine._current_write_hint = "may_write"
        engine._has_write_tool_call = True

        result = await engine._execute_tool_call(
            self._finish_task_call("done"),
            tool_scope=["finish_task"],
            on_event=None,
            iteration=1,
        )
        assert result.success is True
        assert result.finish_accepted is True
        assert result.result.startswith("✓ 任务完成。")

    @pytest.mark.asyncio
    async def test_finish_task_double_call_accepted_when_not_may_write(self):
        """write_hint != may_write 时，二次调用 finish_task 应被接受（只读任务）。"""
        engine = _make_engine()
        engine._current_write_hint = "read_only"

        first = await engine._execute_tool_call(
            self._finish_task_call("first"),
            tool_scope=["finish_task"],
            on_event=None,
            iteration=1,
        )
        assert first.finish_accepted is False

        second = await engine._execute_tool_call(
            self._finish_task_call("second"),
            tool_scope=["finish_task"],
            on_event=None,
            iteration=2,
        )
        assert second.finish_accepted is True
        assert second.result.startswith("✓ 任务完成（无写入）。")
