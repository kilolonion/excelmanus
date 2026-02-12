"""事件数据模型 — 定义 AgentEngine 与 StreamRenderer 之间传递的结构化事件。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional


class EventType(Enum):
    """事件类型枚举。"""

    TOOL_CALL_START = "tool_call_start"
    TOOL_CALL_END = "tool_call_end"
    THINKING = "thinking"
    ITERATION_START = "iteration_start"
    ROUTE_START = "route_start"
    ROUTE_END = "route_end"
    CHAT_SUMMARY = "chat_summary"


@dataclass
class ToolCallEvent:
    """工具调用事件数据。

    在 AgentEngine 的 Tool Calling 循环中产生，
    由 StreamRenderer 消费并渲染到终端。
    """

    event_type: EventType
    tool_name: str = ""
    arguments: Dict[str, Any] = field(default_factory=dict)
    result: str = ""
    success: bool = True
    error: Optional[str] = None
    thinking: str = ""
    iteration: int = 0
    timestamp: datetime = field(default_factory=datetime.now)
    # 路由事件字段
    route_mode: str = ""
    skills_used: List[str] = field(default_factory=list)
    tool_scope: List[str] = field(default_factory=list)
    # 执行摘要字段
    total_iterations: int = 0
    total_tool_calls: int = 0
    success_count: int = 0
    failure_count: int = 0
    elapsed_seconds: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        """序列化为字典，将枚举和日期转为可 JSON 化的值。"""
        d = asdict(self)
        d["event_type"] = self.event_type.value
        d["timestamp"] = self.timestamp.isoformat()
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ToolCallEvent:
        """从字典反序列化为 ToolCallEvent 实例。"""
        data = dict(data)  # 避免修改原始字典
        data["event_type"] = EventType(data["event_type"])
        data["timestamp"] = datetime.fromisoformat(data["timestamp"])
        return cls(**data)


# 回调函数类型别名：接收 ToolCallEvent，无返回值
EventCallback = Callable[[ToolCallEvent], None]
