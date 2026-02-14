"""SubagentExecutor 单元测试。"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from excelmanus.approval import ApprovalManager
from excelmanus.config import ExcelManusConfig
from excelmanus.subagent import SubagentConfig, SubagentExecutor
from excelmanus.tools import ToolDef, ToolRegistry


def _make_config(tmp_path: Path, **overrides) -> ExcelManusConfig:
    defaults = {
        "api_key": "test-key",
        "base_url": "https://test.example.com/v1",
        "model": "test-model",
        "workspace_root": str(tmp_path),
    }
    defaults.update(overrides)
    return ExcelManusConfig(**defaults)


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


def _registry_with_tools(tmp_path: Path) -> ToolRegistry:
    registry = ToolRegistry()

    def read_excel() -> str:
        return "读取成功"

    def write_text_file(file_path: str, content: str) -> str:
        path = Path(tmp_path) / file_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return f"写入完成: {file_path}"

    registry.register_tools(
        [
            ToolDef(
                name="read_excel",
                description="读取",
                input_schema={"type": "object", "properties": {}},
                func=read_excel,
            ),
            ToolDef(
                name="write_text_file",
                description="写入文本",
                input_schema={
                    "type": "object",
                    "properties": {
                        "file_path": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["file_path", "content"],
                    "additionalProperties": False,
                },
                func=write_text_file,
            ),
        ]
    )
    return registry


@pytest.mark.asyncio
async def test_readonly_blocks_high_risk_tool(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    registry = _registry_with_tools(tmp_path)
    approval = ApprovalManager(str(tmp_path))
    executor = SubagentExecutor(
        parent_config=config,
        parent_registry=registry,
        approval_manager=approval,
    )
    sub_cfg = SubagentConfig(
        name="explorer",
        description="只读测试",
        allowed_tools=["write_text_file"],
        permission_mode="readOnly",
        max_iterations=2,
        max_consecutive_failures=1,
    )

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=AsyncMock(
                    side_effect=[
                        _response_from_message(
                            _tool_call_message(
                                "write_text_file",
                                {"file_path": "a.txt", "content": "x"},
                            )
                        )
                    ]
                )
            )
        )
    )

    with patch("excelmanus.subagent.executor.openai.AsyncOpenAI", return_value=fake_client):
        result = await executor.run(config=sub_cfg, prompt="测试写入")

    assert result.success is False
    assert result.error is not None
    assert "只读模式禁止高风险工具" in result.error


@pytest.mark.asyncio
async def test_default_mode_creates_pending_and_stops(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    registry = _registry_with_tools(tmp_path)
    approval = ApprovalManager(str(tmp_path))
    executor = SubagentExecutor(
        parent_config=config,
        parent_registry=registry,
        approval_manager=approval,
    )
    sub_cfg = SubagentConfig(
        name="writer",
        description="默认权限测试",
        allowed_tools=["write_text_file"],
        permission_mode="default",
        max_iterations=2,
        max_consecutive_failures=1,
    )

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=AsyncMock(
                    side_effect=[
                        _response_from_message(
                            _tool_call_message(
                                "write_text_file",
                                {"file_path": "a.txt", "content": "x"},
                            )
                        )
                    ]
                )
            )
        )
    )

    with patch("excelmanus.subagent.executor.openai.AsyncOpenAI", return_value=fake_client):
        result = await executor.run(config=sub_cfg, prompt="测试写入")

    assert result.success is False
    assert result.pending_approval_id is not None
    assert approval.pending is not None


@pytest.mark.asyncio
async def test_accept_edits_auto_executes_and_audits(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    registry = _registry_with_tools(tmp_path)
    approval = ApprovalManager(str(tmp_path))
    executor = SubagentExecutor(
        parent_config=config,
        parent_registry=registry,
        approval_manager=approval,
    )
    sub_cfg = SubagentConfig(
        name="writer",
        description="自动写入",
        allowed_tools=["write_text_file"],
        permission_mode="acceptEdits",
        max_iterations=2,
        max_consecutive_failures=2,
    )

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=AsyncMock(
                    side_effect=[
                        _response_from_message(
                            _tool_call_message(
                                "write_text_file",
                                {"file_path": "out/demo.txt", "content": "hello"},
                            )
                        ),
                        _response_from_message(_text_message("执行完成")),
                    ]
                )
            )
        )
    )

    with patch("excelmanus.subagent.executor.openai.AsyncOpenAI", return_value=fake_client):
        result = await executor.run(config=sub_cfg, prompt="请生成文件")

    assert result.success is True
    assert "执行完成" in result.summary
    assert (tmp_path / "out" / "demo.txt").exists()
    assert result.file_changes


@pytest.mark.asyncio
async def test_default_mode_with_fullaccess_auto_executes(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    registry = _registry_with_tools(tmp_path)
    approval = ApprovalManager(str(tmp_path))
    executor = SubagentExecutor(
        parent_config=config,
        parent_registry=registry,
        approval_manager=approval,
    )
    sub_cfg = SubagentConfig(
        name="analyst",
        description="默认权限 + fullAccess",
        allowed_tools=["write_text_file"],
        permission_mode="default",
        max_iterations=2,
        max_consecutive_failures=2,
    )

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=AsyncMock(
                    side_effect=[
                        _response_from_message(
                            _tool_call_message(
                                "write_text_file",
                                {"file_path": "out/from_fullaccess.txt", "content": "ok"},
                            )
                        ),
                        _response_from_message(_text_message("执行完成")),
                    ]
                )
            )
        )
    )

    with patch("excelmanus.subagent.executor.openai.AsyncOpenAI", return_value=fake_client):
        result = await executor.run(
            config=sub_cfg,
            prompt="请写文件",
            full_access_enabled=True,
        )

    assert result.success is True
    assert result.pending_approval_id is None
    assert approval.pending is None
    assert (tmp_path / "out" / "from_fullaccess.txt").exists()


@pytest.mark.asyncio
async def test_circuit_breaker_on_consecutive_failures(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    registry = ToolRegistry()

    def fail_tool() -> str:
        raise RuntimeError("boom")

    registry.register_tool(
        ToolDef(
            name="read_excel",
            description="读取",
            input_schema={"type": "object", "properties": {}},
            func=fail_tool,
        )
    )
    approval = ApprovalManager(str(tmp_path))
    executor = SubagentExecutor(
        parent_config=config,
        parent_registry=registry,
        approval_manager=approval,
    )
    sub_cfg = SubagentConfig(
        name="analyst",
        description="失败熔断测试",
        allowed_tools=["read_excel"],
        permission_mode="default",
        max_iterations=4,
        max_consecutive_failures=2,
    )

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=AsyncMock(
                    side_effect=[
                        _response_from_message(_tool_call_message("read_excel", {})),
                        _response_from_message(_tool_call_message("read_excel", {})),
                    ]
                )
            )
        )
    )

    with patch("excelmanus.subagent.executor.openai.AsyncOpenAI", return_value=fake_client):
        result = await executor.run(config=sub_cfg, prompt="失败测试")

    assert result.success is False
    assert "连续 2 次工具调用失败" in result.summary


@pytest.mark.asyncio
async def test_collects_observed_files_from_tool_arguments(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    registry = ToolRegistry()

    def read_excel(file_path: str) -> str:
        return json.dumps({"file": file_path}, ensure_ascii=False)

    registry.register_tool(
        ToolDef(
            name="read_excel",
            description="读取",
            input_schema={
                "type": "object",
                "properties": {"file_path": {"type": "string"}},
                "required": ["file_path"],
                "additionalProperties": False,
            },
            func=read_excel,
        )
    )
    approval = ApprovalManager(str(tmp_path))
    executor = SubagentExecutor(
        parent_config=config,
        parent_registry=registry,
        approval_manager=approval,
    )
    sub_cfg = SubagentConfig(
        name="explorer",
        description="文件上下文收集测试",
        allowed_tools=["read_excel"],
        permission_mode="readOnly",
        max_iterations=2,
        max_consecutive_failures=2,
    )

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=AsyncMock(
                    side_effect=[
                        _response_from_message(
                            _tool_call_message(
                                "read_excel",
                                {"file_path": "./stress_test_comprehensive.xlsx"},
                            )
                        ),
                        _response_from_message(_text_message("完成")),
                    ]
                )
            )
        )
    )

    with patch("excelmanus.subagent.executor.openai.AsyncOpenAI", return_value=fake_client):
        result = await executor.run(config=sub_cfg, prompt="读取数据")

    assert result.success is True
    assert "stress_test_comprehensive.xlsx" in result.observed_files
