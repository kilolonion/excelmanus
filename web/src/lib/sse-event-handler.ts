/**
 * 鍏变韩 SSE 浜嬩欢鍒嗗彂鍣?鈥?娑堥櫎 sendMessage / sendContinuation / subscribeToSession 鐨勪笁閲嶅鍒躲€?
 *
 * 鎵€鏈?SSE 浜嬩欢澶勭悊閫昏緫闆嗕腑鍦ㄦ鏂囦欢鐨?`dispatchSSEEvent()` 鍑芥暟涓€?
 * 璋冪敤鏂瑰彧闇€鏋勫缓 `SSEHandlerContext` 骞跺湪 consumeSSE 鍥炶皟涓鎵樼粰璇ュ嚱鏁般€?
 */

import { useChatStore, type PipelineStatus } from "@/stores/chat-store";
import { useSessionStore } from "@/stores/session-store";
import { useUIStore } from "@/stores/ui-store";
import { useExcelStore, type ExcelCellDiff, type ExcelDiffEntry, type ExcelPreviewData, type MergeRange } from "@/stores/excel-store";
import type { AssistantBlock, TaskItem } from "@/lib/types";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/** SSE 浜嬩欢鐨勮鑼冨寲琛ㄧず锛堢敱 consumeSSE 瑙ｆ瀽鍚庝紶鍏ワ級銆?*/
export interface SSEEvent {
  event: string;
  data: Record<string, unknown>;
}

/** 浜嬩欢鍒嗗彂鍣ㄨ繍琛屾墍闇€鐨勪笂涓嬫枃锛岀敱璋冪敤鏂规瀯寤哄苟浼犲叆銆?*/
export interface SSEHandlerContext {
  /** 褰撳墠 assistant 娑堟伅 ID锛堜簨浠惰拷鍔犲埌姝ゆ秷鎭殑 blocks 涓級銆?*/
  assistantMsgId: string;
  /** RAF 澧為噺鎵瑰鐞嗗櫒瀹炰緥銆?*/
  batcher: DeltaBatcher;
  /** 褰撳墠浼氳瘽 ID锛堢敤浜庡浠藉埛鏂扮瓑锛夈€?*/
  effectiveSessionId: string;
  /** 鏄惁澶勪簬 sendMessage 鐨勯娆″彂閫佹祦绋嬶紙vs continuation / subscribe锛夈€?*/
  isFirstSend: boolean;
  /** 鐢ㄦ埛鍘熷娑堟伅鏂囨湰锛堜粎 sendMessage 娴佺▼闇€瑕侊紝鐢ㄤ簬 session_init 鏍囬鎺ㄦ柇锛夈€?*/
  userText?: string;

  // 鈹€鈹€ 鍙彉鐘舵€佸紩鐢紙鐢辫皟鐢ㄦ柟鎸佹湁锛屽垎鍙戝櫒璇诲啓锛夆攢鈹€
  /** thinking block 鏄惁杩涜涓€?*/
  thinkingInProgress: boolean;
  /** 娴佹槸鍚﹂亣鍒拌繃閿欒銆?*/
  hadStreamError: boolean;
}

/** DeltaBatcher 鎺ュ彛锛堜粠 chat-actions.ts 澶嶇敤锛夈€?*/
export interface DeltaBatcher {
  pushText(delta: string): void;
  pushThinking(delta: string): void;
  flush(): void;
  dispose(): void;
  hasPendingContent(): boolean;
}

// ---------------------------------------------------------------------------
// Helpers (浠?chat-actions.ts 鎻愬崌涓烘ā鍧楃骇鍏变韩)
// ---------------------------------------------------------------------------

/** 灏嗗悗绔?route_mode 鏄犲皠涓虹敤鎴峰弸濂界殑涓枃鏍囩 */
export function _friendlyRouteMode(mode: string): string {
  const map: Record<string, string> = {
    all_tools: "Smart Route",
    control_command: "Control Command",
    slash_direct: "Slash Command",
    slash_not_found: "Skill Not Found",
    slash_not_user_invocable: "Skill Not Invocable",
    no_skillpack: "Base Mode",
    fallback: "Fallback Mode",
    hidden: "Route",
  };
  return map[mode] || mode;
}

