"""Antigravity（Google Cloud Code Assist 订阅）Chat Completions 客户端。

复用 GeminiClient 的 OpenAI ↔ Gemini 互转，仅把请求/响应改包成
Cloud Code ``v1internal`` 信封：

    请求: POST {base}/v1internal:streamGenerateContent?alt=sse
          {"project": ..., "model": ..., "requestType": "agent",
           "userAgent": "antigravity", "requestId": ...,
           "request": {<gemini generateContent 载荷>}}
    响应: SSE ``data: {"response": {<gemini chunk>}}`` 包裹。

schema 清洗、toolConfig 校验、sessionId/requestId 规则见各函数文档。
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from typing import Any

import httpx

from excelmanus.providers.gemini import (
    GeminiClient,
    _ChatCompletion,
    _gemini_response_to_openai,
    iter_gemini_sse_deltas,
)
from excelmanus.providers.request_body import gemini_body

logger = logging.getLogger(__name__)

# 凭证层把 GCP project id 写进该请求头，客户端消费后放入信封、不上送上游。
PROJECT_ID_HEADER = "X-Antigravity-Project-Id"

_REQUEST_UA = "antigravity/hub/2.9.1 darwin/arm64"
_PLACEHOLDER_REASON_DESC = "Brief explanation of why you are calling this tool"

# 上游 proto-JSON schema 不支持的关键字：移到 description 提示后删除。
_CONSTRAINT_KEYS = (
    "minLength", "maxLength", "exclusiveMinimum", "exclusiveMaximum",
    "pattern", "minItems", "maxItems", "uniqueItems", "contains", "format",
    "default", "examples", "minimum", "maximum", "multipleOf",
)
# 直接删除（不提示）的 schema 关键字。
_DROP_KEYS = frozenset({
    "$schema", "$defs", "definitions", "$ref", "$id", "id",
    "$anchor", "$vocabulary", "$dynamicRef", "$dynamicAnchor",
    "propertyNames", "patternProperties", "if", "then", "else", "not",
    "$comment", "enumDescriptions", "enumTitles", "prefill", "deprecated",
    "encrypted", "additionalItems", "unevaluatedProperties",
    "unevaluatedItems", "contentSchema",
})
# 需要递归清洗的 schema 容器键。
_NESTED_KEYS = (
    "properties", "items", "prefixItems", "additionalProperties",
    "anyOf", "oneOf", "allOf", "$defs", "definitions", "dependentSchemas",
    "dependencies", "contains", "propertyNames",
)


def _merge_hint(existing: str, hint: str) -> str:
    if not existing:
        return hint
    if existing == hint or existing.startswith(hint + " (") or f"({hint})" in existing:
        return existing
    return f"{existing} ({hint})"


def _append_hint(node: dict[str, Any], hint: str) -> None:
    node["description"] = _merge_hint(str(node.get("description") or ""), hint)


def _resolve_pointer(root: dict[str, Any], ref: str) -> Any:
    """解析 ``#/path/to`` 形式的本地 JSON Pointer。"""
    current: Any = root
    for raw in ref[2:].split("/"):
        part = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict):
            if part not in current:
                return None
            current = current[part]
        elif isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                return None
        else:
            return None
    return current


def _merge_missing(existing: Any, incoming: Any) -> Any:
    """用 incoming 填充 existing 缺失的字段（allOf/条件分支合并语义）。"""
    if existing is None:
        return incoming
    if isinstance(existing, dict) and isinstance(incoming, dict):
        out = dict(existing)
        for key, value in incoming.items():
            out[key] = _merge_missing(out.get(key), value)
        return out
    return existing


def _select_best_branch(branches: list[Any]) -> dict[str, Any] | None:
    best: dict[str, Any] | None = None
    best_score = -1
    for item in branches:
        if not isinstance(item, dict):
            continue
        t = item.get("type")
        if t == "object" or isinstance(item.get("properties"), dict):
            score = 3
        elif t == "array" or isinstance(item.get("items"), (dict, list)):
            score = 2
        elif t and t != "null":
            score = 1
        else:
            score = 0
        if score > best_score:
            best_score, best = score, item
    return best


