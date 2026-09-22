/**
 * 共享 SSE 事件分发器——取代 sendMessage / sendContinuation / subscribeToSession 的三重重复。
 *
 * 所有 SSE 事件处理逻辑集中在此文件的 `dispatchSSEEvent()` 函数中。
 * 调用方只需构建 `SSEHandlerContext` 并在 consumeSSE 回调中传递给该函数。
 */

import { useChatStore } from "@/stores/chat-store";
import { useSessionStore } from "@/stores/session-store";
import { useUIStore } from "@/stores/ui-store";
import { useExcelStore, type ExcelCellDiff, type ExcelDiffEntry, type ExcelPreviewData, type MergeRange } from "@/stores/excel-store";
import { useWordStore } from "@/stores/word-store";
import { useFilePreviewStore } from "@/stores/file-preview-store";
import { useJevStore } from "@/stores/jev-store";
import { classifyWorkspaceFile } from "@/lib/file-kind";
import { displayFileName } from "@/lib/file-identity";
import { openWorkspaceFile } from "@/lib/open-workspace-file";
import { getIsMobile } from "@/hooks/use-mobile";
import type { AssistantBlock, Session, TaskItem } from "@/lib/types";
import { instantSessionTitle } from "@/lib/session-title";
import { workspaceKeyForSessionId } from "@/lib/workspace-file-ref";
import { parseWorkbookTarget, parseWorkbookPresentation, showWorkbookPresentation } from "@/lib/workbook-interaction";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/** SSE 事件的规范化表示（由 consumeSSE 解析后传入）。 */
export interface SSEEvent {
  event: string;
  data: Record<string, unknown>;
}

/** 事件分发器运行所需的上上下文，由调用方构建并传入。 */
export interface SSEHandlerContext {
  /** 当前 assistant 消息 ID（事件将追加到此消息的 blocks 中）。 */
  assistantMsgId: string;
  /** RAF 增量批处理器实例。 */
  batcher: DeltaBatcher;
  /** 当前会话 ID（用于备件登录等）。 */
  effectiveSessionId: string;
  /** 是否属于 sendMessage 的首次发送流程（vs continuation / subscribe）。 */
  isFirstSend: boolean;
  /** 用户原始消息文本（仅 sendMessage 流程需要，用于 session_init 标题推断）。 */
  userText?: string;

  // --- 可变状态引用（由调用方持有，分发器读写）---
  /** thinking block 是否进行中。 */
  thinkingInProgress: boolean;
  /** 流是否遇到错误。 */
  hadStreamError: boolean;
  /** 本轮是否出现过会写入历史的工具副作用（diff / 改文件），用于结束后补同步。 */
  hadPersistedToolWork?: boolean;
  /** 高置信 stay：抑制 done 内全部自动导航（含本轮自动 openCompare）。 */
  suppressAutoOpen?: boolean;
  /** 本轮 excel_diff 是否已自动打开对比视图（供 stay 撤回）。 */
  autoOpenedCompareThisTurn?: boolean;
  /** subscribe 回放：ui_hint 不重放。 */
  fromReplay?: boolean;
}

/** DeltaBatcher 接口（从 chat-actions.ts 复用）。 */
export interface DeltaBatcher {
  pushText(delta: string): void;
  pushThinking(delta: string): void;
  flush(): void;
  dispose(): void;
  hasPendingContent(): boolean;
}

// ---------------------------------------------------------------------------
// Helpers (从 chat-actions.ts 提升为模块级共享)
// ---------------------------------------------------------------------------

function workbenchBusy(): boolean {
  const excelStore = useExcelStore.getState();
  const wordStore = useWordStore.getState();
  const previewStore = useFilePreviewStore.getState();
  return Boolean(
    excelStore.panelOpen
    || excelStore.compareMode
    || excelStore.fullViewPath
    || wordStore.panelOpen
    || wordStore.fullViewPath
    || previewStore.textOpen
    || previewStore.imageOpen
  );
}

function pathIsDismissed(path: string): boolean {
  if (!path) return false;
  const dismissed = useExcelStore.getState().dismissedPaths;
  return Boolean(dismissed && dismissed.has(path));
}

/** 将后端 snake_case diff changes 映射为前端 camelCase ExcelCellDiff[] */
export function _mapDiffChanges(raw: unknown[]): ExcelCellDiff[] {
  if (!Array.isArray(raw)) return [];
  return raw.map((item: unknown) => {
    const c = item as Record<string, unknown>;
    return {
      cell: (c.cell as string) || "",
      old: c.old as string | number | boolean | null,
      new: c.new as string | number | boolean | null,
      oldStyle: (c.old_style ?? c.oldStyle ?? null) as ExcelCellDiff["oldStyle"],
      newStyle: (c.new_style ?? c.newStyle ?? null) as ExcelCellDiff["newStyle"],
      styleOnly: Boolean(c.style_only ?? c.styleOnly),
    };
  });
}

export function normalizeTaskItems(taskListPayload: unknown): TaskItem[] {
  let rawItems: unknown[] = [];
  if (Array.isArray(taskListPayload)) {
    rawItems = taskListPayload;
  } else if (
    taskListPayload
    && typeof taskListPayload === "object"
    && "items" in taskListPayload
    && Array.isArray((taskListPayload as { items?: unknown[] }).items)
  ) {
    rawItems = (taskListPayload as { items: unknown[] }).items;
  }
  return rawItems.map((rawItem, i) => {
    const item = rawItem as Record<string, unknown>;
    const rawVerification = item.verification;
    const verification = typeof rawVerification === "string"
      ? rawVerification || undefined
      : typeof (rawVerification as { expected?: unknown } | null)?.expected === "string"
        ? (rawVerification as { expected: string }).expected || undefined
        : undefined;
    return {
      content:
        (item.content as string)
        || (item.title as string)
        || (item.description as string)
        || `任务 ${i + 1}`,
      status: (item.status as string) || "pending",
      index: typeof item.index === "number" ? item.index : i,
      verification,
    };
  });
}

function applyTaskStatusPatch(
  items: TaskItem[],
  taskIndex: number | null,
  taskStatus: string,
): TaskItem[] {
  if (taskIndex === null || !taskStatus) return items;
  return items.map((item) =>
    item.index === taskIndex ? { ...item, status: taskStatus } : item
  );
}

/** 获取指定 assistant 消息。 */
export function getLastAssistantMessage(
  messages: ReturnType<typeof useChatStore.getState>["messages"],
  id: string,
) {
  const byId = useChatStore.getState().messagesById[id];
  if (byId && byId.role === "assistant") return byId;
  const msg = messages.find((m) => m.id === id);
  if (msg && msg.role === "assistant") return msg;
  return null;
}

