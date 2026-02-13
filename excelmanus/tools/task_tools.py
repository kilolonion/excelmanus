"""任务清单工具：通过 Tool Calling 让 Agent 管理子任务。"""

from __future__ import annotations

from excelmanus.task_list import TaskStatus, TaskStore
from excelmanus.tools.registry import ToolDef

# 模块级 TaskStore 引用，由 init_store() 注入
_store: TaskStore | None = None


def init_store(store: TaskStore) -> None:
    """注入 TaskStore 实例。"""
    global _store
    _store = store


def _get_store() -> TaskStore:
    if _store is None:
        raise RuntimeError("TaskStore 未初始化")
    return _store


def task_create(title: str, subtasks: list[str]) -> str:
    """创建任务清单。"""
    store = _get_store()
    task_list = store.create(title, subtasks)
    return f"已创建任务清单「{task_list.title}」，共 {len(task_list.items)} 个子任务。"


def task_update(task_index: int, status: str, result: str | None = None) -> str:
    """更新任务项状态。"""
    store = _get_store()
    try:
        new_status = TaskStatus(status)
    except ValueError:
        valid = ", ".join(s.value for s in TaskStatus)
        return f"无效状态 '{status}'，合法值: {valid}"
    try:
        item = store.update_item(task_index, new_status, result)
        return f"任务 #{task_index}「{item.title}」已更新为 {item.status.value}。"
    except (ValueError, IndexError) as exc:
        return str(exc)


def get_tools() -> list[ToolDef]:
    """返回任务清单工具定义。"""
    return [
        ToolDef(
            name="task_create",
            description="创建任务清单，将复杂任务拆解为有序子任务列表",
            input_schema={
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "任务清单标题",
                    },
                    "subtasks": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "子任务标题列表",
                    },
                },
                "required": ["title", "subtasks"],
                "additionalProperties": False,
            },
            func=task_create,
        ),
        ToolDef(
            name="task_update",
            description="更新任务项状态（pending → in_progress → completed/failed）",
            input_schema={
                "type": "object",
                "properties": {
                    "task_index": {
                        "type": "integer",
                        "description": "任务项索引（从 0 开始）",
                    },
                    "status": {
                        "type": "string",
                        "enum": ["pending", "in_progress", "completed", "failed"],
                        "description": "新状态",
                    },
                    "result": {
                        "type": "string",
                        "description": "可选的结果描述",
                    },
                },
                "required": ["task_index", "status"],
                "additionalProperties": False,
            },
            func=task_update,
        ),
    ]
