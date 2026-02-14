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

    def write_cells(file_path: str, sheet_name: str, cells: list[dict]) -> str:
        _ = sheet_name
        _ = cells
        path = Path(tmp_path) / file_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        return f"写入单元格完成: {file_path}"

    def create_chart(output_path: str) -> str:
        path = Path(tmp_path) / output_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("chart", encoding="utf-8")
        return f"图表已生成: {output_path}"

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
            ToolDef(
                name="write_cells",
                description="写入单元格",
                input_schema={
                    "type": "object",
                    "properties": {
                        "file_path": {"type": "string"},
                        "sheet_name": {"type": "string"},
                        "cells": {"type": "array"},
                    },
                    "required": ["file_path", "sheet_name", "cells"],
                    "additionalProperties": False,
                },
                func=write_cells,
            ),
            ToolDef(
                name="create_chart",
                description="生成图表",
                input_schema={
                    "type": "object",
                    "properties": {
                        "output_path": {"type": "string"},
                    },
                    "required": ["output_path"],
                    "additionalProperties": False,
                },
                func=create_chart,
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
    assert "只读模式仅允许白名单工具" in result.error


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
async def test_default_mode_audit_only_tool_executes_without_pending(tmp_path: Path) -> None:
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
        description="默认模式审批测试",
        allowed_tools=["create_chart"],
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
                                "create_chart",
                                {"output_path": "charts/subagent.png"},
                            )
                        ),
                        _response_from_message(_text_message("执行完成")),
                    ]
                )
            )
        )
    )

    with patch("excelmanus.subagent.executor.openai.AsyncOpenAI", return_value=fake_client):
        result = await executor.run(config=sub_cfg, prompt="生成图表")

    assert result.success is True
    assert result.pending_approval_id is None
    assert approval.pending is None
    assert (tmp_path / "charts" / "subagent.png").exists()


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
                                {"file_path": "./examples/bench/stress_test_comprehensive.xlsx"},
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
    assert "examples/bench/stress_test_comprehensive.xlsx" in result.observed_files


@pytest.mark.asyncio
async def test_on_event_callback_error_does_not_interrupt_run(tmp_path: Path) -> None:
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
        description="回调鲁棒性测试",
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
                        _response_from_message(_tool_call_message("read_excel", {})),
                        _response_from_message(_text_message("完成")),
                    ]
                )
            )
        )
    )

    def _on_event(_event) -> None:
        raise RuntimeError("event callback boom")

    with patch("excelmanus.subagent.executor.openai.AsyncOpenAI", return_value=fake_client):
        result = await executor.run(
            config=sub_cfg,
            prompt="只读探查",
            on_event=_on_event,
        )

    assert result.success is True
    assert result.summary == "完成"


@pytest.mark.asyncio
async def test_readonly_blocks_write_cells_tool(tmp_path: Path) -> None:
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
        description="只读模式写入拦截测试",
        allowed_tools=["write_cells"],
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
                                "write_cells",
                                {
                                    "file_path": "out/demo.xlsx",
                                    "sheet_name": "Sheet1",
                                    "cells": [{"cell": "A1", "value": 1}],
                                },
                            )
                        ),
                    ]
                )
            )
        )
    )

    with patch("excelmanus.subagent.executor.openai.AsyncOpenAI", return_value=fake_client):
        result = await executor.run(config=sub_cfg, prompt="写入 A1")

    assert result.success is False
    assert result.error is not None
    assert "只读模式仅允许白名单工具" in result.error
    assert "write_cells" in result.error


@pytest.mark.asyncio
async def test_observed_files_kept_when_tool_result_truncated(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    registry = ToolRegistry()

    def scan_excel_files() -> str:
        payload = {
            "files": [
                {"path": f"./examples/huge/report_{i}.xlsx"}
                for i in range(30)
            ]
        }
        return json.dumps(payload, ensure_ascii=False)

    registry.register_tool(
        ToolDef(
            name="scan_excel_files",
            description="扫描 Excel",
            input_schema={"type": "object", "properties": {}},
            func=scan_excel_files,
            max_result_chars=120,
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
        description="长结果路径提取测试",
        allowed_tools=["scan_excel_files"],
        permission_mode="readOnly",
        max_iterations=2,
        max_consecutive_failures=2,
    )

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=AsyncMock(
                    side_effect=[
                        _response_from_message(_tool_call_message("scan_excel_files", {})),
                        _response_from_message(_text_message("完成")),
                    ]
                )
            )
        )
    )

    with patch("excelmanus.subagent.executor.openai.AsyncOpenAI", return_value=fake_client):
        result = await executor.run(config=sub_cfg, prompt="扫描文件")

    assert result.success is True
    assert "examples/huge/report_29.xlsx" in result.observed_files
