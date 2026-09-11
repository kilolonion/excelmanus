"""ToolResult — 工具执行结果的三路投影契约。

value: 有界结构化数据（SDK / 后续代码）
model_text: 给模型的摘要
ui_meta: 给 SSE / 前端的小型事实
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from typing import Any


@dataclass
class ImageInjection:
    """工具返回的图片注入数据（ui_meta.image 的别名来源）。"""

    base64: str
    mime_type: str = "image/png"
    detail: str = "auto"

    def to_dict(self) -> dict[str, Any]:
        return {
            "base64": self.base64,
            "mime_type": self.mime_type,
            "detail": self.detail,
        }


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
    code: str = "TOOL_ERROR",
    fields: dict[str, Any] | None = None,
) -> "ToolResult":
    payload: dict[str, Any] = {"status": "error", "code": code, "message": message}
    if fields:
        for key, value in fields.items():
            payload.setdefault(key, value)
    return ToolResult(
        success=False,
        model_text=json.dumps(payload, ensure_ascii=False, default=str),
        value=payload,
        error=ToolError(code=code, message=message, fields=payload),
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
    is_fail = (
        cleaned.get("ok") is False
        or status in {"error", "failed", "fail", "blocked"}
        or (bool(err) and status not in {"success", "ok", "confirmation_required"})
    )
    if is_fail:
        msg = str(err or cleaned.get("message") or cleaned.get("reason") or status or "error")
        code = str(cleaned.get("code") or cleaned.get("error_code") or "TOOL_ERROR")
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


def payload_has_legacy_magic(payload: dict[str, Any]) -> bool:
    return any(key in payload for key in _MAGIC_UI_KEYS)


def lift_ui_keys(payload: dict[str, Any]) -> tuple[dict[str, Any], ToolUiMeta]:
    """把载荷里的 UI 键提升到 ui_meta。下载/diff 从模型文本中剥掉；cow_mapping 留给模型看。"""
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

    cow = cleaned.get("cow_mapping")
    if isinstance(cow, dict) and cow:
        ui.cow_mapping = {str(k): str(v) for k, v in cow.items()}

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
    if extra.cow_mapping and not base.cow_mapping:
        base.cow_mapping = extra.cow_mapping
    return base


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
    cow_mapping: dict[str, str] | None = None
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
