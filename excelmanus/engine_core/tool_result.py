"""ToolResult — 工具执行结果的三路投影契约。

value: 有界结构化数据（SDK / 后续代码）
model_text: 给模型的摘要
ui_meta: 给 SSE / 前端的小型事实
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from typing import Any

from excelmanus.engine_core.error_payload import (
    TOOL_ERROR,
    canonicalize_error_payload,
    error_code_from_mapping,
    error_message_from_mapping,
    make_error_payload,
    should_canonicalize_error_payload,
)


@dataclass
class ImageInjection:
    """工具返回的图片注入数据（ui_meta.image 的别名来源）。"""

    base64: str = ""
    mime_type: str = "image/png"
    detail: str = "auto"
    attachment: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "mime_type": self.mime_type,
            "detail": self.detail,
        }
        if self.attachment:
            payload["attachment"] = self.attachment
        if self.base64:
            payload["base64"] = self.base64
        return payload


@dataclass
class ToolError:
    code: str
    message: str
    fields: dict[str, Any] = field(default_factory=dict)


# 仅供 coerce 收口：若载荷里还带着这些键，提升到 ui_meta。新工具应直接填 ui_meta。
_MAGIC_UI_KEYS = (
    "__tool_result_image__",
    "_file_download",
    "_excel_diff",
    "_text_diff",
    "cow_mapping",
)


def error_result(
    message: str,
    *,
    code: str = TOOL_ERROR,
    fields: dict[str, Any] | None = None,
    failure_class: str | None = None,
    remediation: str | None = None,
) -> "ToolResult":
    extra = dict(fields or {})
    if failure_class is None:
        raw_class = extra.pop("failure_class", None)
        failure_class = str(raw_class) if raw_class else None
    else:
        extra.pop("failure_class", None)
    if remediation is None:
        raw_hint = extra.pop("remediation", None)
        remediation = str(raw_hint) if raw_hint else None
    else:
        extra.pop("remediation", None)
    payload = make_error_payload(
        message,
        error_code=code,
        failure_class=failure_class,
        remediation=remediation,
        **extra,
    )
    return ToolResult(
        success=False,
        model_text=json.dumps(payload, ensure_ascii=False, default=str),
        value=payload,
        error=ToolError(code=str(code or TOOL_ERROR), message=str(message), fields=payload),
    )


def from_payload(
    payload: dict[str, Any],
    *,
    ui_meta: "ToolUiMeta | None" = None,
    model_text: str | None = None,
) -> "ToolResult":
    """结构化 dict → ToolResult；载荷里残留的 UI 键提升到 ui_meta。"""
    cleaned, lifted = lift_ui_keys(payload)
    ui = _overlay_ui(ui_meta or ToolUiMeta(), lifted)
    status = str(cleaned.get("status") or "").lower()
    err = cleaned.get("error")
    error_kind = str(cleaned.get("error_kind") or "").lower()
    is_fail = (
        cleaned.get("ok") is False
        or status in {"error", "failed", "fail", "blocked"}
        or (bool(err) and status not in {"success", "ok", "confirmation_required", "pending_confirmation"})
        or (
            error_kind in {"permanent", "retryable", "transient"}
            and status not in {"success", "ok", "confirmation_required", "pending_confirmation"}
        )
    )
    if is_fail:
        if should_canonicalize_error_payload(cleaned):
            cleaned = canonicalize_error_payload(cleaned)
        msg = error_message_from_mapping(cleaned, default=status or "error")
        code = error_code_from_mapping(cleaned, default=TOOL_ERROR)
        return ToolResult(
            success=False,
            model_text=model_text or json.dumps(cleaned, ensure_ascii=False, default=str),
            value=cleaned,
            error=ToolError(code=code, message=msg, fields=cleaned),
            ui_meta=ui,
        )
    return ok_result(cleaned, model_text=model_text, ui_meta=ui)


def ok_result(
    payload: dict[str, Any],
    *,
    model_text: str | None = None,
    ui_meta: "ToolUiMeta | None" = None,
) -> "ToolResult":
    payload.setdefault("status", "success")
    cleaned, lifted = lift_ui_keys(payload)
    ui = _overlay_ui(ui_meta or ToolUiMeta(), lifted)
    version = cleaned.get("content_version")
    if isinstance(version, str) and version and not ui.content_version:
        ui.content_version = version
    file_path = cleaned.get("file_path") or cleaned.get("path") or cleaned.get("file")
    if isinstance(file_path, str) and file_path.strip() and file_path not in ui.files:
        ui.files.append(file_path)
    return ToolResult(
        success=True,
        model_text=model_text or json.dumps(cleaned, ensure_ascii=False, default=str),
        value=cleaned,
        ui_meta=ui,
    )


def coerce_legacy_result(raw: Any, *, default_code: str = "TOOL_ERROR") -> "ToolResult":
    """消费边界：非 ToolResult 的返回值（MCP / 第三方）收成三路。

    已是 ToolResult 则原样返回（若 value 仍含旧 UI 键则提升一次）。
    内置宿主工具应直接返回 ToolResult。
    """
    if isinstance(raw, ToolResult):
        return _lift_result_value(raw)
    if isinstance(raw, dict):
        return from_payload(raw)
    if raw is None:
        return ToolResult.from_text("", success=True)
    if isinstance(raw, (int, float, bool)):
        return ToolResult.from_text(str(raw), success=True)
    text = str(raw)
    stripped = text.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        try:
            parsed = json.loads(stripped)
        except (json.JSONDecodeError, TypeError, ValueError):
            parsed = None
        if isinstance(parsed, dict):
            result = from_payload(parsed)
            if result.error is not None and result.error.code == "TOOL_ERROR" and default_code != "TOOL_ERROR":
                result.error.code = default_code
            return result
    return ToolResult.from_text(text, success=True)


def annotate_shadow_schema_violations(
    result: "ToolResult",
    *,
    registry: Any,
    tool_name: str,
    arguments: dict[str, Any],
) -> "ToolResult":
    """shadow 模式：不阻断，把 schema 违规写入结果载荷供模型自纠。"""
    mode = str(getattr(registry, "_schema_validation_mode", "off") or "off").strip().lower()
    if mode != "shadow" or not tool_name:
        return result
    getter = getattr(registry, "get_tool", None)
    tool = getter(tool_name) if callable(getter) else None
    schema = getattr(tool, "input_schema", None) if tool is not None else None
    if not isinstance(schema, dict):
        return result
    collector = getattr(registry, "_collect_schema_violations", None)
    if not callable(collector):
        return result
    violations: list[str] = []
    collector(value=arguments, schema=schema, path="$", violations=violations)
    if not violations:
        return result
    clipped = [str(item) for item in violations[:20] if str(item).strip()]
    hint = (
        "本次已执行（shadow 不阻断）。请按 schema_violations 修正参数后再调；"
        "不要用同一组违规参数重试。"
    )
    value = result.value
    if isinstance(value, dict):
        if value.get("schema_violations"):
            return result
        updated = dict(value)
        updated["schema_validation"] = "shadow"
        updated["schema_violations"] = clipped
        updated["remediation"] = hint
        return replace(
            result,
            value=updated,
            model_text=json.dumps(updated, ensure_ascii=False, default=str),
        )
    wrapped: dict[str, Any] = {
        "status": "success" if result.success else "error",
        "result": result.model_text,
        "schema_validation": "shadow",
        "schema_violations": clipped,
        "remediation": hint,
    }
    return replace(
        result,
        value=wrapped,
        model_text=json.dumps(wrapped, ensure_ascii=False, default=str),
    )


def payload_has_legacy_magic(payload: dict[str, Any]) -> bool:
    return any(key in payload for key in _MAGIC_UI_KEYS)


def lift_ui_keys(payload: dict[str, Any]) -> tuple[dict[str, Any], ToolUiMeta]:
    """把载荷里的 UI 键提升到 ui_meta。下载/diff 从模型文本中剥掉。

    cow_mapping 已从载荷中剥掉，不进入 ui_meta / model_text。
    """
    cleaned = dict(payload)
    ui = ToolUiMeta()

    image = cleaned.pop("__tool_result_image__", None)
    if isinstance(image, dict) and image.get("base64"):
        ui.image = image

    download = cleaned.pop("_file_download", None)
    if isinstance(download, dict) and download.get("file_path"):
        ui.download = download

    excel_diff = cleaned.pop("_excel_diff", None)
    if isinstance(excel_diff, dict):
        ui.diff = excel_diff

    text_diff = cleaned.pop("_text_diff", None)
    if isinstance(text_diff, dict):
        ui.text_diff = text_diff

    cleaned.pop("cow_mapping", None)

    return cleaned, ui


def _overlay_ui(base: ToolUiMeta, extra: ToolUiMeta) -> ToolUiMeta:
    if extra.image and not base.image:
        base.image = extra.image
    if extra.download and not base.download:
        base.download = extra.download
    if extra.diff and not base.diff:
        base.diff = extra.diff
    if extra.text_diff and not base.text_diff:
        base.text_diff = extra.text_diff
    return base


def _collect_content_versions(result: "ToolResult") -> list[str]:
    versions: list[str] = []
    seen: set[str] = set()

    def _add(raw: Any) -> None:
        if isinstance(raw, str) and raw and raw not in seen:
            seen.add(raw)
            versions.append(raw)

    _add(getattr(result.ui_meta, "content_version", None))
    value = result.value
    if isinstance(value, dict):
        _add(value.get("content_version"))
        writes = (value.get("sdk_calls") or {}).get("writes") if isinstance(value.get("sdk_calls"), dict) else None
        if isinstance(writes, list):
            for item in writes:
                if isinstance(item, dict):
                    _add(item.get("content_version"))
    return versions


def _strip_magic_from_text(text: str) -> str:
    stripped = (text or "").strip()
    if not (stripped.startswith("{") and stripped.endswith("}")):
        return text
    if not any(key in stripped for key in _MAGIC_UI_KEYS):
        return text
    try:
        parsed = json.loads(stripped)
    except (json.JSONDecodeError, TypeError, ValueError):
        return text
    if not isinstance(parsed, dict):
        return text
    cleaned, _lifted = lift_ui_keys(parsed)
    return json.dumps(cleaned, ensure_ascii=False, default=str)


def finalize_content(result: "ToolResult", *, max_chars: int = 0) -> "ToolResult":
    """阶段 8：同步、只动 model_text，失败也跑。

    截断、覆盖声明、content_version 诚实性在这里收口。魔法 UI 字段不得进 model_text。
    """
    result = coerce_legacy_result(result)
    text = _strip_magic_from_text(result.model_text or "")
    for key in _MAGIC_UI_KEYS:
        if key in text:
            text = _strip_magic_from_text(text)
            break
    versions = _collect_content_versions(result)
    missing = [ver for ver in versions if ver not in text]
    if missing:
        note = {"content_version": missing[0]} if len(missing) == 1 else {"content_versions": missing}
        text = (text + ("\n" if text else "") + json.dumps(note, ensure_ascii=False)).strip()
    truncated = bool(result.truncated)
    coverage = result.coverage
    cov = dict(coverage) if isinstance(coverage, dict) else {}
    if cov.get("spill_retrieve"):
        return replace(result, model_text=text, truncated=truncated, coverage=coverage)
    if max_chars > 0 and len(text) > max_chars:
        original = len(text)
        text = (
            f"{text[:max_chars]}\n"
            f"[结果已截断，原始长度: {original} 字符，上限: {max_chars} 字符]"
        )
        truncated = True
        coverage = dict(coverage or {})
        coverage["declared"] = True
        coverage["truncated"] = True
        if coverage.get("kind") == "complete":
            coverage["kind"] = "truncated"
    return replace(result, model_text=text, truncated=truncated, coverage=coverage)


def _lift_result_value(result: "ToolResult") -> "ToolResult":
    if not isinstance(result.value, dict) or not payload_has_legacy_magic(result.value):
        return result
    cleaned, lifted = lift_ui_keys(result.value)
    ui = _overlay_ui(result.ui_meta, lifted)
    return replace(
        result,
        value=cleaned,
        ui_meta=ui,
        model_text=json.dumps(cleaned, ensure_ascii=False, default=str),
    )


@dataclass
class ToolUiMeta:
    files: list[str] = field(default_factory=list)
    content_version: str | None = None
    preview: dict[str, Any] | None = None
    image: dict[str, Any] | None = None
    diff: dict[str, Any] | None = None
    download: dict[str, Any] | None = None
    merge: dict[str, Any] | None = None
    text_diff: dict[str, Any] | None = None
    revision: dict[str, Any] | None = None

    def to_sse_ui(self) -> dict[str, Any] | None:
        """投影到 tool_call_end 的 ui 字段（小型事实，不含大载荷）。"""
        payload: dict[str, Any] = {}
        if self.merge:
            payload["merge"] = self.merge
        if self.files:
            payload["files"] = self.files
        if self.content_version:
            payload["content_version"] = self.content_version
        if self.revision:
            payload["revision"] = self.revision
        return payload or None


@dataclass
class ToolResult:
    success: bool
    model_text: str
    value: Any = None
    ui_meta: ToolUiMeta = field(default_factory=ToolUiMeta)
    error: ToolError | None = None
    truncated: bool = False
    coverage: dict[str, Any] | None = None

    @classmethod
    def from_text(cls, text: str, success: bool = True) -> ToolResult:
        """未迁移工具的适配器：字符串进 model_text，不解析魔法字段。"""
        return cls(success=success, model_text=str(text or ""))

    def with_model_text(self, model_text: str) -> ToolResult:
        return replace(self, model_text=model_text)

    @classmethod
    def from_image_injection(
        cls,
        *,
        model_text: str,
        injection: ImageInjection,
        success: bool = True,
        value: Any = None,
    ) -> ToolResult:
        ui = ToolUiMeta(image=injection.to_dict())
        return cls(
            success=success,
            model_text=model_text,
            value=value,
            ui_meta=ui,
        )