def _clean_ag_node(
    node: Any,
    *,
    root: dict[str, Any],
    validated: bool,
    response_mode: bool,
) -> Any:
    """清洗单个 schema 节点（只在 schema 文档内部递归，不触碰数据键）。"""
    if node is True:
        return {}
    if not isinstance(node, dict):
        return node
    out = dict(node)

    # 1. 本地 $ref 内联（循环引用退化为 "See: <name>" 提示）
    seen: set[str] = set()
    while isinstance(out.get("$ref"), str):
        ref = out["$ref"]
        if ref in seen or not ref.startswith("#/"):
            _append_hint(out, f"See: {ref.rsplit('/', 1)[-1]}")
            out.pop("$ref", None)
            break
        seen.add(ref)
        target = _resolve_pointer(root, ref)
        if not isinstance(target, dict):
            _append_hint(out, f"See: {ref.rsplit('/', 1)[-1]}")
            out.pop("$ref", None)
            break
        out = {**target, **{k: v for k, v in out.items() if k != "$ref"}}

    # 2. const → enum
    if "const" in out:
        out.setdefault("enum", [out.pop("const")])

    # 3. enum：工具参数上游不执行 enum，全部转为提示；
    #    响应 schema 仅丢弃 boolean 枚举。
    enum = out.get("enum")
    if isinstance(enum, list):
        drop = not response_mode or out.get("type") == "boolean"
        if drop:
            vals = [str(v) for v in enum]
            if len(vals) == 1:
                _append_hint(out, f"Allowed: {vals[0]}")
            elif 2 <= len(vals) <= 10:
                _append_hint(out, "Allowed: " + ", ".join(vals))
            out.pop("enum")

    # 4. 约束关键字 → description 提示
    for key in _CONSTRAINT_KEYS:
        if key in out:
            value = out.pop(key)
            if isinstance(value, (dict, list)):
                value = json.dumps(value, ensure_ascii=False)
            _append_hint(out, f"{key}: {value}")

    # 5. additionalProperties：响应 schema 保留 false；其余转提示/丢弃
    if "additionalProperties" in out:
        ap = out["additionalProperties"]
        if response_mode and ap is False:
            pass
        else:
            out.pop("additionalProperties")
            if ap is False:
                _append_hint(out, "No extra properties allowed")

    # 6. not → 提示
    if "not" in out:
        _append_hint(
            out, "not: " + json.dumps(out["not"], ensure_ascii=False),
        )
        out.pop("not")

    # 7. allOf 合并（required 求并集，其余字段补缺不覆盖）
    all_of = out.pop("allOf", None)
    if isinstance(all_of, list):
        for item in all_of:
            if not isinstance(item, dict):
                continue
            for key, value in item.items():
                if key == "required" and isinstance(value, list):
                    cur = out.setdefault("required", [])
                    if isinstance(cur, list):
                        for r in value:
                            if r not in cur:
                                cur.append(r)
                elif key in ("if", "then", "else", "allOf"):
                    continue
                else:
                    out[key] = _merge_missing(out.get(key), value)

    # 8. anyOf/oneOf 压平
    for key in ("anyOf", "oneOf"):
        branches = out.pop(key, None)
        if not isinstance(branches, list) or not branches:
            continue
        if isinstance(out.get("properties"), dict):
            has_null = False
            for branch in branches:
                if not isinstance(branch, dict):
                    continue
                if branch.get("type") == "null":
                    has_null = True
                branch_props = branch.get("properties")
                if isinstance(branch_props, dict):
                    for pk, pv in branch_props.items():
                        out["properties"][pk] = _merge_missing(
                            out["properties"].get(pk), pv,
                        )
            if has_null:
                out["nullable"] = True
            continue
        best = _select_best_branch(branches)
        if best is None:
            continue
        merged = dict(best)
        has_null = any(
            isinstance(b, dict) and b.get("type") == "null" for b in branches
        )
        if has_null and merged.get("type") != "null":
            merged["nullable"] = True
        types = sorted({
            str(b.get("type")) for b in branches
            if isinstance(b, dict) and b.get("type")
        })
        if len(types) > 1:
            _append_hint(merged, "Accepts: " + " | ".join(types))
        parent_desc = out.get("description")
        for k, v in out.items():
            if k != "description":
                merged.setdefault(k, v)
        if isinstance(parent_desc, str) and parent_desc:
            child = str(merged.get("description") or "")
            merged["description"] = (
                parent_desc if not child or child == parent_desc
                else f"{parent_desc} ({child})"
            )
        out = merged

    # 9. type 数组压平（Antigravity 支持原生 nullable）
    type_val = out.get("type")
    if isinstance(type_val, list):
        non_null = [t for t in type_val if isinstance(t, str) and t != "null"]
        out["type"] = non_null[0] if non_null else "string"
        if len(non_null) > 1:
            _append_hint(out, "Accepts: " + " | ".join(non_null))
        if "null" in type_val:
            out["nullable"] = True
            _append_hint(out, "(nullable)")

    # 10. 删除不支持的关键字与 x-* 扩展
    for key in _DROP_KEYS:
        out.pop(key, None)
    if not validated:
        out.pop("title", None)
    for key in [k for k in out if k.startswith("x-")]:
        out.pop(key)

    # 11. 递归清洗子 schema
    props = out.get("properties")
    if isinstance(props, dict):
        cleaned_props: dict[str, Any] = {}
        promoted: list[str] = []
        for pk, pv in props.items():
            if isinstance(pv, dict) and isinstance(pv.get("required"), bool):
                pv = dict(pv)
                if pv.pop("required"):
                    promoted.append(pk)
            cleaned_props[pk] = _clean_ag_node(
                pv, root=root, validated=validated, response_mode=response_mode,
            )
        out["properties"] = cleaned_props
        if promoted:
            cur = out.setdefault("required", [])
            if isinstance(cur, list):
                for name in promoted:
                    if name not in cur:
                        cur.append(name)
    for key in ("items", "additionalProperties", "contains", "propertyNames"):
        sub = out.get(key)
        if isinstance(sub, dict):
            out[key] = _clean_ag_node(
                sub, root=root, validated=validated, response_mode=response_mode,
            )
        elif isinstance(sub, list):
            out[key] = [
                _clean_ag_node(
                    i, root=root, validated=validated,
                    response_mode=response_mode,
                ) if isinstance(i, dict) else i
                for i in sub
            ]
    for key in ("$defs", "definitions", "dependentSchemas", "dependencies"):
        sub = out.get(key)
        if isinstance(sub, dict):
            out[key] = {
                k: _clean_ag_node(
                    v, root=root, validated=validated,
                    response_mode=response_mode,
                ) if isinstance(v, dict) else v
                for k, v in sub.items()
            }

    # 12. required ∩ properties，空则删除
    req = out.get("required")
    if isinstance(req, list):
        props = out.get("properties")
        if isinstance(props, dict):
            req = [r for r in req if r in props]
        if req:
            out["required"] = req
        else:
            out.pop("required")

    # 13. 数组缺 items → 补 string（Gemini/Antigravity 硬性要求）
    if out.get("type") == "array" and "items" not in out:
        out["items"] = {"type": "string"}

    # 14. Claude VALIDATED：每个 object schema 至少一个 required 属性
    if validated and out.get("type") == "object":
        props = out.get("properties")
        if not isinstance(props, dict) or not props:
            out.setdefault("properties", {})["reason"] = {
                "type": "string",
                "description": _PLACEHOLDER_REASON_DESC,
            }
            out["required"] = ["reason"]
        elif not out.get("required"):
            props = out["properties"]
            if "_" not in props:
                props["_"] = {"type": "boolean"}
            out["required"] = ["_"]

    return out


