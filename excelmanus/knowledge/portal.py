"""One navigable read surface for package knowledge and calling-session facts.

No filesystem paths are accepted. Dynamic resources use the same catalog and
public settings as execution; reading a document never expands that scope.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from excelmanus.knowledge.documents import TOPICS, TOPIC_BY_ID, references
from excelmanus.knowledge.reading import Document, find_literal
from excelmanus.knowledge.examples import EXAMPLES, EXAMPLE_BY_ID, example_detail

QUERY_TYPES = ("knowledge_index", "knowledge_search", "knowledge_read", "knowledge_toc",
               "knowledge_find", "knowledge_related", "knowledge_spec", "knowledge_examples", "knowledge_workflow")
SCOPES = ("all", "docs", "tools", "settings", "skills", "errors", "examples")
PAGE_ITEMS = 8
PAGE_CHARS = 3200
_ROOTS = {
    "tools": ("当前工具目录", "授权工具、参数、输出与 SDK；可继续查询具体字段。"),
    "runtime": ("当前运行状态", "本次调用的模式、权限、模型与宿主执行环境。"),
    "settings": ("当前配置", "公开配置的生效值、schema 与修改条件。"),
    "skills": ("当前技能目录", "可发现技能及其加载入口；阅读不激活技能。"),
    "errors": ("错误与恢复目录", "执行层共用的错误分类及恢复建议。"),
    "examples": ("调用示例", "当前 schema 校验过的 JSON 与 Python 示例；查询不执行。"),
    "workflows": ("任务工作流", "按任务组合的工具调用、版本依赖、完整示例与验证路线。"),
}


def next_call(query_type: str, query: str = "", **kwargs: Any) -> dict:
    return {"name": "introspect_capability", "arguments": {
        "query_type": query_type, "query": query, **kwargs,
    }}


def link(ref: str, title: str = "", summary: str = "") -> dict:
    result = {"ref": ref, "title": title or ref,
              "next_call": next_call("knowledge_read", ref)}
    if summary:
        result["summary"] = summary if len(summary) <= 180 else summary[:179] + "…"
    return result


def _session_engine(engine: Any) -> Any:
    """A forked registry must not disclose the parent closure's session facts."""
    from excelmanus.tools.context import current_call, binding_from_engine

    call = current_call()
    if engine is None or call is None:
        return None
    actor = "host" if getattr(engine, "_is_host_session", True) else "child"
    if call.binding.actor != actor or call.binding.session_id != str(getattr(engine, "_session_id", None) or ""):
        return None
    if call.binding.workspace != binding_from_engine(engine).workspace:
        return None
    return engine


def _settings(engine: Any, source: dict) -> dict:
    if engine is None:
        return {}
    from excelmanus.self_management import settings_snapshot, _check_access

    can_modify = "configure_agent" in source and _check_access(engine, write=True) is None
    result = settings_snapshot(engine)
    for row in result.values():
        row["session_configurable"] = row.pop("writable")
        row["can_modify_now"] = bool(row["session_configurable"] and can_modify)
    result["thinking_effort"]["allowed_values"] = list(engine._config.thinking_effort_options)
    return result


def _skills(engine: Any) -> dict:
    if engine is None:
        return {}
    loader = getattr(getattr(engine, "_skill_router", None), "_loader", None)
    if loader is None:
        return {}
    return {name: skill for name, skill in loader.get_skillpacks().items()
            if not skill.disable_model_invocation}


def _index() -> list[dict]:
    return [link("doc:" + topic.id, topic.title, topic.summary) for topic in TOPICS] + [
        link(ref, *labels) for ref, labels in _ROOTS.items()
    ]


def _entries(source: dict, engine: Any) -> list[dict]:
    from excelmanus.engine_core.error_payload import ERROR_CODES, remediation_for

    entries = _index()
    entries += [link("tool:" + name, name, tool.description) for name, tool in sorted(source.items())]
    entries += [link("setting:" + name, name, str((row.get("schema") or {}).get("description", "当前公开设置")))
                for name, row in _settings(engine, source).items()]
    entries += [link("skill:" + name, name, skill.description) for name, skill in sorted(_skills(engine).items())]
    entries += [link("error:" + code, code, remediation_for(code)) for code in sorted(ERROR_CODES)]
    return entries


