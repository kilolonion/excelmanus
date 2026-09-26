"""Exercise the public probe/save/read contract with a real SDK and mock HTTP."""
import base64
import io
import json
from unittest.mock import patch

import httpx
import pytest
from PIL import Image
from openai import AsyncOpenAI

from tests.test_model_profile_sync import setup, profile  # noqa: F401 (shared API fixture)


@pytest.mark.asyncio
async def test_probe_result_is_bound_to_saved_profile_credentials(setup):
    s = setup
    payload = {**profile("evidence", "qwen-plus"), "api_key": "key-a", "protocol": "openai",
               "custom_extra_headers": '{"x-tenant":"a"}', "custom_extra_body": '{"temperature":0.2}'}
    assert (await s.client.post("/api/v1/config/models/profiles", json=payload)).status_code == 201
    clients=[]

    def factory(api_key, base_url, protocol="auto", model="", default_headers=None):
        def respond(req):
            body=json.loads(req.content)
            assert req.headers["x-tenant"]=="a"
            if body.get("stream"):
                chunk={"id":"probe","object":"chat.completion.chunk","created":1,"model":"qwen-plus","choices":[{"index":0,"delta":{"reasoning_content":"Brief summary."},"finish_reason":None}]}
                return httpx.Response(200,headers={"content-type":"text/event-stream"},text=f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n")
            messages=body["messages"]
            message={"role":"assistant","content":"Hi"}
            if messages[-1]["role"]=="tool":
                message["content"]=json.loads(messages[-1]["content"])["verification_code"]
            elif body.get("tools"):
                assert body["temperature"]==0.2
                message.update(content=None,tool_calls=[{"id":"call_test","type":"function","function":{"name":"test_add","arguments":'{"a":2,"b":3}'}}])
            elif isinstance(messages[0]["content"],list):
                image=messages[0]["content"][1]["image_url"]["url"].split(",")[1]
                img=Image.open(io.BytesIO(base64.b64decode(image)))
                colours={(255,0,0):"red",(0,0,255):"blue",(0,255,0):"green",(255,255,0):"yellow"}
                message["content"]=f"{colours[img.getpixel((0,0))]},{colours[img.getpixel((95,0))]}"
            return httpx.Response(200,json={"id":"probe","object":"chat.completion","created":1,"model":"qwen-plus","choices":[{"index":0,"message":message,"finish_reason":"stop"}]})
        c=AsyncOpenAI(api_key=api_key,base_url=base_url,default_headers=default_headers,max_retries=0,
                      http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)))
        c._capability_identity={"api_key":api_key,"protocol":protocol,"headers":default_headers or {}}
        clients.append(c)
        return c

    try:
        with patch("excelmanus.providers.create_client",side_effect=factory):
            result=await s.client.post("/api/v1/config/models/capabilities/probe",json={"name":"evidence"})
        assert result.status_code==200, result.text
        caps=result.json()["capabilities"]
        assert caps["supports_tool_calling"] is True
        assert caps["supports_vision"] is True
        assert caps["evidence"]["supports_tool_calling"]=="tool_roundtrip"
        saved=(await s.client.get("/api/v1/config/models/capabilities",params={"name":"evidence"})).json()["capabilities"]
        assert saved["cache_scope"]==caps["cache_scope"]
        # New credentials must not inherit the observation, even on the same model/URL.
        payload["api_key"]="key-b"
        assert (await s.client.put("/api/v1/config/models/profiles/evidence",json=payload)).status_code==200
        assert (await s.client.get("/api/v1/config/models/capabilities",params={"name":"evidence"})).json()["capabilities"] is None
    finally:
        for c in clients: await c.close()


@pytest.mark.asyncio
async def test_retirement_is_vendor_scoped_and_redirects_remain_addable(setup):
    s=setup
    retired={**profile("retired","claude-sonnet-4"),"base_url":"https://api.anthropic.com","protocol":"anthropic"}
    assert (await s.client.post("/api/v1/config/models/profiles",json=retired)).status_code==422
    gateway={**retired,"name":"gateway","base_url":"https://gateway.example/v1","protocol":"openai"}
    assert (await s.client.post("/api/v1/config/models/profiles",json=gateway)).status_code==201
    alias={**profile("redirect","deepseek-v4-flash"),"base_url":"https://api.deepseek.com/v1"}
    assert (await s.client.post("/api/v1/config/models/profiles",json=alias)).status_code==201


@pytest.mark.asyncio
async def test_thinking_controls_use_the_requested_session(setup):
    from tests.test_model_profile_sync import engine_for
    s=setup
    payload={**profile("thinking", "gpt-5.2-codex"),"base_url":"https://api.openai.com/v1","protocol":"openai_responses"}
    assert (await s.client.post("/api/v1/config/models/profiles",json=payload)).status_code==201
    sid, engine=await engine_for(s)
    sid2, other=await engine_for(s)
    before=other.thinking_config.effort
    result=await s.client.get("/api/v1/thinking",params={"session_id":sid})
    assert result.json()["model_allowed_efforts"]==["low","medium","high","xhigh"]
    assert (await s.client.put("/api/v1/thinking",json={"session_id":sid,"effort":"max"})).status_code==400
    assert (await s.client.put("/api/v1/thinking",json={"session_id":sid,"effort":"none"})).status_code==400
    result=await s.client.put("/api/v1/thinking",json={"session_id":sid,"effort":"low","budget":0})
    assert result.status_code==200, result.text
    assert engine.thinking_config.effort=="low"
    assert other.thinking_config.effort==before
