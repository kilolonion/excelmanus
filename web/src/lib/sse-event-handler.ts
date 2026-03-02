/**
 * 共享 SSE 事件分发器 — 消除 sendMessage / sendContinuation / subscribeToSession 的三重复制。
 *
 * 所有 SSE 事件处理逻辑集中在此文件的 `dispatchSSEEvent()` 函数中。
 * 调用方只需构建 `SSEHandlerContext` 并在 consumeSSE 回调中委托给该函数。
 */

import { useChatStore, type PipelineStatus } from "@/stores/chat-store";
import { useSessionStore } from "@/stores/session-store";
import { useUIStore } from "@/stores/ui-store";
import { useExcelStore, type ExcelCellDiff, type ExcelDiffEntry, type ExcelPreviewData, type MergeRange } from "@/stores/excel-store";
import type { AssistantBlock, TaskItem } from "@/lib/types";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/** SSE 事件的规范化表示（由 consumeSSE 解析后传入）。 */
export interface SSEEvent {
  event: string;
  data: Record<string, unknown>;
}

/** 事件分发器运行所需的上下文，由调用方构建并传入。 */
export interface SSEHandlerContext {
  /** 当前 assistant 消息 ID（事件追加到此消息的 blocks 中）。 */
  assistantMsgId: string;
  /** RAF 增量批处理器实例。 */
  batcher: DeltaBatcher;
  /** 当前会话 ID（用于备份刷新等）。 */
  effectiveSessionId: string;
  /** 是否处于 sendMessage 的首次发送流程（vs continuation / subscribe）。 */
  isFirstSend: boolean;
  /** 用户原始消息文本（仅 sendMessage 流程需要，用于 session_init 标题推断）。 */
  userText?: string;

