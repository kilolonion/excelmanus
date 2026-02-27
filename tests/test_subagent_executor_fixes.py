"""回归测试：子代理工具错误系统性修复 (S1~S5)。"""

from __future__ import annotations

import contextvars
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from excelmanus.approval import ApprovalManager
from excelmanus.config import ExcelManusConfig
from excelmanus.subagent import SubagentConfig, SubagentExecutor
from excelmanus.tools import ToolDef, ToolRegistry


# ── 辅助工厂 ──────────────────────────────────────────────


def _make_config(tmp_path: Path, **overrides) -> ExcelManusConfig:
    defaults = {
        "api_key": "test-key",
        "base_url": "https://test.example.com/v1",
        "model": "test-model",
        "workspace_root": str(tmp_path),
    }
    defaults.update(overrides)
    return ExcelManusConfig(**defaults)


def _make_executor(tmp_path: Path) -> tuple[SubagentExecutor, ToolRegistry]:
    config = _make_config(tmp_path)
    registry = ToolRegistry()

    def read_excel(file_path: str = "data.xlsx") -> str:
        return json.dumps({"status": "ok", "rows": 100})

    def write_excel(file_path: str, content: str = "") -> str:
        return "写入完成"

    registry.register_tools([
        ToolDef(
            name="read_excel",
            description="读取 Excel",
            input_schema={
                "type": "object",
                "properties": {"file_path": {"type": "string"}},
            },
            func=read_excel,
        ),
        ToolDef(
            name="write_excel",
            description="写入 Excel",
            input_schema={
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["file_path"],
            },
            func=write_excel,
        ),
    ])

    approval = ApprovalManager(str(tmp_path))
    executor = SubagentExecutor(
        parent_config=config,
        parent_registry=registry,
        approval_manager=approval,
    )
    return executor, registry


def _tool_call_message(tool_name: str, arguments: dict, call_id: str = "call_1"):
    return SimpleNamespace(
        content=None,
        tool_calls=[
            SimpleNamespace(
                id=call_id,
                function=SimpleNamespace(
                    name=tool_name,
                    arguments=json.dumps(arguments, ensure_ascii=False),
                ),
            )
        ],
    )


def _text_message(content: str):
    return SimpleNamespace(content=content, tool_calls=None)


def _response_from_message(message):
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


# ── S1: workspace_context 注入到 system prompt ──────────────


class TestS1WorkspaceContext:
    """S1: 子代理 system prompt 应包含文件全景和 CoW 映射。"""

    def test_build_system_prompt_includes_workspace_context(self, tmp_path: Path) -> None:
        executor, _ = _make_executor(tmp_path)
        sub_cfg = SubagentConfig(
            name="test", description="test", max_iterations=1,
        )
        workspace_ctx = (
            "## 工作区文件全景\n"
            "- data.xlsx (12KB, 3 sheets)\n"
            "- report.xlsx (5KB, 1 sheet)\n\n"
            "## ⚠️ 文件保护路径映射（CoW）\n"
            "| 原始路径 | 副本路径 |\n"
            "| data.xlsx | outputs/data.xlsx |"
        )
        prompt = executor._build_system_prompt(
            config=sub_cfg,
            parent_context="上下文信息",
            workspace_context=workspace_ctx,
        )
        assert "工作区文件全景" in prompt
        assert "data.xlsx" in prompt
        assert "CoW" in prompt

    def test_build_system_prompt_empty_workspace_context_no_inject(self, tmp_path: Path) -> None:
        executor, _ = _make_executor(tmp_path)
        sub_cfg = SubagentConfig(
            name="test", description="test", max_iterations=1,
        )
        prompt = executor._build_system_prompt(
            config=sub_cfg,
            parent_context="上下文",
            workspace_context="",
        )
        assert "工作区文件全景" not in prompt

    @pytest.mark.asyncio
    async def test_workspace_context_reaches_llm_system_prompt(self, tmp_path: Path) -> None:
        """端到端：workspace_context 经 run() 传递到 LLM 的 system message。"""
        executor, _ = _make_executor(tmp_path)
        sub_cfg = SubagentConfig(
            name="test", description="E2E test",
            max_iterations=1, max_consecutive_failures=1,
        )

        captured_messages: list[Any] = []

        async def _fake_create(**kwargs):
            captured_messages.append(kwargs.get("messages", []))
            return _response_from_message(_text_message("完成"))

        fake_client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=AsyncMock(side_effect=_fake_create))
            )
        )

        workspace_ctx = "## 文件全景\n- test.xlsx"
        with patch("excelmanus.subagent.executor.openai.AsyncOpenAI", return_value=fake_client):
            await executor.run(
                config=sub_cfg,
                prompt="测试",
                workspace_context=workspace_ctx,
            )

        assert len(captured_messages) >= 1
        system_msg = captured_messages[0][0]
        assert system_msg["role"] == "system"
        assert "文件全景" in system_msg["content"]
        assert "test.xlsx" in system_msg["content"]


