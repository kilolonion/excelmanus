from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from excelmanus.model_probe import _probe_openai_thinking, run_full_probe, update_capabilities_override


@pytest.mark.asyncio
async def test_run_full_probe_stage_callback_and_timeouts() -> None:
    client = MagicMock()
    stage_events: list[tuple[str, str]] = []

    async def _stage(stage: str, state: str, payload: dict | None = None) -> None:
        stage_events.append((stage, state))

    with patch("excelmanus.model_probe.probe_health", new=AsyncMock(return_value=(True, ""))) as m_health, patch(
        "excelmanus.model_probe.probe_tool_calling",
        new=AsyncMock(return_value=(True, "")),
    ) as m_tool, patch(
        "excelmanus.model_probe.probe_vision",
        new=AsyncMock(return_value=(False, "vision unsupported")),
    ) as m_vision, patch(
        "excelmanus.model_probe.probe_thinking",
        new=AsyncMock(return_value=(True, "", "openai_reasoning")),
    ) as m_thinking:
        caps = await run_full_probe(
            client=client,
            model="test-model",
            base_url="https://test.example.com/v1",
            skip_if_cached=False,
            db=None,
            health_timeout=1.0,
            tool_timeout=2.0,
            vision_timeout=3.0,
            thinking_total_timeout=4.0,
            thinking_strategy_timeout=0.5,
            stage_callback=_stage,
            source="manual_probe",
        )

    m_health.assert_called_once_with(client, "test-model", timeout=1.0)
    m_tool.assert_called_once_with(client, "test-model", timeout=2.0)
    m_vision.assert_called_once_with(client, "test-model", timeout=3.0)
    m_thinking.assert_called_once_with(
        client,
        "test-model",
        "https://test.example.com/v1",
        timeout=4.0,
        strategy_timeout=0.5,
        thinking_mode="auto",
    )

    assert ("health", "running") in stage_events
    assert ("health", "completed") in stage_events
    assert ("tool_calling", "completed") in stage_events
    assert ("vision", "completed") in stage_events
    assert ("thinking", "completed") in stage_events
    assert caps.source == "manual_probe"
    assert caps.fresh_until
    assert caps.stale_until


@pytest.mark.asyncio
async def test_openai_thinking_uses_total_budget_slice() -> None:
    calls: list[float] = []

    async def _fake_try(client, model, messages, timeout, extra_kwargs=None):
        calls.append(timeout)
        await asyncio.sleep(0.06)
        return False, "unsupported parameter"

    with patch(
        "excelmanus.model_probe._get_thinking_strategies",
        return_value=[
            ("s1", {"extra_body": {"enable_thinking": True}}, "enable_thinking"),
            ("s2", {}, "deepseek"),
        ],
    ), patch("excelmanus.model_probe._try_thinking_stream", new=AsyncMock(side_effect=_fake_try)):
        ok, err, t = await _probe_openai_thinking(
            client=MagicMock(),
            model="gpt-test",
            messages=[{"role": "user", "content": "hi"}],
            timeout=0.11,
            strategy_timeout=0.1,
            base_url="https://api.openai.com/v1",
        )

    assert ok is False
    assert t == ""
    assert err
    assert len(calls) >= 2
    assert calls[0] <= 0.101
    assert calls[1] < calls[0]


@pytest.mark.asyncio
async def test_openai_thinking_fatal_short_circuit() -> None:
    calls = 0

    async def _fake_try(client, model, messages, timeout, extra_kwargs=None):
        nonlocal calls
        calls += 1
        return False, "401 unauthorized"

    with patch(
        "excelmanus.model_probe._get_thinking_strategies",
        return_value=[
            ("s1", {}, "openai_reasoning"),
            ("s2", {}, "deepseek"),
        ],
    ), patch("excelmanus.model_probe._try_thinking_stream", new=AsyncMock(side_effect=_fake_try)):
        ok, err, t = await _probe_openai_thinking(
            client=MagicMock(),
            model="gpt-test",
            messages=[{"role": "user", "content": "hi"}],
            timeout=1.0,
            strategy_timeout=0.3,
            base_url="https://api.openai.com/v1",
        )

    assert calls == 1
    assert ok is None
    assert "401" in err
    assert t == ""


@pytest.mark.asyncio
async def test_probe_job_state_machine_full_cycle() -> None:
    """queued → running → succeeded when all targets succeed."""
    from excelmanus.capability_probe_jobs import CapabilityProbeJobManager, ProbeTargetSpec
    from excelmanus.model_probe import ModelCapabilities

    fake_caps = ModelCapabilities(
        model="m", base_url="http://x/v1", healthy=True,
        supports_tool_calling=True, supports_vision=True, supports_thinking=False,
    )

    with patch("excelmanus.capability_probe_jobs.run_full_probe", new=AsyncMock(return_value=fake_caps)), \
         patch("excelmanus.capability_probe_jobs.create_client", return_value=MagicMock()):
        mgr = CapabilityProbeJobManager(job_concurrency=2, provider_concurrency=2)
        spec = ProbeTargetSpec(name="t1", cache_model="m", api_model="m", base_url="http://x/v1", api_key="k", protocol="auto")
        result = await mgr.create_job(targets=[spec])
        assert result["state"] == "queued"
        # Wait for job to finish
        await asyncio.sleep(0.5)
        snap = await mgr.get_job_snapshot(result["job_id"])
        assert snap is not None
        assert snap["state"] == "succeeded"
        assert snap["targets_succeeded"] == 1
        await mgr.shutdown()


