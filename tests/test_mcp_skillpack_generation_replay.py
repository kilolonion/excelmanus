"""Skillpack 生成器离线回放门禁测试。"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from excelmanus.mcp.skillpack_generator import (
    _new_diagnostics,
    generate_skillpack_with_llm,
)

_FIXTURE_FILE = (
    Path(__file__).parent
    / "fixtures"
    / "generation_replay"
    / "skillpack_generator_replay_cases.json"
)


def _make_llm_response(content: str) -> MagicMock:
    choice = MagicMock()
    choice.message.content = content
    response = MagicMock()
    response.choices = [choice]
    return response


def _make_tool(name: str, description: str) -> SimpleNamespace:
    return SimpleNamespace(name=name, description=description)


def _actionable_instructions(prefix: str) -> str:
    return (
        f"工具前缀：{prefix}。"
        "推荐调用顺序：先读取状态，然后执行目标操作，最后复核结果。"
        "错误处理：失败时先检查参数与字段名，再按顺序重试。"
    )


def _scenario_responses(scenario: str, prefix: str) -> list[str]:
    step1_ok = json.dumps(
        {
            "description": "用于文档查询与工具调用协同",
            "triggers": ["文档", "API", "查询"],
        },
        ensure_ascii=False,
    )
    step2_ok = json.dumps(
        {
            "instructions": _actionable_instructions(prefix),
        },
        ensure_ascii=False,
    )
    step2_bad = json.dumps(
        {
            "instructions": "请按需调用工具。",
        },
        ensure_ascii=False,
    )

    if scenario == "clean":
        return [step1_ok, step2_ok]
    if scenario == "step1_repair":
        return ["not json", step1_ok, step2_ok]
    if scenario == "step2_repair":
        return [step1_ok, step2_bad, step2_ok]
    if scenario == "step2_fallback":
        return [step1_ok, step2_bad, step2_bad]
    raise ValueError(f"unsupported scenario: {scenario}")


@pytest.mark.asyncio
async def test_skillpack_generation_replay_gate() -> None:
    """回放集门禁：结构化有效率/修复率/fallback 比例。"""
    cases = json.loads(_FIXTURE_FILE.read_text(encoding="utf-8"))
    assert isinstance(cases, list)
    assert 30 <= len(cases) <= 50

    valid_count = 0
    failed_then_repaired_count = 0
    initial_failed_count = 0
    fallback_count = 0

    tools = [
        _make_tool("resolve-library-id", "解析库 ID"),
        _make_tool("query-docs", "查询文档"),
    ]

    for idx, case in enumerate(cases, 1):
        scenario = case["scenario"]
        responses = _scenario_responses(scenario, prefix=f"mcp_context7_{idx}_")

        mock_client = AsyncMock()
        mock_client.chat.completions.create.side_effect = [
            _make_llm_response(content) for content in responses
        ]

        diag = _new_diagnostics()
        sp = await generate_skillpack_with_llm(
            client=mock_client,
            model="test-model",
            server_name="context7",
            normalized_name="context7",
            tools=tools,
            silent=True,
            diagnostics=diag,
        )

        if sp is not None:
            valid_count += 1
        if diag["step1_fail"] > 0 or diag["step2_fail"] > 0:
            initial_failed_count += 1
        if diag["repair_success"] > 0:
            failed_then_repaired_count += 1
        if diag["fallback_used"] > 0:
            fallback_count += 1

    structured_valid_rate = valid_count / len(cases)
    repair_success_rate = (
        failed_then_repaired_count / initial_failed_count
        if initial_failed_count > 0
        else 0.0
    )
    fallback_ratio = fallback_count / len(cases)

    assert structured_valid_rate >= 0.95
    assert repair_success_rate >= 0.35
    assert fallback_ratio <= 0.30
