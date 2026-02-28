"""任务清单工具：通过 Tool Calling 让 Agent 管理子任务。"""

from __future__ import annotations

from excelmanus.task_list import TaskStatus, TaskStore
from excelmanus.tools.registry import ToolDef


_store: TaskStore | None = None


def init_store(store: TaskStore) -> None:
    """兼容旧接口：设置模块级 TaskStore。"""
    global _store
    _store = store


def _resolve_store(store: TaskStore | None = None) -> TaskStore:
    """解析可用的 TaskStore（优先显式注入，其次模块级实例）。"""
    global _store
    if store is not None:
        _store = store
        return store
    if _store is None:
        _store = TaskStore()
    return _store


def task_create(
    title: str,
    subtasks: list[str],
    replace_existing: bool = False,
    *,
    store: TaskStore | None = None,
) -> str:
    """创建任务清单（兼容旧接口，同时支持显式 store 注入）。"""
    active_store = _resolve_store(store)
    if not isinstance(replace_existing, bool):
        raise ValueError("replace_existing 必须为布尔值。")
    task_list = active_store.create(title, subtasks, replace_existing=replace_existing)
    return f"已创建任务清单「{task_list.title}」，共 {len(task_list.items)} 个子任务。"


def task_update(
    task_index: int,
    status: str,
    result: str | None = None,
    *,
    store: TaskStore | None = None,
) -> str:
    """更新任务项状态（兼容旧接口，同时支持显式 store 注入）。"""
    active_store = _resolve_store(store)
    try:
        new_status = TaskStatus(status)
    except ValueError:
        valid = ", ".join(s.value for s in TaskStatus)
        raise ValueError(f"无效状态 '{status}'，合法值: {valid}") from None
    item = active_store.update_item(task_index, new_status, result)
    return f"任务 #{task_index}「{item.title}」已更新为 {item.status.value}。"


def get_tools(store: TaskStore | None = None) -> list[ToolDef]:
    """返回绑定到指定 TaskStore 实例的工具定义。"""
    active_store = _resolve_store(store)

    def task_create(
        title: str,
        subtasks: list,
        replace_existing: bool = False,
    ) -> str:
        return globals()["task_create"](
            title=title,
            subtasks=subtasks,
            replace_existing=replace_existing,
            store=active_store,
        )

    def task_update(task_index: int, status: str, result: str | None = None) -> str:
        return globals()["task_update"](
            task_index=task_index,
            status=status,
            result=result,
            store=active_store,
        )

    return [
        ToolDef(
            name="task_create",
            description=(
                "创建任务清单，将复杂任务拆解为有序子任务列表。"
                "主动使用此工具的场景："
                "(1) 任务涉及 5 个或以上操作步骤；"
                "(2) 需要读取多个文件/sheet 并综合处理；"
                "(3) 用户一次提出多个相关任务。"
                "不需要使用的场景（直接执行即可）："
                "(a) 单一简单操作（仅读取、回答问题）；"
                "(b) 标准三步模式（探查→操作→验证），这是最常见的工作流，无需额外规划；"
                "(c) 少于 5 步的简单任务。"
                "原则：优先直接行动，只在步骤多且有数据依赖时才创建任务清单。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "任务清单标题",
                    },
                    "subtasks": {
                        "type": "array",
                        "items": {
                            "oneOf": [
                                {"type": "string"},
                                {
                                    "type": "object",
                                    "properties": {
                                        "title": {"type": "string", "description": "子任务标题"},
                                        "verification": {
                                            "oneOf": [
                                                {"type": "string", "description": "自由文本验证条件"},
                                                {
                                                    "type": "object",
                                                    "properties": {
                                                        "check_type": {
                                                            "type": "string",
                                                            "enum": ["row_count", "value_match", "formula_exists", "sheet_exists", "custom"],
                                                            "description": "验证类型",
                                                        },
                                                        "target_file": {"type": "string", "description": "目标文件路径"},
                                                        "target_sheet": {"type": "string", "description": "目标 sheet 名"},
                                                        "target_range": {"type": "string", "description": "目标范围（如 A1:C100）"},
                                                        "expected": {"type": "string", "description": "期望值（如 '38', '>0', '非空'）"},
                                                    },
                                                    "required": ["check_type"],
                                                },
                                            ],
                                            "description": "验证条件：字符串或结构化对象",
                                        },
                                    },
                                    "required": ["title"],
                                },
                            ],
                        },
                        "description": "子任务列表，每项可为字符串或含验证条件的对象",
                    },
                    "replace_existing": {
                        "type": "boolean",
                        "description": "是否允许覆盖当前已存在任务清单，默认 false",
                    },
                },
                "required": ["title", "subtasks"],
                "additionalProperties": False,
            },
            func=task_create,
            write_effect="none",
        ),
        ToolDef(
            name="task_update",
            description=(
                "更新任务项状态（pending → in_progress → completed/failed）。"
                "规则："
                "(1) task_update 应与实际操作工具（如 run_code）在同一次调用中并行发出，避免单独占用一次迭代；"
                "(2) 完成后立即标记为 completed；"
                "(3) 同一时间只有一个任务处于 in_progress；"
                "(4) 遇到阻塞或错误时保持 in_progress，不要标记为 completed；"
                "(5) 任务未完全完成（部分实现、存在错误）时禁止标记 completed。"
            ),
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
            write_effect="none",
        ),
    ]
