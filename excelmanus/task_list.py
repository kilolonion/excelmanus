"""任务清单数据模型：TaskStatus、TaskItem、TaskList、TaskStore。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable


class TaskStatus(Enum):
    """任务状态枚举。"""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


# 合法的状态转换映射
VALID_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.PENDING: {TaskStatus.IN_PROGRESS},
    TaskStatus.IN_PROGRESS: {TaskStatus.COMPLETED, TaskStatus.FAILED},
    TaskStatus.COMPLETED: set(),
    TaskStatus.FAILED: set(),
}


@dataclass
class TaskItem:
    """单个任务项。"""

    title: str
    status: TaskStatus = TaskStatus.PENDING
    result: str | None = None
    verification_criteria: str | None = None

    def transition(self, new_status: TaskStatus) -> None:
        """执行状态转换，非法转换抛出 ValueError。"""
        if new_status not in VALID_TRANSITIONS[self.status]:
            raise ValueError(
                f"非法状态转换: {self.status.value} → {new_status.value}"
            )
        self.status = new_status

    def to_dict(self) -> dict:
        """序列化为字典。"""
        d = {"title": self.title, "status": self.status.value}
        if self.result is not None:
            d["result"] = self.result
        if self.verification_criteria is not None:
            d["verification"] = self.verification_criteria
        return d

    @classmethod
    def from_dict(cls, data: dict) -> TaskItem:
        """从字典反序列化。"""
        return cls(
            title=data["title"],
            status=TaskStatus(data["status"]),
            result=data.get("result"),
            verification_criteria=data.get("verification"),
        )


@dataclass
class TaskList:
    """任务清单。"""

    title: str
    items: list[TaskItem] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)

    def progress_summary(self) -> dict[str, int]:
        """返回各状态的计数。"""
        summary = {s.value: 0 for s in TaskStatus}
        for item in self.items:
            summary[item.status.value] += 1
        return summary

    def to_dict(self) -> dict:
        """序列化为字典。"""
        return {
            "title": self.title,
            "items": [item.to_dict() for item in self.items],
            "created_at": self.created_at.isoformat(),
            "progress": self.progress_summary(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> TaskList:
        """从字典反序列化。"""
        return cls(
            title=data["title"],
            items=[TaskItem.from_dict(item) for item in data["items"]],
            created_at=datetime.fromisoformat(data["created_at"]),
        )


class TaskStore:
    """单会话任务清单存储。

    支持可选的 on_change 回调，状态变更时自动触发持久化。
    """

    def __init__(
        self,
        *,
        on_change: Callable[["TaskStore"], None] | None = None,
    ) -> None:
        self._task_list: TaskList | None = None
        self._plan_file_path: str | None = None
        self._on_change = on_change

    @property
    def plan_file_path(self) -> str | None:
        """关联的计划文档路径（相对于工作区根目录）。"""
        return self._plan_file_path

    @plan_file_path.setter
    def plan_file_path(self, value: str | None) -> None:
        self._plan_file_path = value

    @property
    def current(self) -> TaskList | None:
        return self._task_list

    def create(
        self,
        title: str,
        subtask_titles: list[str | dict],
        *,
        replace_existing: bool = False,
    ) -> TaskList:
        """创建新任务清单。

        subtask_titles 支持两种格式（可混合）：
        - str: 纯标题
        - dict: {"title": "...", "verification": "..."}

        当已存在活跃任务清单时，默认拒绝隐式覆盖；
        仅在 replace_existing=True 时允许替换。
        """
        if self._task_list is not None and not replace_existing:
            raise ValueError(
                f"已有任务清单「{self._task_list.title}」，"
                "如需覆盖请显式传入 replace_existing=True。"
            )
        items: list[TaskItem] = []
        for entry in subtask_titles:
            if isinstance(entry, dict):
                t = str(entry.get("title", "")).strip()
                v = (entry.get("verification") or "").strip() or None
                items.append(TaskItem(title=t, verification_criteria=v))
            else:
                items.append(TaskItem(title=str(entry)))
        self._task_list = TaskList(title=title, items=items)
        self._notify_change()
        return self._task_list

    def update_item(
        self, index: int, new_status: TaskStatus, result: str | None = None
    ) -> TaskItem:
        """更新指定任务项的状态。"""
        if self._task_list is None:
            raise ValueError("当前没有活跃的任务清单")
        if index < 0 or index >= len(self._task_list.items):
            raise IndexError(
                f"任务索引 {index} 超出范围 [0, {len(self._task_list.items) - 1}]"
            )
        item = self._task_list.items[index]
        item.transition(new_status)
        if result is not None:
            item.result = result
        self._notify_change()
        return item

    def clear(self) -> None:
        """清除当前任务清单和关联的计划文档路径。"""
        self._task_list = None
        self._plan_file_path = None
        self._notify_change()

    def _notify_change(self) -> None:
        """触发持久化回调（如已注册）。"""
        if self._on_change is not None:
            try:
                self._on_change(self)
            except Exception:  # noqa: BLE001
                pass  # 持久化失败不影响内存操作

    def to_dict(self) -> dict[str, Any]:
        """序列化整个 TaskStore 状态为 dict。"""
        result: dict[str, Any] = {
            "plan_file_path": self._plan_file_path,
        }
        if self._task_list is not None:
            result["task_list"] = self._task_list.to_dict()
        return result

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any],
        *,
        on_change: Callable[["TaskStore"], None] | None = None,
    ) -> "TaskStore":
        """从 dict 恢复 TaskStore 状态。"""
        store = cls(on_change=on_change)
        store._plan_file_path = data.get("plan_file_path")
        tl_data = data.get("task_list")
        if tl_data is not None and isinstance(tl_data, dict):
            try:
                store._task_list = TaskList.from_dict(tl_data)
            except (KeyError, ValueError):
                pass  # 损坏的数据不阻塞恢复
        return store