@pytest.mark.asyncio
async def test_probe_job_partial_when_mixed() -> None:
    """1 succeeded + 1 failed → job state partial."""
    from excelmanus.capability_probe_jobs import CapabilityProbeJobManager, ProbeTargetSpec
    from excelmanus.model_probe import ModelCapabilities

    good_caps = ModelCapabilities(
        model="good", base_url="http://x/v1", healthy=True,
        supports_tool_calling=True, supports_vision=True, supports_thinking=True,
    )
    bad_caps = ModelCapabilities(model="bad", base_url="http://y/v1", healthy=False, health_error="401 unauthorized")

    call_count = 0

    async def _fake_probe(**kwargs):
        nonlocal call_count
        call_count += 1
        if "good" in kwargs.get("model", ""):
            return good_caps
        return bad_caps

    with patch("excelmanus.capability_probe_jobs.run_full_probe", new=AsyncMock(side_effect=_fake_probe)), \
         patch("excelmanus.capability_probe_jobs.create_client", return_value=MagicMock()):
        mgr = CapabilityProbeJobManager(job_concurrency=4, provider_concurrency=4)
        specs = [
            ProbeTargetSpec(name="t1", cache_model="good", api_model="good", base_url="http://x/v1", api_key="k", protocol="auto"),
            ProbeTargetSpec(name="t2", cache_model="bad", api_model="bad", base_url="http://y/v1", api_key="k", protocol="auto"),
        ]
        result = await mgr.create_job(targets=specs)
        await asyncio.sleep(0.5)
        snap = await mgr.get_job_snapshot(result["job_id"])
        assert snap is not None
        assert snap["state"] == "partial"
        await mgr.shutdown()


@pytest.mark.asyncio
async def test_probe_job_cancel_while_running() -> None:
    """Cancel a running job → state becomes cancelled."""
    from excelmanus.capability_probe_jobs import CapabilityProbeJobManager, ProbeTargetSpec

    async def _slow_probe(**kwargs):
        await asyncio.sleep(10)

    with patch("excelmanus.capability_probe_jobs.run_full_probe", new=AsyncMock(side_effect=_slow_probe)), \
         patch("excelmanus.capability_probe_jobs.create_client", return_value=MagicMock()):
        mgr = CapabilityProbeJobManager()
        spec = ProbeTargetSpec(name="slow", cache_model="m", api_model="m", base_url="http://x/v1", api_key="k", protocol="auto")
        result = await mgr.create_job(targets=[spec])
        await asyncio.sleep(0.1)
        state = await mgr.cancel_job(result["job_id"])
        assert state == "cancelling"
        await asyncio.sleep(0.3)
        snap = await mgr.get_job_snapshot(result["job_id"])
        assert snap is not None
        assert snap["state"] == "cancelled"
        await mgr.shutdown()


@pytest.mark.asyncio
async def test_probe_job_deduplication() -> None:
    """Same model+base_url targets share probe result, second is deduplicated."""
    from excelmanus.capability_probe_jobs import CapabilityProbeJobManager, ProbeTargetSpec
    from excelmanus.model_probe import ModelCapabilities

    call_count = 0
    fake_caps = ModelCapabilities(
        model="m", base_url="http://x/v1", healthy=True,
        supports_tool_calling=True, supports_vision=False, supports_thinking=False,
    )

    async def _counting_probe(**kwargs):
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0.1)
        return fake_caps

    with patch("excelmanus.capability_probe_jobs.run_full_probe", new=AsyncMock(side_effect=_counting_probe)), \
         patch("excelmanus.capability_probe_jobs.create_client", return_value=MagicMock()):
        mgr = CapabilityProbeJobManager(job_concurrency=4, provider_concurrency=4)
        specs = [
            ProbeTargetSpec(name="t1", cache_model="m", api_model="m", base_url="http://x/v1", api_key="k", protocol="auto"),
            ProbeTargetSpec(name="t2", cache_model="m", api_model="m", base_url="http://x/v1", api_key="k2", protocol="auto"),
        ]
        result = await mgr.create_job(targets=specs)
        await asyncio.sleep(1.0)
        snap = await mgr.get_job_snapshot(result["job_id"])
        assert snap is not None
        # Only 1 actual probe call despite 2 targets
        assert call_count == 1
        dedup_count = sum(1 for t in snap["targets"] if t.get("deduplicated"))
        assert dedup_count == 1
        await mgr.shutdown()