def clean_schema_for_antigravity(
    schema: Any,
    *,
    validated: bool = False,
    response_mode: bool = False,
) -> Any:
    """清洗单个 JSON Schema 使其兼容 Antigravity 上游。

    ``validated``：Claude 模型的 VALIDATED functionCalling 需要
    每个 object schema 至少一个 required 属性（空则补占位字段）。
    ``response_mode``：用于 generationConfig.responseSchema，保留
    ``additionalProperties: false`` 与非布尔 enum。
    """
    if not isinstance(schema, dict):
        return schema
    return _clean_ag_node(
        schema, root=schema, validated=validated, response_mode=response_mode,
    )


def _sanitize_tools(inner: dict[str, Any], *, validated: bool) -> None:
    """就地清洗 request.tools 中 functionDeclarations 的 schema。"""
    tools = inner.get("tools")
    if not isinstance(tools, list):
        return
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        for decl_key in ("functionDeclarations", "function_declarations"):
            decls = tool.get(decl_key)
            if not isinstance(decls, list):
                continue
            for decl in decls:
                if not isinstance(decl, dict):
                    continue
                # parametersJsonSchema → parameters
                if "parametersJsonSchema" in decl:
                    decl["parameters"] = decl.pop("parametersJsonSchema")
                for schema_key in ("parameters", "response"):
                    schema = decl.get(schema_key)
                    if isinstance(schema, dict):
                        decl[schema_key] = clean_schema_for_antigravity(
                            schema, validated=validated,
                        )


