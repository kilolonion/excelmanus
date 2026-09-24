"""Settings probe jobs use existing subscription credentials for named/all probes."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from excelmanus import api_routes_config as routes
from excelmanus.api_app_state import get_runtime
from excelmanus.auth.providers.base import ResolvedCredential


@pytest.fixture
def configured_probes():
    model = "workbuddy-global/deepseek-v4.1-flash"
    runtime = get_runtime()
    runtime.config = SimpleNamespace(protocol="openai")
    runtime.config_store = SimpleNamespace(list_profiles=lambda: [
        {"name": model, "model": model, "base_url": "https://www.workbuddy.ai/v2",
         "api_key": "", "protocol": "auto", "thinking_mode": "auto"},
        {"name": "api-key-model", "model": "other-model", "base_url": "https://example.com/v1",
         "api_key": "configured-key", "protocol": "openai"},
    ])
    runtime.cap_probe_job_manager = SimpleNamespace(
        create_job=AsyncMock(return_value={"job_id": "probe-job"}),
    )
    return runtime, model


@pytest.mark.asyncio
@pytest.mark.parametrize("all_profiles", [True, False])
async def test_settings_probe_includes_connected_subscription(configured_probes, all_profiles):
    runtime, model = configured_probes
    credential = ResolvedCredential(
        api_key="subscription-token", base_url="https://www.workbuddy.ai/v2",
        source="oauth", provider="workbuddy-global", protocol="openai",
        extra_headers={"X-User-Id": "subscriber"},
    )
    resolver = SimpleNamespace(resolve_sync=Mock(return_value=credential))
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(credential_resolver=resolver)),
        json=AsyncMock(return_value={"all": True} if all_profiles else {"name": model}),
    )

    response = await routes.create_probe_job(request)

    assert response.status_code == 202
    targets = runtime.cap_probe_job_manager.create_job.await_args.kwargs["targets"]
    assert len(targets) == (2 if all_profiles else 1)
    subscription = targets[0]
    assert subscription.name == model
    assert subscription.cache_model == model
    assert subscription.api_model == "deepseek-v4.1-flash"
    assert subscription.api_key == credential.api_key
    assert subscription.base_url == credential.base_url
    assert subscription.protocol == credential.protocol
    assert subscription.extra_headers == credential.extra_headers
    resolver.resolve_sync.assert_called_once_with(model)
    if all_profiles:
        assert targets[1].api_key == "configured-key"
        assert targets[1].extra_headers is None


@pytest.mark.parametrize("failure", ["missing", "disconnected", "empty", "error"])
def test_unavailable_subscription_does_not_use_another_models_key(configured_probes, failure):
    resolver = None
    if failure != "missing":
        credential = None if failure != "empty" else ResolvedCredential(
            api_key="", base_url="https://www.workbuddy.ai/v2", source="oauth",
        )
        resolver = SimpleNamespace(resolve_sync=Mock(return_value=credential))
        if failure == "error":
            resolver.resolve_sync.side_effect = RuntimeError("credential unavailable")

    targets = routes._build_probe_targets(probe_all=True, credential_resolver=resolver)

    assert len(targets) == 1
    assert targets[0].name == "api-key-model"
    assert targets[0].api_key == "configured-key"
