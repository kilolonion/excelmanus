export interface FileAttachment {
  filename: string;
  path: string;
  size: number;
}

export interface ExampleContext {
  id: string;
  workflow?: string;
  sample?: string;
}

/** 输入框中附件的上传追踪状态 */
export interface AttachedFile {
  id: string;
  file: File;
  status: "uploading" | "success" | "failed";
  uploadResult?: { filename: string; path: string; size: number };
  error?: string;
  /** 预编码的 base64 数据（由示例卡片预上传时生成），sendMessage 可跳过重复编码 */
  cachedBase64?: string;
  /** 侧边栏拖入的已有工作区文件：展示附件芯片，但不走“新上传”通知 */
  fromWorkspace?: boolean;
  /** Scope captured when the file was imported or attached. */
  workspaceKey?: string;
}

export interface Session {
  id: string;
  title: string;
  messageCount: number;
  inFlight: boolean;
  updatedAt?: string;
  /** 本地创建时间戳（Date.now()），用于 mergeSessions 宽限期保护 */
  createdAt?: number;
  workspacePath?: string;
  workspaceId?: string | null;
  workspaceTitle?: string;
  blank?: boolean;
  pendingApproval?: boolean;
  pendingQuestion?: boolean;
}

export interface WorkspaceFolder {
  id: string;
  path: string;
  title: string;
  created_at?: string;
  updated_at?: string;
  sort_index?: number;
  is_default?: boolean;
}

export interface SessionDetail {
  id: string;
  messageCount: number;
  historyRevision?: string;
  inFlight: boolean;
  activeStreamId: string | null;
  latestSeq: number;
  fullAccessEnabled: boolean;
  autoApproveEnabled: boolean;
  chatMode: "write" | "read" | "plan";
  currentModel: string | null;
  currentModelName: string | null;
  visionCapable: boolean | null;
  messages: unknown[];
  pendingApproval: Approval | null;
  pendingQuestion: Question | null;
  lastRoute: { routeMode: string; skillsUsed: string[]; toolScope: string[] } | null;
}

export interface SessionMessagesPage {
  messages: unknown[];
  total: number;
  offset: number;
  limit: number;
  hasMore: boolean;
}

export interface SubagentToolCall {
  index: number;
  name: string;
  argsSummary: string;
  status: "running" | "success" | "error";
  result?: string;
  error?: string;
  args?: Record<string, unknown>;
}

export type SubagentRunStatus =
  | "queued" | "running" | "waiting_input" | "completed" | "paused"
  | "interrupted" | "aborted" | "error" | "max-tokens" | "refusal";

/** 会话任务 API 的记录；时间戳沿用后端的 Unix 秒。 */
export interface SubagentRun {
  run_id: string;
  agent_name: string;
  task: string;
  file_paths: string[];
  background: boolean;
  status: SubagentRunStatus;
  created_at: number;
  started_at: number | null;
  finished_at: number | null;
  iteration: number;
  tool_calls: number;
  last_tool: string;
  resumed_from: string | null;
  changed_files?: string[];
  pending_question?: {
    question_id: string;
    header: string;
    text: string;
    multi_select: boolean;
    options: { label: string; description: string; value: string; is_other: boolean }[];
  };
  result: {
    stop_reason: string;
    output: string;
    diagnostic: string | null;
    iterations: number;
    tool_calls_count: number;
    structured_changes: { path: string; tool_name: string; change_type: string; sheets_affected: string[] }[];
    observed_files: string[];
  } | null;
}

/** 会话任务清单快照（GET /sessions/{id}/task-list），结构对应后端 TaskList.to_dict()。 */
export interface SessionTaskList {
  title: string;
  items: {
    title: string;
    status: string;
    result?: string | null;
    verification?: string | Record<string, unknown> | null;
  }[];
  created_at?: string;
  progress?: Record<string, number>;
  plan_file_path?: string;
}