def _runtime(engine: Any, catalog: Any) -> dict:
    from excelmanus.runtime_capabilities import host_capabilities
    from excelmanus.execution_isolation import docker_enabled
    from excelmanus.tools.context import current_call
    from excelmanus.tools.catalog import resolve_catalog_mode

    call = current_call()
    mode = (resolve_catalog_mode(chat_mode=call.binding.capability.catalog_mode,
                                 tool_access=call.binding.capability.tool_access)
            if call is not None else getattr(catalog, "mode", "unknown"))
    facts = host_capabilities()
    # Host executable paths do not belong in the public configuration view.
    for name in ("soffice", "pdftoppm"):
        facts[name].pop("executable", None)
    result = {
        "catalog_mode": mode,
        "host": facts,
        "runner": "docker" if docker_enabled() else "local",
        "environment_limit": "宿主探测不证明 runner 的权限、容器内可用性或处理当前文件成功。",
        "session": {"status": "unavailable", "reason": "未绑定匹配的会话；不使用其他会话的状态。"},
    }
    if engine is not None and call is not None:
        cap = call.binding.capability
        result["session"] = {
            "status": "available", "actor": call.binding.actor,
            "mode": mode, "tool_access": cap.tool_access,
            "approval": cap.approval, "full_access": cap.full_access,
            "model": engine.current_model, "vision": bool(engine._is_vision_capable),
            "subagent_enabled": engine.subagent_enabled,
        }
    return result


