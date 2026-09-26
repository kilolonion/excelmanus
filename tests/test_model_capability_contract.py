"""Evidence and wire-level regressions from the 2026-09-25 capability audit."""
import asyncio
import base64
import io
import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
from PIL import Image

from excelmanus.capability_identity import capability_scope
from excelmanus.engine_types import ThinkingConfig
from excelmanus.model_catalog import catalog, capability_metadata, local_context_budget, model_spec, recommended_models, model_list_entry
from excelmanus.model_probe import ModelCapabilities, probe_tool_calling, probe_vision, probe_thinking, save_capabilities, load_capabilities, _mark_freshness
from excelmanus.providers.request_body import compile_provider_body
from excelmanus.providers.thinking import compile_thinking
from excelmanus.vision_capability import infer_vision_capable


def response(content="", calls=None):
    return NS(choices=[NS(message=NS(content=content, tool_calls=calls))])


def client(create):
    return NS(chat=NS(completions=NS(create=create)))


def test_every_published_record_has_source_and_date():
    ids = set()
    for record in catalog()["models"]:
        assert record["id"] not in ids
        ids.add(record["id"])
        assert record["verified_at"] == "2026-09-25"
        assert all(url.startswith("https://") for url in record["source_urls"])
        assert record["source_urls"]
    for preset in catalog()["providers"].values():
        doc = model_spec(preset["model"], preset["base_url"], route_only=True)
        assert doc and doc["status"] == "active" and doc["tool_calling"] is True


@pytest.mark.parametrize("model,window,budget", [
    ("qwen-max",32768,30720), ("qwen-turbo",131072,98304),
    ("qwen-long",10000000,1000000), ("claude-sonnet-4-6",1000000,1000000),
    ("doubao-seed-2-1-pro-260915",1048576,1048576),
    ("doubao-seed-2-1-pro-260628",262144,262144),
])
def test_model_window_and_transport_budget_are_distinct(model,window,budget):
    assert model_spec(model)["context_window"] == window
    assert local_context_budget(model) == budget


def test_unverified_identifiers_never_inherit_capabilities():
    for model in ["gpt-4.10-mystery", "gpt-5-new-unknown", "antigravity/gemini-pro-agent", "workbuddy-cn/auto"]:
        meta = capability_metadata(model)
        assert meta["source"] == "unknown"
        assert meta["endpoint_verified"] is False
        assert meta["local_context_budget"] == 32000
    assert not infer_vision_capable("unverified-alias", canonical_model="gpt-4o")
    assert not infer_vision_capable("gpt-5.3-codex-spark")


def test_endpoint_observation_has_priority_in_both_directions():
    assert infer_vision_capable("gpt-4o",probe=False) is False
    assert infer_vision_capable("qwen-coder-plus",probe=True) is True
    assert infer_vision_capable("gpt-4o",probe=False,override="true") is True


def test_regional_and_aggregator_scope():
    assert model_spec("qwen-max","https://dashscope.aliyuncs.com/compatible-mode/v1")["tool_calling"]
    assert model_spec("qwen-max","https://dashscope-intl.aliyuncs.com/compatible-mode/v1")["tool_calling"] is False
    assert model_spec("claude-sonnet-4","https://gateway.example",route_only=True) is None
    assert model_spec("anthropic/claude-sonnet-4","https://openrouter.ai/api/v1",route_only=True)["status"] == "active"


def test_catalog_filters_known_non_agents_and_preserves_remote_evidence():
    candidates={x["id"] for x in recommended_models("https://dashscope.aliyuncs.com/compatible-mode/v1")}
    assert not candidates.intersection({"qwen-long","qwen-turbo","qwen-coder-plus"})
    assert model_list_entry("text-embedding-3-small","https://api.openai.com/v1")["agent_eligible"] is False
    entry=model_list_entry("unknown-model","https://gateway.example",{"context_length":8192})
    assert entry["remote_declarations"] == {"context_length":8192}
    assert entry["capability_metadata"]["source"] == "unknown"


@pytest.mark.asyncio
async def test_plain_text_response_does_not_pass_tool_or_vision_probe():
    c=client(AsyncMock(return_value=response("I cannot see images or use tools.")))
    assert (await probe_tool_calling(c,"unknown"))[0] is None
    assert (await probe_vision(c,"unknown"))[0] is None


@pytest.mark.asyncio
async def test_tool_probe_requires_validated_roundtrip():
    seen=[]
    async def create(**kw):
        seen.append(kw)
        if len(seen)==1:
            return response(calls=[NS(id="call_test",function=NS(name="test_add",arguments='{"a":2,"b":3}'))])
        result=json.loads(kw["messages"][-1]["content"])
        return response(result["verification_code"])
    assert await probe_tool_calling(client(create),"unknown") == (True,"")
    assert len(seen)==2
    assert seen[1]["messages"][-1]["tool_call_id"]=="call_test"


@pytest.mark.asyncio
async def test_wrong_tool_arguments_cannot_pass():
    c=client(AsyncMock(return_value=response(calls=[NS(id="call",function=NS(name="test_add",arguments='{"a":200,"b":3}'))])))
    assert (await probe_tool_calling(c,"unknown"))[0] is None
    assert c.chat.completions.create.await_count==1


@pytest.mark.asyncio
async def test_image_probe_scores_content_not_http_success():
    async def create(**kw):
        data=kw["messages"][0]["content"][1]["image_url"]["url"].split(",")[1]
        img=Image.open(io.BytesIO(base64.b64decode(data)))
        names={(255,0,0):"red",(0,255,0):"green",(0,0,255):"blue",(255,255,0):"yellow"}
        return response(names[img.getpixel((0,0))]+","+names[img.getpixel((95,0))])
    assert await probe_vision(client(create),"unknown") == (True,"")


