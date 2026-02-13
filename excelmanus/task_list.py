"""任务清单数据模型：TaskStatus、TaskItem、TaskList、TaskStore。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


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
        return d

    @classmethod
    def from_dict(cls, data: dict) -> TaskItem:
        """从字典反序列化。"""
        return cls(
            title=data["title"],
            status=TaskStatus(data["status"]),
            result=data.get("result"),
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
    """单会话任务清单存储。"""

    def __init__(self) -> None:
        self._task_list: TaskList | None = None

    @property
    def current(self) -> TaskList | None:
        return self._task_list

    def create(self, title: str, subtask_titles: list[str]) -> TaskList:
        """创建新任务清单，替换已有的。"""
        items = [TaskItem(title=t) for t in subtask_titles]
        self._task_list = TaskList(title=title, items=items)
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
        return item

    def clear(self) -> None:
        """清除当前任务清单。"""
        self._task_list = None
