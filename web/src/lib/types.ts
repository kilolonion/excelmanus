export interface FileAttachment {
  filename: string;
  path: string;
  size: number;
}

export interface Session {
  id: string;
  title: string;
  messageCount: number;
  inFlight: boolean;
  updatedAt?: string;
  status?: "active" | "archived";
}

export interface SessionDetail {
  id: string;
  messageCount: number;
  inFlight: boolean;
  fullAccessEnabled: boolean;
  planModeEnabled: boolean;
  currentModel: string | null;
  currentModelName: string | null;
  messages: unknown[];
  pendingApproval: Approval | null;
  pendingQuestion: Question | null;
  lastRoute: { routeMode: string; skillsUsed: string[]; toolScope: string[] } | null;
}

export type AssistantBlock =
  | { type: "thinking"; content: string; duration?: number; startedAt?: number }
  | { type: "text"; content: string }
  | {
      type: "tool_call";
      toolCallId?: string;
      name: string;
      args: Record<string, unknown>;
      status: "running" | "success" | "error" | "pending";
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
    };

export interface TaskItem {
  content: string;
  status: string;
  index: number;
}

export type Message =
  | { id: string; role: "user"; content: string; files?: FileAttachment[] }
  | { id: string; role: "assistant"; blocks: AssistantBlock[]; affectedFiles?: string[] };

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