def _read(ref: str, source: dict, engine: Any, catalog: Any, *, disclose: bool = True,
          language: str = "all") -> dict:
    if ref == "workflows" or ref.startswith("workflow:"):
        from excelmanus.knowledge.workflows import workflow_detail
        data = workflow_detail("" if ref == "workflows" else ref.partition(":")[2], source, language)
        return data if "items" in data or data.get("status") == "unavailable" else {"content": json.dumps(data, ensure_ascii=False, indent=2), "format": "json"}
    if ref.startswith("spec:"):
        return _specification(ref[5:], source, language)
    if ref.startswith("doc:"):
        topic = TOPIC_BY_ID.get(ref[4:])
        if topic:
            content = topic.read()
            return {"title": topic.title, "content": content,
                    "links": [link(target) for target in references(content)]}
    elif ref == "tools":
        return {"items": [link("tool:" + name, name, tool.description) for name, tool in sorted(source.items())]}
    elif ref.startswith(("tool:", "fields:", "schema:", "schema-output:")):
        target = ref.partition(":")[2]
        from excelmanus.tools.introspection_tools import _handle_tool_detail, split_tool_query

        name, field_path = split_tool_query(target, source)
        if name not in source:
            return {"status": "unavailable", "reason": "此工具不在当前授权目录；文档提及不授予权限。",
                    "links": [link("tools"), link("runtime"), link("doc:execution")]}
        from excelmanus.tools.output_contracts import output_schema_for

        if ref.startswith(("schema:", "schema-output:")):
            from excelmanus.tools.reference_contract import augment_reference_schema

            if field_path:
                return {"status": "not_found", "reason": "完整 schema 引用只接受准确工具名；字段请用 tool: 或 fields:。",
                        "links": [link("tool:" + name), link("schema:" + name)]}
            schema = (output_schema_for(name, tool_def=source[name]) if ref.startswith("schema-output:")
                      else augment_reference_schema(source[name].input_schema))
            if schema is None:
                return {"status": "unavailable", "reason": "工具未声明输出 schema，不推断。",
                        "links": [link("tool:" + name)]}
            return {"content": json.dumps(schema, ensure_ascii=False, sort_keys=True, indent=2), "format": "json",
                    "notice": "完整 schema 分页；按 revision 沿 next_call 拼接 content，保留类型、分支及 $defs。",
                    "links": [link("tool:" + name)]}

        if ref.startswith("fields:"):
            from excelmanus.tools.reference_contract import augment_reference_schema
            from excelmanus.tools.schema_walk import walk_schema_path

            path = field_path
            schema = augment_reference_schema(source[name].input_schema)
            # Operation kinds are discriminated branches, not ordinary JSON
            # properties. Navigation derives them from the live input schema.
            if name == "apply_spreadsheet_changes" and (path == "operations" or path.startswith("operations.")):
                branches = schema.get("properties", {}).get("operations", {}).get("items", {}).get("oneOf", [])
                kinds = {str(kind): branch for branch in branches
                         for kind in branch.get("properties", {}).get("kind", {}).get("enum", [])}
                if path == "operations":
                    return {"items": [link("tool:" + target + ".kind", "kind")]
                            + [link("tool:" + target + "." + kind, kind) for kind in kinds],
                            "links": [link("tool:" + name), link("schema:" + name)]}
                suffix = path.removeprefix("operations.")
                kind = next((key for key in sorted(kinds, key=len, reverse=True)
                             if suffix == key or suffix.startswith(key + ".")), None)
                if kind is not None:
                    schema = kinds[kind]
                    path = suffix[len(kind):].lstrip(".")
                elif suffix == "kind":
                    return {"items": [], "links": [link("fields:" + name + ".operations")]}
            if path == "output" or path.startswith("output."):
                schema = output_schema_for(name, tool_def=source[name]) or {}
                path = path.removeprefix("output").lstrip(".")
            node, available, _ = walk_schema_path(schema, path)
            if node is None:
                return {"status": "not_found", "reason": "字段不存在，请从工具入口重新定位。",
                        "links": [link("tool:" + name)]}
            items = [link("tool:" + target + "." + key, key) for key in available]
            if not path:
                items.extend(link("tool:" + target + ".$defs." + key, key) for key in schema.get("$defs", {}))
            return {"items": items, "links": [link("tool:" + name)]}

        content = _handle_tool_detail(target, disclose=disclose)
        missing = content.startswith(("字段不存在", "未知输出"))
        links = [link("fields:" + target, "可继续查询的字段"), link("schema:" + name, "完整输入 schema"), link("tools")]
        if not field_path and output_schema_for(name, tool_def=source[name]) is not None:
            links.append(link("tool:" + name + ".output", "输出合同"))
            links.append(link("schema-output:" + name, "完整输出 schema"))
        if not field_path:
            links.append({"ref": "spec:" + name, "title": "完整规范与调用示例",
                          "next_call": next_call("knowledge_spec", name)})
        if missing:
            links = [link("tool:" + name), link("fields:" + name)]
        return {"status": "not_found" if missing else "ok", "content": content,
                "links": links,
                "detail_call": next_call("tool_detail", target)}
    elif ref == "runtime":
        return {"data": _runtime(engine, catalog), "links": [link("settings"), link("tools"), link("doc:execution")]}
    elif ref == "settings" or ref.startswith("setting:"):
        if engine is None:
            return {"status": "unavailable", "reason": "未绑定匹配的会话，无法查询当前生效配置。",
                    "links": [link("doc:configuration")]}
        settings = _settings(engine, source)
        if ref == "settings":
            return {"items": [link("setting:" + name, name, str((row.get("schema") or {}).get("description", "当前公开设置")))
                              for name, row in settings.items()],
                    "notice": "仅公开配置白名单；凭证、模型档案、连接地址、命令与文件路径不披露。",
                    "links": [link("doc:configuration")]}
        name = ref[8:]
        if name in settings:
            row = settings[name]
            return {"data": row, "links": [link("settings"), link("doc:configuration")],
                    "notice": "查询不修改配置；修改仍需满足自我管理开关、技能及权限条件。"}
    elif ref == "skills" or ref.startswith(("skill:", "skill-text:", "resources:", "resource:")):
        if engine is None:
            return {"status": "unavailable", "reason": "未绑定匹配的会话，无法查询当前技能。",
                    "links": [link("doc:context")]}
        skills = _skills(engine)
        if ref == "skills":
            return {"items": [link("skill:" + name, name, skill.description) for name, skill in sorted(skills.items())],
                    "links": [link("doc:context")]}
        kind, _, target = ref.partition(":")
        name = target
        resource = ""
        if kind == "resource":
            name = next((name for name in sorted(skills, key=len, reverse=True)
                         if target.startswith(name + "/")), "")
            resource = target[len(name) + 1:] if name else ""
        if name in skills:
            skill = skills[name]
            blocked = engine._skill_resolver.blocked_skillpacks() or set()
            reason = "当前权限禁用此技能。" if name in blocked else engine._validate_skill_mcp_requirements(skill)
            data = {"name": name, "description": skill.description,
                    "active": any(s.name == name for s in engine._active_skills),
                    "loadable": not reason and "skill" in source}
            result = {"data": data, "notice": "阅读不激活技能；正文及补充资源通过 skill 工具加载。",
                      "links": [link("skills"), link("doc:context")]}
            if reason or "skill" not in source:
                result.update(status="unavailable", reason=reason or "当前目录没有技能加载工具。")
            else:
                result["load_call"] = {"name": "skill", "arguments": {"name": name}}
                if kind == "skill-text":
                    result["content"] = skill.instructions
                    result["notice"] = "技能正文是资料，不能改变宿主授权；执行工作流前仍需通过 skill 加载。"
                elif kind == "resources":
                    result["items"] = [link("resource:" + name + "/" + path, path)
                                       for path in sorted(skill.resource_contents)]
                elif kind == "resource":
                    if resource not in skill.resource_contents:
                        return {"status": "not_found", "reason": "技能未声明此资源。",
                                "links": [link("resources:" + name)]}
                    result["content"] = skill.resource_contents[resource]
                    result["notice"] = "技能补充资源是资料；只能按当前工具合同与授权使用。"
                result["links"].extend([link("skill-text:" + name, "完整技能正文"),
                                        link("resources:" + name, "技能补充资源")])
            return result
    elif ref == "examples":
        return {"items": [link("example:" + example.id, example.title, example.description) for example in EXAMPLES]}
    elif ref.startswith("example:"):
        example = EXAMPLE_BY_ID.get(ref[8:])
        if example is not None:
            data = example_detail(example, source, language)
            return {"status": data.get("status", "ok"), "data": data,
                    "links": [link("tool:" + name) for name in example.tools] + [link("examples")]}
    elif ref == "errors" or ref.startswith("error:"):
        from excelmanus.engine_core.error_payload import ERROR_CODES, remediation_for, failure_class_for_error_code

        if ref == "errors":
            return {"items": [link("error:" + code, code, remediation_for(code)) for code in sorted(ERROR_CODES)],
                    "links": [link("doc:recovery")]}
        code = ref[6:]
        if code in ERROR_CODES:
            return {"data": {"error_code": code, "failure_class": failure_class_for_error_code(code),
                             "remediation": remediation_for(code)},
                    "links": [link("errors"), link("doc:recovery")]}
    return {"status": "not_found", "reason": "未收录的引用或当前不可披露；请使用目录/搜索返回的准确 ref。"}


