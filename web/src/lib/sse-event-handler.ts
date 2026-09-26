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
import { useDispatchStore } from "@/stores/dispatch-store";
import type { DispatchReceipt } from "@/lib/types";
import { dedupeFileAttachments, prepareUserMessageDisplay } from "@/lib/upload-notice";
import { classifyWorkspaceFile } from "@/lib/file-kind";
import { displayFileName } from "@/lib/file-identity";
import { handleWorkspaceFilesDeleted } from "@/lib/file-deletion";
import { openWorkspaceFile } from "@/lib/open-workspace-file";
import { getIsMobile } from "@/hooks/use-mobile";
import type { AssistantBlock, Session } from "@/lib/types";
import { instantSessionTitle } from "@/lib/session-title";
import { normalizeRelativePath, workspaceKeyForSessionId } from "@/lib/workspace-file-ref";
import { parseWorkbookTarget, parseWorkbookPresentation, showWorkbookPresentation } from "@/lib/workbook-interaction";
import { isFailureGuidanceBlock, isSameFailure } from "@/lib/failure-recovery";
import { tokenStatsFromUsage } from "@/lib/token-stats";
import { normalizeTaskItems, applyTaskStatusPatch } from "@/lib/history-blocks";
export { normalizeTaskItems } from "@/lib/history-blocks";

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
  /** Whether dispatch receipts should create an in-flight intervention badge. */
  showDispatch?: boolean;
  /** 用户原始消息文本（仅 sendMessage 流程需要，用于 session_init 标题推断）。 */
  userText?: string;

  // --- 可变状态引用（由调用方持有，分发器读写）---
  /** thinking block 是否进行中。 */
  thinkingInProgress: boolean;
  /** 流是否遇到错误。 */
  hadStreamError: boolean;
  /** 本轮是否出现过会写入历史的工具副作用（diff / 改文件），用于结束后补同步。 */
  hadPersistedToolWork?: boolean;
  turnId?: string;
  completedTurnId?: string;
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
  setTarget?(messageId: string): void;
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
  return Boolean(dismissed && [...dismissed].some((entry) => normalizeRelativePath(entry) === normalizeRelativePath(path)));
}

