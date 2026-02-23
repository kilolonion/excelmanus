import { consumeSSE } from "./sse";
import { buildApiUrl, uploadFile } from "./api";
import { useChatStore } from "@/stores/chat-store";
import { useSessionStore } from "@/stores/session-store";
import { useUIStore } from "@/stores/ui-store";
import { useExcelStore } from "@/stores/excel-store";
import type { AssistantBlock, TaskItem } from "@/lib/types";

// Token stats deferred from a call that ended with a pending interaction
// (askuser / approval). sendContinuation accumulates these into its final stats.
let _deferredTokenStats: {
  promptTokens: number;
  completionTokens: number;
  totalTokens: number;
  iterations: number;
} | null = null;

export async function sendMessage(
  text: string,
  files?: File[],
  sessionId?: string | null
) {
  const store = useChatStore.getState();
  const sessionStore = useSessionStore.getState();

  if (store.isStreaming) return;

  // Upload files first
  const uploadedPaths: string[] = [];
  if (files && files.length > 0) {
    for (const file of files) {
      try {
        const result = await uploadFile(file);
        uploadedPaths.push(result.path);
      } catch (err) {
        console.error("Upload failed:", err);
      }
    }
  }

  let messageContent = text;
  if (uploadedPaths.length > 0) {
    const fileList = uploadedPaths.map((p) => `[已上传: ${p}]`).join("\n");
    messageContent = `${fileList}\n\n${text}`;
  }

  const userMsgId = crypto.randomUUID();
  store.addUserMessage(
    userMsgId,
    text,
    files?.map((f) => ({ filename: f.name, path: "", size: f.size }))
  );

  const assistantMsgId = crypto.randomUUID();
  store.addAssistantMessage(assistantMsgId);

  const abortController = new AbortController();
  store.setAbortController(abortController);
  store.setStreaming(true);

  const effectiveSessionId = sessionId || sessionStore.activeSessionId;

  // Helper: get fresh store state
  const S = () => useChatStore.getState();

  // Helper: get last block of specific type from current assistant message
  const getLastBlockOfType = (type: string) => {
    const msg = getLastAssistantMessage(S().messages, assistantMsgId);
    if (!msg) return null;
    for (let i = msg.blocks.length - 1; i >= 0; i--) {
      if (msg.blocks[i].type === type) return msg.blocks[i];
    }
    return null;
  };

  const normalizeTaskItems = (taskListPayload: unknown): TaskItem[] => {
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
      };
    });
  };

  const applyTaskStatusPatch = (
    items: TaskItem[],
    taskIndex: number | null,
    taskStatus: string,
  ): TaskItem[] => {
    if (taskIndex === null || !taskStatus) {
      return items;
    }
    return items.map((item) =>
      item.index === taskIndex ? { ...item, status: taskStatus } : item
    );
  };

  let thinkingInProgress = false;

  const finalizeThinking = () => {
    if (!thinkingInProgress) return;
    thinkingInProgress = false;
    S().updateBlockByType(assistantMsgId, "thinking", (b) => {
      if (b.type === "thinking" && b.startedAt != null && b.duration == null) {
        return { ...b, duration: (Date.now() - b.startedAt) / 1000 };
      }
      return b;
    });
  };

  try {
    await consumeSSE(
      buildApiUrl("/chat/stream", { direct: true }),
      {
        message: messageContent,
        session_id: effectiveSessionId,
      },
      (event) => {
        const data = event.data;

        if (event.event !== "thinking_delta" && event.event !== "thinking") {
          finalizeThinking();
        }

        switch (event.event) {
          // ── Session ────────────────────────────────
          case "session_init": {
            const sid = data.session_id as string;
            const ss = useSessionStore.getState();
            if (!ss.activeSessionId) {
              ss.setActiveSession(sid);
            }
            // Only sync the session id without clearing/reloading messages.
            // switchSession() would clear the messages array and trigger an
            // async backend load whose restored IDs won't match userMsgId,
            // causing duplicate user bubbles.
            const chatState = S();
            if (chatState.currentSessionId !== sid) {
              if (chatState.currentSessionId && chatState.messages.length > 0) {
                chatState.saveCurrentSession();
              }
              useChatStore.setState({ currentSessionId: sid });
            }
            if (text) {
              ss.updateSessionTitle(ss.activeSessionId || sid, text.slice(0, 60));
            }
            // Sync mode state
            const ui = useUIStore.getState();
            if (typeof data.full_access_enabled === "boolean") {
              ui.setFullAccessEnabled(data.full_access_enabled);
            }
            if (typeof data.plan_mode_enabled === "boolean") {
              ui.setPlanModeEnabled(data.plan_mode_enabled);
            }
            break;
          }

          // ── Pipeline Progress ─────────────────────
          case "pipeline_progress": {
            S().setPipelineStatus({
              stage: (data.stage as string) || "",
              message: (data.message as string) || "",
              startedAt: Date.now(),
            });
            break;
          }

          // ── Route ──────────────────────────────────
          case "route_start": {
            // Routing started — optional status indicator
            break;
          }
          case "route_end": {
            const mode = (data.route_mode as string) || "";
            const skills = (data.skills_used as string[]) || [];
            if (mode) {
              S().appendBlock(assistantMsgId, {
                type: "status",
                label: `路由: ${mode}`,
                detail: skills.length > 0 ? `技能: ${skills.join(", ")}` : undefined,
                variant: "route",
              });
            }
            break;
          }

          // ── Iteration ──────────────────────────────
          case "iteration_start": {
            const iter = (data.iteration as number) || 0;
            if (iter > 1) {
              S().appendBlock(assistantMsgId, {
                type: "iteration",
                iteration: iter,
              });
            }
            break;
          }

          // ── Thinking ───────────────────────────────
          case "thinking_delta": {
            S().setPipelineStatus(null);
            const lastThinking = getLastBlockOfType("thinking");
            if (lastThinking && lastThinking.type === "thinking" && lastThinking.duration == null) {
              S().updateBlockByType(assistantMsgId, "thinking", (b) => {
                if (b.type === "thinking") {
                  return { ...b, content: b.content + (data.content as string) };
                }
                return b;
              });
            } else {
              S().appendBlock(assistantMsgId, {
                type: "thinking",
                content: (data.content as string) || "",
                startedAt: Date.now(),
              });
            }
            thinkingInProgress = true;
            break;
          }

          case "thinking": {
            S().appendBlock(assistantMsgId, {
              type: "thinking",
              content: (data.content as string) || "",
              duration: (data.duration as number) || undefined,
              startedAt: Date.now(),
            });
            break;
          }

          // ── Text ───────────────────────────────────
          case "text_delta": {
            S().setPipelineStatus(null);
            const msg = getLastAssistantMessage(S().messages, assistantMsgId);
            const lastBlock = msg?.blocks[msg.blocks.length - 1];
            if (lastBlock && lastBlock.type === "text") {
              S().updateLastBlock(assistantMsgId, (b) => {
                if (b.type === "text") {
                  return { ...b, content: b.content + (data.content as string) };
                }
                return b;
              });
            } else {
              S().appendBlock(assistantMsgId, {
                type: "text",
                content: (data.content as string) || "",
              });
            }
            break;
          }

          // ── Tool Calls ─────────────────────────────
          case "tool_call_start": {
            S().setPipelineStatus(null);
            const toolCallIdRaw = data.tool_call_id;
            const toolCallId = typeof toolCallIdRaw === "string" && toolCallIdRaw.length > 0
              ? toolCallIdRaw
              : undefined;
            S().appendBlock(assistantMsgId, {
              type: "tool_call",
              toolCallId,
              name: (data.tool_name as string) || "",
              args: (data.arguments as Record<string, unknown>) || {},
              status: "running",
              iteration: (data.iteration as number) || undefined,
            });
            break;
          }

          case "tool_call_end": {
            const toolCallIdRaw = data.tool_call_id;
            const toolCallId = typeof toolCallIdRaw === "string" ? toolCallIdRaw : null;
            S().updateToolCallBlock(assistantMsgId, toolCallId, (b) => {
              if (b.type === "tool_call") {
                // If already pending (from pending_approval event), keep pending status but update result
                if (b.status === "pending") {
                  return {
                    ...b,
                    result: (data.result as string) || undefined,
                  } as AssistantBlock;
                }
                if (b.status === "running") {
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

          // ── Subagent ───────────────────────────────
          case "subagent_start": {
            S().appendBlock(assistantMsgId, {
              type: "subagent",
              name: (data.name as string) || "",
              reason: (data.reason as string) || "",
              iterations: 0,
              toolCalls: 0,
              status: "running",
            });
            break;
          }

          case "subagent_iteration": {
            S().updateBlockByType(assistantMsgId, "subagent", (b) => {
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

          case "subagent_summary": {
            S().updateBlockByType(assistantMsgId, "subagent", (b) => {
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
            S().updateBlockByType(assistantMsgId, "subagent", (b) => {
              if (b.type === "subagent") {
                return {
                  ...b,
                  status: "done",
                  iterations: (data.iterations as number) || b.iterations,
                  toolCalls: (data.tool_calls as number) || b.toolCalls,
                };
              }
              return b;
            });
            break;
          }

          // ── Interactive ────────────────────────────
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
            // Mark the associated tool_call block as "pending"
            const approvalToolCallId = (data.tool_call_id as string) || null;
            S().updateToolCallBlock(assistantMsgId, approvalToolCallId, (b) => {
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
            const arResult = (data.result as string) || undefined;

            S().setPendingApproval(null);
            // Transition the pending tool_call block to success/error and attach result
            const arToolCallId = (data.tool_call_id as string) || null;
            S().updateToolCallBlock(assistantMsgId, arToolCallId, (b) => {
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
            S().appendBlock(assistantMsgId, {
              type: "approval_action",
              approvalId,
              toolName,
              success,
              undoable,
            });
            break;
          }

          // ── Task List ──────────────────────────────
          case "task_update": {
            const payloadItems = normalizeTaskItems(data.task_list);
            const taskIndex = typeof data.task_index === "number" ? data.task_index : null;
            const taskStatus = typeof data.task_status === "string" ? data.task_status : "";
            const existingTaskList = getLastBlockOfType("task_list");

            if (existingTaskList && existingTaskList.type === "task_list") {
              S().updateBlockByType(assistantMsgId, "task_list", (b) => {
                if (b.type !== "task_list") return b;
                const baseItems = payloadItems.length > 0 ? payloadItems : b.items;
                return {
                  ...b,
                  items: applyTaskStatusPatch(baseItems, taskIndex, taskStatus),
                };
              });
            } else if (payloadItems.length > 0) {
              S().appendBlock(assistantMsgId, {
                type: "task_list",
                items: applyTaskStatusPatch(payloadItems, taskIndex, taskStatus),
              });
            }
            break;
          }

          // ── Excel Preview / Diff ───────────────────
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
            });
            if (epFilePath) {
              const epFilename = epFilePath.split("/").pop() || epFilePath;
              useExcelStore.getState().addRecentFile({ path: epFilePath, filename: epFilename });
            }
            break;
          }

          case "excel_diff": {
            const edFilePath = (data.file_path as string) || "";
            useExcelStore.getState().addDiff({
              toolCallId: (data.tool_call_id as string) || "",
              filePath: edFilePath,
              sheet: (data.sheet as string) || "",
              affectedRange: (data.affected_range as string) || "",
              changes: (data.changes as { cell: string; old: string | number | null; new: string | number | null }[]) || [],
              timestamp: Date.now(),
            });
            if (edFilePath) {
              const edFilename = edFilePath.split("/").pop() || edFilePath;
              useExcelStore.getState().addRecentFile({ path: edFilePath, filename: edFilename });
              S().addAffectedFiles(assistantMsgId, [edFilePath]);
            }
            break;
          }

          case "files_changed": {
            const changedFiles = (data.files as string[]) || [];
            const excelStore = useExcelStore.getState();
            for (const filePath of changedFiles) {
              if (filePath) {
                const filename = filePath.split("/").pop() || filePath;
                excelStore.addRecentFile({ path: filePath, filename });
              }
            }
            if (changedFiles.length > 0) {
              S().addAffectedFiles(assistantMsgId, changedFiles);
            }
            break;
          }

          case "memory_extracted": {
            const entries = (data.entries as { id: string; content: string; category: string }[]) || [];
            const trigger = (data.trigger as string) || "session_end";
            const count = (data.count as number) || entries.length;
            if (count > 0) {
              S().appendBlock(assistantMsgId, {
                type: "memory_extracted",
                entries,
                trigger,
                count,
              });
            }
            break;
          }

          // ── Reply & Done ───────────────────────────
          case "reply": {
            const content = (data.content as string) || "";
            // Suppress reply text when there's a pending approval or question
            // — the text is already shown inside the tool call card / panel.
            const hasPendingInteraction =
              S().pendingApproval !== null || S().pendingQuestion !== null;
            if (content && !hasPendingInteraction) {
              const msg = getLastAssistantMessage(S().messages, assistantMsgId);
              const lastBlock = msg?.blocks[msg.blocks.length - 1];
              if (!lastBlock || lastBlock.type !== "text") {
                S().appendBlock(assistantMsgId, { type: "text", content });
              }
            }
            // Sync mode state from reply
            const uiReply = useUIStore.getState();
            if (typeof data.full_access_enabled === "boolean") {
              uiReply.setFullAccessEnabled(data.full_access_enabled);
            }
            if (typeof data.plan_mode_enabled === "boolean") {
              uiReply.setPlanModeEnabled(data.plan_mode_enabled);
            }
            // Token stats handling
            const totalTokens = (data.total_tokens as number) || 0;
            if (totalTokens > 0) {
              if (hasPendingInteraction) {
                // Defer token stats — a continuation will display accumulated totals.
                _deferredTokenStats = {
                  promptTokens: (data.prompt_tokens as number) || 0,
                  completionTokens: (data.completion_tokens as number) || 0,
                  totalTokens,
                  iterations: (data.iterations as number) || 0,
                };
              } else {
                S().appendBlock(assistantMsgId, {
                  type: "token_stats",
                  promptTokens: (data.prompt_tokens as number) || 0,
                  completionTokens: (data.completion_tokens as number) || 0,
                  totalTokens,
                  iterations: (data.iterations as number) || 0,
                });
              }
            }
            break;
          }

          case "mode_changed": {
            const uiMode = useUIStore.getState();
            const modeName = data.mode_name as string;
            const enabled = Boolean(data.enabled);
            if (modeName === "full_access") {
              uiMode.setFullAccessEnabled(enabled);
            } else if (modeName === "plan_mode") {
              uiMode.setPlanModeEnabled(enabled);
            }
            // Show mode change as a status block in chat
            const modeLabel = modeName === "full_access" ? "Full Access" : "Plan Mode";
            const modeAction = enabled ? "已开启" : "已关闭";
            S().appendBlock(assistantMsgId, {
              type: "status",
              label: `${modeAction} ${modeLabel}`,
              variant: "info",
            });
            break;
          }

          case "done": {
            // Save session messages
            S().setPipelineStatus(null);
            S().saveCurrentSession();
            S().setStreaming(false);
            S().setAbortController(null);
            break;
          }

          case "error": {
            S().setPipelineStatus(null);
            S().appendBlock(assistantMsgId, {
              type: "text",
              content: `⚠️ ${(data.error as string) || "发生未知错误"}`,
            });
            S().saveCurrentSession();
            S().setStreaming(false);
            S().setAbortController(null);
            break;
          }

          default:
            // Ignore unknown events silently
            break;
        }
      },
      abortController.signal
    );
  } catch (err) {
    if ((err as Error).name !== "AbortError") {
      S().appendBlock(assistantMsgId, {
        type: "text",
        content: `⚠️ 连接错误: ${(err as Error).message}`,
      });
    }
  } finally {
    S().setPipelineStatus(null);
    S().saveCurrentSession();
    S().setStreaming(false);
    S().setAbortController(null);
  }
}

/**
 * 发送延续消息（审批/问答回复），复用最后一条 assistant 消息。
 * 不创建 user/assistant 气泡，绿线不会断开。
 */
export async function sendContinuation(
  text: string,
  sessionId?: string | null,
) {
  const store = useChatStore.getState();
  if (store.isStreaming) return;

  // 找到最后一条 assistant 消息复用其 ID
  const messages = store.messages;
  let assistantMsgId: string | null = null;
  for (let i = messages.length - 1; i >= 0; i--) {
    if (messages[i].role === "assistant") {
      assistantMsgId = messages[i].id;
      break;
    }
  }
  if (!assistantMsgId) {
    // 无已有 assistant 消息，回退到普通发送
    return sendMessage(text, undefined, sessionId);
  }

  const sessionStore = useSessionStore.getState();
  const effectiveSessionId = sessionId || sessionStore.activeSessionId;

  const abortController = new AbortController();
  store.setAbortController(abortController);
  store.setStreaming(true);

  const S = () => useChatStore.getState();

  const getLastBlockOfType = (type: string) => {
    const msg = getLastAssistantMessage(S().messages, assistantMsgId!);
    if (!msg) return null;
    for (let i = msg.blocks.length - 1; i >= 0; i--) {
      if (msg.blocks[i].type === type) return msg.blocks[i];
    }
    return null;
  };

  const normalizeTaskItems = (taskListPayload: unknown): TaskItem[] => {
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
      };
    });
  };

  const applyTaskStatusPatch = (
    items: TaskItem[],
    taskIndex: number | null,
    taskStatus: string,
  ): TaskItem[] => {
    if (taskIndex === null || !taskStatus) return items;
    return items.map((item) =>
      item.index === taskIndex ? { ...item, status: taskStatus } : item
    );
  };

  const msgId = assistantMsgId;
  let thinkingInProgress = false;

  const finalizeThinking = () => {
    if (!thinkingInProgress) return;
    thinkingInProgress = false;
    S().updateBlockByType(msgId, "thinking", (b) => {
      if (b.type === "thinking" && b.startedAt != null && b.duration == null) {
        return { ...b, duration: (Date.now() - b.startedAt) / 1000 };
      }
      return b;
    });
  };

  try {
    await consumeSSE(
      buildApiUrl("/chat/stream", { direct: true }),
      {
        message: text,
        session_id: effectiveSessionId,
      },
      (event) => {
        const data = event.data;

        if (event.event !== "thinking_delta" && event.event !== "thinking") {
          finalizeThinking();
        }

        switch (event.event) {
          // Skip session_init — session already exists
          case "session_init":
            break;

          case "pipeline_progress": {
            S().setPipelineStatus({
              stage: (data.stage as string) || "",
              message: (data.message as string) || "",
              startedAt: Date.now(),
            });
            break;
          }

          case "route_end": {
            const mode = (data.route_mode as string) || "";
            const skills = (data.skills_used as string[]) || [];
            if (mode) {
              S().appendBlock(msgId, {
                type: "status",
                label: `路由: ${mode}`,
                detail: skills.length > 0 ? `技能: ${skills.join(", ")}` : undefined,
                variant: "route",
              });
            }
            break;
          }

          case "iteration_start": {
            const iter = (data.iteration as number) || 0;
            if (iter > 1) {
              S().appendBlock(msgId, { type: "iteration", iteration: iter });
            }
            break;
          }

          case "thinking_delta": {
            S().setPipelineStatus(null);
            const lastThinking = getLastBlockOfType("thinking");
            if (lastThinking && lastThinking.type === "thinking" && lastThinking.duration == null) {
              S().updateBlockByType(msgId, "thinking", (b) => {
                if (b.type === "thinking") {
                  return { ...b, content: b.content + (data.content as string) };
                }
                return b;
              });
            } else {
              S().appendBlock(msgId, {
                type: "thinking",
                content: (data.content as string) || "",
                startedAt: Date.now(),
              });
            }
            thinkingInProgress = true;
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

          case "text_delta": {
            S().setPipelineStatus(null);
            const msg = getLastAssistantMessage(S().messages, msgId);
            const lastBlock = msg?.blocks[msg.blocks.length - 1];
            if (lastBlock && lastBlock.type === "text") {
              S().updateLastBlock(msgId, (b) => {
                if (b.type === "text") {
                  return { ...b, content: b.content + (data.content as string) };
                }
                return b;
              });
            } else {
              S().appendBlock(msgId, {
                type: "text",
                content: (data.content as string) || "",
              });
            }
            break;
          }

          case "tool_call_start": {
            S().setPipelineStatus(null);
            const toolCallIdRaw = data.tool_call_id;
            const toolCallIdVal = typeof toolCallIdRaw === "string" && toolCallIdRaw.length > 0
              ? toolCallIdRaw
              : undefined;
            S().appendBlock(msgId, {
              type: "tool_call",
              toolCallId: toolCallIdVal,
              name: (data.tool_name as string) || "",
              args: (data.arguments as Record<string, unknown>) || {},
              status: "running",
              iteration: (data.iteration as number) || undefined,
            });
            break;
          }

          case "tool_call_end": {
            const toolCallIdRaw = data.tool_call_id;
            const toolCallId = typeof toolCallIdRaw === "string" ? toolCallIdRaw : null;
            S().updateToolCallBlock(msgId, toolCallId, (b) => {
              if (b.type === "tool_call") {
                if (b.status === "pending") {
                  return { ...b, result: (data.result as string) || undefined } as AssistantBlock;
                }
                if (b.status === "running") {
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

          case "subagent_start": {
            S().appendBlock(msgId, {
              type: "subagent",
              name: (data.name as string) || "",
              reason: (data.reason as string) || "",
              iterations: 0,
              toolCalls: 0,
              status: "running",
            });
            break;
          }

          case "subagent_iteration": {
            S().updateBlockByType(msgId, "subagent", (b) => {
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

          case "subagent_summary": {
            S().updateBlockByType(msgId, "subagent", (b) => {
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
            S().updateBlockByType(msgId, "subagent", (b) => {
              if (b.type === "subagent") {
                return {
                  ...b,
                  status: "done",
                  iterations: (data.iterations as number) || b.iterations,
                  toolCalls: (data.tool_calls as number) || b.toolCalls,
                };
              }
              return b;
            });
            break;
          }

          case "pending_approval": {
            const paToolCallId = (data.tool_call_id as string) || null;
            S().updateToolCallBlock(msgId, paToolCallId, (b) => {
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

          case "approval_resolved": {
            const toolName = (data.approval_tool_name as string) || "";
            const approvalId = (data.approval_id as string) || "";
            const success = Boolean(data.success);
            const undoable = Boolean(data.undoable);
            const arResult = (data.result as string) || undefined;
            S().setPendingApproval(null);
            // Transition the pending tool_call block to success/error and attach result
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
            });
            break;
          }

          case "task_update": {
            const payloadItems = normalizeTaskItems(data.task_list);
            const taskIndex = typeof data.task_index === "number" ? data.task_index : null;
            const taskStatus = typeof data.task_status === "string" ? data.task_status : "";
            const existingTaskList = getLastBlockOfType("task_list");
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

          case "excel_preview": {
            const epFilePath2 = (data.file_path as string) || "";
            useExcelStore.getState().addPreview({
              toolCallId: (data.tool_call_id as string) || "",
              filePath: epFilePath2,
              sheet: (data.sheet as string) || "",
              columns: (data.columns as string[]) || [],
              rows: (data.rows as (string | number | null)[][]) || [],
              totalRows: (data.total_rows as number) || 0,
              truncated: Boolean(data.truncated),
            });
            if (epFilePath2) {
              const fn = epFilePath2.split("/").pop() || epFilePath2;
              useExcelStore.getState().addRecentFile({ path: epFilePath2, filename: fn });
            }
            break;
          }

          case "excel_diff": {
            const edFilePath2 = (data.file_path as string) || "";
            useExcelStore.getState().addDiff({
              toolCallId: (data.tool_call_id as string) || "",
              filePath: edFilePath2,
              sheet: (data.sheet as string) || "",
              affectedRange: (data.affected_range as string) || "",
              changes: (data.changes as { cell: string; old: string | number | null; new: string | number | null }[]) || [],
              timestamp: Date.now(),
            });
            if (edFilePath2) {
              const fn = edFilePath2.split("/").pop() || edFilePath2;
              useExcelStore.getState().addRecentFile({ path: edFilePath2, filename: fn });
              S().addAffectedFiles(msgId, [edFilePath2]);
            }
            break;
          }

          case "files_changed": {
            const changedFiles2 = (data.files as string[]) || [];
            const excelStore2 = useExcelStore.getState();
            for (const filePath of changedFiles2) {
              if (filePath) {
                const filename = filePath.split("/").pop() || filePath;
                excelStore2.addRecentFile({ path: filePath, filename });
              }
            }
            if (changedFiles2.length > 0) {
              S().addAffectedFiles(msgId, changedFiles2);
            }
            break;
          }

          case "memory_extracted": {
            const memEntries = (data.entries as { id: string; content: string; category: string }[]) || [];
            const memTrigger = (data.trigger as string) || "session_end";
            const memCount = (data.count as number) || memEntries.length;
            if (memCount > 0) {
              S().appendBlock(msgId, {
                type: "memory_extracted",
                entries: memEntries,
                trigger: memTrigger,
                count: memCount,
              });
            }
            break;
          }

          case "mode_changed": {
            const uiMode = useUIStore.getState();
            const modeName = data.mode_name as string;
            const enabled = Boolean(data.enabled);
            if (modeName === "full_access") uiMode.setFullAccessEnabled(enabled);
            else if (modeName === "plan_mode") uiMode.setPlanModeEnabled(enabled);
            S().appendBlock(msgId, {
              type: "status",
              label: `${enabled ? "已开启" : "已关闭"} ${modeName === "full_access" ? "Full Access" : "Plan Mode"}`,
              variant: "info",
            });
            break;
          }

          case "reply": {
            const content = (data.content as string) || "";
            const hasPendingInteraction =
              S().pendingApproval !== null || S().pendingQuestion !== null;
            if (content && !hasPendingInteraction) {
              const msg = getLastAssistantMessage(S().messages, msgId);
              const lastBlock = msg?.blocks[msg.blocks.length - 1];
              if (!lastBlock || lastBlock.type !== "text") {
                S().appendBlock(msgId, { type: "text", content });
              }
            }
            const uiReply = useUIStore.getState();
            if (typeof data.full_access_enabled === "boolean") {
              uiReply.setFullAccessEnabled(data.full_access_enabled);
            }
            if (typeof data.plan_mode_enabled === "boolean") {
              uiReply.setPlanModeEnabled(data.plan_mode_enabled);
            }
            const totalTokens = (data.total_tokens as number) || 0;
            if (totalTokens > 0) {
              if (hasPendingInteraction) {
                // Defer — another continuation will display the accumulated total.
                _deferredTokenStats = {
                  promptTokens: (data.prompt_tokens as number) || 0,
                  completionTokens: (data.completion_tokens as number) || 0,
                  totalTokens,
                  iterations: (data.iterations as number) || 0,
                };
              } else {
                // Accumulate: deferred stats from prior call + any leftover blocks + current
                let accPrompt = (data.prompt_tokens as number) || 0;
                let accCompletion = (data.completion_tokens as number) || 0;
                let accTotal = totalTokens;
                let accIterations = (data.iterations as number) || 0;
                if (_deferredTokenStats) {
                  accPrompt += _deferredTokenStats.promptTokens;
                  accCompletion += _deferredTokenStats.completionTokens;
                  accTotal += _deferredTokenStats.totalTokens;
                  accIterations += _deferredTokenStats.iterations;
                  _deferredTokenStats = null;
                }
                const curMsg = getLastAssistantMessage(S().messages, msgId);
                if (curMsg) {
                  for (const b of curMsg.blocks) {
                    if (b.type === "token_stats") {
                      accPrompt += b.promptTokens;
                      accCompletion += b.completionTokens;
                      accTotal += b.totalTokens;
                      accIterations += b.iterations;
                    }
                  }
                  if (curMsg.blocks.some((b) => b.type === "token_stats")) {
                    S().setMessages(
                      S().messages.map((m) => {
                        if (m.id !== msgId || m.role !== "assistant") return m;
                        return { ...m, blocks: m.blocks.filter((b) => b.type !== "token_stats") };
                      }),
                    );
                  }
                }
                S().appendBlock(msgId, {
                  type: "token_stats",
                  promptTokens: accPrompt,
                  completionTokens: accCompletion,
                  totalTokens: accTotal,
                  iterations: accIterations,
                });
              }
            }
            break;
          }

          case "done": {
            S().setPipelineStatus(null);
            S().saveCurrentSession();
            S().setStreaming(false);
            S().setAbortController(null);
            break;
          }

          case "error": {
            S().setPipelineStatus(null);
            S().appendBlock(msgId, {
              type: "text",
              content: `⚠️ ${(data.error as string) || "发生未知错误"}`,
            });
            S().saveCurrentSession();
            S().setStreaming(false);
            S().setAbortController(null);
            break;
          }

          default:
            break;
        }
      },
      abortController.signal,
    );
  } catch (err) {
    if ((err as Error).name !== "AbortError") {
      S().appendBlock(msgId, {
        type: "text",
        content: `⚠️ 连接错误: ${(err as Error).message}`,
      });
    }
  } finally {
    S().setPipelineStatus(null);
    S().saveCurrentSession();
    S().setStreaming(false);
    S().setAbortController(null);
  }
}

/**
 * 回退对话到指定用户消息并重新发送（编辑后的内容）。
 * 1. 调用后端 rollback API 截断对话
 * 2. 截断前端消息列表
 * 3. 用新内容发送消息
 */
export async function rollbackAndResend(
  messageId: string,
  newContent: string,
  rollbackFiles: boolean,
  sessionId: string | null,
) {
  const store = useChatStore.getState();
  if (store.isStreaming) return;

  // 找到目标用户消息在前端消息列表中的位置
  const messages = store.messages;
  const msgIndex = messages.findIndex((m) => m.id === messageId);
  if (msgIndex === -1) return;

  // 计算 turn_index（第几个 user 消息）
  let turnIndex = 0;
  for (let i = 0; i < msgIndex; i++) {
    if (messages[i].role === "user") turnIndex++;
  }

  const effectiveSessionId = sessionId || store.currentSessionId;
  if (!effectiveSessionId) return;

  // 调用后端 rollback API
  try {
    const { rollbackChat } = await import("./api");
    await rollbackChat({
      sessionId: effectiveSessionId,
      turnIndex,
      rollbackFiles,
      newMessage: newContent,
    });
  } catch (err) {
    console.error("Rollback failed:", err);
    return;
  }

  // 截断前端消息列表：保留 msgIndex 之前的消息（不包含该用户消息本身，因为要重发）
  const truncated = messages.slice(0, msgIndex);
  store.setMessages(truncated);

  // 用新内容发送消息
  await sendMessage(newContent, undefined, effectiveSessionId);
}

export function stopGeneration() {
  const store = useChatStore.getState();
  if (!store.abortController) return;

  // 1. Tell the backend to cancel the server-side task
  const sessionId = store.currentSessionId;
  if (sessionId) {
    fetch(buildApiUrl("/chat/abort", { direct: true }), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId }),
    }).catch(() => {});
  }

  // 2. Abort the frontend SSE connection
  store.abortController.abort();
  store.setAbortController(null);
  store.setStreaming(false);

  // 3. Patch the last assistant message: mark in-flight blocks as failed
  //    and append a visible "stopped" indicator.
  const messages = store.messages;
  const lastMsg = [...messages].reverse().find((m) => m.role === "assistant");
  if (lastMsg && lastMsg.role === "assistant") {
    let blocksChanged = false;
    const patchedBlocks = lastMsg.blocks.map((block): AssistantBlock => {
      if (block.type === "tool_call" && block.status === "running") {
        blocksChanged = true;
        return { ...block, status: "error", error: "已被用户停止" };
      }
      if (block.type === "subagent" && block.status === "running") {
        blocksChanged = true;
        return { ...block, status: "done", summary: "已被用户停止" };
      }
      return block;
    });

    patchedBlocks.push({
      type: "status",
      label: "对话已停止",
      detail: "用户手动终止了本轮生成",
      variant: "info",
    });

    store.setMessages(
      messages.map((m) =>
        m.id === lastMsg.id ? { ...m, blocks: patchedBlocks } : m
      )
    );
    store.saveCurrentSession();
  }
}

function getLastAssistantMessage(messages: ReturnType<typeof useChatStore.getState>["messages"], id: string) {
  const msg = messages.find((m) => m.id === id);
  if (msg && msg.role === "assistant") return msg;
  return null;
}