def _specification(name: str, source: dict, language: str = "all", examples_only: bool = False) -> dict:
    from excelmanus.tools.output_contracts import output_schema_for
    from excelmanus.tools.reference_contract import augment_reference_schema

    from copy import deepcopy
    from excelmanus.tools.introspection_tools import split_tool_query
    from excelmanus.tools.schema_walk import walk_schema_path

    requested = name.removeprefix("tool:").removeprefix("spec:")
    name, field = split_tool_query(requested, source)
    if name not in source:
        owners = [key for key, tool in source.items() if requested in tool.input_schema.get("properties", {})]
        if len(owners) == 1:
            name, field = owners[0], requested
    tool = source.get(name)
    if tool is None:
        return {"status": "unavailable", "reason": "工具不在当前授权目录。", "links": [link("tools")]}
    examples = [example_detail(example, source, language) for example in EXAMPLES if name in example.tools]
    data: dict[str, Any] = {"tool": name, "languages": ["python", "json"] if language == "all" else [language],
                            "examples": examples, "examples_notice": "未列出示例表示尚未收录，不表示能力不可用。"}
    if not examples_only:
        schema = augment_reference_schema(tool.input_schema)
        if field:
            parent_path, _, leaf = field.rpartition(".")
            parent, _, _ = walk_schema_path(schema, parent_path)
            node = (parent or {}).get("properties", {}).get(leaf)
            if node is None:
                node, _, _ = walk_schema_path(schema, field)
            if node is None:
                return {"status": "not_found", "reason": "字段不存在", "links": [link("fields:" + name)]}
            node = deepcopy(node)
            # Close only the local definitions reachable from the selected
            # field. No dangling $refs and no unrelated operation inventory.
            definitions = {}
            def visit(item):
                if isinstance(item, dict):
                    ref = item.get("$ref", "")
                    if ref.startswith("#/$defs/"):
                        key = ref[len("#/$defs/"):]
                        if key not in definitions and key in schema.get("$defs", {}):
                            definitions[key] = deepcopy(schema["$defs"][key])
                            visit(definitions[key])
                    for child in item.values():
                        visit(child)
                elif isinstance(item, list):
                    for child in item:
                        visit(child)
            visit(node)
            if definitions:
                node["$defs"] = definitions
            schema = node
            data["field"] = field
            data["examples"] = [{"id": item.id, "title": item.title,
                                  "next_call": next_call("knowledge_read", "example:" + item.id, language=language)}
                                 for item in EXAMPLES if name in item.tools]
        data.update(description=tool.description, input_schema=schema,
                    output_schema=output_schema_for(name, tool_def=tool), write_effect=tool.write_effect)
    from excelmanus.tools.introspection_tools import _record_loaded_tool
    _record_loaded_tool(tool)
    return {"title": name + ("." + field if field else "") + (" 调用示例" if examples_only else " 工具规范"), "format": "json",
            "content": json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
            "notice": "函数工具规范来自当前执行合同；不是 HTTP OpenAPI。读取不会执行示例。",
            "links": [link("tool:" + name), link("fields:" + name), link("examples")]}


