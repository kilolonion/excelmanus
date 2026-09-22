"""Version-bound workbook targets shared by questions and presentation tools."""

from __future__ import annotations

from copy import deepcopy
import json
import re
from typing import Any

from excelmanus.workbook.refs import parse_rect
from excelmanus.workbook.snapshot import open_snapshot_at, require_default_sheet
from excelmanus.workspace.identity import resolve_canonical


def target_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "工作区内的表格路径"},
            "sheet": {"type": "string", "description": "工作表名称"},
            "ranges": {"type": "array", "maxItems": 16, "items": {"type": "string"},
                       "description": "A1 区域列表；选区提问时是建议区域，展示时必须提供"},
            "content_version": {"type": "string", "description": "读取或写入回执中的版本；已修改展示必须提供"},
        },
        "required": ["file_path", "sheet"],
        "additionalProperties": False,
    }


def normalize_ranges(raw: Any, sheet: str, *, required: bool = False) -> list[str]:
    if not isinstance(raw, list) or len(raw) > 16 or (required and not raw):
        raise ValueError("请选择 1–16 个区域" if required else "ranges 必须是最多 16 个区域的数组")
    result: list[str] = []
    for address in raw:
        if not isinstance(address, str) or len(address) > 128:
            raise ValueError("区域必须是 A1 地址")
        rect = parse_rect(address, default_sheet=sheet)
        if rect.sheet != sheet:
            raise ValueError("区域必须属于指定工作表")
        normalized = rect.to_a1(include_sheet=False).replace("$", "")
        if normalized not in result:
            result.append(normalized)
    return result


def bind_target(engine: Any, raw: Any, *, require_ranges: bool = False) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("selection/target 必须是对象")
    workspace = getattr(engine, "_workspace_ref", None)
    if workspace is None:
        raise ValueError("当前会话没有绑定工作区")
    path, sheet = raw.get("file_path"), raw.get("sheet")
    if not isinstance(path, str) or not path or len(path) > 1024:
        raise ValueError("file_path 不能为空")
    if not isinstance(sheet, str) or not sheet or len(sheet) > 100:
        raise ValueError("sheet 不能为空")
    identity = resolve_canonical(workspace.root, path)
    if not identity.relative.lower().endswith((".xlsx", ".xlsm", ".xls", ".xlsb", ".csv", ".tsv")):
        raise ValueError("只能展示表格文件")
    version = raw.get("content_version")
    if version is not None and (not isinstance(version, str) or not version or len(version) > 100):
        raise ValueError("content_version 格式无效")
    snapshot = open_snapshot_at(workspace.root / identity.relative, relative=identity.relative,
                                workspace=workspace, expected_version=version)
    if snapshot.is_csv():
        sheet = require_default_sheet(["Sheet1"], sheet)
    else:
        wb = snapshot.open_workbook(data_only=False)
        try:
            sheet = require_default_sheet(wb.sheetnames, sheet)
        finally:
            wb.close()
    return {
        "file_path": identity.public,
        "workspace_id": workspace.workspace_id,
        "sheet": sheet,
        "ranges": normalize_ranges([] if raw.get("ranges") is None else raw["ranges"], sheet, required=require_ranges),
        "content_version": snapshot.content_version,
    }


