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
    SUBAGENT_START = "subagent_start"
    SUBAGENT_END = "subagent_end"
    SUBAGENT_ITERATION = "subagent_iteration"
    SUBAGENT_SUMMARY = "subagent_summary"
    SUBAGENT_TOOL_START = "subagent_tool_start"
    SUBAGENT_TOOL_END = "subagent_tool_end"
    CHAT_SUMMARY = "chat_summary"
    TASK_LIST_CREATED = "task_list_created"
    TASK_ITEM_UPDATED = "task_item_updated"
    USER_QUESTION = "user_question"
    PENDING_APPROVAL = "pending_approval"
    APPROVAL_RESOLVED = "approval_resolved"
    THINKING_DELTA = "thinking_delta"
    TEXT_DELTA = "text_delta"
    TOOL_CALL_ARGS_DELTA = "tool_call_args_delta"
    MODE_CHANGED = "mode_changed"
    EXCEL_PREVIEW = "excel_preview"
    EXCEL_DIFF = "excel_diff"
    TEXT_DIFF = "text_diff"
    FILES_CHANGED = "files_changed"
    PIPELINE_PROGRESS = "pipeline_progress"
    MEMORY_EXTRACTED = "memory_extracted"
    FILE_DOWNLOAD = "file_download"
    PLAN_CREATED = "plan_created"


@dataclass
class ToolCallEvent:
    """工具调用事件数据。

    在 AgentEngine 的 Tool Calling 循环中产生，
    由 StreamRenderer 消费并渲染到终端。
    """

    event_type: EventType
    tool_call_id: str = ""
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
    # subagent 事件字段
    subagent_reason: str = ""
    subagent_tools: List[str] = field(default_factory=list)
    subagent_summary: str = ""
    subagent_success: bool = True
    subagent_name: str = ""
    subagent_permission_mode: str = ""
    subagent_conversation_id: str = ""
    subagent_iterations: int = 0
    subagent_tool_calls: int = 0
    subagent_tool_index: int = 0  # 子代理内部工具调用序号
    # 执行摘要字段
    total_iterations: int = 0
    total_tool_calls: int = 0
    success_count: int = 0
    failure_count: int = 0
    elapsed_seconds: float = 0.0
    # token 使用统计
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    # 任务清单事件字段
    task_list_data: Optional[Dict[str, Any]] = None  # TaskList.to_dict() 的结果
    task_index: Optional[int] = None                  # 更新的任务项索引
    task_status: str = ""                             # 更新后的状态
    task_result: Optional[str] = None                 # 任务项结果
    # ask_user 问题事件字段
    question_id: Optional[str] = None
    question_header: str = ""
    question_text: str = ""
    question_options: List[Dict[str, Any]] = field(default_factory=list)
    question_multi_select: bool = False
    question_queue_size: int = 0
    # 待确认审批事件字段
    approval_id: str = ""
    approval_tool_name: str = ""
    approval_arguments: Dict[str, Any] = field(default_factory=dict)
    approval_risk_level: str = ""
    approval_args_summary: Dict[str, str] = field(default_factory=dict)
    approval_undoable: bool = False
    approval_has_changes: bool = False
    # 流式 delta 字段
    text_delta: str = ""
    thinking_delta: str = ""
    args_delta: str = ""
    # 模式变更事件字段
    mode_name: str = ""        # "full_access" | "plan_mode"
    mode_enabled: bool = False
    # Excel 预览/Diff 事件字段
    excel_file_path: str = ""
    excel_sheet: str = ""
    excel_columns: List[str] = field(default_factory=list)
    excel_rows: List[List[Any]] = field(default_factory=list)
    excel_total_rows: int = 0
    excel_truncated: bool = False
    excel_affected_range: str = ""
    excel_changes: List[Dict[str, Any]] = field(default_factory=list)
    # text_diff 事件字段
    text_diff_file_path: str = ""
    text_diff_hunks: List[str] = field(default_factory=list)
    text_diff_additions: int = 0
    text_diff_deletions: int = 0
    text_diff_truncated: bool = False
    # files_changed 事件字段
    changed_files: List[str] = field(default_factory=list)
    # pipeline_progress 事件字段
    pipeline_stage: str = ""
    pipeline_message: str = ""
    pipeline_phase_index: int = -1  # 当前阶段序号 (0-3)
    pipeline_total_phases: int = 4
    pipeline_spec_path: str = ""  # 当前阶段产出的 spec 文件路径
    pipeline_diff: Optional[Dict[str, Any]] = None  # 阶段间 diff 数据
    pipeline_checkpoint: Optional[Dict[str, Any]] = None  # 断点续跑信息
    # memory_extracted 事件字段
    memory_entries: List[Dict[str, Any]] = field(default_factory=list)
    memory_trigger: str = ""  # "periodic" | "pre_compaction" | "session_end"
    # file_download 事件字段
    download_file_path: str = ""
    download_filename: str = ""
    download_description: str = ""
    # plan_created 事件字段
    plan_file_path: str = ""
    plan_title: str = ""
    plan_task_count: int = 0

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