def _kind(ref: str) -> str:
    prefix = ref.partition(":")[0]
    return {"doc": "docs", "tool": "tools", "schema": "tools", "schema-output": "tools", "spec": "tools",
            "setting": "settings", "error": "errors", "skill": "skills", "skill-text": "skills",
            "resources": "skills", "resource": "skills", "example": "examples", "workflow": "examples"}.get(prefix, prefix)


def _materialize(ref: str, source: dict, engine: Any, catalog: Any, *, disclose: bool = False,
                 language: str = "all") -> tuple[dict, Document | None, str]:
    root, _, anchor = ref.partition("#")
    payload = _read(root, source, engine, catalog, disclose=disclose, language=language)
    if payload.get("status", "ok") != "ok":
        return payload, None, anchor
    if "content" not in payload:
        if "data" in payload:
            payload["content"] = json.dumps(payload["data"], ensure_ascii=False, indent=2, sort_keys=True)
        elif root in _ROOTS:
            payload["content"] = "\n".join(_ROOTS[root])
        else:
            payload["content"] = str(payload.get("title", root))
    topic = TOPIC_BY_ID.get(root.removeprefix("doc:")) if root.startswith("doc:") else None
    title = topic.title if topic else payload.get("title") or str(payload.get("data", {}).get("title", root))
    kind = _kind(root)
    source_name = {"docs": "package_document", "skills": "session_skill", "tools": "runtime_contract",
                   "examples": "schema_checked_example", "errors": "runtime_error_catalog"}.get(kind, "session_state")
    doc = Document(root, title, payload["content"], kind, source_name, topic.keywords if topic else "")
    return payload, doc, anchor


def _corpus(source: dict, engine: Any, catalog: Any, scope: str, language: str = "all") -> tuple[list[Document], list[dict]]:
    """Build only from this call's authorized sources; search never loads tools."""
    refs = [entry["ref"] for entry in _entries(source, engine)]
    refs += ["schema:" + name for name in sorted(source)]
    refs += ["example:" + example.id for example in EXAMPLES]
    for name, skill in sorted(_skills(engine).items()):
        refs.append("skill-text:" + name)
        refs.extend("resource:" + name + "/" + path for path in sorted(skill.resource_contents))
    documents, unavailable = [], []
    for ref in dict.fromkeys(refs):
        if scope != "all" and _kind(ref) != scope:
            continue
        try:
            payload, doc, _ = _materialize(ref, source, engine, catalog, language=language)
        except (OSError, UnicodeError):
            unavailable.append({"ref": ref, "reason": "随包资源缺失或损坏"})
            continue
        if doc is not None:
            documents.append(doc)
        elif payload.get("status") == "unavailable":
            # Do not index or quote unavailable skill bodies or schemas.
            unavailable.append({"ref": ref, "reason": payload.get("reason", "当前不可用")})
    return documents, unavailable