@pytest.mark.asyncio
async def test_explicit_dialect_no_longer_fakes_a_success():
    async def stream():
        yield NS(choices=[NS(delta=NS(content="391"))])
    create=AsyncMock(return_value=stream())
    result=await probe_thinking(client(create),"unknown","https://gateway.example",thinking_mode="enable_thinking")
    assert result[0] is None
    assert create.await_count==1


@pytest.mark.parametrize("model,url,mode,expected",[
    ("kimi-k2.6","https://api.moonshot.cn/v1","auto",{"thinking":{"type":"disabled"}}),
    ("glm-4.7","https://open.bigmodel.cn/api/paas/v4","auto",{"thinking":{"type":"disabled"}}),
    ("mimo-v2.6-flash","https://api.xiaomimimo.com/v1","auto",{"thinking":{"type":"disabled"}}),
    ("deepseek-flash","https://api.deepseek.com/v1","auto",{"thinking":{"type":"disabled"}}),
    ("qwen-plus","https://dashscope.aliyuncs.com/compatible-mode/v1","auto",{"enable_thinking":False}),
    ("any","https://openrouter.ai/api/v1","openrouter",{"reasoning":{"enabled":False}}),
])
def test_none_sends_explicit_disable(model,url,mode,expected):
    assert compile_thinking(model,url,"openai",mode,ThinkingConfig(effort="none"))["extra_body"] == expected


def test_gemini_compat_default_survives_final_wire_compilation():
    extra=compile_thinking("gemini-2.5-flash","https://generativelanguage.googleapis.com/v1beta/openai","openai","auto",ThinkingConfig(effort="none"))
    wire=compile_provider_body("openai",dict(model="gemini-2.5-flash",messages=[],**extra))
    config=wire["extra_body"]["extra_body"]["google"]["thinking_config"]
    assert config=={"include_thoughts":False,"thinking_budget":0}


def test_native_gemini_zero_budget_is_not_dropped():
    extra=compile_thinking("gemini-2.5-flash","https://generativelanguage.googleapis.com","gemini","auto",ThinkingConfig(effort="none"))
    body=compile_provider_body("gemini",dict(model="gemini-2.5-flash",messages=[],**extra))
    assert body["generationConfig"]["thinkingConfig"]["thinkingBudget"]==0


def test_model_effort_validation_and_always_on_reasoning():
    kw=compile_thinking("gpt-5.2-codex","https://api.openai.com/v1","openai_responses","auto",ThinkingConfig(effort="max"))
    assert kw["reasoning_effort"]=="xhigh"
    for model,url in [("gemini-2.5-pro","https://generativelanguage.googleapis.com"),("gpt-5","https://api.openai.com/v1"),("claude-opus-5-5","https://api.anthropic.com")]:
        with pytest.raises(ValueError,match="不支持关闭"):
            compile_thinking(model,url,"openai","disabled",ThinkingConfig())


@pytest.mark.parametrize("protocol",["openai","openai_responses","anthropic","gemini","antigravity"])
def test_audio_and_video_are_rejected_not_silently_dropped(protocol):
    for part in [{"type":"input_audio","input_audio":{"data":"AAAA","format":"wav"}},{"type":"video_url","video_url":{"url":"https://example.com/video"}}]:
        with pytest.raises(ValueError,match="音视频"):
            compile_provider_body(protocol,{"model":"unknown","messages":[{"role":"user","content":[part]}]})


def test_scopes_separate_keys_protocols_headers_and_configuration():
    args=dict(protocol="openai",api_key="secret",base_url="https://example.com/v1",model="m")
    original=capability_scope(**args)
    for changed in [dict(api_key="other"),dict(protocol="openai_responses"),dict(headers={"tenant":"two"}),dict(thinking_mode="disabled"),dict(extra_body='{"thinking":{}}')]:
        assert original!=capability_scope(**{**args,**changed})
    assert "secret" not in original


def test_cache_cannot_cross_identity_or_canonical_alias(tmp_path):
    from excelmanus.database import Database
    db=Database(str(tmp_path/"caps.db"))
    try:
        caps=ModelCapabilities(model="gpt-4o",base_url="https://example.com/v1",cache_scope="scope-a",supports_vision=True)
        _mark_freshness(caps,source="manual_probe")
        save_capabilities(db,caps)
        assert load_capabilities(db,caps.model,caps.base_url,scope="scope-a") is not None
        assert load_capabilities(db,caps.model,caps.base_url,scope="scope-b") is None
        assert load_capabilities(db,"alias",caps.base_url,canonical_model="gpt-4o",scope="scope-a") is None
    finally: db.close()


def test_expired_manual_record_preserves_only_user_declared_dimensions(tmp_path):
    from excelmanus.database import Database
    from excelmanus.capability_identity import active_observation
    db=Database(str(tmp_path/"expired.db"))
    try:
        caps=ModelCapabilities(model="m",base_url="https://example.com",cache_scope="a",healthy=True,
            supports_vision=True,supports_tool_calling=True,manual_override=True,
            evidence={"supports_vision":"user_override","supports_tool_calling":"tool_roundtrip"},
            fresh_until="2000-01-01T00:00:00+00:00")
        save_capabilities(db,caps)
        restored=load_capabilities(db,"m","https://example.com",scope="a")
        assert restored.supports_vision is True
        assert restored.supports_tool_calling is None
        assert restored.healthy is None
        assert active_observation(NS(_model_capabilities=caps,capability_scope="b")) is None
    finally: db.close()