// ---------------------------------------------------------------------------
// 内部快捷引用
// ---------------------------------------------------------------------------

const S = () => useChatStore.getState();

function updateResultBlock(messageId: string, callId: string | null, update: (block: AssistantBlock, previousMessage: boolean) => AssistantBlock, executionId?: string) {
  if (executionId) {
    const owner = [...S().messages].reverse().find((message) => message.role === "assistant"
      && message.blocks.some((block) => block.type === "tool_call" && block.executionId === executionId));
    if (owner) {
      S().updateAssistantMessage(owner.id, (message) => ({ ...message,
        blocks: message.blocks.map((block) => block.type === "tool_call" && block.executionId === executionId
          ? update(block, owner.id !== messageId) : block),
      }));
      return;
    }
  }
  const original = callId ? [...S().messages].reverse().find((message) => message.role === "assistant"
    && message.blocks.some((block) => block.type === "tool_call" && block.toolCallId === callId)) : null;
  if (original && original.id !== messageId) {
    S().updateAssistantMessage(original.id, (message) => ({ ...message,
      blocks: message.blocks.map((block) => block.type === "tool_call" && block.toolCallId === callId ? update(block, true) : block),
    }));
  } else {
    S().updateToolCallBlock(messageId, callId, (block) => update(block, false));
  }
}