def _location(doc: Document, start: int, end: int, *, anchor: str = "") -> dict:
    citation = doc.citation(start, end)
    ref = doc.ref + ("#" + anchor if anchor else "")
    return {"ref": ref, "citation": citation,
            "next_call": next_call("knowledge_read", ref, line_start=citation["line_start"],
                                   line_end=citation["line_end"], content_revision=doc.revision)}


def _search(query: str, source: dict, engine: Any, catalog: Any, scope: str, language: str = "all") -> dict:
    from excelmanus.knowledge.search import rank

    docs, unavailable = _corpus(source, engine, catalog, scope, language)
    items = []
    for hit in rank(docs, query):
        doc = hit.document
        # Locate the excerpt around a literal match if possible, preserving
        # source offsets even for non-ASCII text and very long source lines.
        matches = find_literal(doc, query, start=hit.start, end=hit.end)
        center = matches[0][0] if matches else hit.start
        start, end = max(hit.start, center - 100), min(hit.end, max(hit.start, center - 100) + 420)
        excerpt = doc.text[start:end]
        location = _location(doc, start, end, anchor=hit.section.anchor if hit.section else "")
        if doc.kind == "examples" and language != "all":
            location["next_call"]["arguments"]["language"] = language
        items.append({**location, "title": doc.title, "kind": doc.kind, "score": hit.score,
                      "hierarchy": list(hit.section.hierarchy) if hit.section else [doc.title],
                      "snippet": excerpt, "matched_terms": list(hit.matched_terms[:10])})
    return {"items": items, "retrieval": "BM25 + exact identifiers + bilingual aliases",
            "coverage": {"indexed_resources": len(docs), "unavailable_resources": len(unavailable)},
            "notice": "搜索只用于定位。请执行结果的 next_call 读取准确正文再回答；命中不证明能执行。",
            "suggestions": [] if items else ["缩短关键词，使用准确工具名/错误码，或 scope=all 浏览目录。"]}


def _toc(doc: Document, start: int = 0, end: int | None = None) -> dict:
    end = len(doc.text) if end is None else end
    return {"title": doc.title, "items": [
        {**_location(doc, section.start, section.end, anchor=section.anchor),
         "title": section.title, "level": section.level, "hierarchy": list(section.hierarchy)}
        for section in doc.sections if start <= section.start < end], "content_revision": doc.revision,
        "notice": "无标题资源可用 knowledge_find 查找文本，或按行读取。"}


def _find(doc: Document, needle: str, anchor: str, start: int, end: int) -> dict:
    items = []
    for match_start, match_end in find_literal(doc, needle, start=start, end=end):
        lo, hi = max(start, match_start - 100), min(end, match_end + 180)
        items.append({**_location(doc, match_start, match_end, anchor=anchor),
                      "snippet": doc.text[lo:hi], "match": doc.text[match_start:match_end],
                      "snippet_citation": doc.citation(lo, hi)})
    return {"items": items, "content_revision": doc.revision, "match_mode": "literal_case_insensitive"}


def _related(ref: str, source: dict, engine: Any, catalog: Any) -> dict:
    root, _, anchor = ref.partition("#")
    payload, doc, _ = _materialize(ref, source, engine, catalog)
    if doc is None:
        return payload
    start, end = doc.bounds(anchor)
    outgoing = references(doc.text[start:end])
    if not outgoing:
        outgoing = [item["ref"] for item in payload.get("links", [])]
    items = [{**link(target), "relation": "references"} for target in outgoing if target != ref]
    if anchor:
        items.insert(0, {**link(root, doc.title), "relation": "parent"})
    documents, _ = _corpus(source, engine, catalog, "all")
    for other in documents:
        if other.ref == root:
            continue
        for target in references(other.text):
            if target == ref or (not anchor and target.partition("#")[0] == root):
                items.append({**link(other.ref, other.title), "relation": "referenced_by"})
                break
    return {"items": items, "notice": "引用是导航；目标可能因当前模式或依赖不可用，读取时会说明原因。"}


