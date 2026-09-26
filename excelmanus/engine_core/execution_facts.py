"""Committed effects shared by native calls, code-mode, events and delivery.

Paths in input arguments are intentions, not proof of a write. Observations
also carry content_version: it is never sufficient to infer a publication.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any


def records(value: Any) -> list[dict]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def publications(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, dict) or value.get("dry_run") is True:
        return []
    found: dict[tuple, dict] = {}

    def add(path, version=None, *, operation="update", previous_version=None, operation_id=None):
        if not isinstance(path, str) or not path.strip():
            return
        if version is not None and not isinstance(version, str):
            return
        if not isinstance(operation, str):
            return
        item = {"file_path": path, "content_version": version, "operation": operation}
        if isinstance(previous_version, str) and previous_version:
            item["previous_version"] = previous_version
        if isinstance(operation_id, str) and operation_id:
            item["operation_id"] = operation_id
        found[(path, version, operation)] = item

    receipt = value.get("receipt")
    if isinstance(receipt, dict):
        # Partial failure can still have published targets. Never discard them
        # just because the enclosing tool/script failed afterwards.
        for target in records(receipt.get("targets")):
            if target.get("publish_status") in ("published", "committed"):
                add(target.get("path"), target.get("after_version"),
                    operation=target.get("op", "update"),
                    previous_version=target.get("before_version"),
                    operation_id=receipt.get("operation_id"))
    for target in records(value.get("published")):
        if isinstance(target, dict) and target.get("status") == "committed":
            add(target.get("path"), target.get("content_version"))
    sdk = value.get("sdk_calls")
    if isinstance(sdk, dict):
        for target in records(sdk.get("writes")):
            if isinstance(target, dict):
                add(target.get("file_path"), target.get("content_version"),
                    operation=target.get("operation", "update"),
                    previous_version=target.get("previous_version"),
                    operation_id=target.get("operation_id"))
    # A contract violation may occur after a write has committed. The tool
    # must not silently call it uncommitted: retain the identity as uncertain,
    # requiring the delivery ledger to inspect the receipt/current version.
    if value.get("execution_completed") is True and value.get("operation_id"):
        add(value.get("file_path"), value.get("content_version"),
            operation="uncertain", operation_id=value.get("operation_id"))
    return list(found.values())


def tool_publications(tool: str, value: Any, *, success: bool) -> list[dict[str, Any]]:
    result = publications(value)
    if result or not success or not isinstance(value, dict):
        return result
    if value.get("committed") is False or value.get("status") not in ("ok", "success"):
        return []
    # Legacy tools which already went through the shared file service but do
    # not expose its receipt yet. Use returned identities, never input paths.
    if tool in {"write_word", "write_text_file", "edit_text_file", "copy_file", "rename_file", "delete_file"}:
        path = value.get("file_path") or value.get("destination") or value.get("deleted")
        version = value.get("content_version")
        if isinstance(path, str) and (version or value.get("deleted")):
            return [{"file_path": path, "content_version": version,
                     "operation": "delete" if value.get("deleted") else "update"}]
    return []


def project_publications(result, tool: str):
    """Expose the same publication identities through SDK and SSE metadata."""
    effects = tool_publications(tool, result.value, success=result.success)
    if not effects:
        return result
    ui = replace(result.ui_meta, files=list(dict.fromkeys(item["file_path"] for item in effects)))
    return replace(result, ui_meta=ui)