export type AssistantBlock = { historyKey?: string } & (
  | {
      type: "thinking";
      content: string;
      duration?: number;
      startedAt?: number;
      /** Iteration that produced this block; used to collapse replay aliases. */
      iteration?: number;
    }
  | { type: "text"; content: string; iteration?: number }
  | {
      type: "tool_call";
      toolCallId?: string;
      executionId?: string;
      executionState?: string;
      name: string;
      args: Record<string, unknown>;
      status: "running" | "success" | "error" | "pending" | "streaming";
      result?: string;
      error?: string;
      /** 未执行即被放弃时的机器可读原因（llm_retry / retry_exhausted / turn_failure …）。 */
      abortReason?: string;
      iteration?: number;
      parentCallId?: string;
      /** Task progress immediately after this operation (durable history or SSE). */
      taskList?: TaskItem[];
    }
  | {
      type: "subagent";
      name: string;
      reason: string;
      iterations: number;
      toolCalls: number;
      status: "running" | "done";
      summary?: string;
      conversationId?: string;
      success?: boolean;
      stopReason?: string;
      diagnostic?: string;
      background?: boolean;
      runStatus?: SubagentRunStatus;
      tools: SubagentToolCall[];
    }
  | { type: "task_list"; items: TaskItem[] }
  | { type: "iteration"; iteration: number }
  | {
      type: "status";
      label: string;
      detail?: string;
      variant: "info" | "route" | "summary";
    }
  | {
      type: "approval_action";
      approvalId: string;
      toolName: string;
      success: boolean;
      undoable: boolean;
      hasChanges?: boolean;
      undone?: boolean;
      undoError?: string;
    }
  | {
      type: "token_stats";
      promptTokens: number;
      cachedTokens?: number | null;
      completionTokens: number;
      totalTokens: number;
      iterations: number;
    }
  | {
      type: "memory_extracted";
      entries: { id: string; content: string; category: string }[];
      trigger: string;
      count: number;
    }
  | {
      type: "compaction";
      operationId: string;
      status: "queued" | "running" | "completed" | "skipped" | "failed";
      message: string;
      detail?: string;
      tokensBefore?: number;
      tokensAfter?: number;
      messagesBefore?: number;
      messagesAfter?: number;
      preservedQuotes?: number;
    }
  | {
      type: "file_download";
      toolCallId?: string;
      filePath: string;
      filename: string;
      description: string;
    }
  | {
      // 仅用于读取历史消息；自动验收已删除，不再产生新块
      type: "verification_report";
      verdict: "pass" | "fail" | "unknown";
      confidence: "high" | "medium" | "low";
      checks: string[];
      issues: string[];
      mode: "advisory" | "blocking";
    }
  | {
      type: "config_error";
      items: { name: string; field: string; model: string }[];
    }
  | {
      type: "staging_hint";
      pendingCount: number;
      files: string[];
    }
  | {
      type: "llm_retry";
      retryAttempt: number;
      retryMaxAttempts: number;
      retryDelaySeconds: number;
      retryErrorMessage: string;
      retryStatus: "retrying" | "succeeded" | "exhausted";
    }
  | {
      type: "failure_guidance";
      category: "model" | "transport" | "config" | "quota" | "unknown";
      code: string;
      title: string;
      message: string;
      stage: string;
      retryable: boolean;
      diagnosticId: string;
      actions: { type: "retry" | "open_settings" | "copy_diagnostic"; label: string }[];
      provider?: string;
      model?: string;
    }
  | {
      type: "tool_notice";
      toolName: string;
      argsSummary: string;
      iteration?: number;
    }
  | {
      type: "reasoning_notice";
      content: string;
      iteration?: number;
    });

export interface TaskItem {
  content: string;
  status: string;
  index: number;
  verification?: string;
}

export type Message =
  | { id: string; role: "user"; content: string; files?: FileAttachment[]; timestamp?: number; workbookAction?: import("./workbook-handoff").WorkbookActionContext; workbookContext?: import("./workbook-context").WorkbookMessageContext; dispatchId?: string; dispatchMode?: MessageDispatchMode; dispatchStatus?: string }
  | { id: string; role: "assistant"; blocks: AssistantBlock[]; affectedFiles?: string[]; timestamp?: number };

export type MessageDispatchMode = "steer" | "interrupt" | "queue";

export interface DispatchReceipt {
  dispatch_id: string;
  client_message_id: string;
  mode: MessageDispatchMode;
  status: "queued" | "interrupt_pending" | "applying" | "applied" | "completed" | "failed" | "cancelled" | "interrupted";
  content: string;
  revision: number;
  created_at: number;
  turn_id?: string;
  step_id?: string;
  error?: string;
  message_recorded?: boolean;
  hidden?: boolean;
}

export interface Approval {
  id: string;
  toolName: string;
  arguments: Record<string, unknown>;
  riskLevel?: "high" | "medium" | "low";
  argsSummary?: Record<string, string>;
}

export interface Question {
  id: string;
  header: string;
  text: string;
  options: { label: string; description: string }[];
  multiSelect: boolean;
  /** Number of questions remaining in the batch queue (including this one). */
  queueSize?: number;
  selection?: import("@/lib/workbook-interaction").WorkbookTarget;
  toolCallId?: string;
  sessionId?: string;
  autoOpen?: boolean;
}

export interface ModelInfo {
  name: string;
  model: string;
  display_name?: string;
  resolved_model?: string;
  description?: string;
  active: boolean;
  base_url?: string;
  provider?: string;
  protocol?: string;
  model_family?: string;
  user_scoped?: boolean;
  supports_vision?: boolean | null;
}