def query_knowledge(query_type: str, query: str, *, catalog: Any, engine: Any = None,
                    page: int = 1, revision: str = "", scope: str = "all", limit: int = PAGE_ITEMS,
                    ref: str = "", line_start: int | None = None, line_end: int | None = None,
                    content_revision: str = "", language: str = "all", examples_only: bool = False) -> str:
    from excelmanus import __version__

    engine = _session_engine(engine)
    source = catalog.introspection_source() if catalog is not None else {}
    from excelmanus.tools.context import current_call

    call = current_call()
    if call is not None:
        cap = call.binding.capability
        source = {name: tool for name, tool in source.items()
                  if name not in cap.disallowed_tools
                  and (cap.allowed_tools is None or name in cap.allowed_tools)}
    digest = catalog.digest() if catalog is not None else "unknown"
    base = {"portal_version": 2, "product_version": __version__, "catalog_digest": digest,
            "query_type": query_type, "ref": query, "status": "ok",
            "source": "package_document" if query.startswith("doc:") else "current_catalog_and_session",
            "index_call": next_call("knowledge_index")}
    if (isinstance(page, bool) or not isinstance(page, int) or page < 1
            or isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 20
            or scope not in SCOPES or language not in {"all", "python", "json"}
            or not all(isinstance(value, str) for value in (query, ref, revision, content_revision))
            or not isinstance(examples_only, bool)
            or any(value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 1)
                   for value in (line_start, line_end))):
        return json.dumps({**base, "status": "invalid_query", "reason": "请按 schema 指定查询范围、语言、行号、页码和 limit（1–20）。"}, ensure_ascii=False)
    options = {key: value for key, value in dict(scope=scope, limit=limit, ref=ref, line_start=line_start,
                line_end=line_end, content_revision=content_revision, language=language, examples_only=examples_only).items()
               if value not in (None, "", "all", False) and not (key == "limit" and value == PAGE_ITEMS)}
    restart_options = {key: value for key, value in options.items() if key != "content_revision"}
    restart = next_call(query_type, query, **restart_options)
    payload: dict[str, Any]
    document: Document | None = None
    selection_start = 0
    try:
        if query_type == "knowledge_index":
            entries = _index() if scope == "all" else [entry for entry in _entries(source, engine) if _kind(entry["ref"]) == scope]
            if scope == "examples":
                entries += [link("example:" + example.id, example.title, example.description) for example in EXAMPLES]
            payload = {"title": "ExcelManus 文档与能力服务", "items": entries,
                       "notice": "搜索定位后读取正文；工具规范用 knowledge_spec，示例可按 language 筛选。",
                       "operations": list(QUERY_TYPES)}
        elif query_type == "knowledge_search":
            payload = (_search(query, source, engine, catalog, scope, language) if query.strip() else
                       {"items": [entry for entry in _index() if scope == "all" or _kind(entry["ref"]) == scope],
                        "notice": "空查询返回主题目录。"})
        elif query_type in {"knowledge_read", "knowledge_toc", "knowledge_find"}:
            target = ref if query_type == "knowledge_find" else query
            payload, document, anchor = _materialize(target, source, engine, catalog,
                disclose=query_type == "knowledge_read", language=language)
            if document is not None:
                if content_revision and content_revision != document.revision:
                    return json.dumps({**base, "status": "stale", "reason": "搜索或引用的正文已变化，请重新读取。",
                                       "restart_call": restart}, ensure_ascii=False)
                selection_start, end = document.bounds(anchor, line_start, line_end)
                if query_type == "knowledge_toc":
                    payload = _toc(document, selection_start, end)
                elif query_type == "knowledge_find":
                    payload = _find(document, query, anchor, selection_start, end)
                else:
                    payload["content"] = document.text[selection_start:end]
                    # Large examples and dynamic documents use their lossless
                    # content pages instead of duplicating an unbounded object.
                    if len(json.dumps(payload.get("data", {}), ensure_ascii=False)) > 1600:
                        payload.pop("data", None)
                    payload.update(content_revision=document.revision,
                                   toc_call=next_call("knowledge_toc", document.ref, language=language),
                                   related_call=next_call("knowledge_related", target))
        elif query_type == "knowledge_related":
            payload = _related(query, source, engine, catalog)
        elif query_type == "knowledge_spec":
            payload = _specification(query, source, language, examples_only)
            if payload.get("status", "ok") == "ok":
                document = Document("spec:" + query.removeprefix("tool:").removeprefix("spec:"),
                                    payload["title"], payload["content"], "tools", "runtime_contract")
        elif query_type == "knowledge_workflow":
            from excelmanus.knowledge.workflows import workflow_detail
            data = workflow_detail(query, source, language)
            payload = data if "items" in data or data.get("status") == "unavailable" else {
                "title": query, "format": "json", "content": json.dumps(data, ensure_ascii=False, indent=2),
                "notice": "此工作流来自当前工具合同；按 next_call 读取后续页，不授予额外权限。"}
        elif query_type == "knowledge_examples":
            target = query.removeprefix("tool:")
            selected = [example for example in EXAMPLES if not target or target in example.tools or target == example.id]
            if not selected and query:
                payload = _search(query, source, engine, catalog, "examples", language)
            else:
                payload = {"items": [link("example:" + example.id, example.title, example.description) for example in selected]}
            for item in payload["items"]:
                item["next_call"]["arguments"]["language"] = language
        else:
            payload = {"status": "invalid_query", "reason": "未知门户查询类型。"}
    except LookupError as exc:
        payload = {"status": "not_found", "reason": str(exc),
                   "toc_call": next_call("knowledge_toc", (ref or query).partition("#")[0])}
        document = None
    except ValueError as exc:
        payload = {"status": "invalid_query", "reason": str(exc)}
        document = None
    except (OSError, UnicodeError) as exc:
        # Never silently fall back to a workspace file or old product manual.
        payload = {"status": "unavailable", "reason": "随包知识资源缺失或损坏，请修复安装。",
                   "error_type": type(exc).__name__}

    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    current_revision = hashlib.sha256((str(digest) + query_type + query + json.dumps(options, sort_keys=True) + raw).encode()).hexdigest()[:20]
    base["revision"] = current_revision
    if revision and revision != current_revision:
        return json.dumps({**base, "status": "stale", "reason": "内容或授权目录已变化，请重新读取第一页。",
                           "restart_call": restart}, ensure_ascii=False)
    count = len(payload.get("items", [])) if "items" in payload else len(payload.get("content", ""))
    item_pages: list[list[dict]] = [[]]
    if "items" in payload:
        # Keep result cards below the normal host cap, including citations.
        used = 0
        for item in payload["items"]:
            size = len(json.dumps(item, ensure_ascii=False))
            if item_pages[-1] and (len(item_pages[-1]) >= limit or used + size > 6500):
                item_pages.append([])
                used = 0
            item_pages[-1].append(item)
            used += size
        pages = len(item_pages)
    else:
        pages = max(1, (count + PAGE_CHARS - 1) // PAGE_CHARS)
    if page > pages:
        return json.dumps({**base, "status": "invalid_query", "reason": "页码超出范围。", "pages": pages,
                           "restart_call": restart}, ensure_ascii=False)
    if "items" in payload:
        payload["total_items"] = count
        payload["items"] = item_pages[page - 1]
    elif "content" in payload:
        offset = (page - 1) * PAGE_CHARS
        payload["content"] = payload["content"][offset:page * PAGE_CHARS]
        if document is not None:
            payload["citation"] = document.citation(selection_start + offset,
                                                     selection_start + offset + len(payload["content"]))
    result = {**base, **payload, "page": page, "pages": pages}
    if "citation" in result:
        result["citation"]["fetch_call"] = next_call(query_type, query, page=page, revision=current_revision, **options)
    if page < pages:
        result["next_call"] = next_call(query_type, query, page=page + 1, revision=current_revision, **options)
    return json.dumps(result, ensure_ascii=False, default=str)
