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
    TOOL_CALL_STATE = "tool_call_state"
    THINKING = "thinking"
    ITERATION_START = "iteration_start"
    ROUTE_START = "route_start"  # 历史 replay only；默认路径不再发射
    ROUTE_END = "route_end"  # 历史 replay only；默认路径不再发射
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
    TEXT_PREVIEW = "text_preview"
    FILES_CHANGED = "files_changed"
    MUTATION = "mutation"
    PIPELINE_PROGRESS = "pipeline_progress"
    MEMORY_EXTRACTED = "memory_extracted"
    COMPACTION = "compaction"
    FILE_DOWNLOAD = "file_download"
    PLAN_CREATED = "plan_created"
    VERIFICATION_REPORT = "verification_report"  # 仅用于读取历史，不再产生
    RETRACT_THINKING = "retract_thinking"
    RETRACT_TEXT = "retract_text"
    BATCH_PROGRESS = "batch_progress"  # 批量任务进度
    STAGING_UPDATED = "staging_updated"  # 历史 replay only；overlay 已删除，不再发射
    LLM_RETRY = "llm_retry"  # LLM 调用重试通知
    FAILURE_GUIDANCE = "failure_guidance"  # 结构化失败引导卡片
    CREDENTIAL_REFRESHED = "credential_refreshed"  # OAuth token 自动刷新成功
    CREDENTIAL_EXPIRED = "credential_expired"  # OAuth token 过期且刷新失败
    TOOL_CALL_NOTICE = "tool_call_notice"  # /tools 开启时的简要工具调用通知
    REASONING_NOTICE = "reasoning_notice"  # /reasoning 开启时的推理内容通知
    TURN_START = "turn_start"
    TURN_END = "turn_end"
    TURN_FAILED = "turn_failed"
    STEP_START = "step_start"
    STEP_END = "step_end"
    INBOX_CLAIMED = "inbox_claimed"
    DISPATCH_ACCEPTED = "dispatch_accepted"
    DISPATCH_QUEUED = "dispatch_queued"
    DISPATCH_APPLYING = "dispatch_applying"
    DISPATCH_APPLIED = "dispatch_applied"
    DISPATCH_FAILED = "dispatch_failed"
    DISPATCH_STATE = "dispatch_state"
    TURN_REPLY = "turn_reply"
    UI_HINT = "ui_hint"  # 瞬态建议：不进消息块、不回放
    JEV_TRACE = "jev_trace"  # System One 决策：开启环节发出；不进消息块、默认不回放