@pytest.mark.asyncio
async def test_thinking_budget_exhaustion() -> None:
    """Total budget exhaustion stops trying strategies."""
    calls: list[float] = []

    async def _slow_try(client, model, messages, timeout, extra_kwargs=None):
        calls.append(timeout)
        await asyncio.sleep(0.08)
        return False, "unsupported parameter"

    with patch(
        "excelmanus.model_probe._get_thinking_strategies",
        return_value=[
            ("s1", {}, "a"),
            ("s2", {}, "b"),
            ("s3", {}, "c"),
            ("s4", {}, "d"),
        ],
    ), patch("excelmanus.model_probe._try_thinking_stream", new=AsyncMock(side_effect=_slow_try)):
        ok, _err, _t = await _probe_openai_thinking(
            client=MagicMock(),
            model="test",
            messages=[{"role": "user", "content": "hi"}],
            timeout=0.15,
            strategy_timeout=0.1,
            base_url="https://api.openai.com/v1",
        )

    assert ok is False
    # Should stop before trying all 4 strategies due to budget exhaustion
    assert len(calls) <= 2


@pytest.mark.asyncio
async def test_freshness_on_cache_hit() -> None:
    """Cache hit sets source='cache' if not already set."""
    from excelmanus.model_probe import ModelCapabilities

    cached = ModelCapabilities(model="m", base_url="http://x/v1", healthy=True,
                               supports_tool_calling=True, supports_vision=True, supports_thinking=False)

    with patch("excelmanus.model_probe.load_capabilities", return_value=cached):
        result = await run_full_probe(
            client=MagicMock(), model="m", base_url="http://x/v1",
            skip_if_cached=True, db=MagicMock(),
        )

    assert result.source == "cache"
    assert result.last_success_at
    assert result.fresh_until


@pytest.mark.asyncio
async def test_freshness_on_fresh_probe() -> None:
    """New probe sets source from parameter."""
    with patch("excelmanus.model_probe.probe_health", new=AsyncMock(return_value=(True, ""))), \
         patch("excelmanus.model_probe.probe_tool_calling", new=AsyncMock(return_value=(True, ""))), \
         patch("excelmanus.model_probe.probe_vision", new=AsyncMock(return_value=(True, ""))), \
         patch("excelmanus.model_probe.probe_thinking", new=AsyncMock(return_value=(True, "", "deepseek"))):
        result = await run_full_probe(
            client=MagicMock(), model="m", base_url="http://x/v1",
            skip_if_cached=False, db=None, source="test_source",
        )

    assert result.source == "test_source"
    assert result.fresh_until
    assert result.stale_until


@pytest.mark.asyncio
async def test_stage_callback_on_health_failure() -> None:
    """Health failure emits health 'failed', other stages not started."""
    events: list[tuple[str, str]] = []

    async def _cb(stage: str, state: str, payload: dict | None = None) -> None:
        events.append((stage, state))

    with patch("excelmanus.model_probe.probe_health", new=AsyncMock(return_value=(False, "401 unauthorized"))):
        caps = await run_full_probe(
            client=MagicMock(), model="m", base_url="http://x/v1",
            skip_if_cached=False, db=None, stage_callback=_cb,
        )

    assert ("health", "running") in events
    assert ("health", "failed") in events
    # tool_calling/vision/thinking should NOT have been started
    assert ("tool_calling", "running") not in events
    assert caps.healthy is False


def test_config_fields_exist() -> None:
    """ExcelManusConfig has all 7 cap_probe_* fields."""
    from excelmanus.config import ExcelManusConfig
    import dataclasses
    field_names = {f.name for f in dataclasses.fields(ExcelManusConfig)}
    expected = {
        "cap_probe_job_concurrency",
        "cap_probe_provider_concurrency",
        "cap_probe_health_timeout",
        "cap_probe_tool_timeout",
        "cap_probe_vision_timeout",
        "cap_probe_thinking_total_timeout",
        "cap_probe_thinking_strategy_timeout",
    }
    assert expected.issubset(field_names), f"Missing: {expected - field_names}"


def test_update_capabilities_override_sets_source_and_freshness() -> None:
    db = MagicMock()
    db.conn = MagicMock()
    with patch("excelmanus.model_probe.load_capabilities", return_value=None), patch(
        "excelmanus.model_probe.save_capabilities"
    ) as m_save:
        caps = update_capabilities_override(
            db,
            model="test-model",
            base_url="https://test.example.com/v1",
            overrides={"supports_vision": True},
        )

    assert caps is not None
    assert caps.manual_override is True
    assert caps.source == "override"
    assert caps.last_success_at
    assert caps.fresh_until
    assert caps.stale_until
    m_save.assert_called_once()
