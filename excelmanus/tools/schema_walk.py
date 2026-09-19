"""本地 JSON Schema 节点行走：properties / items / $ref / additionalProperties / union。

只覆盖本项目生成的 schema，不是通用 JSON Schema 引擎。
"""

from __future__ import annotations

from typing import Any


def resolve_local_ref(root: dict[str, Any], ref: str) -> dict[str, Any]:
    if not isinstance(ref, str) or not ref.startswith("#/"):
        return {}
    node: Any = root
    for part in ref[2:].split("/"):
        key = part.replace("~1", "/").replace("~0", "~")
        if not isinstance(node, dict) or key not in node:
            return {}
        node = node[key]
    return node if isinstance(node, dict) else {}


def unwrap_schema(node: dict[str, Any], root: dict[str, Any]) -> dict[str, Any]:
    """跟随 $ref，并跳过 anyOf/oneOf 中的 null 分支。"""
    current: dict[str, Any] = dict(node or {})
    seen: set[str] = set()
    while True:
        ref = current.get("$ref")
        if isinstance(ref, str) and ref not in seen:
            seen.add(ref)
            resolved = resolve_local_ref(root, ref)
            merged = dict(resolved)
            for key, value in current.items():
                if key != "$ref":
                    merged[key] = value
            current = merged
            continue
        progressed = False
        for union_key in ("anyOf", "oneOf"):
            options = current.get(union_key)
            if not isinstance(options, list):
                continue
            non_null = [
                item
                for item in options
                if isinstance(item, dict) and item.get("type") != "null"
            ]
            if len(non_null) == 1:
                merged = dict(non_null[0])
                for key, value in current.items():
                    if key not in {union_key, "$ref"}:
                        merged.setdefault(key, value)
                current = merged
                progressed = True
                break
        if not progressed:
            return current


def _enter_array_items(node: dict[str, Any], root: dict[str, Any]) -> dict[str, Any]:
    items = node.get("items")
    if isinstance(items, dict):
        return unwrap_schema(items, root)
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict) and item.get("type") != "null":
                return unwrap_schema(item, root)
    return node


def property_names(node: dict[str, Any]) -> list[str]:
    props = node.get("properties")
    names = list(props) if isinstance(props, dict) else []
    addl = node.get("additionalProperties")
    if isinstance(addl, dict) and addl:
        names.append("additionalProperties")
    return names


def walk_schema_path(
    schema: dict[str, Any],
    path: str,
    *,
    root: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, list[str], str]:
    """按点号路径走到节点。数组自动进入 items；字典样式走 additionalProperties。"""
    document = root if root is not None else schema
    current = unwrap_schema(schema, document)
    tokens = [part for part in str(path or "").split(".") if part]
    if not tokens:
        return current, property_names(current), ""
    for index, token in enumerate(tokens):
        current = unwrap_schema(current, document)
        if current.get("type") == "array" or "items" in current:
            current = unwrap_schema(_enter_array_items(current, document), document)
        props = current.get("properties") if isinstance(current.get("properties"), dict) else {}
        if token in props:
            current = props[token]
            continue
        addl = current.get("additionalProperties")
        if isinstance(addl, dict) and addl:
            inner = unwrap_schema(addl, document)
            rest = tokens[index + 1 :]
            if not rest:
                inner_props = inner.get("properties") if isinstance(inner.get("properties"), dict) else {}
                if token in inner_props:
                    return inner_props[token], property_names(unwrap_schema(inner_props[token], document)), ""
                return inner, property_names(inner), ""
            if token in (inner.get("properties") or {}):
                return walk_schema_path(inner, ".".join([token, *rest]), root=document)
            return walk_schema_path(inner, ".".join(rest), root=document)
        return None, property_names(current), f"字段不存在: {token}"
    current = unwrap_schema(current, document)
    if current.get("type") == "array" or "items" in current:
        items = _enter_array_items(current, document)
        if property_names(items):
            current = items
    addl = current.get("additionalProperties")
    props = current.get("properties")
    if isinstance(addl, dict) and addl and not (isinstance(props, dict) and props):
        current = unwrap_schema(addl, document)
    return current, property_names(current), ""


def compact_node(node: dict[str, Any], *, limit: int = 6000) -> dict[str, Any]:
    """过大时只保留类型、说明、可查字段，避免截断枚举与必要规则。"""
    encoded = _dump(node)
    if len(encoded) <= limit:
        return dict(node)
    summary: dict[str, Any] = {
        "type": node.get("type"),
        "description": node.get("description"),
        "required": node.get("required"),
        "enum": node.get("enum"),
        "queryable": property_names(node),
        "note": "节点较大，请继续查询子字段，例如 sheets.value_blocks 或 styles.border",
    }
    return {key: value for key, value in summary.items() if value not in (None, [], "")}


def _dump(node: dict[str, Any]) -> str:
    import json

    return json.dumps(node, ensure_ascii=False, separators=(",", ":"), default=str)