@dataclass
class ToolCallEvent:
    """工具调用事件数据。

    在 AgentEngine 的 Tool Calling 循环中产生，
    由 StreamRenderer 消费并渲染到终端。
    """

    event_type: EventType
    tool_call_id: str = ""
    execution_id: str = ""
    execution_state: str = ""
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
    subagent_background: bool = False
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
    question_selection: Optional[Dict[str, Any]] = None
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
    mode_name: str = ""        # "full_access" | "chat_mode" | "show_tool_calls" | "show_reasoning"
    mode_enabled: bool = False
    mode_value: str = ""       # chat_mode 取值 write|read|plan；其它模式可空
    # Excel 预览/Diff 事件字段
    excel_file_path: str = ""
    excel_sheet: str = ""
    excel_columns: List[str] = field(default_factory=list)
    excel_rows: List[List[Any]] = field(default_factory=list)
    excel_total_rows: int = 0
    excel_truncated: bool = False
    excel_cell_styles: List[List[Any]] = field(default_factory=list)
    excel_affected_range: str = ""
    excel_changes: List[Dict[str, Any]] = field(default_factory=list)
    excel_merge_ranges: List[Dict[str, int]] = field(default_factory=list)
    excel_old_merge_ranges: List[Dict[str, int]] = field(default_factory=list)
    excel_metadata_hints: List[str] = field(default_factory=list)
    # Excel 跨文件对比事件扩展字段
    excel_diff_mode: str = ""       # "" (inline) | "cross_file" | "cross_sheet"
    excel_file_b: str = ""          # 对比文件路径（cross_file 模式）
    excel_sheet_b: str = ""         # 对比 sheet 名称
    excel_diff_summary: Optional[Dict[str, Any]] = None  # 对比摘要
    # text_diff 事件字段
    text_diff_file_path: str = ""
    text_diff_hunks: List[str] = field(default_factory=list)
    text_diff_additions: int = 0
    text_diff_deletions: int = 0
    text_diff_truncated: bool = False
    # text_preview 事件字段
    text_preview_file_path: str = ""
    text_preview_content: str = ""
    text_preview_line_count: int = 0
    text_preview_truncated: bool = False
    # files_changed 事件字段
    changed_files: List[str] = field(default_factory=list)
    mutations: List[Dict[str, Any]] = field(default_factory=list)
    # pipeline_progress 事件字段
    pipeline_stage: str = ""
    pipeline_message: str = ""
    # Turn/step 终态
    stop_reason: str = ""
    turn_error: str = ""
    # batch_progress 事件字段（批量任务进度）
    batch_index: int = 0           # 当前任务序号 (0-based)
    batch_total: int = 1           # 总任务数
    batch_item_name: str = ""      # 当前任务名称（如文件名）
    batch_status: str = ""         # "running" | "completed" | "failed"
    batch_elapsed_seconds: float = 0.0  # 当前任务耗时
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
    # staging_updated 事件字段
    staging_action: str = ""             # "applied" | "discarded" | "undone" | "new"
    staging_files: List[Dict[str, Any]] = field(default_factory=list)  # 变化的文件列表
    staging_pending_count: int = 0       # 剩余待应用文件数
    # llm_retry 事件字段
    retry_attempt: int = 0               # 当前重试次数
    retry_max_attempts: int = 0          # 最大尝试次数
    retry_delay_seconds: float = 0.0     # 本次等待延迟（秒）
    retry_error_message: str = ""        # 触发重试的错误信息
    retry_status: str = ""               # "retrying" | "succeeded" | "exhausted"
    # failure_guidance 事件字段
    fg_category: str = ""                # "model" | "transport" | "config" | "quota" | "unknown"
    fg_code: str = ""                    # 机器可读错误码
    fg_title: str = ""                   # 一句话标题
    fg_message: str = ""                 # 用户可见描述
    fg_stage: str = ""                   # 失败阶段
    fg_retryable: bool = False
    fg_diagnostic_id: str = ""           # UUID
    fg_actions: List[Dict[str, str]] = field(default_factory=list)
    fg_provider: str = ""                # provider 标识
    fg_model: str = ""                   # 模型名
    # tool_call_end 可选 UI 投影（merge/files 等小型事实）
    ui: Optional[Dict[str, Any]] = None
    # Code Mode 子调用：父 run_code 的 call id
    parent_call_id: str = ""
    # Driver live 事件（不必持久成第二套日志）
    turn_id: str = ""
    step_id: str = ""
    trace_id: str = ""
    span_id: str = ""
    parent_span_id: str = ""
    request_id: str = ""
    inbox_claimed: List[Dict[str, Any]] = field(default_factory=list)
    dispatch_id: str = ""
    client_message_id: str = ""
    dispatch_mode: str = ""
    dispatch_status: str = ""
    dispatch_error: str = ""
    dispatch: Dict[str, Any] = field(default_factory=dict)
    # ui_hint：回合末 UI 面建议（瞬态，不进消息块）
    ui_hint_surface: str = ""
    ui_hint_file_path: str = ""
    ui_hint_sheet: str = ""
    ui_hint_reason: str = ""
    ui_hint_suppress_auto_open: bool = False
    # jev_trace：有界决策卡（不含密钥 / 完整 state）
    jev_trace: Dict[str, Any] = field(default_factory=dict)
    # compaction：上下文交接生命周期（可回放，非模型上下文）
    compaction: Dict[str, Any] = field(default_factory=dict)

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


TRANSIENT_SSE_TYPES = frozenset({EventType.UI_HINT, EventType.JEV_TRACE})


@dataclass
class MutationEvent:
    """One public identity after a successful AtomicPublish."""

    identity: str
    content_version: str | None = None
    before: str | None = None
    after: str | None = None
    source: str = "runtime"

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "identity": self.identity,
            "source": self.source,
        }
        if self.content_version:
            payload["contentVersion"] = self.content_version
        if self.before:
            payload["before"] = self.before
        if self.after:
            payload["after"] = self.after
        return payload


def mutations_from_identities(
    identities: List[str],
    *,
    content_version: str | None = None,
    content_versions: Dict[str, str] | None = None,
    source: str = "runtime",
) -> List[Dict[str, Any]]:
    versions = content_versions or {}
    return [
        MutationEvent(
            identity=ident,
            content_version=versions.get(ident, content_version),
            source=source,
        ).to_dict()
        for ident in identities
        if ident
    ]


def changed_mutations(
    identities: List[str],
    *,
    workspace_root: str | None = None,
    content_versions: Dict[str, str] | None = None,
    source: str = "runtime",
) -> List[Dict[str, Any]]:
    """Build MutationEvent dicts for a successful write batch.

    Prefer host-tracked after-versions (``remember_content_version`` / receipt).
    Do not re-hash disk: that invents a version the write never reported.
    """
    merged: Dict[str, str] = dict(content_versions or {})
    try:
        from excelmanus.workbook_commit import export_seen_versions

        for ident, version in export_seen_versions().items():
            if version:
                merged.setdefault(ident, version)
    except Exception:
        pass

    del workspace_root
    return mutations_from_identities(
        identities,
        content_versions=merged,
        source=source,
    )


# 回调函数类型别名：接收 ToolCallEvent，无返回值
EventCallback = Callable[[ToolCallEvent], None]