def _sanitize_generation_config(inner: dict[str, Any]) -> None:
    """就地清洗 generationConfig 中的 responseSchema。"""
    for container_key in ("generationConfig", "generation_config"):
        config = inner.get(container_key)
        if not isinstance(config, dict):
            continue
        for key in (
            "responseSchema", "responseJsonSchema",
            "response_schema", "response_json_schema",
        ):
            schema = config.get(key)
            if isinstance(schema, dict):
                config[key] = clean_schema_for_antigravity(
                    schema, response_mode=True,
                )


def _stable_session_id(inner: dict[str, Any]) -> str:
    """以首个 user 文本做稳定 session id（同一会话请求落到同一 session）。"""
    contents = inner.get("contents")
    if isinstance(contents, list):
        for content in contents:
            if not isinstance(content, dict):
                continue
            if content.get("role") != "user":
                continue
            parts = content.get("parts")
            if isinstance(parts, list) and parts:
                text = parts[0].get("text") if isinstance(parts[0], dict) else None
                if isinstance(text, str) and text:
                    digest = hashlib.sha256(text.encode("utf-8")).digest()
                    value = int.from_bytes(digest[:8], "big") & 0x7FFFFFFFFFFFFFFF
                    return f"-{value}"
    return f"-{uuid.uuid4().int % 9_000_000_000_000_000_000}"


def build_antigravity_envelope(
    inner_request: dict[str, Any],
    *,
    model: str,
    project_id: str,
    session_id: str | None = None,
) -> dict[str, Any]:
    """把 Gemini generateContent 载荷包装为 Cloud Code v1internal 信封。

    ``inner_request`` 为 gemini_body() 产物（contents/systemInstruction/
    tools/toolConfig/generationConfig）。本函数完成：
    - sessionId / safetySettings / toolConfig 归位
    - claude → VALIDATED functionCalling；非 claude → 移除 maxOutputTokens
    - 工具与响应 schema 清洗
    - requestType / requestId / userAgent / project 信封字段
    """
    inner = dict(inner_request)
    is_image = "image" in model.lower()

    if not is_image:
        inner["sessionId"] = session_id or _stable_session_id(inner)
    inner.pop("safetySettings", None)

    use_validated = "claude" in model.lower()
    _sanitize_tools(inner, validated=use_validated)
    _sanitize_generation_config(inner)

    if use_validated:
        tool_config = inner.setdefault("toolConfig", {})
        if isinstance(tool_config, dict):
            fcc = tool_config.setdefault("functionCallingConfig", {})
            if isinstance(fcc, dict):
                fcc["mode"] = "VALIDATED"
    else:
        gen_config = inner.get("generationConfig")
        if isinstance(gen_config, dict):
            gen_config.pop("maxOutputTokens", None)

    envelope: dict[str, Any] = {
        "model": model,
        "userAgent": "antigravity",
        "requestType": "image_gen" if is_image else "agent",
        "requestId": (
            f"image_gen/{int(time.time() * 1000)}/{uuid.uuid4()}/12"
            if is_image else f"agent-{uuid.uuid4()}"
        ),
        "request": inner,
    }
    if project_id:
        envelope["project"] = project_id
    return envelope


class _AntigravityChatCompletions:
    """模拟 openai.AsyncOpenAI().chat.completions 接口。"""

    def __init__(self, client: "AntigravityClient") -> None:
        self._client = client

    async def create(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: Any = None,
        stream: bool = False,
        **kwargs: Any,
    ) -> _ChatCompletion | Any:
        thinking_budget = kwargs.pop("_thinking_budget", 0)
        thinking_level = kwargs.pop("_thinking_level", "")
        extra_body = kwargs.pop("extra_body", None)
        prepared_body = kwargs.pop("_prepared_body", None)
        extra_headers = kwargs.pop("extra_headers", None)
        if stream:
            return await self._client._generate_stream(
                model=model, messages=messages, tools=tools,
                tool_choice=kwargs.get("tool_choice"),
                thinking_budget=thinking_budget,
                thinking_level=thinking_level,
                extra_body=extra_body,
                prepared_body=prepared_body,
                extra_headers=extra_headers,
            )
        return await self._client._generate(
            model=model, messages=messages, tools=tools,
            tool_choice=kwargs.get("tool_choice"),
            thinking_budget=thinking_budget,
            thinking_level=thinking_level,
            extra_body=extra_body,
            prepared_body=prepared_body,
            extra_headers=extra_headers,
        )


