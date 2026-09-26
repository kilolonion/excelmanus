"""DB → 模型 API → 已有/新建会话的回归验证；不调用外部模型。"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import quote

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from starlette.requests import Request

from excelmanus.api_app_state import get_runtime, set_config_store
from excelmanus.api_routes_config import router
from excelmanus.config import ExcelManusConfig
from excelmanus.config_transfer import export_config
from excelmanus.database import Database
from excelmanus.session import SessionManager
from excelmanus.stores.config_store import GlobalConfigStore, UserConfigStore
from excelmanus.tools import ToolRegistry


def profile(name="first", model="model-a"):
    return dict(name=name, model=model, api_key="fake-local-key", base_url="https://unit.invalid/v1")


@pytest_asyncio.fixture
async def setup(tmp_path, monkeypatch):
    db = Database(str(tmp_path / "models.db"))
    store = GlobalConfigStore(db)
    config = ExcelManusConfig(
        api_key="fake-bootstrap-key", base_url="https://unit.invalid/v1", model="bootstrap",
        workspace_root=str(tmp_path), db_path=str(tmp_path / "models.db"),
        memory_enabled=False,
    )
    # 客户端构建也使用替身，确保不会意外发出模型请求；会话/路由/数据库使用真实实现。
    monkeypatch.setattr("excelmanus.engine_core.llm_client_manager.create_client", lambda **kw: SimpleNamespace(**kw))
    monkeypatch.setattr("excelmanus.engine.AgentEngine.initialize_mcp", AsyncMock())
    monkeypatch.setattr("excelmanus.engine.AgentEngine.shutdown_mcp", AsyncMock())
    monkeypatch.setattr("excelmanus.engine.AgentEngine.start_registry_scan", lambda *a, **kw: False)
    manager = SessionManager(5, 60, config=config, registry=ToolRegistry(), database=db, config_store=store)
    runtime = get_runtime()
    runtime.config = config
    runtime.session_manager = manager
    runtime.database = db
    set_config_store(store)
    app = FastAPI()
    app.include_router(router)
    app.state.runtime = runtime
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://unit") as client:
        yield SimpleNamespace(client=client, manager=manager, store=store, config=config,
                              user=UserConfigStore(db.conn), app=app)
    await manager.shutdown()
    db.close()


async def engine_for(s):
    sid, engine = await s.manager.acquire_for_chat(None)
    await s.manager.release_for_chat(sid)
    return sid, engine


@pytest.mark.asyncio
async def test_fast_mode_profile_round_trip(setup):
    s = setup
    payload = {**profile(), "service_tier": "fast"}
    response = await s.client.post("/api/v1/config/models/profiles", json=payload)
    assert response.status_code == 201
    assert s.store.get_profile("first")["service_tier"] == "fast"
    config = (await s.client.get("/api/v1/config/models")).json()
    assert config["profiles"][0]["service_tier"] == "fast"
    _, engine = await engine_for(s)
    assert engine._active_profile.service_tier == "fast"
    response = await s.client.put(
        "/api/v1/config/models/profiles/first",
        json={**payload, "service_tier": ""},
    )
    assert response.status_code == 200
    assert s.store.get_profile("first")["service_tier"] == ""


@pytest.mark.asyncio
async def test_model_list_exposes_provider_group_fields(setup):
    s = setup
    for name, model in (("codex proxy", "gpt-6-astra"), ("codex direct", "gpt-6-sol")):
        payload = {**profile(name, model), "protocol": "openai", "model_family": "gpt"}
        assert (await s.client.post("/api/v1/config/models/profiles", json=payload)).status_code == 201

    models = (await s.client.get("/api/v1/models")).json()["models"]
    assert [model["name"] for model in models] == ["codex proxy", "codex direct"]
    assert all(model["base_url"] == "https://unit.invalid/v1" for model in models)
    assert all(model["protocol"] == "openai" for model in models)
    assert all(model["model_family"] == "gpt" for model in models)


@pytest.mark.asyncio
@pytest.mark.parametrize("model", [
    "openai-codex/gpt-6-astra", "workbuddy-cn/claude-sonnet-4.6", "antigravity/gemini-3-pro",
])
async def test_oauth_model_metadata_round_trip_and_live_sessions(setup, model):
    s = setup
    payload = {**profile("subscription", model), "api_key": "", "vision_mode": "true"}
    assert (await s.client.post("/api/v1/config/models/profiles", json=payload)).status_code == 201
    _, engine = await engine_for(s)
    assert engine._is_vision_capable
    payload.update(max_context_tokens=65536, vision_mode="false", thinking_mode="disabled")
    response = await s.client.put("/api/v1/config/models/profiles/subscription", json=payload)
    assert response.status_code == 200
    stored = s.store.get_profile("subscription")
    assert stored["max_context_tokens"] == 65536
    assert stored["vision_mode"] == "false"
    assert stored["api_key"] == ""
    config = (await s.client.get("/api/v1/config/models")).json()["profiles"][0]
    assert config["max_context_tokens"] == 65536
    assert config["vision_mode"] == "false"
    assert not (await s.client.get("/api/v1/models")).json()["models"][0]["supports_vision"]
    _, fresh = await engine_for(s)
    for current in (engine, fresh):
        assert current.max_context_tokens == 65536
        assert current._compaction_manager.max_context_tokens == 65536
        assert not current._is_vision_capable
        assert current._active_profile.thinking_mode == "disabled"
    # Older clients may omit the new fields; only an explicit reset clears them.
    payload.pop("max_context_tokens")
    payload.pop("vision_mode")
    assert (await s.client.put("/api/v1/config/models/profiles/subscription", json=payload)).status_code == 200
    assert engine.max_context_tokens == 65536
    assert not engine._is_vision_capable
    payload.update(max_context_tokens=0, vision_mode="auto")
    assert (await s.client.put("/api/v1/config/models/profiles/subscription", json=payload)).status_code == 200
    assert engine.max_context_tokens != 65536
    assert engine._is_vision_capable is model.startswith("openai-codex/")


@pytest.mark.asyncio
async def test_metadata_import_switch_and_background_probe_preserve_overrides(setup):
    from excelmanus.model_probe import ModelCapabilities

    s = setup
    rows = [
        {**profile("text", "openai-codex/gpt-6-astra"), "max_context_tokens": 32768, "vision_mode": "false"},
        {**profile("images", "workbuddy-cn/custom-model"), "max_context_tokens": 131072, "vision_mode": "true"},
    ]
    token = export_config({"profiles": rows}, mode="simple")
    assert (await s.client.post("/api/v1/config/import", json={"token": token})).status_code == 200
    _, engine = await engine_for(s)
    engine.set_model_capabilities(ModelCapabilities(supports_vision=True))
    assert not engine._is_vision_capable
    assert (await s.client.put("/api/v1/models/active", json={"name": "images"})).status_code == 200
    engine.set_model_capabilities(ModelCapabilities(supports_vision=False))
    assert engine._is_vision_capable
    assert engine.max_context_tokens == 131072
    assert (await s.client.put("/api/v1/models/active", json={"name": "text"})).status_code == 200
    assert not engine._is_vision_capable
    assert engine.max_context_tokens == 32768
    # A saved edit also reaches a session using a model other than the global default.
    engine.switch_model("images")
    rows[1]["max_context_tokens"] = 262144
    rows[1]["vision_mode"] = "false"
    assert (await s.client.put("/api/v1/config/models/profiles/images", json=rows[1])).status_code == 200
    assert engine.max_context_tokens == 262144
    assert not engine._is_vision_capable


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", [
    {"max_context_tokens": -1}, {"max_context_tokens": 1.5}, {"max_context_tokens": True},
    {"max_context_tokens": 2**31}, {"vision_mode": "video"},
    {"max_output_tokens": -1}, {"max_output_tokens": 1.5}, {"max_output_tokens": True},
    {"input_modalities": ["unknown"]}, {"input_modalities": "text,image"},
])
async def test_invalid_model_metadata_is_not_saved(setup, invalid):
    response = await setup.client.post("/api/v1/config/models/profiles", json={**profile(), **invalid})
    assert response.status_code == 422
    assert setup.store.list_profiles() == []


@pytest.mark.asyncio
async def test_independent_modalities_output_limit_and_reset(setup):
    s = setup
    payload = {**profile("media", "openai-codex/gpt-6-astra"), "api_key": "",
               "input_modalities": ["video", "audio"], "max_output_tokens": 8192}
    assert (await s.client.post("/api/v1/config/models/profiles", json=payload)).status_code == 201
    _, engine = await engine_for(s)
    stored = s.store.get_profile("media")
    assert stored["input_modalities"] == ["video", "audio"]
    assert stored["max_output_tokens"] == 8192
    config = (await s.client.get("/api/v1/config/models")).json()["profiles"][0]
    assert config["input_modalities"] == ["video", "audio"]
    assert config["default_input_modalities"] == ["text", "image"]
    assert not engine._is_vision_capable
    assert engine._active_profile.input_modalities == ("video", "audio")
    assert engine._active_profile.max_output_tokens == 8192

    # Omitted fields survive edits by older clients. A legacy image toggle keeps audio/video.
    minimal = profile("media", payload["model"])
    minimal["api_key"] = ""
    assert (await s.client.put("/api/v1/config/models/profiles/media", json=minimal)).status_code == 200
    assert s.store.get_profile("media")["max_output_tokens"] == 8192
    assert (await s.client.put("/api/v1/config/models/profiles/media", json={**minimal, "vision_mode": "true"})).status_code == 200
    assert s.store.get_profile("media")["input_modalities"] == ["video", "audio", "image"]
    assert engine._is_vision_capable

    # Import/export carries an empty explicit selection without treating it as automatic.
    payload.update(input_modalities=[], max_output_tokens=4096)
    token = export_config({"profiles": [payload]}, mode="simple")
    assert (await s.client.post("/api/v1/config/import", json={"token": token})).status_code == 200
    _, fresh = await engine_for(s)
    assert fresh._active_profile.input_modalities == ()
    assert not fresh._is_vision_capable
    assert fresh._active_profile.max_output_tokens == 4096
    payload.update(input_modalities=None, max_output_tokens=0)
    assert (await s.client.put("/api/v1/config/models/profiles/media", json=payload)).status_code == 200
    assert engine._is_vision_capable and fresh._is_vision_capable
    assert fresh._active_profile.max_output_tokens == 0


@pytest.mark.asyncio
async def test_create_rename_switch_delete_updates_actual_sessions(setup):
    s = setup
    assert (await s.client.post("/api/v1/config/models/profiles", json=profile())).status_code == 201
    sid, engine = await engine_for(s)
    name = "供应商/model #2?"
    path = "/api/v1/config/models/profiles/" + quote(name, safe="")
    assert (await s.client.post("/api/v1/config/models/profiles", json=profile(name, "model-b"))).status_code == 201
    assert name in engine.model_names()
    assert name in [m["name"] for m in (await s.client.get("/api/v1/models")).json()["models"]]
    assert (await s.client.put("/api/v1/models/active", json={"name": name})).status_code == 200
    assert engine.current_model == "model-b"
    assert s.user.get_active_model() == name
    assert (await s.client.put(path, json=profile("renamed", "model-c"))).status_code == 200
    assert engine.current_model_name == "renamed"
    assert engine.current_model == "model-c"
    _, fresh = await engine_for(s)
    assert fresh.current_model_name == "renamed"
    assert (await s.client.delete("/api/v1/config/models/profiles/renamed")).status_code == 200
    assert engine.current_model_name == fresh.current_model_name == "first"
    assert (await s.client.delete("/api/v1/config/models/profiles/first")).status_code == 200
    assert (await s.client.get("/api/v1/models")).json()["models"] == []
    assert s.user.get_active_model() is None
    assert s.config.model == s.config.api_key == s.config.base_url == ""
    assert get_runtime().config_incomplete
    assert (await s.manager.get_session_detail(sid))["current_model_name"] is None
    s.manager._refresh_engine_model_profiles(engine)
    assert engine.model_names() == []


@pytest.mark.asyncio
async def test_import_activates_first_profile_and_reloads_active_client(setup):
    s = setup
    token = export_config({"profiles": [profile()]}, mode="simple")
    assert (await s.client.post("/api/v1/config/import", json={"token": token})).status_code == 200
    assert s.user.get_active_model() == "first"
    _, engine = await engine_for(s)
    token = export_config({"profiles": [profile(model="model-updated"), profile("second")]}, mode="simple")
    assert (await s.client.post("/api/v1/config/import", json={"token": token})).status_code == 200
    assert engine.current_model == s.config.model == "model-updated"
    assert "second" in engine.model_names()
    assert engine._client.model == "model-updated"


@pytest.mark.asyncio
async def test_description_can_be_explicitly_cleared(setup):
    s = setup
    payload = {**profile(), "description": "自定义描述"}
    assert (await s.client.post("/api/v1/config/models/profiles", json=payload)).status_code == 201
    # 省略 description 保留原值；显式空字符串清除原值。
    assert (await s.client.put("/api/v1/config/models/profiles/first", json=profile())).status_code == 200
    assert s.store.get_profile("first")["description"] == "自定义描述"
    assert (await s.client.put("/api/v1/config/models/profiles/first", json={**profile(), "description": ""})).status_code == 200
    assert (await s.client.get("/api/v1/models")).json()["models"][0]["description"] == ""


@pytest.mark.asyncio
async def test_invalid_import_does_not_leave_unannounced_partial_profiles(setup):
    s = setup
    token = export_config({"profiles": [profile(), profile("placeholder", "test-model")]}, mode="simple")
    assert (await s.client.post("/api/v1/config/import", json={"token": token})).status_code == 400
    assert s.store.list_profiles() == []
    assert s.config.models == ()


@pytest.mark.asyncio
async def test_partial_import_reports_saved_names_and_synchronizes_them(setup, monkeypatch):
    s = setup
    await s.client.post("/api/v1/config/models/profiles", json=profile())
    _, engine = await engine_for(s)
    add = s.store.add_profile
    monkeypatch.setattr(s.store, "add_profile", lambda **kw: False if kw["name"] == "broken" else add(**kw))
    token = export_config({"profiles": [profile("saved"), profile("broken")]}, mode="simple")
    response = await s.client.post("/api/v1/config/import", json={"token": token})
    assert response.status_code == 500
    assert response.json()["imported"] == {"profiles": ["saved"]}
    assert "saved" in engine.model_names()
    assert "broken" not in engine.model_names()


@pytest.mark.asyncio
async def test_failures_do_not_report_success(setup, monkeypatch):
    s = setup
    await s.client.post("/api/v1/config/models/profiles", json=profile())
    await s.client.post("/api/v1/config/models/profiles", json=profile("second"))
    _, engine = await engine_for(s)
    monkeypatch.setattr(engine, "switch_model", lambda name: "未找到模型")
    response = await s.client.put("/api/v1/models/active", json={"name": "second"})
    assert response.status_code == 409
    assert s.user.get_active_model() == engine.current_model_name == "first"
    assert (await s.client.put("/api/v1/models/active", json={"name": "openai-codex/not-saved"})).status_code == 404
    assert (await s.client.put("/api/v1/config/models/profiles/first", json=profile("second"))).status_code == 409
    monkeypatch.setattr(s.store, "update_profile", lambda *a, **kw: False)
    assert (await s.client.put("/api/v1/config/models/profiles/first", json=profile())).status_code == 500


@pytest.mark.asyncio
async def test_oauth_autocreate_restore_and_disconnect_sync_existing_engine(setup, monkeypatch):
    from excelmanus.auth.router import _auto_add_subscription_models, _sync_subscription_sessions
    from excelmanus.auth.providers.openai_codex import OpenAICodexProvider

    s = setup
    await s.client.post("/api/v1/config/models/profiles", json=profile())
    _, engine = await engine_for(s)
    credentials = MagicMock()
    credentials.get_active_profile.return_value = SimpleNamespace(access_token="fake-oauth-token")
    s.manager.set_credential_store(credentials)
    s.app.state.credential_store = credentials
    monkeypatch.setattr("excelmanus.auth.providers.registry.list_all", lambda: {"codex": OpenAICodexProvider()})
    request = Request({"type": "http", "app": s.app})
    assert await _auto_add_subscription_models(request, "openai-codex")
    await _sync_subscription_sessions(request)
    codex = OpenAICodexProvider._DEFAULT_PROFILE_NAME
    assert codex in engine.model_names()
    assert (await s.client.put("/api/v1/models/active", json={"name": codex})).status_code == 200
    assert engine._active_api_key == "fake-oauth-token"
    # OAuth 身份由 model 也能识别，用户重命名档案不应丢失凭证。
    renamed = profile("my-subscription", codex)
    renamed["api_key"] = ""
    renamed.update(max_context_tokens=65536, vision_mode="false")
    assert (await s.client.put("/api/v1/config/models/profiles/" + codex, json=renamed)).status_code == 200
    assert not await _auto_add_subscription_models(request, "openai-codex")
    await _sync_subscription_sessions(request)
    # 新会话从 DB 恢复激活档案，不得用空的 api_key 覆盖注入凭证。
    _, fresh = await engine_for(s)
    assert fresh._active_api_key == "fake-oauth-token"
    assert engine.max_context_tokens == fresh.max_context_tokens == 65536
    assert not engine._is_vision_capable and not fresh._is_vision_capable
    credentials.get_active_profile.return_value = None
    await _sync_subscription_sessions(request)
    assert engine._active_api_key == fresh._active_api_key == ""