# ── S2: CoW 路径重定向 ──────────────────────────────────────


class TestS2CowRedirect:
    """S2: 子代理工具调用前应自动重定向 CoW 保护路径。"""

    def test_redirect_cow_paths_basic(self) -> None:
        cow = {"data.xlsx": "outputs/data.xlsx"}
        args = {"file_path": "data.xlsx", "content": "hello"}
        new_args, reminders = SubagentExecutor._redirect_cow_paths(
            tool_name="write_excel",
            arguments=args,
            cow_mappings=cow,
            workspace_root="/workspace",
        )
        assert new_args["file_path"] == "outputs/data.xlsx"
        assert len(reminders) == 1
        assert "受保护" in reminders[0]

    def test_redirect_cow_paths_absolute_path(self) -> None:
        cow = {"data.xlsx": "outputs/data.xlsx"}
        args = {"file_path": "/workspace/data.xlsx"}
        new_args, _ = SubagentExecutor._redirect_cow_paths(
            tool_name="read_excel",
            arguments=args,
            cow_mappings=cow,
            workspace_root="/workspace",
        )
        assert new_args["file_path"] == "/workspace/outputs/data.xlsx"

    def test_redirect_cow_paths_no_match(self) -> None:
        cow = {"data.xlsx": "outputs/data.xlsx"}
        args = {"file_path": "other.xlsx"}
        new_args, reminders = SubagentExecutor._redirect_cow_paths(
            tool_name="read_excel",
            arguments=args,
            cow_mappings=cow,
            workspace_root="/workspace",
        )
        assert new_args["file_path"] == "other.xlsx"
        assert len(reminders) == 0

    def test_redirect_cow_paths_empty_mappings(self) -> None:
        args = {"file_path": "data.xlsx"}
        new_args, reminders = SubagentExecutor._redirect_cow_paths(
            tool_name="read_excel",
            arguments=args,
            cow_mappings=None,
            workspace_root="/workspace",
        )
        assert new_args is args  # 原封不动返回
        assert len(reminders) == 0

    def test_redirect_cow_paths_multiple_fields(self) -> None:
        cow = {"src.xlsx": "outputs/src.xlsx", "dst.xlsx": "outputs/dst.xlsx"}
        args = {"source": "src.xlsx", "destination": "dst.xlsx"}
        new_args, reminders = SubagentExecutor._redirect_cow_paths(
            tool_name="copy_file",
            arguments=args,
            cow_mappings=cow,
            workspace_root="/workspace",
        )
        assert new_args["source"] == "outputs/src.xlsx"
        assert new_args["destination"] == "outputs/dst.xlsx"
        assert len(reminders) == 2


# ── S3: 双重截断修复 ────────────────────────────────────────


class TestS3NoDoubleTruncation:
    """S3: 工具结果不应被二次硬截断。"""

    @pytest.mark.asyncio
    async def test_tool_result_not_double_truncated(self, tmp_path: Path) -> None:
        """工具返回 >4000 字符的结果，restricted 模式下不应被硬截到 4000。

        ToolDef.max_result_chars 设为 8000（大于旧 _SUMMARY_MAX_CHARS=4000），
        验证 memory 中收到的 content 长度 > 4000，证明二次截断已移除。
        """
        executor, _ = _make_executor(tmp_path)

        long_result = "x" * 6000

        def read_excel_long(file_path: str = "data.xlsx") -> str:
            return long_result

        executor._registry.register_tools([
            ToolDef(
                name="read_excel_long",
                description="返回大量数据",
                input_schema={
                    "type": "object",
                    "properties": {"file_path": {"type": "string"}},
                },
                func=read_excel_long,
                max_result_chars=8000,  # 大于旧硬截断 4000
            ),
        ])

        sub_cfg = SubagentConfig(
            name="test", description="截断测试",
            allowed_tools=["read_excel_long"],
            max_iterations=2, max_consecutive_failures=1,
        )

        captured_tool_results: list[str] = []
        original_add = None

        # 拦截 memory.add_tool_result 来检查传入的 content
        from excelmanus.memory import ConversationMemory
        _orig_add = ConversationMemory.add_tool_result

        def _capture_add(self_mem, call_id, content):
            captured_tool_results.append(content)
            return _orig_add(self_mem, call_id, content)

        fake_client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(
                    create=AsyncMock(
                        side_effect=[
                            _response_from_message(
                                _tool_call_message("read_excel_long", {"file_path": "a.xlsx"})
                            ),
                            _response_from_message(_text_message("完成")),
                        ]
                    )
                )
            )
        )

        with (
            patch("excelmanus.subagent.executor.openai.AsyncOpenAI", return_value=fake_client),
            patch.object(ConversationMemory, "add_tool_result", _capture_add),
        ):
            await executor.run(config=sub_cfg, prompt="读取大表")

        # 工具结果不应被硬截到 4000
        assert len(captured_tool_results) >= 1
        assert len(captured_tool_results[0]) > 4000