/** 灏嗗悗绔?snake_case diff changes 鏄犲皠涓哄墠绔?camelCase ExcelCellDiff[] */
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

function normalizeTaskItems(taskListPayload: unknown): TaskItem[] {
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
    return {
      content:
        (item.content as string)
        || (item.title as string)
        || (item.description as string)
        || `浠诲姟 ${i + 1}`,
      status: (item.status as string) || "pending",
      index: typeof item.index === "number" ? item.index : i,
      verification: (item.verification as string) || undefined,
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

/** 鑾峰彇鎸囧畾 assistant 娑堟伅銆?*/
export function getLastAssistantMessage(
  messages: ReturnType<typeof useChatStore.getState>["messages"],
  id: string,
) {
  const msg = messages.find((m) => m.id === id);
  if (msg && msg.role === "assistant") return msg;
  return null;
}

// ---------------------------------------------------------------------------
// 鍐呴儴蹇嵎寮曠敤
// ---------------------------------------------------------------------------

const S = () => useChatStore.getState();

function _getLastBlockOfType(msgId: string, type: string) {
  const msg = getLastAssistantMessage(S().messages, msgId);
  if (!msg) return null;
  for (let i = msg.blocks.length - 1; i >= 0; i--) {
    if (msg.blocks[i].type === type) return msg.blocks[i];
  }
  return null;
}

// ---------------------------------------------------------------------------
// 鏍稿績鍒嗗彂鍣?
// ---------------------------------------------------------------------------

/**
 * 澶勭悊鍗曚釜 SSE 浜嬩欢銆傜敱 sendMessage / sendContinuation / subscribeToSession 缁熶竴璋冪敤銆?
 *
 * 璋冪敤鏂瑰湪 consumeSSE 鍥炶皟涓簲锛?
 * 1. 鍦ㄩ潪 thinking 浜嬩欢鍓嶈皟鐢?finalizeThinking(ctx)
 * 2. 鍦ㄩ潪澧為噺浜嬩欢鍓嶈皟鐢?ctx.batcher.flush()
 * 3. 璋冪敤 dispatchSSEEvent(event, ctx)
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

    // 鈹€鈹€ 浼氳瘽 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
    case "session_init": {
      if (!ctx.isFirstSend) break; // continuation / subscribe 璺宠繃
      const sid = data.session_id as string;
      const ss = useSessionStore.getState();
      if (!ss.activeSessionId) {
        ss.setActiveSession(sid);
      }
      const chatState = S();
      if (chatState.currentSessionId !== sid) {
        if (chatState.currentSessionId && chatState.messages.length > 0) {
          chatState.saveCurrentSession();
        }
        useChatStore.setState({ currentSessionId: sid });
      }
      if (ctx.userText) {
        ss.updateSessionTitle(ss.activeSessionId || sid, ctx.userText.slice(0, 20));
      }
      const ui = useUIStore.getState();
      if (typeof data.full_access_enabled === "boolean") {
        ui.setFullAccessEnabled(data.full_access_enabled);
      }
      if (typeof data.chat_mode === "string") {
        ui.setChatMode(data.chat_mode as "write" | "read" | "plan");
      }
      break;
    }

    // 鈹€鈹€ 鑷姩鐢熸垚鐨勪細璇濇爣棰?鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
    case "session_title": {
      const titleSid = (data.session_id as string) || "";
      const titleText = (data.title as string) || "";
      if (titleSid && titleText) {
        useSessionStore.getState().updateSessionTitle(titleSid, titleText);
      }
      break;
    }

    // 鈹€鈹€ 璁㈤槄鎭㈠锛堜粎 subscribe 娴佺▼锛?鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
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
          message: "姝ｅ湪鎭㈠浜嬩欢娴?..",
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

    // 鈹€鈹€ 娴佹按绾胯繘搴?鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
    case "pipeline_progress": {
      const stage = (data.stage as string) || "";
      const pipelineMsg = (data.message as string) || "";
      const progressToolCallId = (data.tool_call_id as string) || "";
      S().setPipelineStatus({
        stage,
        message: pipelineMsg,
        startedAt: Date.now(),
        phaseIndex: typeof data.phase_index === "number" ? data.phase_index : undefined,
        totalPhases: typeof data.total_phases === "number" ? data.total_phases : undefined,
        specPath: (data.spec_path as string) || undefined,
        diff: (data.diff as PipelineStatus["diff"]) ?? undefined,
        checkpoint: (data.checkpoint as Record<string, unknown>) ?? undefined,
        batchIndex: typeof data.batch_index === "number" ? data.batch_index : undefined,
        batchTotal: typeof data.batch_total === "number" ? data.batch_total : undefined,
      });
      if (progressToolCallId) {
        S().setToolProgress(progressToolCallId, {
          stage,
          message: pipelineMsg,
          phaseIndex: typeof data.phase_index === "number" ? data.phase_index : undefined,
          totalPhases: typeof data.total_phases === "number" ? data.total_phases : undefined,
        });
      }
      // 绱Н VLM 鎻愬彇闃舵鐢ㄤ簬鏃堕棿绾垮崱鐗囷紙鍚崟杞彁鍙栵級
      if (ctx.isFirstSend) {
        const phaseIndex = typeof data.phase_index === "number" ? data.phase_index : undefined;
        const totalPhases = typeof data.total_phases === "number" ? data.total_phases : undefined;
        if ((stage.startsWith("vlm_extract_") || stage.startsWith("single_pass")) && phaseIndex != null && totalPhases != null) {
          S().pushVlmPhase({
            stage,
            message: pipelineMsg,
            startedAt: Date.now(),
            diff: (data.diff as PipelineStatus["diff"]) ?? undefined,
            specPath: (data.spec_path as string) || undefined,
            phaseIndex,
            totalPhases,
          });
        }
      }
      break;
    }

    // 鈹€鈹€ 鎵归噺浠诲姟杩涘害 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
    case "batch_progress": {
      S().setBatchProgress({
        batchIndex: typeof data.batch_index === "number" ? data.batch_index : 0,
        batchTotal: typeof data.batch_total === "number" ? data.batch_total : 1,
        batchItemName: (data.batch_item_name as string) || `浠诲姟 ${((data.batch_index as number) || 0) + 1}`,
        batchStatus: ((data.batch_status as string) || "running") as "running" | "failed" | "completed",
        batchElapsed: typeof data.batch_elapsed_seconds === "number" ? data.batch_elapsed_seconds : 0,
        message: (data.message as string) || "",
      });
      break;
    }

    // 鈹€鈹€ 璺敱 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
    case "route_start":
      break;

    case "route_end": {
      const mode = (data.route_mode as string) || "";
      const skills = (data.skills_used as string[]) || [];
      if (mode) {
        S().appendBlock(msgId, {
          type: "status",
          label: _friendlyRouteMode(mode),
          detail: skills.length > 0 ? skills.join(",") : undefined,
          variant: "route",
        });
      }
      break;
    }

    // 鈹€鈹€ 杩唬 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
    case "iteration_start": {
      const iter = (data.iteration as number) || 0;
      if (iter > 1) {
        S().appendBlock(msgId, { type: "iteration", iteration: iter });
      }
      break;
    }

    // 鈹€鈹€ 鎬濊€?鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
    case "thinking_delta": {
      S().setPipelineStatus(null);
      const lastThinking = _getLastBlockOfType(msgId, "thinking");
      if (lastThinking && lastThinking.type === "thinking" && lastThinking.duration == null) {
        ctx.batcher.pushThinking((data.content as string) || "");
      } else {
        ctx.batcher.flush();
        S().appendBlock(msgId, {
          type: "thinking",
          content: (data.content as string) || "",
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

    // 鈹€鈹€ 鏂囨湰 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
    case "text_delta": {
      S().setPipelineStatus(null);
      const msg = getLastAssistantMessage(S().messages, msgId);
      const lastBlock = msg?.blocks[msg.blocks.length - 1];
      if (!lastBlock || lastBlock.type !== "text") {
        S().appendBlock(msgId, { type: "text", content: "" });
      }
      ctx.batcher.pushText((data.content as string) || "");
      break;
    }

    // 鈹€鈹€ 娴佸紡宸ュ叿鍙傛暟 delta 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
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

    // 鈹€鈹€ 宸ュ叿璋冪敤 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
    case "tool_call_start": {
      S().setPipelineStatus(null);
      const toolCallIdRaw = data.tool_call_id;
      const toolCallId = typeof toolCallIdRaw === "string" && toolCallIdRaw.length > 0
        ? toolCallIdRaw
        : undefined;
      const msgForStart = getLastAssistantMessage(S().messages, msgId);
      const streamingExists = toolCallId && msgForStart?.blocks.some(
        (b) => b.type === "tool_call" && b.toolCallId === toolCallId && (b.status as string) === "streaming",
      );
      if (streamingExists) {
        S().updateToolCallBlock(msgId, toolCallId!, (b) => {
          if (b.type === "tool_call") {
            return {
              ...b,
              args: (data.arguments as Record<string, unknown>) || b.args,
              status: "running",
              iteration: (data.iteration as number) || undefined,
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
          iteration: (data.iteration as number) || undefined,
        });
      }
      break;
    }

    case "tool_call_end": {
      const toolCallIdRaw = data.tool_call_id;
      const toolCallId = typeof toolCallIdRaw === "string" ? toolCallIdRaw : null;
      if (toolCallId) {
        useExcelStore.getState().clearStreamingArgs(toolCallId);
        S().clearToolProgress(toolCallId);
      }
      // ask_user batch 缁撴潫鍚庢竻鐞嗘畫鐣欑殑 pendingQuestion
      if ((data.tool_name as string) === "ask_user" && S().pendingQuestion) {
        S().setPendingQuestion(null);
      }
      S().updateToolCallBlock(msgId, toolCallId, (b) => {
        if (b.type === "tool_call") {
          if (b.status === "pending") {
            return { ...b, result: (data.result as string) || undefined } as AssistantBlock;
          }
          if (b.status === "running" || (b.status as string) === "streaming") {
            return {
              ...b,
              status: data.success ? "success" : "error",
              result: (data.result as string) || undefined,
              error: (data.error as string) || undefined,
            } as AssistantBlock;
          }
        }
        return b;
      });
      // 浠?run_code 绛夊伐鍏风粨鏋滀腑鎻愬彇鍚堝苟鎽樿 鈫?store
      if (data.success) {
        const toolName = (data.tool_name as string) || "";
        const resultStr = (data.result as string) || "";
        if (["run_code", "discover_file_relationships", "compare_excel"].includes(toolName) && resultStr.trimStart().startsWith("{")) {
          try {
            const parsed = JSON.parse(resultStr.trim());
            if (parsed && typeof parsed === "object" && (typeof parsed.rows_matched === "number" || typeof parsed.matched_count === "number" || typeof parsed.output_file === "string")) {
              const src = parsed.merge_result ?? parsed;
              useExcelStore.getState().setMergeResult({
                sourceFiles: Array.isArray(src.source_files) ? src.source_files : [],
                outputFile: src.output_file ?? src.output ?? "",
                rowsMatched: src.rows_matched ?? src.matched_count ?? 0,
                rowsAdded: src.rows_added ?? src.added_count ?? 0,
                rowsUnmatched: src.rows_unmatched ?? src.unmatched_count ?? 0,
                keyColumns: Array.isArray(src.key_columns) ? src.key_columns : [],
                joinType: src.join_type ?? src.how ?? "",
                toolCallId: toolCallId ?? "",
              });
            }
          } catch { /* not JSON or no merge fields */ }
        }
      }
      break;
    }

    // 鈹€鈹€ 瀛愪唬鐞?鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
    case "subagent_start": {
      S().appendBlock(msgId, {
        type: "subagent",
        name: (data.name as string) || "",
        reason: (data.reason as string) || "",
        iterations: 0,
        toolCalls: 0,
        status: "running",
        conversationId: (data.conversation_id as string) || "",
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
        if (args.file_path) parts.push(String(args.file_path).split("/").pop() || "");
        if (args.code_preview) parts.push(String(args.code_preview));
        return {
          ...b,
          tools: [...(b.tools || []), {
            index: (data.tool_index as number) || 0,
            name: (data.tool_name as string) || "",
            argsSummary: parts.join(" \u00b7 "),
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
            success: (data.success as boolean) ?? true,
            iterations: (data.iterations as number) || b.iterations,
            toolCalls: (data.tool_calls as number) || b.toolCalls,
          };
        }
        return b;
      });
      break;
    }

    // 鈹€鈹€ 浜や簰 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
    case "user_question": {
      S().setPendingQuestion({
        id: (data.id as string) || "",
        header: (data.header as string) || "",
        text: (data.text as string) || "",
        options: (data.options as { label: string; description: string }[]) || [],
        multiSelect: Boolean(data.multi_select),
        queueSize: typeof data.queue_size === "number" ? data.queue_size : undefined,
      });
      break;
    }

    case "pending_approval": {
      const approvalToolCallId = (data.tool_call_id as string) || null;
      S().updateToolCallBlock(msgId, approvalToolCallId, (b) => {
        if (b.type === "tool_call") {
          return { ...b, status: "pending" as const } as AssistantBlock;
        }
        return b;
      });
      S().setPendingApproval({
        id: (data.approval_id as string) || "",
        toolName: (data.approval_tool_name as string) || "",
        arguments: {},
        riskLevel: (data.risk_level as "high" | "medium" | "low") || "high",
        argsSummary: (data.args_summary as Record<string, string>) || {},
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
      S().setPendingApproval(null);
      const arToolCallId = (data.tool_call_id as string) || null;
      S().updateToolCallBlock(msgId, arToolCallId, (b) => {
        if (b.type === "tool_call" && b.status === "pending") {
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
      // 瀹炴椂鍒锋柊鎿嶄綔鍘嗗彶鏃堕棿绾?
      if (success && hasChanges) {
        const sid = useSessionStore.getState().activeSessionId;
        if (sid) {
          useExcelStore.getState().fetchOperationHistory(sid);
        }
      }
      break;
    }

    // 鈹€鈹€ 浠诲姟鍒楄〃 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
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

    // 鈹€鈹€ Excel 棰勮 / 宸紓 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
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
        useExcelStore.getState().addRecentFileIfNotDismissed({ path: epFilePath, filename: fn });
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
      useExcelStore.getState().addDiff(edEntry);
      if (edFilePath) {
        const fn = edFilePath.split("/").pop() || edFilePath;
        useExcelStore.getState().addRecentFileIfNotDismissed({ path: edFilePath, filename: fn });
        S().addAffectedFiles(msgId, [edFilePath]);
      }
      // 璺ㄦ枃浠跺姣旇嚜鍔ㄦ墦寮€瀵规瘮瑙嗗浘
      if (edDiffMode === "cross_file" && edEntry.filePathB && edEntry.diffSummary) {
        const es = useExcelStore.getState();
        if (!es.compareMode && !es.panelOpen) {
          es.openCompare(edFilePath, edEntry.filePathB);
        }
      }
      break;
    }

    case "text_diff": {
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

    case "verification_report": {
      S().appendBlock(msgId, {
        type: "verification_report",
        verdict: (data.verdict as "pass" | "fail" | "unknown") || "unknown",
        confidence: (data.confidence as "high" | "medium" | "low") || "low",
        checks: (data.checks as string[]) || [],
        issues: (data.issues as string[]) || [],
        mode: (data.mode as "advisory" | "blocking") || "advisory",
      });
      break;
    }

    case "files_changed": {
      const changedFiles = (data.files as string[]) || [];
      const excelStore = useExcelStore.getState();
      for (const filePath of changedFiles) {
        if (filePath) {
          const filename = filePath.split("/").pop() || filePath;
          excelStore.addRecentFileIfNotDismissed({ path: filePath, filename });
        }
      }
      if (changedFiles.length > 0) {
        S().addAffectedFiles(msgId, changedFiles);
        excelStore.bumpWorkspaceFilesVersion();
        if (ctx.effectiveSessionId) {
          excelStore.fetchBackups(ctx.effectiveSessionId);
        }
      }
      break;
    }

    case "staging_updated": {
      const stAction = (data.action as string) || "";
      const stFiles = (data.files as { original_path: string; backup_path: string }[]) || [];
      const stPending = (data.pending_count as number) ?? 0;
      useExcelStore.getState().handleStagingUpdated(stAction, stFiles, stPending);
      if (stAction === "finish_hint" && stPending > 0) {
        S().appendBlock(msgId, {
          type: "staging_hint",
          pendingCount: stPending,
          files: stFiles.map((f) => f.original_path),
        });
      }
      if (ctx.effectiveSessionId) {
        useExcelStore.getState().fetchBackups(ctx.effectiveSessionId);
      }
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
      const dlFilePath = (data.file_path as string) || "";
      const dlFilename = (data.filename as string) || dlFilePath.split("/").pop() || "download";
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

    // 鈹€鈹€ 璁″垝鍒涘缓 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
    case "plan_created": {
      const planTitle = (data.plan_title as string) || "";
      const planTaskCount = (data.plan_task_count as number) || 0;
      if (planTitle) {
        S().appendBlock(msgId, {
          type: "status",
          label: `宸插垱寤鸿鍒掋€?{planTitle}銆嶏紙${planTaskCount} 椤逛换鍔★級`,
          variant: "info",
        });
      }
      break;
    }

    // 鈹€鈹€ 瀵硅瘽鎽樿锛堝墠绔潤榛樻秷璐癸紝涓嶆覆鏌擄級 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
    case "chat_summary":
      break;

    // 鈹€鈹€ 妯″紡鍙樻洿 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
    case "mode_changed": {
      const uiMode = useUIStore.getState();
      const modeName = data.mode_name as string;
      const enabled = Boolean(data.enabled);
      if (modeName === "full_access") {
        uiMode.setFullAccessEnabled(enabled);
      } else if (modeName === "chat_mode") {
        uiMode.setChatMode(data.value as "write" | "read" | "plan");
      }
      const _modeLabelMap: Record<string, string> = { full_access: "Full Access", chat_mode: "Chat Mode" };
      const modeLabel = _modeLabelMap[modeName] || modeName;
      const modeAction = enabled ? "Enabled" : "Disabled";
      S().appendBlock(msgId, {
        type: "status",
        label: `${modeAction} ${modeLabel}`,
        variant: "info",
      });
      break;
    }

    // 鈹€鈹€ 鍥炲涓庡畬鎴?鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
    case "reply": {
      const content = (data.content as string) || "";
      const hasPendingInteraction =
        S().pendingApproval !== null || S().pendingQuestion !== null;
      if (content && !hasPendingInteraction) {
        const msg = getLastAssistantMessage(S().messages, msgId);
        const hasTextBlock = msg?.blocks.some((b) => b.type === "text" && b.content);
        if (!hasTextBlock) {
          S().appendBlock(msgId, { type: "text", content });
        }
      }
      const uiReply = useUIStore.getState();
      if (typeof data.full_access_enabled === "boolean") {
        uiReply.setFullAccessEnabled(data.full_access_enabled);
      }
      if (typeof data.chat_mode === "string") {
        uiReply.setChatMode(data.chat_mode as "write" | "read" | "plan");
      }
      // Token 缁熻鐢卞悇璋冪敤鏂瑰湪 dispatchSSEEvent 涔嬪悗鑷澶勭悊
      // 锛坰endMessage 鏈夊欢杩熼€昏緫锛宻endContinuation 鏈夌疮鍔犻€昏緫锛夈€?
      break;
    }

    case "done": {
      // 浠呮竻闄ゆ祦姘寸嚎杩涘害鎸囩ず鍣ㄣ€?
      // 涓嶈鍦ㄦ澶勮皟鐢?setStreaming(false) / setAbortController(null) / saveCurrentSession()锛?
      // 杩欎簺娓呯悊鐢?chat-actions.ts 鐨?finally 鍧楃粺涓€鎵ц銆?
      // 濡傛灉鍦?done 浜嬩欢涓彁鍓嶆竻闄わ紝浼氬鑷?SessionSync 鐨?useEffect 鍦?finally 涔嬪墠瑙﹀彂锛?
      // 寮曞彂 refreshSessionMessagesFromBackend 璇诲埌鍚庣灏氭湭鎸佷箙鍖栫殑鏃ф暟鎹紝閫犳垚娑堟伅闂儊銆?
      S().setPipelineStatus(null);

      // 鈹€鈹€ 鑷姩鎵撳紑 Excel 棰勮闈㈡澘 鈹€鈹€
      // 浠诲姟瀹屾垚鍚庯紝鑻ユ湰杞璇濇秹鍙?Excel 鏂囦欢鍙樻洿锛岃嚜鍔ㄦ墦寮€鏈€鍚庝竴涓枃浠剁殑棰勮
      {
        const EXCEL_RE = /\.(xlsx|xlsm|xls|csv)$/i;
        const doneMsg = getLastAssistantMessage(S().messages, msgId);
        const affected = doneMsg?.affectedFiles ?? [];
        const excelFiles = affected.filter((f) => EXCEL_RE.test(f));
        if (excelFiles.length > 0) {
          const lastFile = excelFiles[excelFiles.length - 1];
          const excelStore = useExcelStore.getState();
          // 浠呭湪闈㈡澘鏈墦寮€鏃惰嚜鍔ㄦ墦寮€锛岄伩鍏嶈鐩栫敤鎴锋鍦ㄦ煡鐪嬬殑鍐呭
          if (!excelStore.panelOpen) {
            excelStore.openPanel(lastFile);
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
        // 杩藉姞鎴栨洿鏂?retry block
        S().upsertBlockByType(msgId, "llm_retry", {
          type: "llm_retry",
          retryAttempt: retryAttempt,
          retryMaxAttempts: retryMax,
          retryDelaySeconds: retryDelay,
          retryErrorMessage: retryError,
          retryStatus: "retrying",
        });
      } else if (retryStatus === "succeeded") {
        // 閲嶈瘯鎴愬姛锛氭洿鏂?block 鐘舵€?
        S().upsertBlockByType(msgId, "llm_retry", {
          type: "llm_retry",
          retryAttempt: retryAttempt,
          retryMaxAttempts: retryMax,
          retryDelaySeconds: 0,
          retryErrorMessage: "",
          retryStatus: "succeeded",
        });
      } else if (retryStatus === "exhausted") {
        // 閲嶈瘯鑰楀敖
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
      // 鍚?category 鍘婚噸锛氱Щ闄ゅ悓娑堟伅鍐呯浉鍚?category 鐨勬棫 failure_guidance block
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
        // 鏇挎崲鐜版湁鍚?category 鐨?block
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
      // 鎶樺彔鍚岃疆 llm_retry(exhausted) block
      S().updateAssistantMessage(msgId, (m) => ({
        ...m,
        blocks: m.blocks.filter(
          (b) => !(b.type === "llm_retry" && b.retryStatus === "exhausted"),
        ),
      }));
      break;
    }

    // 鈹€鈹€ 宸ュ叿璋冪敤閫氱煡锛?tools 寮€鍚椂锛?鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
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

    // 鈹€鈹€ 鎺ㄧ悊杩囩▼閫氱煡锛?reasoning 寮€鍚椂锛?鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
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
// 渚挎嵎鍑芥暟锛歵hinking 鐘舵€佺鐞?
// ---------------------------------------------------------------------------

/** 鍦ㄩ潪 thinking 浜嬩欢鍓嶈皟鐢紝鍏抽棴杩涜涓殑 thinking block銆?*/
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

/** 鏍囧噯鐨勪簨浠跺墠澶勭悊锛氳皟鐢ㄦ柟鍦?consumeSSE 鍥炶皟椤堕儴浣跨敤銆?*/
export function preDispatch(event: SSEEvent, ctx: SSEHandlerContext): void {
  if (event.event !== "thinking_delta" && event.event !== "thinking") {
    finalizeThinking(ctx);
  }
  if (event.event !== "text_delta" && event.event !== "thinking_delta") {
    ctx.batcher.flush();
  }
}
