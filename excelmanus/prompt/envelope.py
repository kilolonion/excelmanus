"""请求信封：唯一有权决定 wire 上的 tools + system + messages。

assemble 产出请求投影（messages）；seal 产出出网载荷（wire_messages）。
连续两步 identity 相同且 compaction / projection generation 不变时，tools /
system / 历史前缀必须字节相同。动态事实只追加到 durable 尾部。
计划模式切换只追加 system，不改 messages[0]。这是唯一路径。
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field, replace
from typing import Any

from excelmanus.attachments.project import content_has_image, strip_projection_meta
from excelmanus.engine_utils import build_mention_context_block
from excelmanus.prompt.assemble import (
    commit_prompt_dynamic,
    prepare_system_prompts_for_request,
    rollback_prompt_dynamic,
)
from excelmanus.tools.runtime import schema_tool_name

logger = logging.getLogger(__name__)


def digest_text(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]


def sort_tool_schemas(schemas: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    # Normalize the transmitted schema too, not only its digest. JSON strings in
    # assistant tool arguments are outside this function and stay byte-for-byte.
    ordered = json.loads(json.dumps(schemas or [], ensure_ascii=False, sort_keys=True))
    return sorted(ordered, key=schema_tool_name)


def digest_tools(tools: list[dict[str, Any]] | None) -> str:
    canonical = json.dumps(sort_tool_schemas(tools), ensure_ascii=False, sort_keys=True)
    return digest_text(canonical)


_JSON_SEPARATORS = (",", ":")
_EPOCH_KEY_FIELDS = (
    "session_id",
    "model",
    "protocol",
    "call_config_digest",
    "tools_digest",
    "system_digest",
    "catalog_digest",
)
_EPOCH_CHANGE_FIELDS = ("model", "protocol", "call_config_digest", "catalog_digest")
_EPOCH_DESCRIBE_FIELDS = (
    "model",
    "protocol",
    "call_config_digest",
    "catalog_digest",
    "tools_digest",
    "system_digest",
    "wire_digest",
    "session_id",
)


def _json_ready(value: Any) -> Any:
    """递归归一化，避免 tuple/float/bytes 导致 canonical JSON 抖动。"""
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError("canonical JSON 不接受非有限浮点")
        return float(format(value, ".15g"))
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value)


def canonical_json(value: Any) -> str:
    """确定性 JSON：递归 sort_keys、ensure_ascii=False、固定分隔符。"""
    return json.dumps(
        _json_ready(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=_JSON_SEPARATORS,
        allow_nan=False,
    )


def canonical_wire_payload(payload: Any) -> str:
    """出网载荷的规范化字符串。列表按 NDJSON（每条消息一行、含尾换行），可直接做前缀比较。"""
    if isinstance(payload, str):
        return payload
    if payload is None:
        return ""
    if isinstance(payload, (list, tuple)):
        if not payload:
            return ""
        return "".join(canonical_json(item) + "\n" for item in payload)
    return canonical_json(payload) + "\n"


def _canonical_endpoint(base_url: str) -> str:
    text = (base_url or "").strip().split("?", 1)[0].rstrip("/").lower()
    return text


def _infer_protocol(endpoint: str, model: str) -> str:
    host = endpoint
    lower_model = (model or "").strip().lower()
    if "cloudcode-pa.googleapis.com" in host:
        return "antigravity"
    if "generativelanguage.googleapis.com" in host or "generatecontent" in host:
        return "gemini"
    if "anthropic" in host or "api.anthropic.com" in host:
        return "anthropic"
    if lower_model.startswith(("claude-", "claude3", "claude4", "claude_")):
        return "anthropic"
    if lower_model.startswith(("gemini-", "gemini2", "gemini1", "gemini_")):
        return "gemini"
    if "backend-api" in host or "/responses" in host:
        return "openai_responses"
    return "openai"


def normalize_protocol(*, protocol: str = "", base_url: str = "", model: str = "") -> str:
    """provider/base_url 归一化标识，供 EpochIdentity.protocol 与剥参粘性共用。"""
    proto = (protocol or "").strip().lower()
    endpoint = _canonical_endpoint(base_url)
    if proto in {"", "auto"}:
        from excelmanus.model_catalog import model_spec
        proto = (model_spec(model, base_url, route_only=True) or {}).get("recommended_protocol") or _infer_protocol(endpoint, model)
    if endpoint:
        return f"{proto}|{endpoint}"
    return proto or "unknown"


def protocol_from_engine(engine: Any) -> str:
    """Active route label. Delegates to request.route so config/active 不再双读。"""
    from excelmanus.request.route import protocol_from_engine as protocol_from_active_route

    return protocol_from_active_route(engine)


def call_config_from_engine(engine: Any, *, transport: str | None = None) -> dict[str, Any]:
    """出网采样 / 能力参数。transport 进入 digest，inline↔file 翻转即新 epoch。"""
    config = getattr(engine, "_config", None) or getattr(engine, "config", None)
    tc = getattr(engine, "_thinking_config", None)
    profile = getattr(engine, "_active_profile", None)
    data: dict[str, Any] = {}
    if config is not None:
        for key in ("temperature", "max_tokens", "top_p"):
            val = getattr(config, key, None)
            if val is not None:
                data[key] = val
    if tc is not None:
        data["reasoning"] = {
            "effort": getattr(tc, "effort", None),
            "budget_tokens": getattr(tc, "budget_tokens", 0),
        }
    thinking_mode = getattr(profile, "thinking_mode", None) if profile is not None else None
    if thinking_mode:
        data["thinking_mode"] = thinking_mode
    if transport:
        data["transport"] = transport
    return data


def _tools_digest_of(tools: Any) -> str:
    if isinstance(tools, str):
        return tools
    if isinstance(tools, list):
        return digest_tools(tools)
    return digest_tools(None)


def _system_digest_of(system: Any) -> str:
    if isinstance(system, str):
        return digest_text(system)
    if system is None:
        return digest_text("")
    return digest_text(canonical_json(system))


def _call_config_digest_of(call_config: Any) -> str:
    if call_config is None:
        call_config = {}
    if isinstance(call_config, str):
        return digest_text(call_config)
    return digest_text(canonical_json(call_config))


@dataclass(frozen=True)
class EpochIdentity:
    session_id: str
    model: str
    protocol: str             # provider/base_url 归一化标识
    call_config_digest: str   # temperature / max_tokens / reasoning 等出网采样与能力参数
    tools_digest: str
    system_digest: str
    catalog_digest: str
    wire_digest: str          # seal 之后 wire 载荷的规范化 JSON 摘要

    def key(self) -> str:
        """缓存域身份。wire_digest 随历史增长，不进入 key，以免每轮打满 miss。"""
        sid = (self.session_id or "").strip() or "session"
        material = canonical_json({field: getattr(self, field) for field in _EPOCH_KEY_FIELDS})
        digest = hashlib.sha1(material.encode("utf-8")).hexdigest()[:16]
        return f"em_{sid}-{digest}"

    def describe_change(self, prev: EpochIdentity | None) -> str:
        if prev is None:
            return "新 epoch"
        parts: list[str] = []
        for field_name in _EPOCH_DESCRIBE_FIELDS:
            old = getattr(prev, field_name)
            new = getattr(self, field_name)
            if old != new:
                parts.append(f"{field_name}: {old} → {new}")
        return "；".join(parts) if parts else "无变化"


def compute_epoch_identity(
    *,
    session_id: str,
    model: str,
    protocol: str,
    call_config: Any,
    tools: Any,
    system: Any,
    catalog_digest: str,
    wire_payload: Any,
) -> EpochIdentity:
    digest_payload = canonical_wire_payload(wire_payload)
    return EpochIdentity(
        session_id=str(session_id or ""),
        model=str(model or ""),
        protocol=str(protocol or ""),
        call_config_digest=_call_config_digest_of(call_config),
        tools_digest=_tools_digest_of(tools),
        system_digest=_system_digest_of(system),
        catalog_digest=str(catalog_digest or ""),
        wire_digest=digest_text(digest_payload),
    )


def epoch_changed(prev: EpochIdentity | None, curr: EpochIdentity) -> bool:
    """model / protocol / call_config_digest / catalog_digest 任一变化 → True。"""
    if prev is None:
        return True
    return any(getattr(prev, name) != getattr(curr, name) for name in _EPOCH_CHANGE_FIELDS)


def assert_wire_prefix_stable(
    prev_payload: Any,
    curr_payload: Any,
    *,
    prev_epoch: EpochIdentity | None,
    curr_epoch: EpochIdentity,
) -> str | None:
    """同 epoch：curr 必须以 prev 为前缀（规范化 JSON 字节）。不同 epoch：返回 None。"""
    if prev_epoch is None or epoch_changed(prev_epoch, curr_epoch):
        return None
    prev_text = canonical_wire_payload(prev_payload)
    curr_text = canonical_wire_payload(curr_payload)
    if curr_text.startswith(prev_text):
        return None
    return (
        "出网 wire 前缀不变量破坏：当前载荷不是上一请求的前缀延伸"
        f"（epoch={curr_epoch.key()}）。"
    )


def catalog_fingerprint(engine: Any) -> str:
    registry = getattr(engine, "_registry", None) or getattr(engine, "registry", None)
    if registry is None:
        return ""
    digest = getattr(registry, "catalog_digest", None)
    if callable(digest):
        try:
            value = digest()
        except Exception:
            value = ""
        if isinstance(value, str) and value:
            return value
    getter = getattr(registry, "get_tool_names", None)
    if not callable(getter):
        return ""
    try:
        names = [str(name) for name in (getter() or [])]
    except Exception:
        return ""
    return "\x1f".join(sorted(names))


def session_prompt_cache_key(engine: Any) -> str:
    last = getattr(engine, "_last_envelope", None)
    if isinstance(last, RequestEnvelope):
        return last.prompt_cache_key
    epoch = getattr(last, "epoch", None) if last is not None else None
    if isinstance(epoch, EpochIdentity):
        return epoch.key()
    sid = getattr(engine, "_session_id", None)
    if isinstance(sid, str) and sid.strip():
        return f"em_{sid.strip()}"
    return "em_session"


def reset_system_projection(engine: Any) -> None:
    """压缩或清空会话后重定 leading system，下一封信封从当前渲染重开系列。"""
    engine._envelope_system_head = None
    engine._envelope_system_effective = None
    engine._image_wire_pin_seq = ()
    engine._files_wire_mode = None
    engine._last_wire_messages = None


def invalidate_envelope(engine: Any) -> None:
    """durable 历史被改写（回滚 / 编辑重发 / 手动压缩）后丢弃信封状态。

    所有改写历史的入口都必须调用本函数：否则下一封信封会带着缩水/改写的
    历史去比旧前缀，断言 fail-closed 且失败路径不更新 _last_envelope，
    会话从此每次请求都失败且无法自愈。
    """
    reset_system_projection(engine)
    engine._last_envelope = None


@dataclass(frozen=True)
class SystemProjection:
    leading: str
    trailing: str | None = None
    rebased: bool = False


def project_system_for_route(
    *,
    rendered: str,
    head: str | None,
    effective: str | None,
    starts_series: bool,
) -> SystemProjection:
    """决定 leading system 与是否在历史尾追加完整新提示。"""
    text = rendered if isinstance(rendered, str) else ""
    if not text.strip() or starts_series or not head:
        return SystemProjection(leading=text, trailing=None, rebased=True)
    latest = effective if isinstance(effective, str) and effective else head
    trailing = text if text != latest else None
    return SystemProjection(leading=head, trailing=trailing, rebased=False)


def _memory_of(engine: Any) -> Any:
    return getattr(engine, "_memory", None) or getattr(engine, "memory", None)


def flush_dynamic_contexts(
    engine: Any,
    *,
    defer_commit: bool = False,
) -> list[str]:
    """把 mention / hook / 技能快照追加到 durable 尾部，禁止插在 system 与历史之间。"""
    appended: list[str] = []
    appended_messages: list[Any] = []
    memory = _memory_of(engine)
    if memory is None:
        return appended

    mentions = getattr(engine, "_mention_contexts", None) or []
    engine._mention_contexts_pending_restore = list(mentions)
    mention_block = build_mention_context_block(mentions)
    mention_digest = digest_text(mention_block)
    if mention_block:
        memory.add_user_message(mention_block, hidden=True, prompt_kind="mention")
        appended.append(mention_block)
        messages = getattr(memory, "messages", None)
        if isinstance(messages, list) and messages:
            appended_messages.append(messages[-1])
    # 请求编译可延迟消费；兼容直接调用此 helper 的路径则保持一次性语义。
    if defer_commit:
        engine._mention_pending_digest = mention_digest
    else:
        engine._mention_contexts = []
        engine._mention_flush_digest = mention_digest

    pending = list(getattr(engine, "_prompt_user_contexts", None) or [])
    engine._prompt_contexts_pending_restore = list(pending)
    for text in pending:
        if isinstance(text, str) and text.strip():
            kind = "hook" if text.startswith("## Hook 上下文") else "prompt_context"
            memory.add_user_message(text, hidden=True, prompt_kind=kind)
            appended.append(text)
            messages = getattr(memory, "messages", None)
            if isinstance(messages, list) and messages:
                appended_messages.append(messages[-1])
    engine._prompt_dynamic_appended_messages = appended_messages
    if not defer_commit:
        engine._prompt_user_contexts = []
    return appended


@dataclass(frozen=True)
class EnvelopeIdentity:
    plan_active: bool
    tool_access: str
    tools_digest: str
    system_digest: str
    catalog_digest: str = ""


@dataclass
class RequestEnvelope:
    identity: EnvelopeIdentity
    system: str
    tools: list[dict[str, Any]]
    messages: list[dict[str, Any]]  # 请求投影：durable refs 已展开为 wire-ish parts
    prompt_cache_key: str
    compaction_generation: int = 0
    projection_generation: int = 0
    system_messages: list[dict[str, Any]] = field(default_factory=list)
    in_history: bool = False
    system_head: str = ""
    wire_messages: list[dict[str, Any]] = field(default_factory=list)  # 最终出网载荷（Files 替换后）
    epoch: EpochIdentity | None = None  # 未定型（缺 model/protocol）时为 None，由 A2 补齐
    transport: str = "inline"  # "inline" | "file"
    digest_payload: str = ""  # 规范化 JSON 字符串，键序稳定，可直接做前缀比较


def _history_prefix_stable(
    previous: list[dict[str, Any]],
    current: list[dict[str, Any]],
) -> bool:
    """previous 必须整体是 current 的前缀。

    历史只允许追加；当前请求比上一请求短，或旧请求最后一条消息被改写/跳过，
    都视为缓存前缀破坏。比较必须覆盖 previous 的全部消息，不能因为
    它比 current 短就裁掉最后一条。
    """
    if len(current) < len(previous):
        return False
    return current[: len(previous)] == previous


def assert_prefix_stable(prev: RequestEnvelope, curr: RequestEnvelope) -> None:
    """同 identity 且 generation 不变时，tools / system / 历史前缀必须一致。"""
    if prev.identity != curr.identity:
        return
    if prev.compaction_generation != curr.compaction_generation:
        return
    if prev.projection_generation != curr.projection_generation:
        return
    if prev.tools != curr.tools:
        raise AssertionError("envelope tools changed while identity stayed the same")
    if prev.system != curr.system:
        raise AssertionError("envelope system changed while identity stayed the same")
    if not _history_prefix_stable(prev.messages, curr.messages):
        raise AssertionError("envelope history prefix is not monotonic")


def assert_in_history_keeps_head(prev: RequestEnvelope, curr: RequestEnvelope) -> None:
    """in-history 且 tools 未变时，计划模式切换不得改 messages[0]。"""
    if not curr.in_history or not prev.in_history:
        return
    if prev.compaction_generation != curr.compaction_generation:
        return
    if prev.projection_generation != curr.projection_generation:
        return
    if prev.tools != curr.tools:
        return
    if not prev.messages or not curr.messages:
        return
    if prev.messages[0] != curr.messages[0]:
        raise AssertionError("in-history mode switch changed leading system")


def resolve_envelope_tools(engine: Any, *, tool_access: str) -> list[dict[str, Any]]:
    """唯一来源是 MetaToolBuilder（授权目录、初始披露与本轮已加载工具）。"""
    builder = getattr(engine, "_meta_tool_builder", None)
    getter = getattr(builder, "build_v5_tools", None)
    if callable(getter):
        try:
            raw = getter(tool_access=tool_access)
        except Exception:
            raw = None
        if isinstance(raw, list) and raw:
            return sort_tool_schemas(raw)
    snapshot = getattr(engine, "_prompt_tool_snapshot", None)
    if isinstance(snapshot, list) and snapshot:
        return sort_tool_schemas(snapshot)
    return []


def compaction_wire_context(
    engine: Any,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]] | None]:
    """摘要请求重放当时 wire 的 leading system + tools，不是完整渲染 system。"""
    last = getattr(engine, "_last_envelope", None)
    if isinstance(last, RequestEnvelope):
        head = last.system_head or ""
        prefix = [{"role": "system", "content": head}] if head.strip() else []
        tools = list(last.tools) if last.tools else None
        return prefix, tools
    cached = getattr(engine, "_last_system_msgs", None)
    prefix = list(cached) if isinstance(cached, list) else []
    snapshot = getattr(engine, "_prompt_tool_snapshot", None)
    tools = list(snapshot) if isinstance(snapshot, list) and snapshot else None
    return prefix, tools


def assemble_envelope(
    engine: Any,
    *,
    tool_access: str = "may_write",
    vision_capable: bool | None = None,
    persist: bool = True,
    commit_dynamic: bool = True,
) -> tuple[RequestEnvelope | None, str | None]:
    """组装本步唯一请求信封。失败时返回 (None, error)。

    persist=False 只窥探 leading + tools，不写信封状态、不冲 hook。
    """
    prepared, error = prepare_system_prompts_for_request(
        engine, consume_dynamic=persist,
    )
    if error is not None:
        return None, error
    if persist:
        flush_dynamic_contexts(engine, defer_commit=not commit_dynamic)

    system = prepared[0] if prepared else ""
    tools = resolve_envelope_tools(engine, tool_access=tool_access)
    memory = _memory_of(engine)
    if vision_capable is None:
        vision_capable = bool(getattr(engine, "_is_vision_capable", True))

    prev = getattr(engine, "_last_envelope", None)
    last_vision = getattr(engine, "_last_vision_capable", None)
    if (
        persist
        and isinstance(prev, RequestEnvelope)
        and last_vision is not None
        and bool(last_vision) != bool(vision_capable)
    ):
        # 视觉能力切换会改变图片投影形态，必须重开系列，不能改写旧前缀。
        reset_system_projection(engine)
        prev = None
    in_history = True
    generation = int(
        getattr(engine, "_compaction_generation", 0)
        or getattr(memory, "_compaction_generation", 0)
        or 0
    )
    starts_series = (
        not isinstance(prev, RequestEnvelope)
        or generation != prev.compaction_generation
        or prev.tools != tools
    )
    head = getattr(engine, "_envelope_system_head", None)
    effective = getattr(engine, "_envelope_system_effective", None)
    projection = project_system_for_route(
        rendered=system,
        head=head if isinstance(head, str) and head else None,
        effective=effective if isinstance(effective, str) and effective else None,
        starts_series=starts_series,
    )
    leading = projection.leading
    leading_prompts = [leading] if leading.strip() else []
    if persist and memory is not None and projection.trailing:
        adder = getattr(memory, "add_system_message", None)
        if callable(adder):
            adder(projection.trailing, hidden=True, prompt_kind="system_update")
            if not commit_dynamic:
                engine._prompt_dynamic_appended_messages.append(memory.messages[-1])
    defer_system_drop = persist and starts_series and memory is not None and not commit_dynamic
    if defer_system_drop:
        engine._prompt_drop_system_updates = True
    if persist and starts_series and memory is not None and commit_dynamic:
        # 系列边界 = 缓存前缀已 miss 的安全重写点（与 compaction 同一特权）：
        # 清掉历史尾部的旧 in-history system_update，否则新 head 与旧尾部
        # system 叠加，模型会同时读到多份模式指令。
        dropper = getattr(memory, "drop_system_updates", None)
        if callable(dropper):
            dropped = dropper()
            if dropped:
                logger.info(
                    "series 重开：清理 %d 条历史 in-history system_update，由新 head 接管",
                    dropped,
                )
    image_report: dict[str, Any] = {}
    pin_seq = getattr(engine, "_image_wire_pin_seq", None)
    if memory is None:
        messages = (
            [{"role": "system", "content": leading}] if leading.strip() else []
        )
        if projection.trailing:
            messages.append({"role": "system", "content": projection.trailing})
    else:
        try:
            messages = memory.project_for_request(
                system_prompts=leading_prompts,
                vision_capable=vision_capable,
                image_pins=pin_seq if isinstance(pin_seq, (list, tuple)) else None,
                image_report=image_report,
                exclude_system_updates=defer_system_drop,
            )
        except Exception as exc:
            logger.error("envelope projection failed: %s", exc, exc_info=True)
            rollback_prompt_dynamic(
                engine,
                getattr(engine, "_prompt_dynamic_appended_messages", None),
            )
            engine._prompt_dynamic_appended_messages = []
            return None, f"请求投影失败: {exc}"

    proj_gen = getattr(engine, "_projection_generation", 0)
    if not isinstance(proj_gen, int):
        proj_gen = 0
    if persist:
        if memory is not None and getattr(memory, "_projection_dirty", False):
            proj_gen += 1
        if image_report.get("rewrote_pinned_inline"):
            proj_gen += 1

    identity = EnvelopeIdentity(
        plan_active=(getattr(engine, "_current_chat_mode", "write") or "write") == "plan",
        tool_access=tool_access,
        tools_digest=digest_tools(tools),
        system_digest=digest_text(system),
        catalog_digest=catalog_fingerprint(engine),
    )
    system_messages = (
        [{"role": "system", "content": leading}] if leading.strip() else []
    )
    if projection.trailing:
        system_messages.append({"role": "system", "content": projection.trailing})
    wire_messages = strip_projection_meta(messages)
    envelope = RequestEnvelope(
        identity=identity,
        system=system,
        tools=tools,
        messages=messages,
        prompt_cache_key=session_prompt_cache_key(engine),
        compaction_generation=generation,
        projection_generation=proj_gen,
        system_messages=system_messages,
        in_history=in_history,
        system_head=leading,
        wire_messages=wire_messages,
        epoch=None,
        transport="inline",
        digest_payload=canonical_wire_payload(wire_messages),
    )
    if persist and isinstance(prev, RequestEnvelope):
        try:
            assert_prefix_stable(prev, envelope)
            assert_in_history_keeps_head(prev, envelope)
        except AssertionError as exc:
            logger.error("request envelope prefix invariant broken: %s", exc)
            # fail closed：绝不带着已改写/缩短的前缀继续出网。
            rollback_prompt_dynamic(
                engine,
                getattr(engine, "_prompt_dynamic_appended_messages", None),
            )
            engine._prompt_dynamic_appended_messages = []
            return None, f"请求信封前缀不变量破坏: {exc}"
    if persist:
        if memory is not None:
            memory._projection_dirty = False
        engine._projection_generation = proj_gen
        engine._last_image_report = dict(image_report)
        new_pins = image_report.get("pin_seq")
        if isinstance(new_pins, (list, tuple)):
            engine._image_wire_pin_seq = tuple(new_pins)
        if projection.rebased:
            engine._envelope_system_head = leading
        engine._envelope_system_effective = system
        if not isinstance(prev, RequestEnvelope):
            from excelmanus.prompt.cache_restore import warn_if_restored_prefix_drifted

            warn_if_restored_prefix_drifted(engine, envelope)
        engine._last_envelope = envelope
        engine._last_vision_capable = bool(vision_capable)
        engine._last_system_msgs = (
            [{"role": "system", "content": leading}] if leading.strip() else []
        )
        engine._prompt_tool_snapshot = list(tools)
        from excelmanus.prompt.cache_restore import remember_prefix_snapshot

        remember_prefix_snapshot(engine, envelope)
        if commit_dynamic:
            commit_prompt_dynamic(engine)
        engine._prompt_dynamic_appended_messages = [] if commit_dynamic else getattr(
            engine, "_prompt_dynamic_appended_messages", []
        )
    return envelope, None


def _files_mode_of(engine: Any) -> str | None:
    mode = getattr(engine, "_files_wire_mode", None)
    if mode == "inline":
        # 失败降级不是永久的：退避窗口过后允许再试 file 模式，
        # 否则一次上传失败会让整个会话永久背 base64。
        import time

        if time.time() < float(getattr(engine, "_files_inline_until", 0) or 0):
            return "inline"
        return None
    if mode == "file":
        return mode
    return None


def _epoch_for_sealed(
    engine: Any,
    envelope: RequestEnvelope,
    wire: list[dict[str, Any]],
    *,
    transport: str,
    model: str | None,
    protocol: str | None,
    call_config: Any,
) -> EpochIdentity | None:
    config = getattr(engine, "_config", None) or getattr(engine, "config", None)
    resolved_model = str(
        model
        or getattr(engine, "_active_model", None)
        or getattr(config, "model", None)
        or ""
    ).strip()
    if not resolved_model:
        return None
    resolved_protocol = protocol or protocol_from_engine(engine)
    resolved_config = (
        call_config
        if call_config is not None
        else call_config_from_engine(engine, transport=transport)
    )
    catalog = ""
    identity = getattr(envelope, "identity", None)
    if identity is not None:
        catalog = str(getattr(identity, "catalog_digest", "") or "")
    session_id = str(getattr(engine, "_session_id", "") or "")
    return compute_epoch_identity(
        session_id=session_id,
        model=resolved_model,
        protocol=resolved_protocol,
        call_config=resolved_config,
        tools=envelope.tools,
        system=envelope.system_head or envelope.system,
        catalog_digest=catalog,
        wire_payload=wire,
    )


async def seal_envelope(
    engine: Any,
    envelope: RequestEnvelope,
    *,
    persist: bool = True,
    model: str | None = None,
    protocol: str | None = None,
    call_config: Any = None,
    route: Any = None,
) -> tuple[RequestEnvelope | None, str | None]:
    """把 Files 传输收进信封。凭据与端点只读 ResolvedRoute。"""
    from excelmanus.attachments.files_api import (
        apply_files_transport,
        collect_wire_file_ids,
        files_api_enabled,
    )

    if route is None:
        from excelmanus.request.route import resolve_route
        from excelmanus.request.types import ResolvedRoute

        current = getattr(engine, "_resolved_route", None)
        route = current if isinstance(current, ResolvedRoute) else resolve_route(engine)
        engine._resolved_route = route

    inline = strip_projection_meta(envelope.messages)
    wire = inline
    mode = _files_mode_of(engine)
    config = getattr(engine, "_config", None) or getattr(engine, "config", None)
    base_url = str(getattr(route, "endpoint", "") or "")
    api_key = getattr(route, "api_key", None)
    client = getattr(engine, "_client", None)
    has_images = any(content_has_image(msg.get("content")) for msg in envelope.messages)
    used_files = False
    want_files = (
        mode != "inline"
        and bool(getattr(route, "capabilities", {}).get("files"))
        and config is not None
        and files_api_enabled(config, str(base_url or ""))
        and has_images
        and client is not None
        and hasattr(client, "files")
    )
    if want_files:
        try:
            files_messages = await apply_files_transport(
                envelope.messages,
                client,
                base_url=str(base_url or ""),
                api_key=api_key,
                route=route,
            )
            files_wire = strip_projection_meta(files_messages)
            if collect_wire_file_ids(files_wire):
                wire = files_wire
                used_files = True
        except Exception:
            logger.warning("Files 传输失败，回退 inline 请求版本", exc_info=True)
            wire = inline

    proj_gen = envelope.projection_generation
    transport = "file" if used_files else "inline"
    if persist:
        if used_files:
            engine._files_wire_mode = "file"
            engine._files_inline_until = 0.0
        elif want_files:
            import time

            engine._files_wire_mode = "inline"
            # 退避窗口：窗口内不再尝试 Files，过后自动重试 file 模式
            engine._files_inline_until = time.time() + 600.0
        prev_wire = getattr(engine, "_last_wire_messages", None)
        if isinstance(prev_wire, list) and prev_wire:
            if not _history_prefix_stable(prev_wire, wire):
                proj_gen += 1
        engine._projection_generation = proj_gen
        engine._last_wire_messages = list(wire)

    digest_payload = canonical_wire_payload(wire)
    epoch = _epoch_for_sealed(
        engine,
        envelope,
        wire,
        transport=transport,
        model=model,
        protocol=protocol,
        call_config=call_config,
    )
    prompt_cache_key = epoch.key() if epoch is not None else envelope.prompt_cache_key
    sealed = replace(
        envelope,
        wire_messages=wire,
        projection_generation=proj_gen,
        epoch=epoch,
        transport=transport,
        digest_payload=digest_payload,
        prompt_cache_key=prompt_cache_key,
    )
    if persist:
        engine._last_envelope = sealed
        from excelmanus.prompt.cache_restore import remember_prefix_snapshot

        remember_prefix_snapshot(engine, sealed)
    return sealed, None