# ── S4+S5: contextvar 注入 ──────────────────────────────────


class TestS4S5ContextvarInjection:
    """S4+S5: 子代理工具调用时应正确注入 FileAccessGuard + sandbox env contextvars。"""

    def test_set_and_reset_contextvars_guard(self, tmp_path: Path) -> None:
        from excelmanus.security import FileAccessGuard
        from excelmanus.tools._guard_ctx import get_guard

        guard = FileAccessGuard(str(tmp_path))
        tokens = SubagentExecutor._set_contextvars(guard, None)

        assert get_guard() is guard

        SubagentExecutor._reset_contextvars(tokens)
        # 恢复后不应再返回刚才设置的 guard
        restored = get_guard()
        assert restored is not guard or restored is None

    def test_set_and_reset_contextvars_sandbox(self) -> None:
        from excelmanus.tools.code_tools import _current_sandbox_env

        fake_env = SimpleNamespace(workspace_root="/test")
        tokens = SubagentExecutor._set_contextvars(None, fake_env)

        assert _current_sandbox_env.get(None) is fake_env

        SubagentExecutor._reset_contextvars(tokens)
        assert _current_sandbox_env.get(None) is not fake_env

    def test_set_and_reset_contextvars_both(self, tmp_path: Path) -> None:
        from excelmanus.security import FileAccessGuard
        from excelmanus.tools._guard_ctx import get_guard
        from excelmanus.tools.code_tools import _current_sandbox_env

        guard = FileAccessGuard(str(tmp_path))
        fake_env = SimpleNamespace(workspace_root="/test")
        tokens = SubagentExecutor._set_contextvars(guard, fake_env)

        assert get_guard() is guard
        assert _current_sandbox_env.get(None) is fake_env

        SubagentExecutor._reset_contextvars(tokens)

    def test_set_contextvars_none_is_noop(self) -> None:
        tokens = SubagentExecutor._set_contextvars(None, None)
        assert tokens == []
        SubagentExecutor._reset_contextvars(tokens)  # 不应报错

    @pytest.mark.asyncio
    async def test_guard_propagates_to_tool_thread(self, tmp_path: Path) -> None:
        """子代理工具在 asyncio.to_thread 中应能拿到正确的 FileAccessGuard。"""
        from excelmanus.security import FileAccessGuard
        from excelmanus.tools._guard_ctx import get_guard

        guard = FileAccessGuard(str(tmp_path))
        captured_guard: list[Any] = []

        def spy_tool(file_path: str = "test.xlsx") -> str:
            captured_guard.append(get_guard())
            return "ok"

        executor, registry = _make_executor(tmp_path)
        registry.register_tools([
            ToolDef(
                name="spy_tool",
                description="捕获 guard",
                input_schema={
                    "type": "object",
                    "properties": {"file_path": {"type": "string"}},
                },
                func=spy_tool,
            ),
        ])

        sub_cfg = SubagentConfig(
            name="test", description="guard 传播测试",
            allowed_tools=["spy_tool"],
            max_iterations=2, max_consecutive_failures=1,
        )

        fake_client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(
                    create=AsyncMock(
                        side_effect=[
                            _response_from_message(
                                _tool_call_message("spy_tool", {"file_path": "a.xlsx"})
                            ),
                            _response_from_message(_text_message("完成")),
                        ]
                    )
                )
            )
        )

        with patch("excelmanus.subagent.executor.openai.AsyncOpenAI", return_value=fake_client):
            await executor.run(
                config=sub_cfg,
                prompt="测试",
                file_access_guard=guard,
            )

        assert len(captured_guard) == 1
        assert captured_guard[0] is guard
