export interface FileAttachment {
  filename: string;
  path: string;
  size: number;
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
  inFlight: boolean;
  activeStreamId: string | null;
  latestSeq: number;
  fullAccessEnabled: boolean;
  chatMode: "write" | "read" | "plan";
  presentAs?: "native" | "code";
  currentModel: string | null;
  currentModelName: string | null;
  visionCapable: boolean | null;
  messages: unknown[];
  pendingApproval: Approval | null;
  pendingQuestion: Question | null;
  lastRoute: { routeMode: string; skillsUsed: string[]; toolScope: string[] } | null;
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

export type AssistantBlock =
  | { type: "thinking"; content: string; duration?: number; startedAt?: number }
  | { type: "text"; content: string }
  | {
      type: "tool_call";
      toolCallId?: string;
      name: string;
      args: Record<string, unknown>;
      status: "running" | "success" | "error" | "pending" | "streaming";
      result?: string;
      error?: string;
      iteration?: number;
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
    };

export interface TaskItem {
  content: string;
  status: string;
  index: number;
  verification?: string;
}

export type Message =
  | { id: string; role: "user"; content: string; files?: FileAttachment[]; timestamp?: number }
  | { id: string; role: "assistant"; blocks: AssistantBlock[]; affectedFiles?: string[]; timestamp?: number };

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
  user_scoped?: boolean;
  supports_vision?: boolean | null;
}