class _AntigravityChat:
    def __init__(self, client: "AntigravityClient") -> None:
        self.completions = _AntigravityChatCompletions(client)


class AntigravityClient(GeminiClient):
    """Antigravity v1internal 客户端（鸭子类型兼容 openai.AsyncOpenAI）。"""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        default_headers: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> None:
        # 不走 GeminiClient.__init__（其会把 base_url 归一化为 .../v1beta）
        self._api_key = api_key
        self._default_model = None
        self._base_url = base_url.rstrip("/")
        self._http = httpx.AsyncClient(timeout=300.0)
        self._default_headers = dict(default_headers or {})
        self.chat = _AntigravityChat(self)

    def _request_headers(
        self, extra_headers: dict[str, str] | None,
    ) -> tuple[dict[str, str], str]:
        """合并默认头与 per-request 头；剥离内部 project 头用于信封。"""
        headers = dict(self._default_headers)
        if extra_headers:
            headers.update(extra_headers)
        project_id = ""
        for key in list(headers):
            if key.lower() == PROJECT_ID_HEADER.lower():
                project_id = headers.pop(key) or project_id
        headers.setdefault("Content-Type", "application/json")
        headers["Authorization"] = f"Bearer {self._api_key}"
        headers.setdefault("User-Agent", _REQUEST_UA)
        return headers, project_id

    async def _generate(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: Any = None,
        tool_choice: Any = None,
        thinking_budget: int = 0,
        thinking_level: str = "",
        extra_body: dict[str, Any] | None = None,
        prepared_body: dict[str, Any] | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> _ChatCompletion:
        inner = (
            dict(prepared_body) if prepared_body is not None
            else gemini_body(
                model, messages, tools, tool_choice=tool_choice,
                thinking_budget=thinking_budget,
                thinking_level=thinking_level, extra_body=extra_body,
            )
        )
        headers, project_id = self._request_headers(extra_headers)
        envelope = build_antigravity_envelope(
            inner, model=model, project_id=project_id,
        )
        url = f"{self._base_url}/v1internal:generateContent"
        try:
            resp = await self._http.post(url, json=envelope, headers=headers)
        except httpx.HTTPError as exc:
            raise RuntimeError(f"Antigravity 请求失败: {exc}") from exc
        if resp.status_code != 200:
            raise RuntimeError(
                f"Antigravity API 错误 (HTTP {resp.status_code}): "
                f"{resp.text[:500]}"
            )
        data = resp.json()
        inner_resp = data.get("response") if isinstance(data, dict) else None
        if not isinstance(inner_resp, dict):
            inner_resp = data if isinstance(data, dict) else {}
        return _gemini_response_to_openai(inner_resp, model)

    async def _generate_stream(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: Any = None,
        tool_choice: Any = None,
        thinking_budget: int = 0,
        thinking_level: str = "",
        extra_body: dict[str, Any] | None = None,
        prepared_body: dict[str, Any] | None = None,
        extra_headers: dict[str, str] | None = None,
    ):
        inner = (
            dict(prepared_body) if prepared_body is not None
            else gemini_body(
                model, messages, tools, tool_choice=tool_choice,
                thinking_budget=thinking_budget,
                thinking_level=thinking_level, extra_body=extra_body,
            )
        )
        headers, project_id = self._request_headers(extra_headers)
        envelope = build_antigravity_envelope(
            inner, model=model, project_id=project_id,
        )
        url = f"{self._base_url}/v1internal:streamGenerateContent"

        async def _stream_generator():
            async with self._http.stream(
                "POST", url, json=envelope, headers=headers,
                params={"alt": "sse"},
            ) as resp:
                if resp.status_code != 200:
                    error_text = await resp.aread()
                    raise RuntimeError(
                        f"Antigravity API 错误 (HTTP {resp.status_code}): "
                        f"{error_text[:500]}"
                    )
                async for delta in iter_gemini_sse_deltas(
                    resp, envelope_key="response",
                ):
                    yield delta

        return _stream_generator()