  // ── 可变状态引用（由调用方持有，分发器读写）──
  /** thinking block 是否进行中。 */
  thinkingInProgress: boolean;
  /** 流是否遇到过错误。 */
  hadStreamError: boolean;
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

/** 将后端 route_mode 映射为用户友好的中文标签 */
export function _friendlyRouteMode(mode: string): string {
  const map: Record<string, string> = {
    all_tools: "智能路由",
    control_command: "控制命令",
    slash_direct: "技能指令",
    slash_not_found: "技能未找到",
    slash_not_user_invocable: "技能不可用",
    no_skillpack: "基础模式",
    fallback: "回退模式",
    hidden: "路由",
  };
  return map[mode] || mode;
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
        || `任务 ${i + 1}`,
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

/** 获取指定 assistant 消息。 */
export function getLastAssistantMessage(
  messages: ReturnType<typeof useChatStore.getState>["messages"],
  id: string,
) {
  const msg = messages.find((m) => m.id === id);
  if (msg && msg.role === "assistant") return msg;
  return null;
}

// ---------------------------------------------------------------------------
// 内部快捷引用
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

  switch (event.event) {
    // ── 会话 ────────────────────────────────
    case "session_init": {
      if (!ctx.isFirstSend) break; // continuation / subscribe 跳过
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

    // ── 自动生成的会话标题 ─────────────────────
    case "session_title": {
      const titleSid = (data.session_id as string) || "";
      const titleText = (data.title as string) || "";
      if (titleSid && titleText) {
        useSessionStore.getState().updateSessionTitle(titleSid, titleText);
      }
      break;
    }

    // ── 订阅恢复（仅 subscribe 流程） ─────────────
    case "subscribe_resume": {
      const status = (data.status as string) || "";
      if (status === "reconnected") {
        S().setPipelineStatus({
          stage: "resuming",
          message: "正在恢复事件流...",
          startedAt: Date.now(),
        });
      }
      break;
    }

    // ── 流水线进度 ─────────────────────
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
      // 累积 VLM 提取阶段用于时间线卡片（含单轮提取）
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

    // ── 批量任务进度 ─────────────────────
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

    // ── 路由 ──────────────────────────────────
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

    // ── 迭代 ──────────────────────────────
    case "iteration_start": {
      const iter = (data.iteration as number) || 0;
      if (iter > 1) {
        S().appendBlock(msgId, { type: "iteration", iteration: iter });
      }
      break;
    }

    // ── 思考 ───────────────────────────────
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

    // ── 文本 ───────────────────────────────────
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

    // ── 流式工具参数 delta ────────────────────
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

    // ── 工具调用 ─────────────────────────────
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
      break;
    }

    // ── 子代理 ───────────────────────────────
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

    // ── 交互 ────────────────────────────
    case "user_question": {
      S().setPendingQuestion({
        id: (data.id as string) || "",
        header: (data.header as string) || "",
        text: (data.text as string) || "",
        options: (data.options as { label: string; description: string }[]) || [],
        multiSelect: Boolean(data.multi_select),
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
      // 实时刷新操作历史时间线
      if (success && hasChanges) {
        const sid = useSessionStore.getState().activeSessionId;
        if (sid) {
          useExcelStore.getState().fetchOperationHistory(sid);
        }
      }
      break;
    }

    // ── 任务列表 ──────────────────────────────
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

    // ── Excel 预览 / 差异 ───────────────────
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

    // ── 计划创建 ────────────────────────────
    case "plan_created": {
      const planTitle = (data.plan_title as string) || "";
      const planTaskCount = (data.plan_task_count as number) || 0;
      if (planTitle) {
        S().appendBlock(msgId, {
          type: "status",
          label: `已创建计划「${planTitle}」（${planTaskCount} 项任务）`,
          variant: "info",
        });
      }
      break;
    }

    // ── 对话摘要（前端静默消费，不渲染） ────────
    case "chat_summary":
      break;

    // ── 模式变更 ────────────────────────────
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
      const modeAction = enabled ? "已开启" : "已关闭";
      S().appendBlock(msgId, {
        type: "status",
        label: `${modeAction} ${modeLabel}`,
        variant: "info",
      });
      break;
    }

    // ── 回复与完成 ───────────────────────────
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
      // Token 统计由各调用方在 dispatchSSEEvent 之后自行处理
      // （sendMessage 有延迟逻辑，sendContinuation 有累加逻辑）。
      break;
    }

    case "done": {
      // 仅清除流水线进度指示器。
      // 不要在此处调用 setStreaming(false) / setAbortController(null) / saveCurrentSession()！
      // 这些清理由 chat-actions.ts 的 finally 块统一执行。
      // 如果在 done 事件中提前清除，会导致 SessionSync 的 useEffect 在 finally 之前触发，
      // 引发 refreshSessionMessagesFromBackend 读到后端尚未持久化的旧数据，造成消息闪烁。
      S().setPipelineStatus(null);

      // ── 自动打开 Excel 预览面板 ──
      // 任务完成后，若本轮对话涉及 Excel 文件变更，自动打开最后一个文件的预览
      {
        const EXCEL_RE = /\.(xlsx|xlsm|xls|csv)$/i;
        const doneMsg = getLastAssistantMessage(S().messages, msgId);
        const affected = doneMsg?.affectedFiles ?? [];
        const excelFiles = affected.filter((f) => EXCEL_RE.test(f));
        if (excelFiles.length > 0) {
          const lastFile = excelFiles[excelFiles.length - 1];
          const excelStore = useExcelStore.getState();
          // 仅在面板未打开时自动打开，避免覆盖用户正在查看的内容
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
        // 追加或更新 retry block
        S().upsertBlockByType(msgId, "llm_retry", {
          type: "llm_retry",
          retryAttempt: retryAttempt,
          retryMaxAttempts: retryMax,
          retryDelaySeconds: retryDelay,
          retryErrorMessage: retryError,
          retryStatus: "retrying",
        });
      } else if (retryStatus === "succeeded") {
        // 重试成功：更新 block 状态
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
      // 同 category 去重：移除同消息内相同 category 的旧 failure_guidance block
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
        // 替换现有同 category 的 block
        S().setMessages(
          S().messages.map((m) => {
            if (m.id !== msgId || m.role !== "assistant") return m;
            return {
              ...m,
              blocks: (m as { blocks: import("@/lib/types").AssistantBlock[] }).blocks.map((b) => {
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
            };
          }),
        );
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
      // 折叠同轮 llm_retry(exhausted) block
      S().setMessages(
        S().messages.map((m) => {
          if (m.id !== msgId || m.role !== "assistant") return m;
          return {
            ...m,
            blocks: (m as { blocks: import("@/lib/types").AssistantBlock[] }).blocks.filter(
              (b) => !(b.type === "llm_retry" && b.retryStatus === "exhausted"),
            ),
          };
        }),
      );
      break;
    }

    default:
      break;
  }
}

// ---------------------------------------------------------------------------
// 便捷函数：thinking 状态管理
// ---------------------------------------------------------------------------

/** 在非 thinking 事件前调用，关闭进行中的 thinking block。 */
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

/** 标准的事件前处理：调用方在 consumeSSE 回调顶部使用。 */
export function preDispatch(event: SSEEvent, ctx: SSEHandlerContext): void {
  if (event.event !== "thinking_delta" && event.event !== "thinking") {
    finalizeThinking(ctx);
  }
  if (event.event !== "text_delta" && event.event !== "thinking_delta") {
    ctx.batcher.flush();
  }
}
