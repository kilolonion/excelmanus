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
}

export interface Session {
  id: string;
  title: string;
  messageCount: number;
  inFlight: boolean;
  updatedAt?: string;
  status?: "active" | "archived";
  /** 本地创建时间戳（Date.now()），用于 mergeSessions 宽限期保护 */
  createdAt?: number;
}

export interface SessionDetail {
  id: string;
  messageCount: number;
  inFlight: boolean;
  fullAccessEnabled: boolean;
  chatMode: "write" | "read" | "plan";
  currentModel: string | null;
  currentModelName: string | null;
  visionCapable: boolean;
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
}

export interface ModelInfo {
  name: string;
  model: string;
  description?: string;
  active: boolean;
  base_url?: string;
}