/** Apply one changed-files batch from either MUTATION or legacy FILES_CHANGED. */
function applyChangedFiles(
  ctx: SSEHandlerContext,
  msgId: string,
  changedFiles: string[],
  mutations: { identity?: string; content_version?: string }[] = [],
): void {
  if (changedFiles.length === 0) return;
  ctx.hadPersistedToolWork = true;
  const excelStore = useExcelStore.getState();
  const wordStore = useWordStore.getState();
  // 文件事件属于产生它的会话工作区，按事件会话键入桶，避免会话切换期间错挂到当前工作区。
  const sourceWorkspaceKey = workspaceKeyForSessionId(ctx.effectiveSessionId);
  for (const filePath of changedFiles) {
    if (!filePath) continue;
    const filename = filePath.split("/").pop() || filePath;
    excelStore.addRecentFileIfNotDismissed({ path: filePath, filename }, sourceWorkspaceKey);
    const version = mutations.find((item) => item.identity?.replace(/^\.\//, "") === filePath.replace(/^\.\//, ""))?.content_version;
    excelStore.notifyWorkbookChanged(filePath, sourceWorkspaceKey, version || undefined);
  }
  wordStore.handleFilesChanged(changedFiles);
  S().addAffectedFiles(msgId, changedFiles);
  excelStore.bumpWorkspaceFilesVersion();
}

function _getLastBlockOfType(msgId: string, type: string) {
  const msg = getLastAssistantMessage(S().messages, msgId);
  if (!msg) return null;
  for (let i = msg.blocks.length - 1; i >= 0; i--) {
    if (msg.blocks[i].type === type) return msg.blocks[i];
  }
  return null;
}

/** 流式增量按字符串原样保留，包括空格和换行。 */
function _streamDeltaContent(data: Record<string, unknown>): string {
  return typeof data.content === "string" ? data.content : "";
}

// ---------------------------------------------------------------------------
// 核心分发器
// ---------------------------------------------------------------------------

/**
 * 处理单个 SSE 事件。由 sendMessage / sendContinuation / subscribeToSession 统一调用。
 *
 * 调用方在 consumeSSE 回调中应：
 * 1. 在非 thinking 事件前调用 finalizeThinking(ctx)
 * 2. 在非增量事件前调用 ctx.batcher.flush()
 * 3. 调用 dispatchSSEEvent(event, ctx)
 */
export function dispatchSSEEvent(event: SSEEvent, ctx: SSEHandlerContext): void {
  const { data } = event;
  const msgId = ctx.assistantMsgId;
  const eventSeq = typeof data.seq === "number" ? data.seq : null;
  const eventStreamId = typeof data.stream_id === "string" && data.stream_id
    ? data.stream_id
    : null;

  if (eventSeq !== null) {
    const state = S();
    const streamId = eventStreamId ?? state.activeStreamId;
    if (streamId) {
      state.setStreamState(streamId, Math.max(state.latestSeq, eventSeq));
    }
  }

  switch (event.event) {
    case "stream_init": {
      if (eventStreamId) {
        S().setStreamState(eventStreamId, eventSeq ?? 0);
      }
      S().clearResumeFailed();
      break;
    }

    // --- 会话 ---
    case "session_init": {
      if (!ctx.isFirstSend) break; // continuation / subscribe 跳过
      const sid = data.session_id as string;
      const ss = useSessionStore.getState();
      // JEV 路由可能在会话获取前重绑了工作区；以服务端事实为准。
      const wsPatch: Partial<Session> = {};
      if (typeof data.workspace_id === "string" && data.workspace_id) {
        wsPatch.workspaceId = data.workspace_id;
      }
      if (typeof data.workspace_path === "string" && data.workspace_path) {
        wsPatch.workspacePath = data.workspace_path;
      }
      if (typeof data.workspace_title === "string" && data.workspace_title) {
        wsPatch.workspaceTitle = data.workspace_title;
      }
      if (Object.keys(wsPatch).length > 0) {
        ss.patchSession(sid, wsPatch);
      }
      if (ss.activeSessionId !== sid) {
        ss.setActiveSession(sid);
      }
      const chatState = S();
      if (chatState.loadedSessionId !== sid) {
        chatState.bindLoadedSession(sid);
      }
      if (ctx.userText) {
        const instant = instantSessionTitle(ctx.userText);
        if (instant) {
          ss.updateSessionTitle(ss.activeSessionId || sid, instant);
        }
      }
      const ui = useUIStore.getState();
      if (typeof data.full_access_enabled === "boolean") {
        ui.setFullAccessEnabled(data.full_access_enabled);
      }
      if (typeof data.auto_approve_enabled === "boolean") {
        ui.setAutoApproveEnabled(data.auto_approve_enabled);
      }
      if (typeof data.chat_mode === "string") {
        ui.setChatMode(data.chat_mode as "write" | "read" | "plan");
      }
      break;
    }

    // --- 自动生成的会话标题 ---
    case "session_title": {
      const titleSid = (data.session_id as string) || "";
      const titleText = (data.title as string) || "";
      if (titleSid && titleText) {
        useSessionStore.getState().updateSessionTitle(titleSid, titleText);
      }
      break;
    }

    // --- 订阅恢复（仅 subscribe 流程）---
    case "subscribe_resume": {
      const status = (data.status as string) || "";
      const streamId = (data.stream_id as string) || eventStreamId;
      if (streamId) {
        S().setStreamState(streamId, eventSeq ?? S().latestSeq);
      }
      S().clearResumeFailed();
      if (status === "reconnected") {
        S().setPipelineStatus({
          stage: "resuming",
          message: "正在恢复事件流...",
          startedAt: Date.now(),
        });
      }
      break;
    }

    case "resume_failed": {
      const reason = (data.reason as string) || "unknown";
      S().markResumeFailed(reason);
      S().setPipelineStatus({
        stage: "resume_failed",
        message: "事件恢复失败，正在回源快照...",
        startedAt: Date.now(),
      });
      break;
    }

    // --- 流水线进度 ---
    case "pipeline_progress": {
      S().setPipelineStatus({
        stage: (data.stage as string) || "working",
        message: (data.message as string) || "",
        startedAt: Date.now(),
      });
      break;
    }

    // 批量任务进度
    case "batch_progress": {
      S().setBatchProgress({
        batchIndex: typeof data.batch_index === "number" ? data.batch_index : 0,
        batchTotal: typeof data.batch_total === "number" ? data.batch_total : 1,
        batchItemName: (data.batch_item_name as string) || `任务 ${((data.batch_index as number) || 0) + 1}`,
        batchStatus: ((data.batch_status as string) || "running") as "running" | "failed" | "completed",
        batchElapsed: typeof data.batch_elapsed_seconds === "number" ? data.batch_elapsed_seconds : 0,
        message: (data.message as string) || "",
      });
      break;
    }

    // 路由（历史 replay 仍可能到达，对话流不再展示）
    case "route_start":
    case "route_end":
      break;

    // 迭代
    case "iteration_start": {
      const iter = (data.iteration as number) || 0;
      if (iter > 1) {
        S().appendBlock(msgId, { type: "iteration", iteration: iter });
      }
      break;
    }

    // 思考
    case "thinking_delta": {
      S().setPipelineStatus(null);
      const thinkingDelta = _streamDeltaContent(data);
      const lastThinking = _getLastBlockOfType(msgId, "thinking");
      if (lastThinking && lastThinking.type === "thinking" && lastThinking.duration == null) {
        ctx.batcher.pushThinking(thinkingDelta);
      } else {
        ctx.batcher.flush();
        S().appendBlock(msgId, {
          type: "thinking",
          content: thinkingDelta,
          startedAt: Date.now(),
        });
      }
      ctx.thinkingInProgress = true;
      break;
    }

    case "thinking": {
      S().appendBlock(msgId, {
        type: "thinking",
        content: (data.content as string) || "",
        duration: (data.duration as number) || undefined,
        startedAt: Date.now(),
      });
      break;
    }

    case "retract_thinking": {
      ctx.thinkingInProgress = false;
      ctx.batcher.flush();
      S().retractLastThinking(msgId);
      break;
    }

    // --- 文本 ---
    case "text_delta": {
      S().setPipelineStatus(null);
      const msg = getLastAssistantMessage(S().messages, msgId);
      const lastBlock = msg?.blocks[msg.blocks.length - 1];
      if (!lastBlock || lastBlock.type !== "text") {
        S().appendBlock(msgId, { type: "text", content: "" });
      }
      ctx.batcher.pushText(_streamDeltaContent(data));
      break;
    }

    // --- 流式工具参数 delta ---
    case "tool_call_args_delta": {
      const adToolCallId = (data.tool_call_id as string) || "";
      const adToolName = (data.tool_name as string) || "";
      const adDelta = (data.args_delta as string) || "";
      if (adToolCallId && adDelta) {
        useExcelStore.getState().appendStreamingArgs(adToolCallId, adDelta);
        const adMsg = getLastAssistantMessage(S().messages, msgId);
        const hasBlock = adMsg?.blocks.some(
          (b) => b.type === "tool_call" && b.toolCallId === adToolCallId,
        );
        if (!hasBlock && adToolName) {
          S().setPipelineStatus(null);
          S().appendBlock(msgId, {
            type: "tool_call",
            toolCallId: adToolCallId,
            name: adToolName,
            args: {},
            status: "streaming" as "running",
            iteration: undefined,
          });
        }
      }
      break;
    }

    // --- 工具调用 ---
    case "tool_call_state": {
      const callId = data.tool_call_id as string;
      const executionId = data.execution_id as string;
      const executionState = data.execution_state as string;
      if (!callId || !executionId) break;
      const message = getLastAssistantMessage(S().messages, msgId);
      const existing = message?.blocks.find((b) => b.type === "tool_call" && b.toolCallId === callId
        && (!b.executionId || b.executionId === executionId));
      if (existing) {
        S().updateToolCallBlock(msgId, callId, (b) => {
          if (b.type !== "tool_call" || b.toolCallId !== callId || (b.executionId && b.executionId !== executionId)) return b;
          if (b.status === "success" || b.status === "error") return b;
          return { ...b, executionId, executionState,
            status: executionState === "queued" ? "pending" : b.status } as AssistantBlock;
        });
      } else if (executionState === "queued") {
        S().appendBlock(msgId, { type: "tool_call", toolCallId: callId,
          executionId, executionState, status: "pending", name: (data.tool_name as string) || "",
          args: (data.arguments as Record<string, unknown>) || {},
          parentCallId: (data.parent_call_id as string) || undefined,
        });
      }
      break;
    }
    case "tool_call_start": {
      S().setPipelineStatus(null);
      const toolCallIdRaw = data.tool_call_id;
      const toolCallId = typeof toolCallIdRaw === "string" && toolCallIdRaw.length > 0
        ? toolCallIdRaw
        : undefined;
      const parentCallIdRaw = data.parent_call_id;
      const parentCallId = typeof parentCallIdRaw === "string" && parentCallIdRaw.length > 0
        ? parentCallIdRaw
        : undefined;
      const msgForStart = getLastAssistantMessage(S().messages, msgId);
      const streamingExists = toolCallId && msgForStart?.blocks.some(
        (b) => b.type === "tool_call" && b.toolCallId === toolCallId
          && (b.status === "streaming" || b.executionState === "queued" || b.executionState === "cancelling"
            || (!!data.execution_id && b.executionId === data.execution_id)),
      );
      if (streamingExists) {
        S().updateToolCallBlock(msgId, toolCallId!, (b) => {
          if (b.type === "tool_call") {
            if (b.status === "success" || b.status === "error") return b;
            if (b.executionId && data.execution_id && b.executionId !== data.execution_id) return b;
            return {
              ...b,
              args: (data.arguments as Record<string, unknown>) || b.args,
              status: "running",
              executionId: (data.execution_id as string) || b.executionId,
              executionState: (data.execution_state as string) || "running",
              iteration: (data.iteration as number) || undefined,
              parentCallId: parentCallId ?? b.parentCallId,
            } as AssistantBlock;
          }
          return b;
        });
      } else {
        S().appendBlock(msgId, {
          type: "tool_call",
          toolCallId,
          name: (data.tool_name as string) || "",
          args: (data.arguments as Record<string, unknown>) || {},
          status: "running",
          executionId: (data.execution_id as string) || undefined,
          executionState: (data.execution_state as string) || undefined,
          iteration: (data.iteration as number) || undefined,
          parentCallId,
        });
      }
      break;
    }

    case "tool_call_end": {
      if (data.tool_name === "show_workbook" && data.success !== false && !ctx.fromReplay) {
        const presentation = parseWorkbookPresentation(data.result as string);
        if (presentation && showWorkbookPresentation(presentation, ctx.effectiveSessionId)) ctx.suppressAutoOpen = true;
      }
      const toolCallIdRaw = data.tool_call_id;
      const toolCallId = typeof toolCallIdRaw === "string" ? toolCallIdRaw : null;
      if (toolCallId) {
        useExcelStore.getState().clearStreamingArgs(toolCallId);
        S().clearToolProgress(toolCallId);
      }
      // ask_user batch 结束后清理残留的 pendingQuestion
      if ((data.tool_name as string) === "ask_user" && S().pendingQuestion
        && (!S().pendingQuestion?.toolCallId || S().pendingQuestion?.toolCallId === toolCallId)) {
        S().setPendingQuestion(null);
        useSessionStore.getState().patchSession(ctx.effectiveSessionId, {
          pendingQuestion: false,
        });
      }
      updateResultBlock(msgId, toolCallId, (b, previousMessage) => {
        if (b.type === "tool_call") {
          const executionId = data.execution_id as string | undefined;
          if (executionId && (b.toolCallId !== toolCallId || (b.executionId && b.executionId !== executionId))) return b;
          if (b.status === "pending" && !previousMessage && !data.execution_state) {
            return { ...b, result: (data.result as string) || undefined } as AssistantBlock;
          }
          if ((executionId && b.executionId === executionId) || previousMessage || b.status === "running" || b.status === "streaming" || (b.status === "pending" && data.execution_state)) {
            return {
              ...b,
              status: data.success ? "success" : "error",
              result: (data.result as string) || undefined,
              error: (data.error as string) || undefined,
              executionState: (data.execution_state as string) || b.executionState,
            } as AssistantBlock;
          }
        }
        return b;
      }, (data.execution_id as string) || undefined);
      if (data.success && data.ui && typeof data.ui === "object") {
        const merge = (data.ui as Record<string, unknown>).merge;
        if (merge && typeof merge === "object") {
          const src = merge as Record<string, unknown>;
          if (
            typeof src.rows_matched === "number"
            || typeof src.matched_count === "number"
            || typeof src.output_file === "string"
          ) {
            useExcelStore.getState().setMergeResult({
              sourceFiles: Array.isArray(src.source_files) ? src.source_files as string[] : [],
              outputFile: (src.output_file ?? src.output ?? "") as string,
              rowsMatched: (src.rows_matched ?? src.matched_count ?? 0) as number,
              rowsAdded: (src.rows_added ?? src.added_count ?? 0) as number,
              rowsUnmatched: (src.rows_unmatched ?? src.unmatched_count ?? 0) as number,
              keyColumns: Array.isArray(src.key_columns) ? src.key_columns as string[] : [],
              joinType: (src.join_type ?? src.how ?? "") as string,
              toolCallId: toolCallId ?? "",
            });
          }
        }
      }
      break;
    }

    // --- 子代理 ---
    case "subagent_start": {
      S().appendBlock(msgId, {
        type: "subagent",
        name: (data.name as string) || "",
        reason: (data.reason as string) || "",
        iterations: 0,
        toolCalls: 0,
        status: "running",
        conversationId: (data.conversation_id as string) || "",
        background: data.background === true,
        tools: [],
      });
      break;
    }

    case "subagent_iteration": {
      const cid = (data.conversation_id as string) || null;
      S().updateSubagentBlock(msgId, cid, (b) => {
        if (b.type === "subagent" && b.status === "running") {
          return {
            ...b,
            iterations: (data.iteration as number) || b.iterations,
            toolCalls: (data.tool_calls as number) || b.toolCalls,
          };
        }
        return b;
      });
      break;
    }

    case "subagent_tool_start": {
      const cid = (data.conversation_id as string) || null;
      S().updateSubagentBlock(msgId, cid, (b) => {
        if (b.type !== "subagent" || b.status !== "running") return b;
        const args = (data.arguments as Record<string, unknown>) || {};
        const parts: string[] = [];
        if (args.sheet) parts.push(String(args.sheet));
        if (args.range) parts.push(String(args.range));
        if (args.file_path) parts.push(displayFileName(String(args.file_path)));
        if (args.code_preview) parts.push(String(args.code_preview));
        return {
          ...b,
          tools: [...(b.tools || []), {
            index: (data.tool_index as number) || 0,
            name: (data.tool_name as string) || "",
            argsSummary: parts.join(" · "),
            status: "running" as const,
            args,
          }],
        };
      });
      break;
    }

    case "subagent_tool_end": {
      const cid = (data.conversation_id as string) || null;
      S().updateSubagentBlock(msgId, cid, (b) => {
        if (b.type !== "subagent") return b;
        const tools = [...(b.tools || [])];
        const toolName = (data.tool_name as string) || "";
        const idx = tools.findLastIndex(
          (t) => t.name === toolName && t.status === "running"
        );
        if (idx >= 0) {
          tools[idx] = {
            ...tools[idx],
            status: (data.success as boolean) ? "success" : "error",
            result: (data.result as string) || undefined,
            error: (data.error as string) || undefined,
          };
        }
        return { ...b, tools };
      });
      break;
    }

    case "subagent_summary": {
      const cid = (data.conversation_id as string) || null;
      S().updateSubagentBlock(msgId, cid, (b) => {
        if (b.type === "subagent") {
          return {
            ...b,
            summary: (data.summary as string) || "",
            iterations: (data.iterations as number) || b.iterations,
            toolCalls: (data.tool_calls as number) || b.toolCalls,
          };
        }
        return b;
      });
      break;
    }

    case "subagent_end": {
      const cid = (data.conversation_id as string) || null;
      S().updateSubagentBlock(msgId, cid, (b) => {
        if (b.type === "subagent") {
          return {
            ...b,
            status: "done",
            runStatus: b.runStatus === "paused" ? "paused" : undefined,
            success: (data.success as boolean) ?? true,
            stopReason: (data.stop_reason as string) || (data.reason as string) || "",
            diagnostic: (data.diagnostic as string) || "",
            iterations: (data.iterations as number) || b.iterations,
            toolCalls: (data.tool_calls as number) || b.toolCalls,
          };
        }
        return b;
      });
      break;
    }

    // --- 交互 ---
    case "user_question": {
      S().setPendingQuestion({
        id: (data.id as string) || "",
        header: (data.header as string) || "",
        text: (data.text as string) || "",
        options: (data.options as { label: string; description: string }[]) || [],
        multiSelect: Boolean(data.multi_select),
        queueSize: typeof data.queue_size === "number" ? data.queue_size : undefined,
        selection: parseWorkbookTarget(data.selection),
        toolCallId: typeof data.tool_call_id === "string" ? data.tool_call_id : undefined,
        sessionId: ctx.effectiveSessionId,
        autoOpen: !ctx.fromReplay,
      });
      useSessionStore.getState().patchSession(ctx.effectiveSessionId, {
        pendingQuestion: true,
        pendingApproval: false,
      });
      break;
    }

    case "pending_approval": {
      const approvalId = (data.approval_id as string) || "";
      // 用户已提交/关闭同一单据后，忽略重放或迟到的 pending_approval，避免弹窗复活。
      if (approvalId && S()._lastDismissedApprovalId === approvalId) {
        break;
      }
      const approvalToolCallId = (data.tool_call_id as string) || null;
      S().updateToolCallBlock(msgId, approvalToolCallId, (b) => {
        if (b.type === "tool_call") {
          return { ...b, status: "pending" as const } as AssistantBlock;
        }
        return b;
      });
      S().setPendingApproval({
        id: approvalId,
        toolName: (data.approval_tool_name as string) || "",
        arguments: {},
        riskLevel: (data.risk_level as "high" | "medium" | "low") || "high",
        argsSummary: (data.args_summary as Record<string, string>) || {},
      });
      useSessionStore.getState().patchSession(ctx.effectiveSessionId, {
        pendingApproval: true,
        pendingQuestion: false,
      });
      break;
    }

    case "approval_resolved": {
      const toolName = (data.approval_tool_name as string) || "";
      const approvalId = (data.approval_id as string) || "";
      const success = Boolean(data.success);
      const undoable = Boolean(data.undoable);
      const hasChanges = Boolean(data.has_changes);
      const arResult = (data.result as string) || undefined;
      if (approvalId) {
        S().dismissApproval(approvalId);
      } else {
        S().setPendingApproval(null);
      }
      useSessionStore.getState().patchSession(ctx.effectiveSessionId, {
        pendingApproval: false,
      });
      const arToolCallId = (data.tool_call_id as string) || null;
      // 单个问题已回答不代表整组 ask_user 已完成，等待真实 tool_call_end。
      if (toolName !== "ask_user" || arToolCallId) updateResultBlock(msgId, arToolCallId, (b) => {
        if (b.type === "tool_call") {
          return {
            ...b,
            status: success ? ("success" as const) : ("error" as const),
            result: arResult ?? b.result,
            error: success ? undefined : (arResult ?? b.error),
          } as AssistantBlock;
        }
        return b;
      });
      S().appendBlock(msgId, {
        type: "approval_action",
        approvalId,
        toolName,
        success,
        undoable,
        hasChanges,
      });
      // 实时刷新操作历史时间线
      if (success && hasChanges) {
        const sid = useSessionStore.getState().activeSessionId;
        if (sid) {
          useExcelStore.getState().fetchOperationHistory(sid);
        }
      }
      break;
    }

    // --- 任务列表 ---
    case "task_update": {
      const payloadItems = normalizeTaskItems(data.task_list);
      const taskIndex = typeof data.task_index === "number" ? data.task_index : null;
      const taskStatus = typeof data.task_status === "string" ? data.task_status : "";
      const existingTaskList = _getLastBlockOfType(msgId, "task_list");
      if (existingTaskList && existingTaskList.type === "task_list") {
        S().updateBlockByType(msgId, "task_list", (b) => {
          if (b.type !== "task_list") return b;
          const baseItems = payloadItems.length > 0 ? payloadItems : b.items;
          return { ...b, items: applyTaskStatusPatch(baseItems, taskIndex, taskStatus) };
        });
      } else if (payloadItems.length > 0) {
        S().appendBlock(msgId, {
          type: "task_list",
          items: applyTaskStatusPatch(payloadItems, taskIndex, taskStatus),
        });
      }
      break;
    }

    // --- Excel 预览 / 差异 ---
    case "excel_preview": {
      const epFilePath = (data.file_path as string) || "";
      useExcelStore.getState().addPreview({
        toolCallId: (data.tool_call_id as string) || "",
        filePath: epFilePath,
        sheet: (data.sheet as string) || "",
        columns: (data.columns as string[]) || [],
        rows: (data.rows as (string | number | null)[][]) || [],
        totalRows: (data.total_rows as number) || 0,
        truncated: Boolean(data.truncated),
        cellStyles: Array.isArray(data.cell_styles) ? data.cell_styles as ExcelPreviewData["cellStyles"] : undefined,
        mergeRanges: Array.isArray(data.merge_ranges) ? data.merge_ranges as MergeRange[] : undefined,
        metadataHints: Array.isArray(data.metadata_hints) ? data.metadata_hints as string[] : undefined,
      });
      if (epFilePath) {
        const fn = epFilePath.split("/").pop() || epFilePath;
        useExcelStore.getState().addRecentFileIfNotDismissed(
          { path: epFilePath, filename: fn },
          workspaceKeyForSessionId(ctx.effectiveSessionId),
        );
      }
      break;
    }

    case "excel_diff": {
      const edFilePath = (data.file_path as string) || "";
      const edDiffMode = (data.diff_mode as string) || undefined;
      const edEntry: ExcelDiffEntry = {
        toolCallId: (data.tool_call_id as string) || "",
        filePath: edFilePath,
        sheet: (data.sheet as string) || "",
        affectedRange: (data.affected_range as string) || "",
        changes: _mapDiffChanges(data.changes as unknown[]),
        mergeRanges: Array.isArray(data.merge_ranges) ? data.merge_ranges as MergeRange[] : undefined,
        oldMergeRanges: Array.isArray(data.old_merge_ranges) ? data.old_merge_ranges as MergeRange[] : undefined,
        metadataHints: Array.isArray(data.metadata_hints) ? data.metadata_hints as string[] : undefined,
        timestamp: Date.now(),
      };
      if (edDiffMode === "cross_file" || edDiffMode === "cross_sheet") {
        edEntry.diffMode = edDiffMode;
        edEntry.filePathB = (data.file_path_b as string) || "";
        edEntry.sheetB = (data.sheet_b as string) || "";
        const rawSummary = data.diff_summary as Record<string, unknown> | undefined;
        if (rawSummary) {
          edEntry.diffSummary = {
            totalCellsCompared: (rawSummary.total_cells_compared as number) || 0,
            cellsDifferent: (rawSummary.cells_different as number) || 0,
            rowsAdded: (rawSummary.rows_added as number) || 0,
            rowsDeleted: (rawSummary.rows_deleted as number) || 0,
            rowsModified: (rawSummary.rows_modified as number) || 0,
            columnsAdded: (rawSummary.columns_added as string[]) || [],
            columnsDeleted: (rawSummary.columns_deleted as string[]) || [],
          };
        }
      }
      ctx.hadPersistedToolWork = true;
      useExcelStore.getState().addDiff(edEntry);
      if (edFilePath) {
        const fn = edFilePath.split("/").pop() || edFilePath;
        useExcelStore.getState().addRecentFileIfNotDismissed(
          { path: edFilePath, filename: fn },
          workspaceKeyForSessionId(ctx.effectiveSessionId),
        );
        S().addAffectedFiles(msgId, [edFilePath]);
      }
      // 跨文件差异自动打开对比视图
      if (edDiffMode === "cross_file" && edEntry.filePathB && edEntry.diffSummary) {
        const es = useExcelStore.getState();
        if (!ctx.suppressAutoOpen && !es.compareMode && !es.panelOpen) {
          es.openCompare(edFilePath, edEntry.filePathB);
          ctx.autoOpenedCompareThisTurn = true;
        }
      }
      break;
    }

    case "text_diff": {
      ctx.hadPersistedToolWork = true;
      const tdFilePath = (data.file_path as string) || "";
      useExcelStore.getState().addTextDiff({
        toolCallId: (data.tool_call_id as string) || "",
        filePath: tdFilePath,
        hunks: (data.hunks as string[]) || [],
        additions: (data.additions as number) || 0,
        deletions: (data.deletions as number) || 0,
        truncated: !!data.truncated,
        timestamp: Date.now(),
      });
      if (tdFilePath) {
        S().addAffectedFiles(msgId, [tdFilePath]);
      }
      break;
    }

    case "text_preview": {
      useExcelStore.getState().addTextPreview({
        toolCallId: (data.tool_call_id as string) || "",
        filePath: (data.file_path as string) || "",
        content: (data.content as string) || "",
        lineCount: (data.line_count as number) || 0,
        truncated: !!data.truncated,
      });
      break;
    }

    case "mutation": {
      const mutations = (data.mutations as { identity?: string; content_version?: string }[]) || [];
      const files = [...new Set([...(data.files as string[] || []), ...mutations.flatMap((m) => m.identity ? [m.identity] : [])])];
      applyChangedFiles(ctx, msgId, files, mutations);
      break;
    }

    case "files_changed": {
      // 历史 replay / 旧客户端兼容；新写入统一发 mutation。
      applyChangedFiles(ctx, msgId, (data.files as string[]) || []);
      break;
    }

    case "staging_updated": {
      break;
    }

    case "memory_extracted": {
      const entries = (data.entries as { id: string; content: string; category: string }[]) || [];
      const trigger = (data.trigger as string) || "session_end";
      const count = (data.count as number) || entries.length;
      if (count > 0) {
        S().appendBlock(msgId, {
          type: "memory_extracted",
          entries,
          trigger,
          count,
        });
      }
      break;
    }

    case "file_download": {
      ctx.hadPersistedToolWork = true;
      const dlFilePath = (data.file_path as string) || "";
      const dlFilename = (data.filename as string) || displayFileName(dlFilePath) || "download";
      const dlDescription = (data.description as string) || "";
      if (dlFilePath) {
        S().appendBlock(msgId, {
          type: "file_download",
          toolCallId: (data.tool_call_id as string) || undefined,
          filePath: dlFilePath,
          filename: dlFilename,
          description: dlDescription,
        });
      }
      break;
    }

    // 计划创建
    case "plan_created": {
      const planTitle = (data.plan_title as string) || "";
      const planTaskCount = (data.plan_task_count as number) || 0;
      if (planTitle) {
        S().appendBlock(msgId, {
          type: "status",
          label: `计划创建成功：${planTitle}，共 ${planTaskCount} 个任务`,
          variant: "info",
        });
      }
      break;
    }

    // ── 对话摘要（前端静默消费，不覆写） ───────────────────────
    case "chat_summary":
      break;

    // ── 模式变更 ───────────────────────────────────────────
    case "mode_changed": {
      const uiMode = useUIStore.getState();
      const modeName = data.mode_name as string;
      const enabled = Boolean(data.enabled);
      if (modeName === "full_access") {
        uiMode.setFullAccessEnabled(enabled);
        if (enabled) uiMode.setAutoApproveEnabled(false);
      } else if (modeName === "auto_approve") {
        uiMode.setAutoApproveEnabled(enabled);
        if (enabled) uiMode.setFullAccessEnabled(false);
      } else if (modeName === "chat_mode") {
        uiMode.setChatMode(data.value as "write" | "read" | "plan");
      }
      const _modeLabelMap: Record<string, string> = {
        full_access: "完全访问",
        auto_approve: "自动审批",
        chat_mode: "对话模式",
      };
      const modeLabel = _modeLabelMap[modeName] || modeName;
      const modeAction = enabled ? "Enabled" : "Disabled";
      S().appendBlock(msgId, {
        type: "status",
        label: `${modeAction} ${modeLabel}`,
        variant: "info",
      });
      break;
    }

    // ── 回复与完成 ───────────────────────────────────────────
    case "reply": {
      const content = (data.content as string) || "";
      const hasPendingInteraction =
        S().pendingApproval !== null || S().pendingQuestion !== null;
      if (content && !hasPendingInteraction) {
        const msg = getLastAssistantMessage(S().messages, msgId);
        const textBlocks = msg?.blocks.filter((b) => b.type === "text") ?? [];
        const combinedLen = textBlocks.reduce(
          (n, b) => n + (b.type === "text" ? b.content.length : 0),
          0,
        );
        if (combinedLen === 0) {
          if (textBlocks.length === 0) {
            S().appendBlock(msgId, { type: "text", content });
          } else {
            S().updateBlockByType(msgId, "text", (b) => (
              b.type === "text" ? { ...b, content } : b
            ));
          }
        }
      }
      const uiReply = useUIStore.getState();
      if (typeof data.full_access_enabled === "boolean") {
        uiReply.setFullAccessEnabled(data.full_access_enabled);
      }
      if (typeof data.auto_approve_enabled === "boolean") {
        uiReply.setAutoApproveEnabled(data.auto_approve_enabled);
      }
      if (typeof data.chat_mode === "string") {
        uiReply.setChatMode(data.chat_mode as "write" | "read" | "plan");
      }
      // Token 统计由其他调用方在 dispatchSSEEvent 之后自行处理
      // （sendMessage 有清理逻辑，sendContinuation 有附加逻辑，等等）
      break;
    }

    case "ui_hint": {
      if (ctx.fromReplay) break;
      const activeSession = useSessionStore.getState().activeSessionId;
      if (activeSession && activeSession !== ctx.effectiveSessionId) break;
      const surface = String(data.surface || "");
      const suppress = Boolean(data.suppress_auto_open);
      const suppressChanged = suppress && !ctx.suppressAutoOpen;
      let closedCompare = false;
      const recordUI = (impact: string, changed = false) => {
        const didChange = changed || suppressChanged || closedCompare;
        useJevStore.getState().appendFromEvent({
          pack: "ui.surface", gate: "enforce", stage: didChange ? "effect" : "outcome",
          source: "frontend", action: surface, kind: "noop",
          evaluated: false, applied: didChange, state_changed: didChange,
          reason: "ui_guard_result", transport: "unavailable", latency_ms: 0,
          impact: (suppressChanged ? "已关闭本轮自动打开；" : "")
            + (closedCompare ? "已收起本轮自动对比；" : "") + impact,
        });
      };
      if (suppress) {
        ctx.suppressAutoOpen = true;
        if (ctx.autoOpenedCompareThisTurn && useExcelStore.getState().compareMode) {
          useExcelStore.getState().closeCompare();
          closedCompare = !useExcelStore.getState().compareMode;
        }
      }
      if (surface === "stay" || surface === "none" || !surface) {
        recordUI("保持当前界面");
        break;
      }
      if (getIsMobile()) {
        recordUI("移动端未执行界面切换");
        break;
      }
      if (surface === "files_tab") {
        const changed = useUIStore.getState().sidebarTab !== "files";
        useUIStore.getState().setSidebarTab("files");
        recordUI(changed ? "已切换文件列表" : "文件列表已经打开", changed);
        break;
      }
      if (workbenchBusy()) {
        recordUI("正在使用工作台，未切换界面");
        break;
      }
      const filePath = String(data.file_path || "");
      const sheetRaw = String(data.sheet || "");
      const sheet = sheetRaw || undefined;
      if (filePath && pathIsDismissed(filePath)) {
        recordUI("用户已关闭该文件，未重新打开");
        break;
      }
      if (!filePath || classifyWorkspaceFile(filePath) !== "spreadsheet") {
        recordUI("缺少有效表格目标，未切换界面");
        break;
      }
      if (surface === "side_panel") {
        openWorkspaceFile(filePath, {
          intent: "preview",
          sheet,
          sessionId: ctx.effectiveSessionId,
        });
        const opened = useExcelStore.getState();
        const changed = opened.panelOpen && opened.activeFilePath === filePath;
        recordUI(changed ? "已设置侧栏打开状态；文件加载结果另行处理" : "侧栏打开请求未改变状态", changed);
      } else if (surface === "sheet_full") {
        openWorkspaceFile(filePath, {
          intent: "full",
          sheet,
          sessionId: ctx.effectiveSessionId,
        });
        const changed = useExcelStore.getState().fullViewPath === filePath;
        recordUI(changed ? "已设置表格页打开状态；文件加载结果另行处理" : "表格页打开请求未改变状态", changed);
      } else if (surface === "compare") {
        const fileB = String(data.file_path_b || "");
        if (filePath && fileB && filePath !== fileB && classifyWorkspaceFile(fileB) === "spreadsheet" && !pathIsDismissed(fileB)) {
          useExcelStore.getState().openCompare(filePath, fileB);
          const opened = useExcelStore.getState();
          const changed = opened.compareMode && opened.compareFileA === filePath && opened.compareFileB === fileB;
          recordUI(changed ? "已设置对比视图状态；文件加载结果另行处理" : "对比视图请求未改变状态", changed);
        } else {
          recordUI("缺少有效对比目标，未切换界面");
        }
      } else {
        recordUI("未识别界面建议，保持当前界面");
      }
      break;
    }

    case "jev_trace": {
      if (ctx.fromReplay) break;
      const activeSession = useSessionStore.getState().activeSessionId;
      if (activeSession && activeSession !== ctx.effectiveSessionId) break;
      useJevStore.getState().appendFromEvent(data);
      break;
    }

    case "done": {
      // 仅清除流水线进度指示器。
      // 不要在此处调用 setStreaming(false) / setAbortController(null) / saveCurrentSession()；
      // 这些清理由 chat-actions.ts 的 finally 块统一执行。
      // 如果在 done 事件中提前清除，会导致 SessionSync 的 useEffect 在 finally 之前触发，
      // 触发 refreshSessionMessagesFromBackend 读到后端尚未持久化的数据，造成消息丢失。
      S().setPipelineStatus(null);

      // ── 自动打开工作台文档 ──
      // 本轮若改了 spreadsheet / word，且用户没在看别的工作台面板，打开最后一个。
      // 高置信 stay 的 ui_hint 会置 suppressAutoOpen，跳过全部自动导航。
      if (!ctx.suppressAutoOpen) {
        const doneMsg = getLastAssistantMessage(S().messages, msgId);
        const affected = doneMsg?.affectedFiles ?? [];
        const lastSpreadsheet = [...affected].reverse().find(
          (f) => classifyWorkspaceFile(f) === "spreadsheet",
        );
        const lastWord = [...affected].reverse().find(
          (f) => classifyWorkspaceFile(f) === "word",
        );
        const target = lastSpreadsheet ?? lastWord;
        if (target) {
          const excelStore = useExcelStore.getState();
          const wordStore = useWordStore.getState();
          const previewStore = useFilePreviewStore.getState();
          if (
            !excelStore.panelOpen
            && !wordStore.panelOpen
            && !previewStore.textOpen
            && !previewStore.imageOpen
          ) {
            openWorkspaceFile(target, { sessionId: ctx.effectiveSessionId });
          }
        }
      }
      break;
    }

    case "llm_retry": {
      const retryStatus = data.retry_status as string;
      const retryAttempt = (data.retry_attempt as number) || 0;
      const retryMax = (data.retry_max_attempts as number) || 0;
      const retryDelay = (data.retry_delay_seconds as number) || 0;
      const retryError = (data.retry_error_message as string) || "";

      if (retryStatus === "retrying") {
        // 追加重试 retry block
        S().upsertBlockByType(msgId, "llm_retry", {
          type: "llm_retry",
          retryAttempt: retryAttempt,
          retryMaxAttempts: retryMax,
          retryDelaySeconds: retryDelay,
          retryErrorMessage: retryError,
          retryStatus: "retrying",
        });
      } else if (retryStatus === "succeeded") {
        // 重试成功，更新 block 状态
        S().upsertBlockByType(msgId, "llm_retry", {
          type: "llm_retry",
          retryAttempt: retryAttempt,
          retryMaxAttempts: retryMax,
          retryDelaySeconds: 0,
          retryErrorMessage: "",
          retryStatus: "succeeded",
        });
      } else if (retryStatus === "exhausted") {
        // 重试耗尽
        S().upsertBlockByType(msgId, "llm_retry", {
          type: "llm_retry",
          retryAttempt: retryAttempt,
          retryMaxAttempts: retryMax,
          retryDelaySeconds: 0,
          retryErrorMessage: retryError,
          retryStatus: "exhausted",
        });
      }
      break;
    }

    case "failure_guidance": {
      ctx.hadStreamError = true;
      S().setPipelineStatus(null);
      // 用 category 去重，避免同一 category 的多个 failure_guidance block
      const fgCategory = (data.category as string) || "unknown";
      const existingBlocks = (() => {
        const msgs = S().messages;
        for (let i = msgs.length - 1; i >= 0; i--) {
          if (msgs[i].id === msgId && msgs[i].role === "assistant") {
            return (msgs[i] as { blocks: import("@/lib/types").AssistantBlock[] }).blocks;
          }
        }
        return [];
      })();
      const hasSameCategory = existingBlocks.some(
        (b) => b.type === "failure_guidance" && b.category === fgCategory,
      );
      if (hasSameCategory) {
        // 替换已有同 category 的 block
        S().updateAssistantMessage(msgId, (m) => ({
          ...m,
          blocks: m.blocks.map((b) => {
            if (b.type === "failure_guidance" && b.category === fgCategory) {
              return {
                type: "failure_guidance" as const,
                category: fgCategory as "model" | "transport" | "config" | "quota" | "unknown",
                code: (data.code as string) || "",
                title: (data.title as string) || "",
                message: (data.message as string) || "",
                stage: (data.stage as string) || "",
                retryable: !!data.retryable,
                diagnosticId: (data.diagnostic_id as string) || "",
                actions: (data.actions as { type: "retry" | "open_settings" | "copy_diagnostic"; label: string }[]) || [],
                provider: (data.provider as string) || undefined,
                model: (data.model as string) || undefined,
              };
            }
            return b;
          }),
        }));
      } else {
        S().appendBlock(msgId, {
          type: "failure_guidance",
          category: fgCategory as "model" | "transport" | "config" | "quota" | "unknown",
          code: (data.code as string) || "",
          title: (data.title as string) || "",
          message: (data.message as string) || "",
          stage: (data.stage as string) || "",
          retryable: !!data.retryable,
          diagnosticId: (data.diagnostic_id as string) || "",
          actions: (data.actions as { type: "retry" | "open_settings" | "copy_diagnostic"; label: string }[]) || [],
          provider: (data.provider as string) || undefined,
          model: (data.model as string) || undefined,
        });
      }
      // 收起同期的 llm_retry(exhausted) block
      S().updateAssistantMessage(msgId, (m) => ({
        ...m,
        blocks: m.blocks.filter(
          (b) => !(b.type === "llm_retry" && b.retryStatus === "exhausted"),
        ),
      }));
      break;
    }

    // ── 工具调用通知：（tools 开启时）──────────────────────────────
    case "tool_call_notice": {
      const tnToolName = (data.tool_name as string) || "";
      const tnArgsSummary = (data.args_summary as string) || "";
      const tnIteration = (data.iteration as number) || 0;
      S().appendBlock(msgId, {
        type: "tool_notice",
        toolName: tnToolName,
        argsSummary: tnArgsSummary,
        iteration: tnIteration,
      });
      break;
    }

    // ── 推理过程通知：（reasoning 开启时）────────────────────────────
    case "reasoning_notice": {
      const rnContent = (data.content as string) || "";
      const rnIteration = (data.iteration as number) || 0;
      if (rnContent) {
        S().appendBlock(msgId, {
          type: "reasoning_notice",
          content: rnContent,
          iteration: rnIteration,
        });
      }
      break;
    }

    default:
      break;
  }
}

// ---------------------------------------------------------------------------
// 辅助函数：thinking 状态管理
// ---------------------------------------------------------------------------

/** 在非 thinking 事件前调用，关闭进行中的 thinking block。*/
export function finalizeThinking(ctx: SSEHandlerContext): void {
  if (!ctx.thinkingInProgress) return;
  ctx.thinkingInProgress = false;
  ctx.batcher.flush();
  S().updateBlockByType(ctx.assistantMsgId, "thinking", (b) => {
    if (b.type === "thinking" && b.startedAt != null && b.duration == null) {
      return { ...b, duration: (Date.now() - b.startedAt) / 1000 };
    }
    return b;
  });
}

/** 标准的事件前处理：调用方在 consumeSSE 回调顶部使用。*/
export function preDispatch(event: SSEEvent, ctx: SSEHandlerContext): void {
  if (event.event === "heartbeat") return;
  if (event.event !== "thinking_delta" && event.event !== "thinking") {
    finalizeThinking(ctx);
  }
  if (event.event !== "text_delta" && event.event !== "thinking_delta") {
    ctx.batcher.flush();
  }
}
