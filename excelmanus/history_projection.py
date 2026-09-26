"""Read-only UI projections of durable task operations, including paged history."""

from __future__ import annotations

import json
from copy import deepcopy

from excelmanus.task_list import TaskStatus, TaskStore

TASK_TOOLS = frozenset({"task_create", "task_update", "write_plan"})


def task_calls(message: dict) -> list[dict]:
    if not isinstance(message, dict) or message.get("role") != "assistant":
        return []
    return [call for call in message.get("tool_calls") or []
            if isinstance(call, dict)
            and isinstance(call.get("function"), dict)
            and call["function"].get("name") in TASK_TOOLS]


def project_task_lists(page: list[dict], history: list[dict]) -> list[dict]:
    """Attach each successful task call's snapshot without modifying model history.

    The prefix supplies task titles/state when the page starts with task_update.
    Snapshots are copied at each operation, never taken from the current TaskStore.
    """
    if not any(task_calls(message) for message in page):
        return page
    results = {message["tool_call_id"]: message.get("content")
               for message in history if isinstance(message, dict) and message.get("role") == "tool"
               and isinstance(message.get("tool_call_id"), str)}
    store = TaskStore()
    snapshots: dict[str, dict] = {}
    for message in history:
        for call in task_calls(message):
            call_id = call.get("id")
            if not isinstance(call_id, str) or not call_id or call_id not in results:
                continue
            content = results[call_id]
            try:
                result = json.loads(content) if isinstance(content, str) else content
            except (ValueError, TypeError):
                result = None
            if isinstance(result, dict) and (
                str(result.get("status")) in {"error", "failed", "fail", "blocked"}
                or result.get("ok") is False or result.get("success") is False
                or bool(result.get("error"))
            ):
                continue
            function = call["function"]
            try:
                arguments = function.get("arguments") or {}
                args = json.loads(arguments) if isinstance(arguments, str) else arguments
                if not isinstance(args, dict):
                    continue
                if function["name"] == "task_create":
                    subtasks = args.get("subtasks")
                    if not isinstance(subtasks, list):
                        continue
                    store.create(str(args.get("title") or ""), subtasks, replace_existing=True)
                elif function["name"] == "write_plan":
                    from excelmanus.plan_mode import parse_plan_markdown

                    title, subtasks = parse_plan_markdown(args.get("content") or "")
                    store.create(str(args.get("title") or title), subtasks, replace_existing=True)
                elif store.current is not None:
                    index = args.get("task_index")
                    if not isinstance(index, int) or not 0 <= index < len(store.current.items):
                        continue
                    # A persisted successful result is authoritative even if transition
                    # validation changed since this operation was executed.
                    item = store.current.items[index]
                    item.status = TaskStatus(args.get("status"))
                    item.result = args.get("result")
                if store.current is not None:
                    snapshots[call_id] = deepcopy(store.current.to_dict())
            except (ValueError, TypeError, KeyError):
                continue
    projected = []
    for message in page:
        if not task_calls(message):
            projected.append(message)
            continue
        projected.append({**message, "tool_calls": [
            {**call, "task_list": snapshots[call["id"]]}
            if isinstance(call, dict) and isinstance(call.get("id"), str) and call["id"] in snapshots else call
            for call in message["tool_calls"]
        ]})
    return projected
