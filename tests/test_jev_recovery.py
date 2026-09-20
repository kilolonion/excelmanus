from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from excelmanus.config import ExcelManusConfig
from excelmanus.engine_types import ToolCallResult
from excelmanus.system_one.host import maybe_suggest_recovery
from excelmanus.system_one.types import Decision


def _config(**overrides: object) -> ExcelManusConfig:
    values: dict[str, object] = {
        "api_key": "test-key",
        "base_url": "https://test.example.com/v1",
        "model": "test-model",
        "max_iterations": 8,
        "max_consecutive_failures": 3,
        "workspace_root": str(Path(__file__).resolve().parent),
        "ai_gateway_api_key": "vck_test",
        "jev_enabled": "shadow",
        "jev_recovery": "shadow",
    }
    values.update(overrides)
    return ExcelManusConfig(**values)


@pytest.mark.asyncio
async def test_recovery_is_not_called_for_success_or_twice() -> None:
    engine = SimpleNamespace(
        config=_config(),
        _subagent_config=None,
        _is_host_session=True,
        _recovery_hint=None,
        memory=SimpleNamespace(get_messages=lambda: [{"role": "user", "content": "继续"}]),
        _last_iteration_count=2,
    )
    success = [ToolCallResult("read", {}, "ok", True)]
    with patch(
        "excelmanus.system_one.evaluate",
        AsyncMock(return_value=Decision.noop("next:none", next="none")),
    ) as mocked:
        await maybe_suggest_recovery(engine, success)
        mocked.assert_not_awaited()
        failure = [ToolCallResult("read", {}, "bad", False, error="bad")]
        await maybe_suggest_recovery(engine, failure)
        await maybe_suggest_recovery(engine, failure)
    mocked.assert_awaited_once()
    assert engine._recovery_hint["next"] == "none"
