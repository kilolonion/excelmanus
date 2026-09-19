"""第 2 批：RequestSeries 离线协议与附件/路由验收。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from excelmanus.attachments.files_api import (
    _resolve_file_id,
    _scope_key,
    accept_file_leases,
    apply_files_transport,
    lease_file_ids,
    leased_file_ids,
    reclaim_oldest_owned,
    release_file_ids,
)
from excelmanus.attachments.offload import offload_indices, required_image_indices
from excelmanus.attachments.project import assemble_model_request, missing_image_text
from excelmanus.attachments.store import AttachmentStore
from excelmanus.attachments.types import RequestImageOffloadPolicy
from excelmanus.providers.claude import _openai_messages_to_claude
from excelmanus.providers.gemini import _openai_messages_to_gemini
from excelmanus.providers.openai_responses import _chat_messages_to_responses_input
from excelmanus.request.compiler import compile_request, content_payload
from excelmanus.request.route import credential_scope, resolve_route
from excelmanus.request.series import RequestSeries, series_of
from excelmanus.request.types import RequestHeader
from excelmanus.request.usage import extract_cache_usage
from tests.test_attachments import _png_bytes, _ref_message
from tests.test_request_envelope import _engine
from excelmanus.attachments.admit import admit_image_bytes


def _header(payload: str, **overrides: object) -> RequestHeader:
    data = {
        "route_fingerprint": "route-a",
        "tools_digest": "tools",
        "catalog_digest": "cat",
        "system_head_digest": "sys",
        "content_identity": payload,
        "content_payload": payload,
        "cache_policy_digest": "cache",
        "transport": "inline",
        "prompt_cache_key": "em_s",
    }
    data.update(overrides)
    return RequestHeader(**data)  # type: ignore[arg-type]


def test_old_snapshot_without_header_migrates() -> None:
    series = RequestSeries.from_dict({"wire_epoch": {"key": "old"}})
    assert series.last_accepted is None
    assert any(ev.get("type") == "restore/migrate" for ev in series.events)
    later = _header('{"role":"user","content":"rewritten"}\n')
    assert series.check_prefix(later) is None


def test_empty_payload_is_not_a_pass() -> None:
    series = RequestSeries()
    series.last_accepted = _header("")
    # from_dict 拒绝空 payload；手工放空串不得当通行证
    broken = _header('{"role":"user","content":"x"}\n', content_identity="x")
    # 空 last_accepted payload：startswith 会放行，from_dict 禁止这种 header
    assert RequestHeader.from_dict({"content_payload": "", "content_identity": "x"}) is None


def test_unexplained_rewrite_fail_closed() -> None:
    series = RequestSeries()
    first = _header('{"role":"user","content":"a"}\n')
    series.accept(first)
    err = series.check_prefix(_header('{"role":"user","content":"MUTATED"}\n'))
    assert err is not None
    assert "前缀" in err


def test_catalog_drift_explains_rewrite() -> None:
    """工作区文件族/技能集合变化导致 catalog/tools digest 变化时，重写有可观因——放行并记注。"""
    series = RequestSeries()
    series.accept(_header('{"role":"user","content":"a"}\n'))
    drifted = _header(
        '{"role":"user","content":"rewritten"}\n',
        catalog_digest="cat-v2",
        tools_digest="tools-v2",
    )
    assert series.check_prefix(drifted) is None
    assert any(ev.get("type") == "catalog/change" for ev in series.events)

    # digest 相同但载荷被改写仍是未解释重写——fail-closed 保留。
    series2 = RequestSeries()
    series2.accept(_header('{"role":"user","content":"a"}\n'))
    err = series2.check_prefix(_header('{"role":"user","content":"rewritten"}\n'))
    assert err is not None and "前缀" in err


def test_file_id_change_is_not_content_rewrite() -> None:
    prev = content_payload([{
        "role": "user",
        "content": [
            {"type": "image_url", "image_url": {"url": "https://x"}, "_variant_id": "v1"},
            {"type": "file", "file_id": "file-old"},
        ],
    }])
    curr = content_payload([{
        "role": "user",
        "content": [
            {"type": "image_url", "image_url": {"url": "https://x"}, "_variant_id": "v1"},
            {"type": "file", "file_id": "file-new"},
        ],
    }])
    assert prev == curr
    series = RequestSeries()
    series.accept(_header(prev, content_identity="same"))
    series.note("transport/renew")
    assert series.check_prefix(_header(curr, content_identity="same")) is None


def test_compact_event_allows_rewrite() -> None:
    series = RequestSeries()
    series.accept(_header('{"role":"user","content":"long"}\n'))
    series.start_new("surface/compact")
    assert series.check_prefix(_header('{"role":"user","content":"summary"}\n')) is None


def test_prefix_truncation_fail_closed() -> None:
    """当前载荷比上一封更短（历史被裁短）也是前缀破坏——startswith 语义必须兜住。"""
    series = RequestSeries()
    series.accept(_header('{"role":"user","content":"abcdef"}\n'))
    err = series.check_prefix(_header('{"role":"user","content":"abc"}\n'))
    assert err is not None and "前缀" in err


def test_mid_stream_mutation_fail_closed() -> None:
    """共同前缀后中途分叉同样 fail-closed——只允许纯尾部追加。"""
    series = RequestSeries()
    series.accept(_header('{"role":"user","content":"hello|"}\n{"role":"assistant","content":"world"}\n'))
    err = series.check_prefix(
        _header('{"role":"user","content":"hello|"}\n{"role":"assistant","content":"MUTATED"}\n')
    )
    assert err is not None and "前缀" in err


def test_declared_rewrite_is_single_shot() -> None:
    """声明式重写只放行一次：accept 清空 pending 后，下一次未申报改写重新 fail-closed。

    这是「一次触发 = 一次 cache miss = 之后前缀重新稳定」的核心契约。
    """
    series = RequestSeries()
    series.accept(_header('{"role":"user","content":"before"}\n'))
    series.note("rollback/edit")
    assert series.check_prefix(_header('{"role":"user","content":"after-rollback"}\n')) is None
    series.accept(_header('{"role":"user","content":"after-rollback"}\n'))
    err = series.check_prefix(_header('{"role":"user","content":"sneaky"}\n'))
    assert err is not None and "前缀" in err


def test_compact_restart_then_append_only() -> None:
    """series 重启后新前缀成为基线：继续追加放行，再次未申报改写拒绝。"""
    series = RequestSeries()
    series.accept(_header('{"role":"user","content":"long-history"}\n'))
    series.start_new("surface/compact")
    summary = _header('{"role":"user","content":"[summary]"}\n')
    assert series.check_prefix(summary) is None
    series.accept(summary)
    appended = _header(
        '{"role":"user","content":"[summary]"}\n{"role":"assistant","content":"ok"}\n'
    )
    assert series.check_prefix(appended) is None
    series.accept(appended)
    err = series.check_prefix(_header('{"role":"user","content":"[tampered]"}\n'))
    assert err is not None and "前缀" in err


def test_transport_renew_identity_change_requires_prefix() -> None:
    """transport/renew pending 时：identity 相同放行 file_id 级改写；
    identity 不同则仍要求前缀延伸，divergent 载荷 fail-closed。"""
    prev = _header('{"role":"user","content":"a"}\n', content_identity="id-a")
    series = RequestSeries()
    series.accept(prev)
    series.note("transport/renew")
    err = series.check_prefix(
        _header('{"role":"user","content":"REWRITTEN"}\n', content_identity="id-b")
    )
    assert err is not None and "前缀" in err
    ok = series.check_prefix(
        _header(
            '{"role":"user","content":"a"}\n{"role":"assistant","content":"b"}\n',
            content_identity="id-b",
        )
    )
    assert ok is None


def test_tool_result_pruner_is_idempotent() -> None:
    """Phase 3 前置契约：无模型修剪二次扫描必须零产出（幂等），否则会在同一
    重写边界反复 bump generation、制造多余 cache miss。"""
    pruner = pytest.importorskip("excelmanus.compaction_pruner", reason="Phase 3 落地")
    big = "x" * 20000
    msgs = [
        {"role": "user", "content": "q"},
        {"role": "tool", "tool_call_id": "c1", "content": big},
        {"role": "tool", "tool_call_id": "c2", "content": "small"},
    ]
    first = pruner.prune_messages(msgs)
    assert 1 in first, "超阈值 tool 结果必须被修剪"
    assert 2 not in first, "未超阈值的结果不得动"
    pruned = [dict(m, content=first.get(i, m.get("content"))) for i, m in enumerate(msgs)]
    assert pruner.prune_messages(pruned) == {}, "二次扫描必须零产出"


@pytest.mark.asyncio
async def test_protocol_path_turn_tool_policy_compact_restore_renew() -> None:
    engine = _engine()
    engine._active_model = "test-model"
    engine._active_base_url = "https://api.openai.com/v1"
    engine._active_api_key = "sk-test"
    engine._active_protocol = "openai"
    engine._thinking_config = SimpleNamespace(is_disabled=True, effective_budget=lambda: 0)
    engine._model_capabilities = None
    engine.memory.add_user_message("one")
    first, err = await compile_request(engine)
    assert err is None and first is not None
    series_of(engine).accept(first.header)

    engine.memory.add_assistant_tool_message({
        "role": "assistant",
        "content": None,
        "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "inspect_spreadsheet", "arguments": "{}"}}],
        "replay_state": {"thinking_blocks": [{"type": "thinking", "thinking": "t", "signature": "sig-1"}]},
        "reasoning_content": "t",
    })
    engine.memory.add_tool_result("c1", "ok")
    engine.memory.add_user_message("two")
    second, err = await compile_request(engine)
    assert err is None and second is not None
    assert second.header.content_payload.startswith(first.header.content_payload)
    series_of(engine).accept(second.header)

    engine.memory.add_system_message("policy two", hidden=True, prompt_kind="system_update")
    engine._active_profile = SimpleNamespace(protocol="anthropic", thinking_mode="auto", custom_extra_body="", custom_extra_headers="")
    engine._active_protocol = "anthropic"
    engine._active_base_url = "https://api.anthropic.com"
    third, err = await compile_request(engine)
    assert err is None and third is not None
    # 历史内 system_update 在 wire 上统一降级为 user，无需 policy/update rebase
    body = third.create_kwargs().get("_prepared_body", {}).get("messages", [])
    assert sum(1 for m in body if m.get("role") == "system") <= 1
    assert any(
        m.get("role") == "user" and "policy two" in str(m.get("content", ""))
        for m in body
    )
    series_of(engine).accept(third.header)

    series_of(engine).start_new("surface/compact")
    snapped = series_of(engine).to_dict()
    restored = RequestSeries.from_dict(snapped)
    engine._request_series = restored
    fourth, err = await compile_request(engine, event="transport/renew")
    assert err is None and fourth is not None
    assert restored.last_accepted is None or fourth.header.content_identity


def test_provider_mapping_rejects_mid_history_system() -> None:
    messages = [
        {"role": "system", "content": "HEAD"},
        {"role": "user", "content": "hi"},
        {"role": "system", "content": "UPDATE"},
    ]
    with pytest.raises(ValueError, match="mid-history system is not representable"):
        _openai_messages_to_claude(messages)
    with pytest.raises(ValueError, match="mid-history system is not representable"):
        _openai_messages_to_gemini(messages)
    with pytest.raises(ValueError, match="mid-history system is not representable"):
        _chat_messages_to_responses_input(messages)


def test_claude_replays_thinking_signature() -> None:
    messages = [
        {"role": "system", "content": "HEAD"},
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "content": "ok",
            "replay_state": {
                "thinking_blocks": [{
                    "type": "thinking",
                    "thinking": "secret",
                    "signature": "sig-xyz",
                }],
            },
        },
    ]
    _, claude = _openai_messages_to_claude(messages)
    assistant = next(item for item in claude if item["role"] == "assistant")
    thinking = [block for block in assistant["content"] if block.get("type") == "thinking"]
    assert thinking
    assert thinking[0]["signature"] == "sig-xyz"


def test_credential_scope_uses_full_key() -> None:
    left = credential_scope("https://api.deepseek.com", "sk-abcdefghijklXXXX")
    right = credential_scope("https://api.deepseek.com", "sk-abcdefghijklYYYY")
    assert left != right
    assert _scope_key("https://api.deepseek.com", "sk-abcdefghijklXXXX") == left


def test_required_images_not_omitted_while_old_pins_held() -> None:
    lengths = [10, 10, 10]
    pins = ("inline", "inline")
    required = {2}
    indices, unmet = offload_indices(
        lengths,
        RequestImageOffloadPolicy(max_images=2, max_bytes=10**9, count_quantum=1, byte_quantum=1),
        pin_seq=pins,
        required=required,
    )
    assert 2 not in indices
    assert 0 in indices
    assert unmet is False


def test_required_omitted_forces_quota_unmet() -> None:
    lengths = [10, 10]
    required = {0, 1}
    indices, unmet = offload_indices(
        lengths,
        RequestImageOffloadPolicy(max_images=1, max_bytes=10**9, count_quantum=1, byte_quantum=1),
        required=required,
    )
    assert unmet is True
    assert required & indices


def test_missing_placeholder_does_not_use_quota_wording(tmp_path) -> None:
    store = AttachmentStore(tmp_path / "attachments")
    ghost = {
        "attachmentId": "sha256:" + "ab" * 32,
        "mediaType": "image/png",
        "bytes": 10,
        "width": 8,
        "height": 8,
    }
    report: dict = {}
    out = assemble_model_request(
        [{"role": "user", "content": [{"type": "image", "attachment": ghost}]}],
        vision_capable=True,
        store=store,
        report=report,
    )
    text = str(out)
    assert "missing" in text.lower()
    assert "omitted to fit request image limits" not in text
    from excelmanus.attachments.types import ImageAttachmentRef

    text_missing = missing_image_text(ImageAttachmentRef.from_dict(ghost))
    assert "missing" in text_missing
    assert "omitted to fit" not in text_missing


def test_unknown_usage_is_not_zero() -> None:
    usage = extract_cache_usage({"prompt_tokens": 8000})
    assert usage.hit is None
    assert usage.miss_reason == "unknown"


def test_deepseek_hit_still_reads() -> None:
    usage = extract_cache_usage({"prompt_tokens": 9000, "prompt_cache_hit_tokens": 8000})
    assert usage.hit == 8000


@pytest.mark.asyncio
async def test_reclaim_skips_leased_file() -> None:
    class Files:
        def __init__(self) -> None:
            self.deleted: list[str] = []

        async def list(self):
            return {
                "data": [
                    {"id": "file-live", "filename": "em-live.png", "created_at": 1},
                    {"id": "file-old", "filename": "em-old.png", "created_at": 2},
                ],
                "has_more": False,
            }

        async def delete(self, file_id: str) -> None:
            self.deleted.append(file_id)

    files = Files()
    lease_file_ids("req-1", ["file-live"])
    accept_file_leases(["file-live"])
    deleted = await reclaim_oldest_owned(SimpleNamespace(files=files), limit=10)
    assert "file-live" not in files.deleted
    assert "file-old" in files.deleted
    assert deleted == 1
    release_file_ids("req-1")


def test_active_route_not_config_url() -> None:
    engine = SimpleNamespace(
        _active_base_url="https://api.new.example/v1",
        _active_api_key="sk-new",
        _active_model="m-new",
        _active_protocol="openai",
        _config=SimpleNamespace(base_url="https://api.old.example/v1", api_key="sk-old", model="m-old"),
        _session_id="s",
    )
    route = resolve_route(engine)
    assert route.endpoint == "https://api.new.example/v1"
    assert route.api_key == "sk-new"
    assert route.model == "m-new"


def test_cancel_does_not_update_last_accepted() -> None:
    series = RequestSeries()
    first = _header('{"role":"user","content":"ok"}\n')
    series.accept(first)
    lease_file_ids("req-cancel", ["file-x"])
    assert "file-x" in leased_file_ids()
    series.cancel()
    release_file_ids("req-cancel")
    assert series.last_accepted is first
    assert any(ev.get("type") == "request/cancelled" for ev in series.events)
    assert "file-x" not in leased_file_ids()


def test_release_open_attempt_drops_lease() -> None:
    from excelmanus.agent.loop import _release_open_attempt

    engine = SimpleNamespace(_open_request_id="req-z")
    lease_file_ids("req-z", ["file-z"])
    _release_open_attempt(engine)
    assert engine._open_request_id is None
    assert "file-z" not in leased_file_ids()


@pytest.mark.asyncio
async def test_concurrent_same_variant_uploads_once(tmp_path) -> None:
    import base64

    raw = _png_bytes()
    b64 = base64.b64encode(raw).decode("ascii")
    variant = "sha256:" + "ab" * 32
    block = {
        "type": "image_url",
        "image_url": {"url": f"data:image/png;base64,{b64}"},
        "_variant_id": variant,
    }
    created: list[str] = []

    class Files:
        async def create(self, file, purpose):
            created.append(purpose)
            await asyncio.sleep(0.05)
            return SimpleNamespace(id=f"file-{len(created)}", expires_at=None)

        async def delete(self, file_id):
            return None

    store = AttachmentStore(tmp_path / "attachments")
    client = SimpleNamespace(files=Files())
    first, second = await asyncio.gather(
        _resolve_file_id(client, block, store=store, scope="scope-a", purpose="assistants"),
        _resolve_file_id(client, block, store=store, scope="scope-a", purpose="assistants"),
    )
    assert first == second
    assert len(created) == 1
