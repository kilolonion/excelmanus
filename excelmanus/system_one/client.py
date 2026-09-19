"""TypeSafe / Gateway 客户端。统一内部 noul；业务禁止直接扣 SDK 字段。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from excelmanus.system_one.packs import PackSpec, QuestionSpec
from excelmanus.system_one.providers import (
    GATEWAY_MODEL,
    GATEWAY_URL,
    JEV_ACTIVE_PROVIDER_SETTING,
    GATEWAY_KEY_SETTING,
    TYPESAFE_BASE_URL,
    TYPESAFE_KEY_SETTING,
    load_jev_providers,
    resolve_jev_connection,
)
from excelmanus.system_one.types import (
    Answer,
    ChoiceAnswer,
    Evaluation,
    NoulAnswer,
    ScoreAnswer,
)

_SDK_IMPORT_ERROR: str | None
try:
    from typesafe_sdk import AsyncTypeSafeClient, Choice, Noul, Score  # type: ignore

    _HAS_SDK = True
    _SDK_IMPORT_ERROR = None
except Exception as exc:  # pragma: no cover - 没装 extra 时的路径
    AsyncTypeSafeClient = None  # type: ignore[assignment]
    Choice = None  # type: ignore[assignment]
    Noul = None  # type: ignore[assignment]
    Score = None  # type: ignore[assignment]
    _HAS_SDK = False
    _SDK_IMPORT_ERROR = str(exc)


class SystemOneUnavailable(RuntimeError):
    """SDK 未装、无密钥或调用失败。evaluate 捕获后按家族 fail-open/closed。"""


def sdk_available() -> bool:
    return _HAS_SDK


def detect_transport(api_key: str | None, protocol: str | None = None) -> str:
    """显式 ``gateway`` 优先；否则 ``vck_`` 走 Gateway。"""
    if protocol == "gateway":
        return "gateway"
    if protocol == "typesafe":
        return "typesafe_direct"
    if (api_key or "").strip().startswith("vck_"):
        return "gateway"
    return "typesafe_direct"


def resolve_jev_api_key(values: Mapping[str, str] | None = None) -> tuple[str | None, str]:
    """读取密钥，不记录值。返回 (key, source_name)。

    优先当前激活的 Jev 提供商；没有提供商列表时回退旧键。
    """
    if values is None:
        from excelmanus.settings_runtime import get_setting

        def _raw(name: str) -> str:
            return (get_setting(name) or "").strip()
    else:
        def _raw(name: str) -> str:
            return str(values.get(name) or "").strip()

    records = load_jev_providers(values)
    connection = resolve_jev_connection(
        records,
        _raw(JEV_ACTIVE_PROVIDER_SETTING),
        typesafe_key=_raw(TYPESAFE_KEY_SETTING),
        gateway_key=_raw(GATEWAY_KEY_SETTING),
        model_override=_raw("EXCELMANUS_JEV_MODEL"),
    )
    return connection.api_key, connection.source


def client_ready(api_key: str | None, protocol: str | None = None) -> bool:
    """Gateway 不需要 typesafe-sdk；直连才需要。"""
    if not (api_key or "").strip():
        return False
    if detect_transport(api_key, protocol) == "gateway":
        return True
    return sdk_available()


def _build_sdk_questions(spec: PackSpec) -> dict[str, Any]:
    questions: dict[str, Any] = {}
    for item in spec.questions:
        questions[item.qid] = _sdk_question(item)
    return questions


def _sdk_question(item: QuestionSpec) -> Any:
    if not _HAS_SDK:
        raise SystemOneUnavailable("typesafe-sdk is not installed")
    if item.kind == "noul":
        if Noul is None:
            raise SystemOneUnavailable("typesafe-sdk is not installed")
        kwargs: dict[str, Any] = {"instructions": item.instructions}
        if isinstance(item.criteria, dict):
            kwargs["criteria"] = item.criteria
        return Noul(**kwargs)
    if item.kind == "choice":
        if Choice is None:
            raise SystemOneUnavailable("typesafe-sdk is not installed")
        criteria = item.criteria if isinstance(item.criteria, dict) else {}
        return Choice(instructions=item.instructions, criteria=criteria)
    if item.kind == "score":
        if Score is None:
            raise SystemOneUnavailable("typesafe-sdk is not installed")
        legend = item.criteria if isinstance(item.criteria, tuple) else tuple(item.criteria or ())
        return Score(instructions=item.instructions, criteria=list(legend))
    raise SystemOneUnavailable(f"unknown question kind: {item.kind}")


def noul_from_payload(payload: Mapping[str, Any] | Any) -> float:
    """Gateway ``probability`` 与 TypeSafe ``noul`` 同义。"""
    if isinstance(payload, Mapping):
        if "noul" in payload:
            return float(payload["noul"])
        if "probability" in payload:
            return float(payload["probability"])
        return 0.5
    for attr in ("noul", "probability"):
        value = getattr(payload, attr, None)
        if value is not None:
            return float(value)
    return 0.5


def _choice_from_payload(payload: Mapping[str, Any] | Any) -> ChoiceAnswer:
    if isinstance(payload, Mapping):
        choice = str(payload.get("choice") or "")
        probs = payload.get("probabilities") or {}
        conf = payload.get("confidence")
        if not isinstance(probs, Mapping):
            probs = {}
        return ChoiceAnswer(
            choice=choice,
            probabilities={str(k): float(v) for k, v in probs.items()},
            confidence=float(conf) if conf is not None else 0.0,
        )
    probs = getattr(payload, "probabilities", {}) or {}
    if not isinstance(probs, Mapping):
        probs = {}
    conf = getattr(payload, "confidence", 0.0)
    return ChoiceAnswer(
        choice=str(getattr(payload, "choice", "") or ""),
        probabilities={str(k): float(v) for k, v in probs.items()},
        confidence=float(conf or 0.0),
    )


def _score_from_payload(payload: Mapping[str, Any] | Any) -> ScoreAnswer:
    if isinstance(payload, Mapping):
        probs = payload.get("probabilities") or {}
        if not isinstance(probs, Mapping):
            probs = {}
        legend = payload.get("legend") or ()
        if isinstance(legend, str):
            legend = (legend,)
        return ScoreAnswer(
            score=float(payload.get("score") or 0.0),
            probabilities={str(k): float(v) for k, v in probs.items()},
            confidence=float(payload.get("confidence") or 0.0),
            legend=tuple(str(item) for item in legend),
        )
    probs = getattr(payload, "probabilities", {}) or {}
    if not isinstance(probs, Mapping):
        probs = {}
    legend = getattr(payload, "legend", ()) or ()
    return ScoreAnswer(
        score=float(getattr(payload, "score", 0.0) or 0.0),
        probabilities={str(k): float(v) for k, v in probs.items()},
        confidence=float(getattr(payload, "confidence", 0.0) or 0.0),
        legend=tuple(str(item) for item in legend),
    )


def normalize_answers(
    spec: PackSpec,
    raw: Any,
    *,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Answer]:
    """把 SDK / Gateway / dict fixture 收成内部 Answer。"""
    if not metadata and isinstance(raw, Mapping):
        metadata = raw
    metadata = metadata or {}
    blob = _answers_blob(raw)
    confidences = _confidence_map(raw, metadata)
    out: dict[str, Answer] = {}
    for item in spec.questions:
        payload = _lookup_answer(blob, item.qid)
        if payload is None:
            continue
        if item.kind == "noul":
            out[item.qid] = NoulAnswer(noul=noul_from_payload(payload))
        elif item.kind == "choice":
            answer = _choice_from_payload(payload)
            if not answer.confidence and item.qid in confidences:
                answer = ChoiceAnswer(
                    choice=answer.choice,
                    probabilities=answer.probabilities,
                    confidence=confidences[item.qid],
                )
            out[item.qid] = answer
        else:
            answer = _score_from_payload(payload)
            if not answer.confidence and item.qid in confidences:
                answer = ScoreAnswer(
                    score=answer.score,
                    probabilities=answer.probabilities,
                    confidence=confidences[item.qid],
                    legend=answer.legend,
                )
            out[item.qid] = answer
    return out


def _answers_blob(raw: Any) -> Any:
    if raw is None:
        return {}
    if isinstance(raw, Mapping):
        if "answers" in raw and isinstance(raw["answers"], Mapping):
            return raw["answers"]
        return raw
    for attr in ("answers", "nouls"):
        value = getattr(raw, attr, None)
        if value is not None:
            return value
    return raw


def _lookup_answer(blob: Any, qid: str) -> Any:
    if isinstance(blob, Mapping):
        if qid in blob:
            return blob[qid]
        return None
    getter = getattr(blob, "get", None)
    if callable(getter):
        try:
            return getter(qid)
        except Exception:
            return None
    return getattr(blob, qid, None)


def _confidence_map(raw: Any, metadata: Mapping[str, Any]) -> dict[str, float]:
    provider = metadata.get("providerMetadata") or metadata.get("provider_metadata") or {}
    if not isinstance(provider, Mapping):
        provider = {}
    typesafe = provider.get("typesafe") if isinstance(provider, Mapping) else None
    if isinstance(typesafe, Mapping):
        conf = typesafe.get("confidence")
        if isinstance(conf, Mapping):
            return {str(k): float(v) for k, v in conf.items()}
    nested = getattr(raw, "provider_metadata", None) or getattr(raw, "providerMetadata", None)
    if isinstance(nested, Mapping):
        typesafe = nested.get("typesafe")
        if isinstance(typesafe, Mapping) and isinstance(typesafe.get("confidence"), Mapping):
            return {str(k): float(v) for k, v in typesafe["confidence"].items()}
    return {}


def gateway_questions(spec: PackSpec) -> dict[str, Any]:
    """Gateway 题型：noul→boolean，confidence 可能在 providerMetadata。"""
    questions: dict[str, Any] = {}
    for item in spec.questions:
        if item.kind == "noul":
            body: dict[str, Any] = {"type": "boolean", "instructions": item.instructions}
            if isinstance(item.criteria, dict):
                body["criteria"] = item.criteria
            questions[item.qid] = body
        elif item.kind == "choice":
            questions[item.qid] = {
                "type": "choice",
                "instructions": item.instructions,
                "criteria": item.criteria if isinstance(item.criteria, dict) else {},
            }
        else:
            legend = item.criteria if isinstance(item.criteria, tuple) else tuple(item.criteria or ())
            questions[item.qid] = {
                "type": "score",
                "instructions": item.instructions,
                "criteria": list(legend),
            }
    return questions


def _gateway_http_reason(status: int) -> str:
    if status == 401:
        return "gateway_http_401"
    if status == 422:
        return "gateway_http_422"
    if status == 429:
        return "gateway_http_429"
    if status == 529:
        return "gateway_http_529"
    return f"gateway_http_{status}"


async def system_one(
    spec: PackSpec,
    state: Mapping[str, Any],
    *,
    model: str,
    api_key: str,
    timeout_seconds: float,
    protocol: str | None = None,
    base_url: str | None = None,
) -> Evaluation:
    if not api_key:
        raise SystemOneUnavailable("missing TypeSafe API key")
    if detect_transport(api_key, protocol) == "gateway":
        endpoint = GATEWAY_URL if base_url is None else base_url.strip()
        if not endpoint:
            raise SystemOneUnavailable("gateway provider is missing base_url")
        model_id = model if (model and "/" in model) else GATEWAY_MODEL
        return await _gateway_system_one(
            spec,
            state,
            api_key=api_key,
            timeout_seconds=timeout_seconds,
            url=endpoint,
            model_id=model_id,
        )
    if not _HAS_SDK:
        raise SystemOneUnavailable(_SDK_IMPORT_ERROR or "typesafe-sdk is not installed")
    endpoint = TYPESAFE_BASE_URL if base_url is None else base_url.strip()
    if not endpoint:
        raise SystemOneUnavailable("typesafe provider is missing base_url")
    from urllib.parse import urlparse

    parsed_base_url = urlparse(endpoint)
    if parsed_base_url.scheme not in {"http", "https"} or not parsed_base_url.netloc:
        raise SystemOneUnavailable("typesafe_invalid_base_url")
    import time

    started = time.monotonic()
    client_cls = AsyncTypeSafeClient
    client_kwargs: dict[str, Any] = {"api_key": api_key}
    if endpoint != TYPESAFE_BASE_URL:
        client_kwargs["base_url"] = endpoint
    try:
        client_context = client_cls(**client_kwargs)  # type: ignore[misc]
    except TypeError as exc:
        if "base_url" in client_kwargs:
            raise SystemOneUnavailable(
                "installed typesafe-sdk does not support a custom base_url"
            ) from exc
        raise
    async with client_context as client:
        response = await _call_with_timeout(
            client.system_one(
                model=model,
                state=dict(state),
                questions=_build_sdk_questions(spec),
            ),
            timeout_seconds,
        )
    latency_ms = (time.monotonic() - started) * 1000.0
    usage = _usage_of(response)
    answers = normalize_answers(spec, response)
    model_name = str(getattr(response, "model", None) or model)
    return Evaluation(
        pack_id=spec.pack_id,
        answers=answers,
        model=model_name,
        latency_ms=latency_ms,
        usage=usage,
    )


async def _gateway_system_one(
    spec: PackSpec,
    state: Mapping[str, Any],
    *,
    api_key: str,
    timeout_seconds: float,
    url: str = GATEWAY_URL,
    model_id: str = GATEWAY_MODEL,
) -> Evaluation:
    """Vercel AI Gateway evaluate。禁止走 OpenAI 兼容 /v1/chat/completions。"""
    import time

    import httpx  # pyright: ignore[reportMissingImports]

    from urllib.parse import urlparse

    parsed_url = urlparse((url or "").strip())
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        raise SystemOneUnavailable("gateway_invalid_base_url")
    started = time.monotonic()
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "ai-gateway-protocol-version": "0.0.1",
        "ai-gateway-auth-method": "api-key",
        "ai-evaluation-model-specification-version": "4",
        "ai-model-id": model_id or GATEWAY_MODEL,
    }
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.post(
                url or GATEWAY_URL,
                headers=headers,
                json={"state": dict(state), "questions": gateway_questions(spec)},
            )
    except httpx.TimeoutException as exc:
        raise SystemOneUnavailable(f"system_one timed out after {timeout_seconds}s") from exc
    except httpx.HTTPError as exc:
        raise SystemOneUnavailable(f"gateway:{type(exc).__name__}") from exc
    if response.status_code >= 400:
        raise SystemOneUnavailable(_gateway_http_reason(response.status_code))
    try:
        payload = response.json()
    except ValueError as exc:
        raise SystemOneUnavailable("gateway_bad_json") from exc
    if not isinstance(payload, Mapping):
        raise SystemOneUnavailable("gateway_bad_payload")
    answers = normalize_answers(spec, payload, metadata=payload)
    raw_usage = payload.get("usage")
    usage = dict(raw_usage) if isinstance(raw_usage, Mapping) else {}
    return Evaluation(
        pack_id=spec.pack_id,
        answers=answers,
        model=str(payload.get("model") or model_id or GATEWAY_MODEL),
        latency_ms=(time.monotonic() - started) * 1000.0,
        usage=usage,
    )


async def _call_with_timeout(awaitable: Any, timeout_seconds: float) -> Any:
    import asyncio

    try:
        return await asyncio.wait_for(awaitable, timeout=timeout_seconds)
    except TimeoutError as exc:
        raise SystemOneUnavailable(f"system_one timed out after {timeout_seconds}s") from exc


def _usage_of(response: Any) -> dict[str, Any]:
    usage = getattr(response, "usage", None)
    if isinstance(usage, Mapping):
        return dict(usage)
    if usage is None:
        return {}
    out: dict[str, Any] = {}
    for key in ("input_tokens", "output_tokens", "inputTokens", "outputTokens"):
        value = getattr(usage, key, None)
        if value is not None:
            out[key] = value
    return out