function autoNavigationBlocked(ctx: SSEHandlerContext): boolean {
  return Boolean(ctx.fromReplay || ctx.suppressAutoOpen
    || useExcelStore.getState().autoOpenSuppressedSessionId === ctx.effectiveSessionId
    || (useSessionStore.getState().activeSessionId && useSessionStore.getState().activeSessionId !== ctx.effectiveSessionId));
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

function receiveDispatch(raw: unknown, ctx: SSEHandlerContext): DispatchReceipt | null {
  const receipt = raw as DispatchReceipt | null;
  if (!receipt?.dispatch_id || !receipt.client_message_id || typeof receipt.revision !== "number") return null;
  useDispatchStore.getState().upsert(ctx.effectiveSessionId, receipt);
  return useDispatchStore.getState().sessions[ctx.effectiveSessionId]?.[receipt.client_message_id] ?? receipt;
}

function activateDispatch(receipt: DispatchReceipt, ctx: SSEHandlerContext) {
  if (receipt.hidden) return;
  const visible = prepareUserMessageDisplay(receipt.content);
  const existing = S().messagesById[receipt.client_message_id];
  if (!existing) {
    S().addUserMessage(receipt.client_message_id, visible.content, visible.files);
  } else if (existing.role === "user") {
    // A queued message has no optimistic bubble. If a reconnect races the
    // local send, merge the receipt's durable attachments without replacing
    // the user's original visible text with transport metadata.
    const files = dedupeFileAttachments([...(existing.files ?? []), ...visible.files]);
    S().updateUserDispatch(receipt.client_message_id, {
      ...(files.length > 0 ? { files } : {}),
      dispatchId: receipt.dispatch_id,
      dispatchMode: receipt.mode,
      dispatchStatus: receipt.status,
    });
  }
  if (!existing) {
    S().updateUserDispatch(receipt.client_message_id, { dispatchId: receipt.dispatch_id, dispatchMode: receipt.mode, dispatchStatus: receipt.status });
  }
  // Keep the optimistic first reply; later turns and steers get their own owner.
  if (!existing || ctx.turnId) {
    ctx.batcher.flush();
    ctx.assistantMsgId = `dispatch:${receipt.dispatch_id}`;
    S().addAssistantMessage(ctx.assistantMsgId);
    ctx.batcher.setTarget?.(ctx.assistantMsgId);
  }
}

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
  mutations: { identity?: string; content_version?: string; deleted?: boolean }[] = [],
): void {
  if (changedFiles.length === 0) return;
  ctx.hadPersistedToolWork = true;
  const excelStore = useExcelStore.getState();
  const wordStore = useWordStore.getState();
  // 文件事件属于产生它的会话工作区，按事件会话键入桶，避免会话切换期间错挂到当前工作区。
  const sourceWorkspaceKey = workspaceKeyForSessionId(ctx.effectiveSessionId);
  // 删除与写入分流：deleted 身份走统一清理（最近打开/已打开标签/面板一并剔除），
  // 绝不能再进入 recentFiles，否则各入口会重新列出已删文件、口径混乱。
  const deletedIdentities = new Set(
    mutations.filter((item) => item.deleted && item.identity)
      .map((item) => (item.identity as string).replace(/^\.\//, "")),
  );
  const deletedFiles: string[] = [];
  const liveFiles: string[] = [];
  for (const filePath of changedFiles) {
    if (!filePath) continue;
    if (deletedIdentities.has(filePath.replace(/^\.\//, ""))) deletedFiles.push(filePath);
    else liveFiles.push(filePath);
  }
  if (deletedFiles.length > 0) {
    handleWorkspaceFilesDeleted(deletedFiles, sourceWorkspaceKey);
  }
  for (const filePath of liveFiles) {
    const filename = filePath.split("/").pop() || filePath;
    excelStore.addRecentFileIfNotDismissed({ path: filePath, filename }, sourceWorkspaceKey);
    const version = mutations.find((item) => item.identity?.replace(/^\.\//, "") === filePath.replace(/^\.\//, ""))?.content_version;
    excelStore.notifyWorkbookChanged(filePath, sourceWorkspaceKey, version || undefined);
  }
  wordStore.handleFilesChanged(liveFiles);
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

/**
 * Provider adapters can expose one reasoning pass as a streamed ``thinking``
 * block and a compatibility ``reasoning_notice``.  Compare the visible text
 * across the whole assistant message instead of only looking at the last
 * block: a replay may insert a tool/status block between the two events.
 */
function _sameReasoningText(left: string, right: string): boolean {
  if (!left || !right) return false;
  if (left === right) return true;
  // A thinking event is capped on the wire.  Accept the capped prefix so a
  // long reasoning summary does not render as a second card on reconnect.
  return (left.length >= 2000 && right.startsWith(left))
    || (right.length >= 2000 && left.startsWith(right));
}

function _hasReasoningBlock(
  msgId: string,
  content: string,
  iteration?: number,
  type: "thinking" | "reasoning_notice" = "thinking",
): boolean {
  const message = getLastAssistantMessage(S().messages, msgId);
  if (!message) return false;
  return message.blocks.some((block) => {
    if (block.type !== type || !block.content) return false;
    if (iteration !== undefined && block.iteration !== undefined && block.iteration !== iteration) return false;
    return _sameReasoningText(block.content, content);
  });
}

/** 流式增量按字符串原样保留，包括空格和换行。 */
function _streamDeltaContent(data: Record<string, unknown>): string {
  return typeof data.content === "string" ? data.content : "";
}

/** 本轮失败收尾时，给"从未进入执行器"的调用一句确切状态。 */
function turnFailureNote(stopReason: string): string {
  const reason =
    stopReason === "llm_unavailable" ? "模型服务中断"
    : stopReason === "timeout" ? "本轮超时"
    : stopReason === "interrupt" ? "本轮已中断"
    : stopReason === "cancelled" ? "本轮已取消"
    : stopReason === "wall_clock" || stopReason === "budget" ? "本轮预算耗尽"
    : "本轮失败收尾";
  return `${reason}，本次调用未执行，没有产生任何副作用。`;
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

  // A reconnect can overlap the old fetch for a short time.  Once a new
  // stream has been announced, events carrying the old stream id must not
  // mutate either the cursor or the visible message.  Without this guard a
  // late old event could move ``activeStreamId`` backwards and be replayed a
  // second time on the next subscribe.
  if (eventStreamId && event.event !== "stream_init") {
    const active = S().activeStreamId;
    // A live first event can be the first packet observed after a proxy
    // dropped ``stream_init``; accept it and bind the new stream.  Replayed
    // packets carry an explicit marker, so an old stream cannot roll the UI
    // back during an overlapping reconnect.
    if (active && active !== eventStreamId && data.replayed === true) return;
    // Replayed events are explicitly marked by /chat/subscribe.  A client
    // that already rendered that sequence can safely discard it, which also
    // protects streamed reasoning deltas whose individual chunks cannot be
    // compared by text alone.
    if (data.replayed === true && eventSeq !== null && eventSeq <= S().latestSeq) return;
  }

  // stream_init carries a historical metadata seq for wire compatibility;
  // only replayable events advance the resume cursor.
  if (eventSeq !== null && event.event !== "stream_init") {
    const state = S();
    const streamId = eventStreamId ?? state.activeStreamId;
    if (streamId) {
      state.setStreamState(streamId, Math.max(state.latestSeq, eventSeq));
    }
  }

  switch (event.event) {
    case "dispatch_snapshot": {
      for (const receipt of Array.isArray(data.dispatches) ? data.dispatches : []) receiveDispatch(receipt, ctx);
      const active = receiveDispatch(data.active_dispatch, ctx);
      if (active) activateDispatch(active, ctx);
      ctx.turnId = String(data.turn_id || "");
      break;
    }
    case "turn_start": {
      const receipt = receiveDispatch(data.dispatch, ctx);
      const turnId = typeof data.turn_id === "string" ? data.turn_id : "";
      // A direct idle send still carries a dispatch receipt for idempotency,
      // but it is a new task rather than an intervention in an existing one.
      const isInitialDirectTurn = ctx.showDispatch === false && !ctx.turnId;
      if (receipt && turnId !== ctx.turnId && !isInitialDirectTurn) activateDispatch(receipt, ctx);
      ctx.turnId = turnId;
      ctx.completedTurnId = undefined;
      ctx.thinkingInProgress = false;
      break;
    }
    // 本轮失败收尾：从未进入执行器的调用定案为"未执行"（不留"进行中"）。
    // 已派发的执行（有 executionId，含后台任务/子代理）不动，由执行器自己的
    // 终态事件收口——后台命令继续跑，只是状态要确切。
    case "turn_failed": {
      const stopReason = (data.stop_reason as string) || "error";
      const tfNote = turnFailureNote(stopReason);
      const tfMsg = getLastAssistantMessage(S().messages, msgId);
      const stragglers = (tfMsg?.blocks ?? []).filter((b) =>
        b.type === "tool_call" && !b.executionId && !!b.toolCallId
        && (b.status === "streaming" || b.status === "running" || b.status === "pending"));
      for (const block of stragglers) {
        if (block.type !== "tool_call" || !block.toolCallId) continue;
        useExcelStore.getState().clearStreamingArgs(block.toolCallId);
        S().clearToolProgress(block.toolCallId);
        S().updateToolCallBlock(msgId, block.toolCallId, (b) => {
          if (b.type !== "tool_call" || b.executionId) return b;
          if (b.status === "success" || (b.status === "error" && b.executionState !== "aborted")) return b;
          return {
            ...b,
            status: "error",
            executionState: "aborted",
            error: tfNote,
            result: tfNote,
            abortReason: stopReason,
          } as AssistantBlock;
        });
      }
      break;
    }
    case "dispatch_state": {
      const receipt = receiveDispatch(data, ctx);
      if (!receipt) break;
      if (!ctx.turnId && receipt.turn_id) ctx.turnId = receipt.turn_id;
      S().updateUserDispatchById(receipt.dispatch_id, receipt.status);
      if (receipt.mode === "steer" && receipt.status === "completed" && receipt.turn_id === ctx.turnId
          && !S().messagesById[receipt.client_message_id]) activateDispatch(receipt, ctx);
      break;
    }
    case "stream_init": {
      if (eventStreamId) {
        // Preserve the historical observable call for integrations that use
        // stream_init as metadata, then reset the actual replay cursor: this
        // event is not present in SessionStreamState.event_buffer.
        S().setStreamState(eventStreamId, eventSeq ?? 0);
        if (eventSeq !== null) S().setStreamState(eventStreamId, 0);
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

    case "dispatch_accepted":
    case "dispatch_queued":
    case "dispatch_applying":
    case "dispatch_applied":
    case "dispatch_failed": {
      const dispatchId = typeof data.dispatch_id === "string" ? data.dispatch_id : "";
      const status = typeof data.status === "string" ? data.status : event.event.replace("dispatch_", "");
      if (dispatchId) S().updateUserDispatchById(dispatchId, status);
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
      const thinkingIteration = typeof data.iteration === "number" ? data.iteration : undefined;
      const lastThinking = _getLastBlockOfType(msgId, "thinking");
      if (lastThinking && lastThinking.type === "thinking" && lastThinking.duration == null
        && (thinkingIteration === undefined || lastThinking.iteration === undefined
          || lastThinking.iteration === thinkingIteration)) {
        ctx.batcher.pushThinking(thinkingDelta);
      } else {
        ctx.batcher.flush();
        S().appendBlock(msgId, {
          type: "thinking",
          content: thinkingDelta,
          startedAt: Date.now(),
          ...(thinkingIteration !== undefined ? { iteration: thinkingIteration } : {}),
        });
      }
      ctx.thinkingInProgress = true;
      break;
    }

    case "thinking": {
      const thinkingContent = (data.content as string) || "";
      const thinkingIteration = typeof data.iteration === "number" ? data.iteration : undefined;
      // A non-streaming provider sends a complete THINKING event.  A replay
      // or compatibility adapter may send the same event after deltas; keep
      // one visible block for that pass.
      const existingThinking = getLastAssistantMessage(S().messages, msgId)?.blocks.some((block) =>
        block.type === "thinking"
        && (thinkingIteration === undefined || block.iteration === undefined || block.iteration === thinkingIteration)
        && (block.content === thinkingContent
          || (Boolean(block.content) && block.content.startsWith(thinkingContent))
          || (Boolean(block.content) && thinkingContent.startsWith(block.content))),
      );
      if (!thinkingContent || existingThinking || _hasReasoningBlock(msgId, thinkingContent, thinkingIteration, "thinking")) break;
      S().appendBlock(msgId, {
        type: "thinking",
        content: thinkingContent,
        duration: (data.duration as number) || undefined,
        startedAt: Date.now(),
        ...(thinkingIteration !== undefined ? { iteration: thinkingIteration } : {}),
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
      const iteration = typeof data.iteration === "number" ? data.iteration : undefined;
      if (!lastBlock || lastBlock.type !== "text" || lastBlock.iteration !== iteration) {
        ctx.batcher.flush();
        S().appendBlock(msgId, { type: "text", content: "", ...(iteration !== undefined ? { iteration } : {}) });
      }
      ctx.batcher.pushText(_streamDeltaContent(data));
      break;
    }

    case "retract_text": {
      if (typeof data.iteration !== "number") break;
      ctx.batcher.flush();
      S().updateAssistantMessage(msgId, (message) => ({ ...message,
        blocks: message.blocks.filter((block) => block.type !== "text" || block.iteration !== data.iteration),
      }));
      break;
    }

    // --- 流式工具参数 delta ---
    case "tool_call_args_delta": {
      const adToolCallId = (data.tool_call_id as string) || "";
      const adToolName = (data.tool_name as string) || "";
      const adDelta = (data.args_delta as string) || "";
      if (adToolCallId && adDelta) {
        const adMsg = getLastAssistantMessage(S().messages, msgId);
        const adBlock = adMsg?.blocks.find(
          (b) => b.type === "tool_call" && b.toolCallId === adToolCallId,
        );
        if (adBlock && adBlock.type === "tool_call" && adBlock.executionState === "aborted") {
          // 同一个 call id 被下一次尝试复用：先把"未执行"的定案翻回流式，
          // 并丢掉上一轮的半截参数，避免旧状态粘在新尝试上。
          useExcelStore.getState().clearStreamingArgs(adToolCallId);
          S().updateToolCallBlock(msgId, adToolCallId, (b) => b.type === "tool_call"
            ? { ...b, status: "streaming", executionState: undefined, error: undefined, result: undefined } as AssistantBlock
            : b);
        } else if (!adBlock && adToolName) {
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
        useExcelStore.getState().appendStreamingArgs(adToolCallId, adDelta);
      }
      break;
    }

    // --- 未执行即被放弃的调用：定案为失败，不留"进行中" ---
    case "tool_call_aborted": {
      const abId = (data.tool_call_id as string) || "";
      if (!abId) break;
      const abMessage = ((data.message as string) || "").trim();
      const abCode = (data.error as string) || "TOOL_CALL_NOT_EXECUTED";
      // 折叠行显示的是 error 字段：这里给"人话"，机器码留在 block.abortReason。
      const abDetail = abMessage || abCode;
      const abReason = (data.reason as string) || undefined;
      useExcelStore.getState().clearStreamingArgs(abId);
      S().clearToolProgress(abId);
      const abMsg = getLastAssistantMessage(S().messages, msgId);
      const abExists = abMsg?.blocks.some(
        (b) => b.type === "tool_call" && b.toolCallId === abId,
      );
      if (abExists) {
        S().updateToolCallBlock(msgId, abId, (b) => {
          if (b.type !== "tool_call") return b;
          // 已经落定终态的块（真的执行过）不改写：执行器的终态优先。
          if (b.status === "success" || (b.status === "error" && b.executionState !== "aborted")) return b;
          return {
            ...b,
            status: "error",
            executionState: "aborted",
            error: abDetail,
            result: abMessage || b.result,
            abortReason: abReason,
          } as AssistantBlock;
        });
      } else {
        S().appendBlock(msgId, {
          type: "tool_call",
          toolCallId: abId,
          name: (data.tool_name as string) || "",
          args: {},
          status: "error",
          executionState: "aborted",
          error: abDetail,
          result: abMessage || undefined,
          abortReason: abReason,
        });
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
          && (b.status === "running" || b.status === "streaming" || b.executionState === "queued" || b.executionState === "cancelling"
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
      if (data.tool_name === "show_workbook" && data.success !== false && data.replayed !== true && !autoNavigationBlocked(ctx)) {
        const presentation = parseWorkbookPresentation(data.result as string);
        if (presentation && !pathIsDismissed(presentation.target.file_path)) showWorkbookPresentation(presentation, ctx.effectiveSessionId);
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
      const taskCall = getLastAssistantMessage(S().messages, msgId)?.blocks.findLast((block) =>
        block.type === "tool_call" && (typeof data.tool_call_id === "string" && data.tool_call_id
          ? block.toolCallId === data.tool_call_id
          : ["task_create", "task_update", "write_plan"].includes(block.name)),
      );
      if (taskCall?.type === "tool_call" && taskCall.toolCallId && payloadItems.length) {
        S().updateToolCallBlock(msgId, taskCall.toolCallId, (block) => block.type === "tool_call"
          ? { ...block, taskList: applyTaskStatusPatch(payloadItems, taskIndex, taskStatus) } : block);
        break;
      }
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
        if (!autoNavigationBlocked(ctx) && !workbenchBusy() && !pathIsDismissed(edFilePath) && !pathIsDismissed(edEntry.filePathB)) {
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
      const mutations = (data.mutations as { identity?: string; content_version?: string; deleted?: boolean }[]) || [];
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

    case "compaction": {
      const operationId = typeof data.operation_id === "string" ? data.operation_id : "";
      const status = typeof data.status === "string" ? data.status : "running";
      const block: AssistantBlock = {
        type: "compaction",
        operationId,
        status: status as "queued" | "running" | "completed" | "skipped" | "failed",
        message: typeof data.message === "string" ? data.message : "正在压缩历史对话",
        detail: typeof data.detail === "string" ? data.detail : undefined,
        tokensBefore: typeof data.tokens_before === "number" ? data.tokens_before : undefined,
        tokensAfter: typeof data.tokens_after === "number" ? data.tokens_after : undefined,
        messagesBefore: typeof data.messages_before === "number" ? data.messages_before : undefined,
        messagesAfter: typeof data.messages_after === "number" ? data.messages_after : undefined,
        preservedQuotes: typeof data.preserved_quotes === "number" ? data.preserved_quotes : undefined,
      };
      const current = _getLastBlockOfType(ctx.assistantMsgId, "compaction") as Extract<AssistantBlock, { type: "compaction" }> | null;
      if (current && operationId && current.operationId === operationId) {
        S().updateBlockByType(ctx.assistantMsgId, "compaction", () => block);
      } else {
        S().appendBlock(ctx.assistantMsgId, block);
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
    case "turn_reply":
    case "reply": {
      receiveDispatch(data.dispatch, ctx);
      if (event.event === "reply" && data.turn_id && ctx.completedTurnId === data.turn_id) break;
      if (event.event === "turn_reply") {
        ctx.completedTurnId = String(data.turn_id || "");
        const receipt = data.dispatch as DispatchReceipt | undefined;
        if (receipt?.status === "interrupted") {
          S().setPendingQuestion(null);
          S().setPendingApproval(null);
        }
        if (Number(data.total_tokens) > 0) {
          S().upsertBlockByType(msgId, "token_stats", tokenStatsFromUsage(data));
        }
      }
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
        } else if (event.event === "turn_reply" && !textBlocks.map((b) => b.type === "text" ? b.content : "").join("").includes(content)) {
          S().appendBlock(msgId, { type: "text", content });
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
      if (autoNavigationBlocked(ctx)) {
        recordUI("用户已收起表格，保持当前界面");
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
      if (!autoNavigationBlocked(ctx) && !workbenchBusy()) {
        const doneMsg = getLastAssistantMessage(S().messages, msgId);
        const affected = doneMsg?.affectedFiles ?? [];
        const lastSpreadsheet = [...affected].reverse().find(
          (f) => classifyWorkspaceFile(f) === "spreadsheet" && !pathIsDismissed(f),
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
      const guidance: Extract<AssistantBlock, { type: "failure_guidance" }> = {
        type: "failure_guidance",
        category: (data.category as typeof guidance.category) || "unknown",
        code: (data.code as string) || "",
        title: (data.title as string) || "",
        message: (data.message as string) || "",
        stage: (data.stage as string) || "",
        retryable: !!data.retryable,
        diagnosticId: (data.diagnostic_id as string) || "",
        actions: (data.actions as typeof guidance.actions) || [],
        provider: (data.provider as string) || undefined,
        model: (data.model as string) || undefined,
      };
      S().updateAssistantMessage(msgId, (m) => {
        let inserted = false;
        const blocks = m.blocks.flatMap((block): AssistantBlock[] => {
          if (block.type === "llm_retry" && block.retryStatus === "exhausted") return [];
          // 同一诊断的历史卡可能是 unknown；同时保留同分类只显示最新失败的行为。
          if (isFailureGuidanceBlock(block)
            && (isSameFailure(block, guidance) || block.category === guidance.category)) {
            if (inserted) return [];
            inserted = true;
            return [guidance];
          }
          return [block];
        });
        if (!inserted) blocks.push(guidance);
        return { ...m, blocks };
      });
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
      const rnIteration = typeof data.iteration === "number" ? data.iteration : undefined;
      if (rnContent) {
        // ``reasoning_notice`` is a compatibility event for clients that do
        // not render the normal thinking stream.  When the same turn already
        // has a thinking block, drawing the notice as a second block makes a
        // single provider summary look like repeated reasoning.  The server
        // caps the legacy thinking event at 2000 chars, so accept a matching
        // prefix for long summaries as well.
        const last = getLastAssistantMessage(S().messages, msgId);
        if (_hasReasoningBlock(msgId, rnContent, rnIteration, "thinking")) {
          break;
        }
        const duplicateNotice = last?.blocks.some((block) =>
          block.type === "reasoning_notice" &&
          (rnIteration === undefined || block.iteration === undefined || block.iteration === rnIteration) &&
          _sameReasoningText(block.content, rnContent),
        );
        if (duplicateNotice) break;
        S().appendBlock(msgId, {
          type: "reasoning_notice",
          content: rnContent,
          ...(rnIteration !== undefined ? { iteration: rnIteration } : {}),
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
  // Keep replay dedupe side-effect free: callers invoke preDispatch before
  // dispatchSSEEvent, so a duplicate packet must not finalize the current
  // thinking block or flush text deltas on its way to being discarded.
  if (event.data.replayed === true
    && typeof event.data.seq === "number"
    && event.data.seq <= S().latestSeq) return;
  if (event.event !== "thinking_delta" && event.event !== "thinking") {
    finalizeThinking(ctx);
  }
  if (event.event !== "text_delta" && event.event !== "thinking_delta") {
    ctx.batcher.flush();
  }
}
