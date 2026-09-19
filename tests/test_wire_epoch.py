"""A1：出网载荷唯一真相 + 缓存域身份 + 剥参粘性 + 投影去宿主路径。"""

from __future__ import annotations

import json
import logging
import re
from types import SimpleNamespace

import pytest

from excelmanus.attachments.admit import admit_image_bytes
from excelmanus.attachments.project import assemble_model_request
from excelmanus.attachments.store import AttachmentStore, reset_attachment_store
from excelmanus.attachments.types import ImageRequestPolicy, RequestImageOffloadPolicy
from excelmanus.engine_core.llm_caller import (
    LLMCaller,
    degraded_params,
    mark_degraded,
    reset_degraded_params,
)
from excelmanus.prompt.envelope import (
    assert_wire_prefix_stable,
    canonical_json,
    canonical_wire_payload,
    compute_epoch_identity,
    epoch_changed,
    protocol_from_engine,
    seal_envelope,
)
from excelmanus.request.types import PreparedRequest
from tests.test_attachments import _png_bytes, _ref_message
from tests.test_request_envelope import _engine


HOST_DRIVE = re.compile(r"[A-Za-z]:\\")
HOST_USERS = "/Users/"
HOST_HOME = "/home/"


def _identity(**overrides: object):
    payload: dict[str, object] = {
        "session_id": "s1",
        "model": "m1",
        "protocol": "openai|https://x.example/v1",
        "call_config": {"temperature": 0.2, "max_tokens": 1024},
        "tools": [{
            "type": "function",
            "function": {"name": "inspect_spreadsheet", "description": "d", "parameters": {}},
        }],
        "system": "SYS",
        "catalog_digest": "cat-1",
        "wire_payload": [{"role": "user", "content": "a"}],
    }
    payload.update(overrides)
    return compute_epoch_identity(**payload)  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def _reset_sticky_degrade() -> None:
    reset_degraded_params()
    yield
    reset_degraded_params()


def test_same_epoch_rewritten_wire_fails_with_prefix_message() -> None:
    prev = _identity(wire_payload=[{"role": "user", "content": "a"}])
    curr = _identity(wire_payload=[{"role": "user", "content": "MUTATED"}])
    assert epoch_changed(prev, curr) is False
    err = assert_wire_prefix_stable(
        [{"role": "user", "content": "a"}],
        [{"role": "user", "content": "MUTATED"}],
        prev_epoch=prev,
        curr_epoch=curr,
    )
    assert err is not None
    assert "前缀" in err


def test_same_epoch_wire_prefix_extension_returns_none() -> None:
    prev_msgs = [{"role": "user", "content": "a"}]
    curr_msgs = [
        {"role": "user", "content": "a"},
        {"role": "assistant", "content": "b"},
    ]
    prev = _identity(wire_payload=prev_msgs)
    curr = _identity(wire_payload=curr_msgs)
    assert epoch_changed(prev, curr) is False
    assert assert_wire_prefix_stable(
        prev_msgs, curr_msgs, prev_epoch=prev, curr_epoch=curr,
    ) is None
    prev_digest = canonical_wire_payload(prev_msgs)
    curr_digest = canonical_wire_payload(curr_msgs)
    assert curr_digest.startswith(prev_digest)
    assert "前缀" not in (assert_wire_prefix_stable(
        prev_digest, curr_digest, prev_epoch=prev, curr_epoch=curr,
    ) or "")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("model", "m2"),
        ("protocol", "anthropic|https://api.anthropic.com"),
        ("call_config", {"temperature": 0.9}),
        ("catalog_digest", "cat-changed"),
    ],
)
def test_epoch_changed_and_describe_change_per_dimension(field: str, value: object) -> None:
    prev = _identity()
    curr = _identity(**{field: value})
    assert epoch_changed(prev, curr) is True
    described = curr.describe_change(prev)
    if field == "call_config":
        assert "call_config_digest" in described
    else:
        assert field in described


def test_epoch_key_changes_with_model_or_catalog() -> None:
    base = _identity()
    by_model = _identity(model="m-other")
    by_catalog = _identity(catalog_digest="cat-other")
    assert base.key().startswith("em_s1-")
    assert by_model.key() != base.key()
    assert by_catalog.key() != base.key()
    # 同 epoch 维度、仅 wire 增长：缓存域 key 必须稳定
    grown = _identity(wire_payload=[
        {"role": "user", "content": "a"},
        {"role": "assistant", "content": "b"},
    ])
    assert grown.key() == base.key()
    assert epoch_changed(base, grown) is False


def test_tools_array_change_is_not_epoch_change() -> None:
    """present_as=code 只改 L4 wire：tools_digest 变，catalog_digest 不变 → 非 epoch_changed。

    assemble_envelope 仍会因 prev.tools != tools 而 starts_series。
    """
    prev = _identity()
    curr = _identity(tools=[{
        "type": "function",
        "function": {"name": "run_code", "description": "d", "parameters": {}},
    }])
    assert epoch_changed(prev, curr) is False
    assert prev.catalog_digest == curr.catalog_digest
    assert prev.tools_digest != curr.tools_digest
    assert curr.describe_change(prev).startswith("tools_digest:")


def test_canonical_json_is_key_order_stable() -> None:
    left = canonical_json({"b": 1, "a": {"z": 1.0, "m": True}})
    right = canonical_json({"a": {"m": True, "z": 1.0}, "b": 1})
    assert left == right
    assert left == '{"a":{"m":true,"z":1.0},"b":1}'