def prepare_questions(engine: Any, questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    prepared = deepcopy(questions)
    for question in prepared:
        if isinstance(question, dict) and question.get("selection") is not None:
            question["selection"] = bind_target(engine, question["selection"])
    return prepared


def validate_selection_answer(engine: Any, target: dict[str, Any], raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict) or not raw.get("content_version"):
        raise ValueError("请在表格中选择区域并确认，或输入说明")
    workspace = getattr(engine, "_workspace_ref", None)
    if workspace is None or raw.get("workspace_id") != target.get("workspace_id") or raw.get("workspace_id") != workspace.workspace_id:
        raise ValueError("选区不属于当前问题的工作区")
    path = raw.get("file_path")
    if not isinstance(path, str) or resolve_canonical(workspace.root, path).relative != resolve_canonical(workspace.root, target["file_path"]).relative:
        raise ValueError("请在问题指定的文件中选择区域")
    if raw.get("sheet") != target["sheet"]:
        raise ValueError("请在问题指定的工作表中选择区域")
    # Re-read once before accepting. A later write still uses normal optimistic concurrency.
    return bind_target(engine, raw, require_ranges=True)


# ── ask_user 入参自愈 ──────────────────────────────────────
# 模型对 questions 项的常见字段名变体统一折叠为规范名；
# 无法折叠出非空数组时抛出附最小示例的 ValueError。

ASK_USER_ARGUMENT_EXAMPLE: dict[str, Any] = {
    "questions": [
        {
            "text": "问题正文",
            "header": "短标题",
            "options": [{"label": "选项A"}, {"label": "选项B"}],
        }
    ]
}

_QUESTION_TEXT_KEYS = ("text", "question", "prompt", "message", "content", "body")
_QUESTION_HEADER_KEYS = ("header", "title", "subject")
_QUESTION_OPTIONS_KEYS = ("options", "choices", "answers")
_QUESTION_MULTI_KEYS = ("multiSelect", "multi_select", "multiple")
_QUESTION_SELECTION_KEYS = ("selection", "target")
_OPTION_LABEL_KEYS = ("label", "text", "title", "name", "value")
_OPTION_DESC_KEYS = ("description", "desc", "detail", "reason")
_QUESTION_HEADER_MAX = 12
_ITEM_LEVEL_KEYS = frozenset(
    _QUESTION_TEXT_KEYS
    + _QUESTION_HEADER_KEYS
    + _QUESTION_OPTIONS_KEYS
    + _QUESTION_MULTI_KEYS
    + _QUESTION_SELECTION_KEYS
)


def _first_text(mapping: dict[str, Any], keys: tuple[str, ...]) -> tuple[str | None, str | None]:
    """返回首个非空字符串字段的 (value, key)。"""
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip(), key
    return None, None


def _normalize_option_item(item: Any) -> Any:
    """选项元素归一化：字符串提升为 {"label": str}，dict 折叠 label/description 别名。"""
    if isinstance(item, str):
        label = item.strip()
        return {"label": label} if label else item
    if not isinstance(item, dict):
        return item
    normalized = dict(item)
    label, label_key = _first_text(normalized, _OPTION_LABEL_KEYS)
    if label and not str(normalized.get("label") or "").strip():
        normalized["label"] = label
        if label_key != "label":
            normalized.pop(label_key, None)
    description, desc_key = _first_text(normalized, _OPTION_DESC_KEYS)
    if description and not str(normalized.get("description") or "").strip():
        normalized["description"] = description
        if desc_key != "description":
            normalized.pop(desc_key, None)
    return normalized


def _normalize_options_value(raw: Any) -> Any:
    """options 值归一化：dict/逗号分隔字符串提升为标准选项数组。"""
    if isinstance(raw, dict):
        return [{"label": str(k), "description": str(v)} for k, v in raw.items()]
    if isinstance(raw, str):
        parts = [part.strip() for part in re.split(r"[,，;；\n]+", raw) if part.strip()]
        return [{"label": part} for part in parts]
    if isinstance(raw, list):
        return [_normalize_option_item(item) for item in raw]
    return raw


def _normalize_question_item(item: Any) -> Any:
    """折叠单个问题项的字段别名，产出规范键名。"""
    if not isinstance(item, dict):
        return item
    normalized = dict(item)

    # text：正文别名折叠；只有标题类字段时提升为正文，仍比直接失败可用。
    text, text_key = _first_text(normalized, _QUESTION_TEXT_KEYS)
    if text is None:
        text, text_key = _first_text(normalized, _QUESTION_HEADER_KEYS)
    if text and not str(normalized.get("text") or "").strip():
        normalized["text"] = text
        if text_key != "text":
            normalized.pop(text_key, None)

    # header：别名折叠并截断到 12 字符（schema 标注为建议值，运行层不再硬失败）。
    header, header_key = _first_text(normalized, _QUESTION_HEADER_KEYS)
    if header:
        normalized["header"] = header[:_QUESTION_HEADER_MAX]
        if header_key != "header":
            normalized.pop(header_key, None)

    # options：别名折叠；dict/字符串形式提升为标准选项数组。
    options_key = next(
        (key for key in _QUESTION_OPTIONS_KEYS if normalized.get(key) not in (None, "")),
        None,
    )
    if options_key is not None:
        options = _normalize_options_value(normalized[options_key])
        if isinstance(options, list):
            normalized["options"] = options
            if options_key != "options":
                normalized.pop(options_key, None)

    # multiSelect：别名与字符串布尔值折叠。
    multi_key = next(
        (key for key in _QUESTION_MULTI_KEYS if normalized.get(key) is not None),
        None,
    )
    if multi_key is not None:
        multi = normalized[multi_key]
        if isinstance(multi, str):
            lowered = multi.strip().lower()
            if lowered in {"true", "1", "yes", "y", "是"}:
                multi = True
            elif lowered in {"false", "0", "no", "n", "否"}:
                multi = False
        normalized["multiSelect"] = multi
        if multi_key != "multiSelect":
            normalized.pop(multi_key, None)

    # selection：别名折叠（取值合法性由 prepare_questions/bind_target 校验）。
    sel_key = next(
        (
            key
            for key in _QUESTION_SELECTION_KEYS
            if isinstance(normalized.get(key), dict)
        ),
        None,
    )
    if sel_key is not None and sel_key != "selection":
        value = normalized.pop(sel_key)
        if not isinstance(normalized.get("selection"), dict):
            normalized["selection"] = value

    return normalized


def normalize_ask_user_arguments(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """把 ask_user 的常见参数变体折叠为规范 questions 数组。

    - 顶层接受 questions（数组/对象/字符串）或 question（对象/字符串）；
    - 单问题时顶层散落的项级字段（options/header/...）并入该项；
    - 项级字段别名折叠见 ``_normalize_question_item``。

    无法折叠出非空数组时抛 ValueError（消息内附最小合法示例）。
    """
    example = json.dumps(ASK_USER_ARGUMENT_EXAMPLE, ensure_ascii=False)
    if not isinstance(arguments, dict):
        raise ValueError(f"工具参数错误: arguments 必须是对象。最小合法示例: {example}")
    consumed_key: str | None = None
    questions_value = arguments.get("questions")
    if isinstance(questions_value, dict):
        questions_value = [questions_value]
    elif isinstance(questions_value, str):
        questions_value = [{"text": questions_value}]
        consumed_key = "questions"
    if not isinstance(questions_value, list) or not questions_value:
        question_value = arguments.get("question")
        if isinstance(question_value, dict):
            questions_value = [question_value]
        elif isinstance(question_value, str) and question_value.strip():
            questions_value = [{"text": question_value}]
        else:
            raise ValueError(
                f"工具参数错误: questions 必须为非空数组。最小合法示例: {example}"
            )
        consumed_key = "question"
    items = [
        {"text": item} if isinstance(item, str) and item.strip() else item
        for item in questions_value
    ]
    if len(items) == 1 and isinstance(items[0], dict):
        merged = dict(items[0])
        for key in _ITEM_LEVEL_KEYS:
            if key != consumed_key and key in arguments and key not in merged:
                merged[key] = arguments[key]
        items[0] = merged
    return [_normalize_question_item(item) for item in items]