def test_different_epoch_skips_prefix_check() -> None:
    prev = _identity()
    curr = _identity(model="m2", wire_payload=[{"role": "user", "content": "MUTATED"}])
    assert epoch_changed(prev, curr) is True
    assert assert_wire_prefix_stable(
        [{"role": "user", "content": "a"}],
        [{"role": "user", "content": "MUTATED"}],
        prev_epoch=prev,
        curr_epoch=curr,
    ) is None


@pytest.mark.asyncio
async def test_mark_degraded_sticky_and_skips_param_on_later_calls(
    caplog: pytest.LogCaptureFixture,
) -> None:
    engine = SimpleNamespace(
        _client=None,
        _config=SimpleNamespace(model="test-model", base_url="https://api.openai.com/v1"),
        _active_model="test-model",
        _active_profile=SimpleNamespace(protocol="openai"),
    )
    protocol = protocol_from_engine(engine)
    calls: list[dict[str, object]] = []

    async def create(**kwargs):
        calls.append(dict(kwargs))
        if "prompt_cache_key" in kwargs:
            raise TypeError("unexpected keyword argument 'prompt_cache_key'")
        if "stream_options" in kwargs:
            raise TypeError("unexpected keyword argument 'stream_options'")
        return SimpleNamespace(ok=True)

    engine._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
    )
    caller = LLMCaller(engine)  # type: ignore[arg-type]
    with caplog.at_level(logging.WARNING, logger="excelmanus.llm_caller"):
        mark_degraded(protocol, "test-model", "prompt_cache_key")
        assert "prompt_cache_key" in degraded_params(protocol, "test-model")
        await caller.create_chat_completion_with_retry(
            {
                "model": "test-model",
                "messages": [{"role": "user", "content": "hi"}],
                "prompt_cache_key": "em_should_not_retry",
                "stream_options": {"include_usage": True},
            }
        )
    assert "prompt_cache_key" not in calls[0]
    assert "stream_options" in calls[0]
    assert "stream_options" in degraded_params(protocol, "test-model")

    calls.clear()
    prepared = getattr(engine, "_prepared_request", None)
    assert isinstance(prepared, PreparedRequest)  # W5 降级重编译已替换当前尝试
    retry_kwargs = prepared.create_kwargs()
    # 模拟编译/绑定口再次带上两个参数：粘性降级必须在发送口剥离，
    # 且不得触发"outbound != compiled attempt"守卫。
    retry_kwargs["prompt_cache_key"] = "em_should_not_retry"
    retry_kwargs["stream_options"] = {"include_usage": True}
    await caller.create_chat_completion_with_retry(retry_kwargs)
    assert len(calls) == 1
    assert "prompt_cache_key" not in calls[0]
    assert "stream_options" not in calls[0]

    warnings = [
        rec.getMessage()
        for rec in caplog.records
        if "出网参数已降级" in rec.getMessage()
    ]
    assert any("protocol=" in text and "test-model" in text and "prompt_cache_key" in text for text in warnings)
    assert sum("param=prompt_cache_key" in text for text in warnings) == 1
    assert sum("param=stream_options" in text for text in warnings) == 1


def _assert_no_host_leak(payload: object) -> None:
    dumped = json.dumps(payload, ensure_ascii=False)
    assert "readonly_path" not in dumped
    assert HOST_DRIVE.search(dumped) is None
    assert HOST_USERS not in dumped
    assert HOST_HOME not in dumped


def test_projection_json_contains_no_host_paths(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("EXCELMANUS_HOME", str(tmp_path))
    reset_attachment_store()
    store = AttachmentStore(tmp_path / "attachments")
    ref = admit_image_bytes(
        _png_bytes(),
        name=str(tmp_path / "Users" / "shot.png"),
        store=store,
    )
    vision = assemble_model_request(
        [_ref_message(ref)],
        vision_capable=True,
        store=store,
        policy=ImageRequestPolicy(max_pixels=640_000, max_bytes=1_048_576),
    )
    _assert_no_host_leak(vision)
    text_only = assemble_model_request(
        [_ref_message(ref)],
        vision_capable=False,
        store=store,
    )
    _assert_no_host_leak(text_only)
    offloaded = assemble_model_request(
        [_ref_message(ref)],
        vision_capable=True,
        store=store,
        offload=RequestImageOffloadPolicy(
            max_images=0, max_bytes=1, count_quantum=1, byte_quantum=1,
        ),
    )
    _assert_no_host_leak(offloaded)
    dumped = json.dumps(vision, ensure_ascii=False)
    assert ref.attachment_id in dumped
    assert "image/" in dumped or "sha256:" in dumped
    reset_attachment_store()


@pytest.mark.asyncio
async def test_seal_carries_wire_epoch_transport_digest() -> None:
    engine = _engine()
    engine.memory.add_user_message("hello")
    from excelmanus.prompt.envelope import assemble_envelope

    env, err = assemble_envelope(engine)
    assert err is None and env is not None
    assert env.epoch is None
    sealed, seal_err = await seal_envelope(engine, env)
    assert seal_err is None and sealed is not None
    assert sealed.wire_messages
    assert sealed.epoch is not None
    assert sealed.transport == "inline"
    assert sealed.digest_payload == canonical_wire_payload(sealed.wire_messages)
    assert sealed.prompt_cache_key == sealed.epoch.key()
    assert sealed.digest_payload.startswith(env.digest_payload) or sealed.digest_payload == env.digest_payload
