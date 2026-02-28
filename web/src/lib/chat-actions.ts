import { consumeSSE } from "./sse";
import { buildApiUrl } from "./api";
import { uuid } from "@/lib/utils";
import { useChatStore, type PipelineStatus } from "@/stores/chat-store";
import { useSessionStore } from "@/stores/session-store";
import { useAuthStore } from "@/stores/auth-store";
import { useUIStore } from "@/stores/ui-store";
import { useExcelStore, type ExcelCellDiff, type ExcelPreviewData, type MergeRange } from "@/stores/excel-store";
import type { AssistantBlock, TaskItem, AttachedFile, FileAttachment } from "@/lib/types";

/** 将后端 snake_case diff changes 映射为前端 camelCase ExcelCellDiff[] */
function _mapDiffChanges(raw: unknown[]): ExcelCellDiff[] {
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

/** 将后端 route_mode 映射为用户友好的中文标签 */
function _friendlyRouteMode(mode: string): string {
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

const _IMAGE_EXTS = new Set([".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"]);
function _isImageFile(name: string): boolean {
  const dot = name.lastIndexOf(".");
  return dot >= 0 && _IMAGE_EXTS.has(name.slice(dot).toLowerCase());
}

function _isImageLike(file: File): boolean {
  return _isImageFile(file.name) || (file.type || "").toLowerCase().startsWith("image/");
}

function _fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      // result 格式为 "data:<mime>;base64,<data>" — 仅提取 base64 部分
      const result = reader.result as string;
      const idx = result.indexOf(",");
      resolve(idx >= 0 ? result.slice(idx + 1) : result);
    };
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

// ---------------------------------------------------------------------------
// 基于 RAF 的增量批处理器：缓冲高频 text_delta / thinking_delta 事件，
// 每个动画帧最多刷新一次，避免过度重渲染。
// 改进：增加立即刷新机制，确保非增量事件不会与缓冲的增量事件产生时序问题
// ---------------------------------------------------------------------------
class DeltaBatcher {
  private _textBuf = "";
  private _thinkingBuf = "";
  private _rafId: number | null = null;
  private _disposed = false;

  constructor(private _onFlush: (text: string, thinking: string) => void) {}

  pushText(delta: string) {
    if (this._disposed) return;
    this._textBuf += delta;
    this._schedule();
  }

  pushThinking(delta: string) {
    if (this._disposed) return;
    this._thinkingBuf += delta;
    this._schedule();
  }

  flush() {
    if (this._disposed) return;
    if (this._rafId !== null) {
      cancelAnimationFrame(this._rafId);
      this._rafId = null;
    }
    this._doFlush();
  }

  dispose() {
    if (this._disposed) return;
    // 先刷新残余内容到 store，防止断连/热重载时丢失尾部 delta
    if (this._rafId !== null) {
      cancelAnimationFrame(this._rafId);
      this._rafId = null;
    }
    this._doFlush();
    this._disposed = true;
    this._textBuf = "";
    this._thinkingBuf = "";
  }

  // 检查是否有待处理的内容
  hasPendingContent(): boolean {
    return this._textBuf.length > 0 || this._thinkingBuf.length > 0;
  }

  private _schedule() {
    if (this._disposed || this._rafId !== null) return;
    this._rafId = requestAnimationFrame(() => {
      if (this._disposed) return;
      this._rafId = null;
      this._doFlush();
    });
  }

  private _doFlush() {
    if (this._disposed) return;
    const text = this._textBuf;
    const thinking = this._thinkingBuf;
    this._textBuf = "";
    this._thinkingBuf = "";
    if (text || thinking) {
      try {
        this._onFlush(text, thinking);
      } catch (error) {
        console.error("[DeltaBatcher] flush error:", error);
      }
    }
  }
}

// 因待处理交互（askuser / approval）而延迟的 Token 统计。
// sendContinuation 会将这些累积到最终统计中。
let _deferredTokenStats: {
  promptTokens: number;
  completionTokens: number;
  totalTokens: number;
  iterations: number;
} | null = null;

export async function sendMessage(
  text: string,
  files?: AttachedFile[],
  sessionId?: string | null
) {
  const store = useChatStore.getState();
  const sessionStore = useSessionStore.getState();
  const uiState = useUIStore.getState();

  if (store.isStreaming) return;

  // 模型配置未就绪时阻断发送，直接在聊天中显示提示
  if (uiState.configReady === false || uiState.configReady === null) {
    const userMsgId = uuid();
    store.addUserMessage(userMsgId, text);
    const assistantMsgId = uuid();
    store.addAssistantMessage(assistantMsgId);
    const items = uiState.configPlaceholderItems;
    store.appendBlock(assistantMsgId, {
      type: "config_error",
      items: items.length > 0 ? items : [{ name: "main", field: "api_key", model: "" }],
    });
    store.saveCurrentSession();
    return;
  }

  // 清除上次可能残留的延迟 token 统计，避免跨会话泄漏
  _deferredTokenStats = null;

  // 尽早创建 AbortController 并设置流式状态 - 在任何异步操作（base64 编码）之前。
  // SessionSync 的 useEffect 通过检查 abortController 来决定是否调用 switchSession()。
  // 如果延迟到文件处理之后，SessionSync 的 effect 可能在 await 间隙触发，
  // 发现 abortController===null 后调用 switchSession，清空 addUserMessage 即将创建的消息。
  const abortController = new AbortController();
  store.setAbortController(abortController);
  store.setStreaming(true);
  store.setPipelineStatus({
    stage: "connecting",
    message: "正在连接...",
    startedAt: Date.now(),
  });

  // 文件已由 ChatInput 预先上传。这里只需：
  // 1. 收集上传成功的路径用于 agent 通知
  // 2. 将图片编码为 base64 用于 SSE 多模态载荷
  const uploadedDocPaths: string[] = [];
  const uploadedImagePaths: string[] = [];
  const imageAttachments: { data: string; media_type: string }[] = [];
  const fileUploadResults: { filename: string; path: string; size: number }[] = [];
  if (files && files.length > 0) {
    for (const af of files) {
      const isImage = _isImageLike(af.file);

      // 将图片编码为 base64 用于 LLM 多模态载荷。
      // 即使文件上传失败，也能确保 agent 可以"看到"图片。
      if (isImage) {
        try {
          const b64 = await _fileToBase64(af.file);
          imageAttachments.push({
            data: b64,
            media_type: af.file.type || "image/png",
          });
        } catch (b64Err) {
          console.error("Base64 encoding failed for image:", af.file.name, b64Err);
        }
      }

      // 使用预上传的结果
      if (af.status === "success" && af.uploadResult) {
        fileUploadResults.push(af.uploadResult);
        if (isImage) {
          uploadedImagePaths.push(af.uploadResult.path);
        } else {
          uploadedDocPaths.push(af.uploadResult.path);
        }
      } else {
        // 上传失败或仍在进行中 — 记录但不含路径
        fileUploadResults.push({ filename: af.file.name, path: "", size: af.file.size });
      }
    }
  }

  // 为 agent 构建结构化文件通知
  let messageContent = text;
  const notices: string[] = [];
  for (const p of uploadedDocPaths) {
    notices.push(`[已上传文件: ${p}]`);
  }
  for (const p of uploadedImagePaths) {
    notices.push(`[已上传图片: ${p}]`);
  }
  if (notices.length > 0) {
    messageContent = `${notices.join("\n")}\n\n${text}`;
  }

  const effectiveSessionId = sessionId || sessionStore.activeSessionId;

  // 在添加消息之前同步 currentSessionId，确保 SessionSync 的 useEffect
  //（在下次渲染后触发）看到 currentSessionId === activeSessionId，
  // 从而跳过会清空我们即将添加的消息的 switchSession() 调用。
  if (effectiveSessionId && store.currentSessionId !== effectiveSessionId) {
    if (store.currentSessionId && store.messages.length > 0) {
      store.saveCurrentSession();
    }
    useChatStore.setState({ currentSessionId: effectiveSessionId });
  }

  const userMsgId = uuid();
  store.addUserMessage(
    userMsgId,
    text,
    fileUploadResults.length > 0 ? fileUploadResults : undefined
  );

  const assistantMsgId = uuid();
  store.addAssistantMessage(assistantMsgId);

  // 辅助函数：获取最新的 store 状态
  const S = () => useChatStore.getState();

  // RAF 批量增量刷新器：累积 text_delta / thinking_delta，
  // 每个动画帧最多应用一次到 store。
  const batcher = new DeltaBatcher((textDelta, thinkingDelta) => {
    if (textDelta) {
      const msg = getLastAssistantMessage(S().messages, assistantMsgId);
      const lastBlock = msg?.blocks[msg.blocks.length - 1];
      if (lastBlock && lastBlock.type === "text") {
        S().updateLastBlock(assistantMsgId, (b) => {
          if (b.type === "text") {
            return { ...b, content: b.content + textDelta };
          }
          return b;
        });
      } else {
        S().appendBlock(assistantMsgId, { type: "text", content: textDelta });
      }
    }
    if (thinkingDelta) {
      S().updateBlockByType(assistantMsgId, "thinking", (b) => {
        if (b.type === "thinking") {
          return { ...b, content: b.content + thinkingDelta };
        }
        return b;
      });
    }
  });

  // 辅助函数：从当前 assistant 消息中获取指定类型的最后一个 block
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
        verification: (item.verification as string) || undefined,
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
  let _hadStreamError = false;

  const finalizeThinking = () => {
    if (!thinkingInProgress) return;
    thinkingInProgress = false;
    // 在确定时长之前刷新所有缓冲的思考增量
    batcher.flush();
    S().updateBlockByType(assistantMsgId, "thinking", (b) => {
      if (b.type === "thinking" && b.startedAt != null && b.duration == null) {
        return { ...b, duration: (Date.now() - b.startedAt) / 1000 };
      }
      return b;
    });
  };

  // 诊断日志：记录图片附件状态
  if (files && files.length > 0) {
    console.log(
      "[sendMessage] files=%d, imageAttachments=%d, uploadedImagePaths=%o",
      files.length,
      imageAttachments.length,
      uploadedImagePaths,
    );
    for (const att of imageAttachments) {
      console.log(
        "[sendMessage] image: media_type=%s, data_length=%d",
        att.media_type,
        att.data.length,
      );
    }
  }

  try {
    await consumeSSE(
      buildApiUrl("/chat/stream", { direct: true }),
      {
        message: messageContent,
        session_id: effectiveSessionId,
        chat_mode: useUIStore.getState().chatMode,
        ...(imageAttachments.length > 0 ? { images: imageAttachments } : {}),
      },
      (event) => {
        const data = event.data;

        if (event.event !== "thinking_delta" && event.event !== "thinking") {
          finalizeThinking();
        }
        // 在任何非增量事件前刷新缓冲的增量，以保持 block 顺序
        if (event.event !== "text_delta" && event.event !== "thinking_delta") {
          batcher.flush();
        }

        switch (event.event) {
          // ── 会话 ────────────────────────────────
          case "session_init": {
            const sid = data.session_id as string;
            const ss = useSessionStore.getState();
            if (!ss.activeSessionId) {
              ss.setActiveSession(sid);
            }
            // 仅同步会话 ID 而不清空/重新加载消息。
            // switchSession() 会清空消息数组并触发异步后端加载，
            // 恢复的 ID 不会与 userMsgId 匹配，导致用户气泡重复。
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
            // 同步模式状态
            const ui = useUIStore.getState();
            if (typeof data.full_access_enabled === "boolean") {
              ui.setFullAccessEnabled(data.full_access_enabled);
            }
            if (typeof data.chat_mode === "string") {
              ui.setChatMode(data.chat_mode as "write" | "read" | "plan");
            }
            break;
          }

          // ── 流水线进度 ─────────────────────
          case "pipeline_progress": {
            const stage = (data.stage as string) || "";
            const pipelineMsg = (data.message as string) || "";
            const phaseIndex = typeof data.phase_index === "number" ? data.phase_index : undefined;
            const totalPhases = typeof data.total_phases === "number" ? data.total_phases : undefined;
            const specPath = (data.spec_path as string) || undefined;
            const diff = (data.diff as PipelineStatus["diff"]) ?? undefined;
            const checkpoint = (data.checkpoint as Record<string, unknown>) ?? undefined;

            S().setPipelineStatus({
              stage, message: pipelineMsg, startedAt: Date.now(),
              phaseIndex, totalPhases, specPath, diff, checkpoint,
            });

            // 累积 VLM 提取阶段用于时间线卡片
            if (stage.startsWith("vlm_extract_") && phaseIndex != null && totalPhases != null) {
              S().pushVlmPhase({
                stage,
                message: pipelineMsg,
                startedAt: Date.now(),
                diff,
                specPath,
                phaseIndex,
                totalPhases,
              });
            }
            break;
          }

          // ── 路由 ──────────────────────────────────
          case "route_start": {
            // 路由已启动 — 可选的状态指示器
            break;
          }
          case "route_end": {
            const mode = (data.route_mode as string) || "";
            const skills = (data.skills_used as string[]) || [];
            if (mode) {
              S().appendBlock(assistantMsgId, {
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
              S().appendBlock(assistantMsgId, {
                type: "iteration",
                iteration: iter,
              });
            }
            break;
          }

          // ── 思考 ───────────────────────────────
          case "thinking_delta": {
            S().setPipelineStatus(null);
            const lastThinking = getLastBlockOfType("thinking");
            if (lastThinking && lastThinking.type === "thinking" && lastThinking.duration == null) {
              // 已有未关闭的思考 block — 批量缓冲增量
              batcher.pushThinking((data.content as string) || "");
            } else {
              // 尚无未关闭的思考 block — 先刷新待处理的文本，
              // 然后同步创建新的思考 block。
              batcher.flush();
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

          case "retract_thinking": {
            thinkingInProgress = false;
            batcher.flush();
            S().retractLastThinking(assistantMsgId);
            break;
          }

          // ── 文本 ───────────────────────────────────
          case "text_delta": {
            S().setPipelineStatus(null);
            // 确保存在文本 block 供批处理器追加内容。
            const msg = getLastAssistantMessage(S().messages, assistantMsgId);
            const lastBlock = msg?.blocks[msg.blocks.length - 1];
            if (!lastBlock || lastBlock.type !== "text") {
              S().appendBlock(assistantMsgId, {
                type: "text",
                content: "",
              });
            }
            batcher.pushText((data.content as string) || "");
            break;
          }

          // ── 流式工具参数 delta ────────────────────
          case "tool_call_args_delta": {
            const adToolCallId = (data.tool_call_id as string) || "";
            const adToolName = (data.tool_name as string) || "";
            const adDelta = (data.args_delta as string) || "";
            if (adToolCallId && adDelta) {
              useExcelStore.getState().appendStreamingArgs(adToolCallId, adDelta);
              // 如果还没有对应的 tool_call block，提前创建 streaming block
              const msg = getLastAssistantMessage(S().messages, assistantMsgId);
              const hasBlock = msg?.blocks.some(
                (b) => b.type === "tool_call" && b.toolCallId === adToolCallId,
              );
              if (!hasBlock && adToolName) {
                S().setPipelineStatus(null);
                S().appendBlock(assistantMsgId, {
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
            // 如果 streaming block 已存在，升级为 running
            const msgForStart = getLastAssistantMessage(S().messages, assistantMsgId);
            const streamingExists = toolCallId && msgForStart?.blocks.some(
              (b) => b.type === "tool_call" && b.toolCallId === toolCallId && b.status === ("streaming" as "running"),
            );
            if (streamingExists) {
              S().updateToolCallBlock(assistantMsgId, toolCallId!, (b) => {
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
              S().appendBlock(assistantMsgId, {
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
            // 清理流式参数缓存
            if (toolCallId) {
              useExcelStore.getState().clearStreamingArgs(toolCallId);
            }
            S().updateToolCallBlock(assistantMsgId, toolCallId, (b) => {
              if (b.type === "tool_call") {
                // 如果已为 pending（来自 pending_approval 事件），保持 pending 状态但更新结果
                if (b.status === "pending") {
                  return {
                    ...b,
                    result: (data.result as string) || undefined,
                  } as AssistantBlock;
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
            S().appendBlock(assistantMsgId, {
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
            S().updateSubagentBlock(assistantMsgId, cid, (b) => {
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
            S().updateSubagentBlock(assistantMsgId, cid, (b) => {
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
            S().updateSubagentBlock(assistantMsgId, cid, (b) => {
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
            S().updateSubagentBlock(assistantMsgId, cid, (b) => {
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
            S().updateSubagentBlock(assistantMsgId, cid, (b) => {
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
            // 将关联的 tool_call block 标记为 "pending"
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
            const hasChanges = Boolean(data.has_changes);
            const arResult = (data.result as string) || undefined;

            S().setPendingApproval(null);
            // 将 pending 状态的 tool_call block 转换为 success/error 并附加结果
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
              hasChanges,
            });
            break;
          }

          // ── 任务列表 ──────────────────────────────
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
              const epFilename = epFilePath.split("/").pop() || epFilePath;
              useExcelStore.getState().addRecentFileIfNotDismissed({ path: epFilePath, filename: epFilename });
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
              changes: _mapDiffChanges(data.changes as unknown[]),
              mergeRanges: Array.isArray(data.merge_ranges) ? data.merge_ranges as MergeRange[] : undefined,
              oldMergeRanges: Array.isArray(data.old_merge_ranges) ? data.old_merge_ranges as MergeRange[] : undefined,
              metadataHints: Array.isArray(data.metadata_hints) ? data.metadata_hints as string[] : undefined,
              timestamp: Date.now(),
            });
            if (edFilePath) {
              const edFilename = edFilePath.split("/").pop() || edFilePath;
              useExcelStore.getState().addRecentFileIfNotDismissed({ path: edFilePath, filename: edFilename });
              S().addAffectedFiles(assistantMsgId, [edFilePath]);
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
              S().addAffectedFiles(assistantMsgId, [tdFilePath]);
            }
            break;
          }

          case "verification_report": {
            S().appendBlock(assistantMsgId, {
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
              S().addAffectedFiles(assistantMsgId, changedFiles);
              excelStore.bumpWorkspaceFilesVersion();
              // W7: 自动刷新备份列表
              if (effectiveSessionId) {
                excelStore.fetchBackups(effectiveSessionId);
              }
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

          case "file_download": {
            const dlFilePath = (data.file_path as string) || "";
            const dlFilename = (data.filename as string) || dlFilePath.split("/").pop() || "download";
            const dlDescription = (data.description as string) || "";
            if (dlFilePath) {
              S().appendBlock(assistantMsgId, {
                type: "file_download",
                toolCallId: (data.tool_call_id as string) || undefined,
                filePath: dlFilePath,
                filename: dlFilename,
                description: dlDescription,
              });
            }
            break;
          }

          // ── 回复与完成 ───────────────────────────
          case "reply": {
            const content = (data.content as string) || "";
            // 存在待处理审批或问题时抑制回复文本
            // — 文本已在工具调用卡片/面板中展示。
            const hasPendingInteraction =
              S().pendingApproval !== null || S().pendingQuestion !== null;
            if (content && !hasPendingInteraction) {
              const msg = getLastAssistantMessage(S().messages, assistantMsgId);
              // 如果已存在文本 block（由流式 text_delta 事件填充）则跳过
              const hasTextBlock = msg?.blocks.some((b) => b.type === "text" && b.content);
              if (!hasTextBlock) {
                S().appendBlock(assistantMsgId, { type: "text", content });
              }
            }
            // 从 reply 同步模式状态
            const uiReply = useUIStore.getState();
            if (typeof data.full_access_enabled === "boolean") {
              uiReply.setFullAccessEnabled(data.full_access_enabled);
            }
            if (typeof data.chat_mode === "string") {
              uiReply.setChatMode(data.chat_mode as "write" | "read" | "plan");
            }
            // Token 统计处理
            const totalTokens = (data.total_tokens as number) || 0;
            if (totalTokens > 0) {
              if (hasPendingInteraction) {
                // 延迟 Token 统计 — 后续的 continuation 将展示累计总数。
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
            } else if (modeName === "chat_mode") {
              uiMode.setChatMode(data.value as "write" | "read" | "plan");
            }
            // 在聊天中以状态 block 展示模式变更
            const _modeLabelMap: Record<string, string> = { full_access: "Full Access", chat_mode: "Chat Mode" };
            const modeLabel = _modeLabelMap[modeName] || modeName;
            const modeAction = enabled ? "已开启" : "已关闭";
            S().appendBlock(assistantMsgId, {
              type: "status",
              label: `${modeAction} ${modeLabel}`,
              variant: "info",
            });
            break;
          }

          case "done": {
            // 保存会话消息
            S().setPipelineStatus(null);
            S().saveCurrentSession();
            S().setStreaming(false);
            S().setAbortController(null);
            break;
          }

          case "error": {
            // 非致命错误：追加错误信息但不终止流式状态。
            // SSE 连接可能仍会传递后续事件（工具调用、文本、done）。
            // 清理由 "done" 事件或连接实际关闭时的 finally 块处理。
            _hadStreamError = true;
            S().setPipelineStatus(null);
            const errMsg = (data.error as string) || "发生未知错误";
            const errLower = errMsg.toLowerCase();
            // 检测模型配置相关错误，提供更友好的提示
            const isModelConfigError = [
              "unauthorized", "401", "403", "forbidden",
              "invalid api", "authentication", "api key",
              "model not found", "model_not_found", "does not exist",
              "invalid model", "no such model",
              "connection refused", "connect timeout", "name or service not known",
              "payment_required", "402", "insufficient quota", "quota exceeded",
              "billing", "balance",
              "内部错误", "服务内部",
            ].some((kw) => errLower.includes(kw));
            if (isModelConfigError) {
              useUIStore.getState().setConfigError(errMsg);
              S().appendBlock(assistantMsgId, {
                type: "text",
                content: `🚫 **模型配置错误**\n\n${errMsg}\n\n> 请检查模型配置是否正确（API Key、Base URL、Model ID），可在右上角 ⚙️ 设置中修改。`,
              });
            } else {
              S().appendBlock(assistantMsgId, {
                type: "text",
                content: `⚠️ ${errMsg}`,
              });
            }
            break;
          }

          default:
            // 静默忽略未知事件
            break;
        }
      },
      abortController.signal
    );
  } catch (err) {
    if ((err as Error).name !== "AbortError") {
      _hadStreamError = true;
      S().appendBlock(assistantMsgId, {
        type: "text",
        content: `⚠️ 连接错误: ${(err as Error).message}`,
      });
    }
  } finally {
    batcher.dispose();
    S().setPipelineStatus(null);
    S().saveCurrentSession();
    S().setStreaming(false);
    S().setAbortController(null);

    // 自动恢复：如果流式传输期间发生错误，调度一次后端刷新，
    // 使用户无需手动刷新页面即可看到权威的对话状态。
    if (_hadStreamError && effectiveSessionId) {
      const sid = effectiveSessionId;
      setTimeout(async () => {
        try {
          const { refreshSessionMessagesFromBackend } = await import("@/stores/chat-store");
          const chat = useChatStore.getState();
          // 仅在仍在同一会话且未在流式传输时刷新
          if (chat.currentSessionId === sid && !chat.isStreaming && !chat.abortController) {
            await refreshSessionMessagesFromBackend(sid);
          }
        } catch {
          // 静默处理 — SessionSync 轮询最终会恢复
        }
      }, 1500);
    }
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
  store.setPipelineStatus({
    stage: "connecting",
    message: "正在连接...",
    startedAt: Date.now(),
  });

  const S = () => useChatStore.getState();

  // 用于 continuation 流的 RAF 批量增量刷新器
  const batcher = new DeltaBatcher((textDelta, thinkingDelta) => {
    if (textDelta) {
      const msg = getLastAssistantMessage(S().messages, msgId);
      const lastBlock = msg?.blocks[msg.blocks.length - 1];
      if (lastBlock && lastBlock.type === "text") {
        S().updateLastBlock(msgId, (b) => {
          if (b.type === "text") {
            return { ...b, content: b.content + textDelta };
          }
          return b;
        });
      } else {
        S().appendBlock(msgId, { type: "text", content: textDelta });
      }
    }
    if (thinkingDelta) {
      S().updateBlockByType(msgId, "thinking", (b) => {
        if (b.type === "thinking") {
          return { ...b, content: b.content + thinkingDelta };
        }
        return b;
      });
    }
  });

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
        verification: (item.verification as string) || undefined,
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
  let _hadStreamError = false;

  const finalizeThinking = () => {
    if (!thinkingInProgress) return;
    thinkingInProgress = false;
    batcher.flush();
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
        // 在任何非增量事件前刷新缓冲的增量，以保持 block 顺序
        if (event.event !== "text_delta" && event.event !== "thinking_delta") {
          batcher.flush();
        }

        switch (event.event) {
          // 跳过 session_init — 会话已存在
          case "session_init":
            break;

          case "pipeline_progress": {
            S().setPipelineStatus({
              stage: (data.stage as string) || "",
              message: (data.message as string) || "",
              startedAt: Date.now(),
              phaseIndex: typeof data.phase_index === "number" ? data.phase_index : undefined,
              totalPhases: typeof data.total_phases === "number" ? data.total_phases : undefined,
              specPath: (data.spec_path as string) || undefined,
              diff: (data.diff as PipelineStatus["diff"]) ?? undefined,
              checkpoint: (data.checkpoint as Record<string, unknown>) ?? undefined,
            });
            break;
          }

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
              batcher.pushThinking((data.content as string) || "");
            } else {
              batcher.flush();
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
            if (!lastBlock || lastBlock.type !== "text") {
              S().appendBlock(msgId, { type: "text", content: "" });
            }
            batcher.pushText((data.content as string) || "");
            break;
          }

          case "tool_call_args_delta": {
            const _adId = (data.tool_call_id as string) || "";
            const _adName = (data.tool_name as string) || "";
            const _adDelta = (data.args_delta as string) || "";
            if (_adId && _adDelta) {
              useExcelStore.getState().appendStreamingArgs(_adId, _adDelta);
              const _adMsg = getLastAssistantMessage(S().messages, msgId);
              const _adHas = _adMsg?.blocks.some(
                (b) => b.type === "tool_call" && b.toolCallId === _adId,
              );
              if (!_adHas && _adName) {
                S().setPipelineStatus(null);
                S().appendBlock(msgId, {
                  type: "tool_call",
                  toolCallId: _adId,
                  name: _adName,
                  args: {},
                  status: "streaming" as "running",
                  iteration: undefined,
                });
              }
            }
            break;
          }

          case "tool_call_start": {
            S().setPipelineStatus(null);
            const toolCallIdRaw = data.tool_call_id;
            const toolCallIdVal = typeof toolCallIdRaw === "string" && toolCallIdRaw.length > 0
              ? toolCallIdRaw
              : undefined;
            const _sMsg = getLastAssistantMessage(S().messages, msgId);
            const _sExists = toolCallIdVal && _sMsg?.blocks.some(
              (b) => b.type === "tool_call" && b.toolCallId === toolCallIdVal && (b.status as string) === "streaming",
            );
            if (_sExists) {
              S().updateToolCallBlock(msgId, toolCallIdVal!, (b) => {
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
                toolCallId: toolCallIdVal,
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
            const hasChanges = Boolean(data.has_changes);
            const arResult = (data.result as string) || undefined;
            S().setPendingApproval(null);
            // 将 pending 状态的 tool_call block 转换为 success/error 并附加结果
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
              cellStyles: Array.isArray(data.cell_styles) ? data.cell_styles as ExcelPreviewData["cellStyles"] : undefined,
              mergeRanges: Array.isArray(data.merge_ranges) ? data.merge_ranges as MergeRange[] : undefined,
              metadataHints: Array.isArray(data.metadata_hints) ? data.metadata_hints as string[] : undefined,
            });
            if (epFilePath2) {
              const fn = epFilePath2.split("/").pop() || epFilePath2;
              useExcelStore.getState().addRecentFileIfNotDismissed({ path: epFilePath2, filename: fn });
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
              changes: _mapDiffChanges(data.changes as unknown[]),
              mergeRanges: Array.isArray(data.merge_ranges) ? data.merge_ranges as MergeRange[] : undefined,
              oldMergeRanges: Array.isArray(data.old_merge_ranges) ? data.old_merge_ranges as MergeRange[] : undefined,
              metadataHints: Array.isArray(data.metadata_hints) ? data.metadata_hints as string[] : undefined,
              timestamp: Date.now(),
            });
            if (edFilePath2) {
              const fn = edFilePath2.split("/").pop() || edFilePath2;
              useExcelStore.getState().addRecentFileIfNotDismissed({ path: edFilePath2, filename: fn });
              S().addAffectedFiles(msgId, [edFilePath2]);
            }
            break;
          }

          case "text_diff": {
            const tdFilePath2 = (data.file_path as string) || "";
            useExcelStore.getState().addTextDiff({
              toolCallId: (data.tool_call_id as string) || "",
              filePath: tdFilePath2,
              hunks: (data.hunks as string[]) || [],
              additions: (data.additions as number) || 0,
              deletions: (data.deletions as number) || 0,
              truncated: !!data.truncated,
              timestamp: Date.now(),
            });
            if (tdFilePath2) {
              S().addAffectedFiles(msgId, [tdFilePath2]);
            }
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
            const changedFiles2 = (data.files as string[]) || [];
            const excelStore2 = useExcelStore.getState();
            for (const filePath of changedFiles2) {
              if (filePath) {
                const filename = filePath.split("/").pop() || filePath;
                excelStore2.addRecentFileIfNotDismissed({ path: filePath, filename });
              }
            }
            if (changedFiles2.length > 0) {
              S().addAffectedFiles(msgId, changedFiles2);
              excelStore2.bumpWorkspaceFilesVersion();
              // W7: 自动刷新备份列表
              if (effectiveSessionId) {
                excelStore2.fetchBackups(effectiveSessionId);
              }
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

          case "file_download": {
            const dlFilePath2 = (data.file_path as string) || "";
            const dlFilename2 = (data.filename as string) || dlFilePath2.split("/").pop() || "download";
            const dlDescription2 = (data.description as string) || "";
            if (dlFilePath2) {
              S().appendBlock(msgId, {
                type: "file_download",
                toolCallId: (data.tool_call_id as string) || undefined,
                filePath: dlFilePath2,
                filename: dlFilename2,
                description: dlDescription2,
              });
            }
            break;
          }

          case "mode_changed": {
            const uiMode = useUIStore.getState();
            const modeName = data.mode_name as string;
            const enabled = Boolean(data.enabled);
            if (modeName === "full_access") uiMode.setFullAccessEnabled(enabled);
            else if (modeName === "chat_mode") uiMode.setChatMode(data.value as "write" | "read" | "plan");
            S().appendBlock(msgId, {
              type: "status",
              label: `${enabled ? "已开启" : "已关闭"} ${modeName === "full_access" ? "Full Access" : modeName}`,
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
            const totalTokens = (data.total_tokens as number) || 0;
            if (totalTokens > 0) {
              if (hasPendingInteraction) {
                // 延迟 — 后续的 continuation 将展示累计总数。
                _deferredTokenStats = {
                  promptTokens: (data.prompt_tokens as number) || 0,
                  completionTokens: (data.completion_tokens as number) || 0,
                  totalTokens,
                  iterations: (data.iterations as number) || 0,
                };
              } else {
                // 累加：前次调用的延迟统计 + 剩余 block + 当前
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
            _hadStreamError = true;
            S().setPipelineStatus(null);
            S().appendBlock(msgId, {
              type: "text",
              content: `⚠️ ${(data.error as string) || "发生未知错误"}`,
            });
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
      _hadStreamError = true;
      S().appendBlock(msgId, {
        type: "text",
        content: `⚠️ 连接错误: ${(err as Error).message}`,
      });
    }
  } finally {
    batcher.dispose();
    S().setPipelineStatus(null);
    S().saveCurrentSession();
    S().setStreaming(false);
    S().setAbortController(null);

    if (_hadStreamError && effectiveSessionId) {
      const sid = effectiveSessionId;
      setTimeout(async () => {
        try {
          const { refreshSessionMessagesFromBackend } = await import("@/stores/chat-store");
          const chat = useChatStore.getState();
          if (chat.currentSessionId === sid && !chat.isStreaming && !chat.abortController) {
            await refreshSessionMessagesFromBackend(sid);
          }
        } catch {
          // 静默处理 — SessionSync 轮询最终会恢复
        }
      }, 1500);
    }
  }
}

/**
 * 回退对话到指定用户消息并重新发送（编辑后的内容）。
 * 1. 调用后端 rollback API（resend_mode=true 移除目标用户消息）
 * 2. 截断前端消息列表到目标消息之前
 * 3. 用 sendMessage 重新发送（在前后端各添加一条用户消息）
 */
export async function rollbackAndResend(
  messageId: string,
  newContent: string,
  rollbackFiles: boolean,
  sessionId: string | null,
  files?: File[],
  retainedFiles?: FileAttachment[],
) {
  const store = useChatStore.getState();
  if (store.isStreaming) return;

  // 找到目标用户消息在前端消息列表中的位置
  const messages = store.messages;
  const msgIndex = messages.findIndex((m) => m.id === messageId);
  if (msgIndex === -1) return;

  // 目标必须是 user 消息
  if (messages[msgIndex].role !== "user") return;

  // 计算 turn_index（第几个 user 消息）
  let turnIndex = 0;
  for (let i = 0; i < msgIndex; i++) {
    if (messages[i].role === "user") turnIndex++;
  }

  const effectiveSessionId = sessionId || store.currentSessionId;
  if (!effectiveSessionId) return;

  // 调用后端 rollback API（resend_mode 会移除目标用户消息）
  try {
    const { rollbackChat } = await import("./api");
    await rollbackChat({
      sessionId: effectiveSessionId,
      turnIndex,
      rollbackFiles,
      resendMode: true,
    });
  } catch (err) {
    console.warn("Rollback failed, attempting to resync session:", err);
    // 后端会话可能已过期/重建，尝试刷新前端消息以重新同步
    try {
      const { refreshSessionMessagesFromBackend } = await import("@/stores/chat-store");
      await refreshSessionMessagesFromBackend(effectiveSessionId);
    } catch { /* SessionSync 轮询最终会恢复 */ }
    return;
  }

  // 前端截断到目标用户消息之前（与后端 resend_mode 一致）
  const truncated = messages.slice(0, msgIndex);
  store.setMessages(truncated);

  // 为保留的原有附件创建合成 AttachedFile（已上传，无需重传）
  const retainedAttached: AttachedFile[] = (retainedFiles ?? []).map((f, i) => ({
    id: `retained-${Date.now()}-${i}`,
    file: new File([], f.filename),
    status: "success" as const,
    uploadResult: { filename: f.filename, path: f.path, size: f.size },
  }));

  // 预先上传新文件（与 ChatInput 的 triggerUpload 相同），
  // 确保 sendMessage 收到带有 uploadResult 的正确 AttachedFile 对象。
  let newAttached: AttachedFile[] = [];
  if (files && files.length > 0) {
    const { uploadFile } = await import("./api");
    newAttached = await Promise.all(
      files.map(async (f, i): Promise<AttachedFile> => {
        const id = `resend-${Date.now()}-${i}`;
        try {
          const uploadResult = await uploadFile(f);
          return { id, file: f, status: "success" as const, uploadResult };
        } catch (err) {
          console.error("Edit-resend upload failed:", f.name, err);
          return { id, file: f, status: "failed" as const, error: String(err) };
        }
      }),
    );
  }

  const allAttached = [...retainedAttached, ...newAttached];

  // sendMessage 会在前后端各添加用户消息 + 触发流式回复
  await sendMessage(newContent, allAttached.length > 0 ? allAttached : undefined, effectiveSessionId);
}

/**
 * 重试指定 assistant 消息：回滚到其前一条 user 消息，然后重新发送。
 * 如果指定了 switchToModel，会先切换模型再重新发送。
 */
export async function retryAssistantMessage(
  assistantMessageId: string,
  sessionId: string | null,
  switchToModel?: string,
) {
  const store = useChatStore.getState();
  if (store.isStreaming) return;

  const messages = store.messages;
  const assistantIdx = messages.findIndex((m) => m.id === assistantMessageId);
  if (assistantIdx === -1) return;

  // 找到该 assistant 消息前面最近的 user 消息
  let userIdx = -1;
  for (let i = assistantIdx - 1; i >= 0; i--) {
    if (messages[i].role === "user") {
      userIdx = i;
      break;
    }
  }
  if (userIdx === -1) return;

  const userMessage = messages[userIdx];
  if (userMessage.role !== "user") return;
  const userContent = userMessage.content;

  // 计算 turn_index（第几个 user 消息）
  let turnIndex = 0;
  for (let i = 0; i < userIdx; i++) {
    if (messages[i].role === "user") turnIndex++;
  }

  const effectiveSessionId = sessionId || store.currentSessionId;
  if (!effectiveSessionId) return;

  // 如果需要切换模型，先切换
  if (switchToModel) {
    try {
      const { apiPut } = await import("./api");
      await apiPut("/models/active", { name: switchToModel });
      useUIStore.getState().setCurrentModel(switchToModel);
    } catch (err) {
      console.error("Model switch failed:", err);
      return;
    }
  }

  // 调用后端 rollback API
  try {
    const { rollbackChat } = await import("./api");
    await rollbackChat({
      sessionId: effectiveSessionId,
      turnIndex,
      rollbackFiles: false,
      resendMode: true,
    });
  } catch (err) {
    console.warn("Rollback failed, attempting to resync session:", err);
    try {
      const { refreshSessionMessagesFromBackend } = await import("@/stores/chat-store");
      await refreshSessionMessagesFromBackend(effectiveSessionId);
    } catch { /* SessionSync 轮询最终会恢复 */ }
    return;
  }

  // 前端截断到 user 消息之前
  const truncated = messages.slice(0, userIdx);
  store.setMessages(truncated);

  // 重新发送
  await sendMessage(userContent, undefined, effectiveSessionId);
}

export function stopGeneration() {
  const store = useChatStore.getState();
  if (!store.abortController) return;

  // 1. 通知后端取消服务端任务
  const sessionId = store.currentSessionId;
  if (sessionId) {
    import("./api").then(({ abortChat }) => abortChat(sessionId)).catch(() => {});
  }

  // 2. 中断前端 SSE 连接
  store.abortController.abort();
  store.setAbortController(null);
  store.setStreaming(false);

  // 3. 修补最后一条 assistant 消息：将进行中的 block 标记为失败，
  //    并追加可见的"已停止"指示器。
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

// ---------------------------------------------------------------------------
// 活跃订阅守卫：防止多个并发的 subscribe 连接。
// ---------------------------------------------------------------------------
let _activeSubscribeSessionId: string | null = null;

/**
 * SSE 重连：页面刷新后重新接入正在执行的聊天任务事件流。
 * 复用最后一条 assistant 消息（若存在），不创建新的用户消息。
 *
 * 由 SessionSync 在检测到 in_flight && !hasLocalLiveStream 时调用。
 */
export async function subscribeToSession(sessionId: string) {
  const store = useChatStore.getState();

  // 防止重复的 subscribe 连接
  if (store.abortController) return;
  if (_activeSubscribeSessionId === sessionId) return;

  // 查找最后一条 assistant 消息用于追加事件
  const messages = store.messages;
  let assistantMsgId: string | null = null;
  for (let i = messages.length - 1; i >= 0; i--) {
    if (messages[i].role === "assistant") {
      assistantMsgId = messages[i].id;
      break;
    }
  }
  // 如果尚无 assistant 消息，创建一条
  if (!assistantMsgId) {
    assistantMsgId = uuid();
    store.addAssistantMessage(assistantMsgId);
  }

  const msgId = assistantMsgId;
  _activeSubscribeSessionId = sessionId;

  const abortController = new AbortController();
  store.setAbortController(abortController);
  store.setStreaming(true);
  store.setPipelineStatus({
    stage: "reconnecting",
    message: "正在重连...",
    startedAt: Date.now(),
  });

  const S = () => useChatStore.getState();

  const batcher = new DeltaBatcher((textDelta, thinkingDelta) => {
    if (textDelta) {
      const msg = getLastAssistantMessage(S().messages, msgId);
      const lastBlock = msg?.blocks[msg.blocks.length - 1];
      if (lastBlock && lastBlock.type === "text") {
        S().updateLastBlock(msgId, (b) => {
          if (b.type === "text") {
            return { ...b, content: b.content + textDelta };
          }
          return b;
        });
      } else {
        S().appendBlock(msgId, { type: "text", content: textDelta });
      }
    }
    if (thinkingDelta) {
      S().updateBlockByType(msgId, "thinking", (b) => {
        if (b.type === "thinking") {
          return { ...b, content: b.content + thinkingDelta };
        }
        return b;
      });
    }
  });

  const getLastBlockOfType = (type: string) => {
    const msg = getLastAssistantMessage(S().messages, msgId);
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
        verification: (item.verification as string) || undefined,
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

  let thinkingInProgress = false;

  const finalizeThinking = () => {
    if (!thinkingInProgress) return;
    thinkingInProgress = false;
    batcher.flush();
    S().updateBlockByType(msgId, "thinking", (b) => {
      if (b.type === "thinking" && b.startedAt != null && b.duration == null) {
        return { ...b, duration: (Date.now() - b.startedAt) / 1000 };
      }
      return b;
    });
  };

  try {
    await consumeSSE(
      buildApiUrl("/chat/subscribe", { direct: true }),
      { session_id: sessionId, skip_replay: true },
      (event) => {
        const data = event.data;

        if (event.event !== "thinking_delta" && event.event !== "thinking") {
          finalizeThinking();
        }
        if (event.event !== "text_delta" && event.event !== "thinking_delta") {
          batcher.flush();
        }

        switch (event.event) {
          case "session_init":
            break;

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

          case "pipeline_progress": {
            const stage = (data.stage as string) || "";
            const pipelineMsg = (data.message as string) || "";
            S().setPipelineStatus({
              stage,
              message: pipelineMsg,
              startedAt: Date.now(),
              phaseIndex: typeof data.phase_index === "number" ? data.phase_index : undefined,
              totalPhases: typeof data.total_phases === "number" ? data.total_phases : undefined,
              specPath: (data.spec_path as string) || undefined,
              diff: (data.diff as PipelineStatus["diff"]) ?? undefined,
              checkpoint: (data.checkpoint as Record<string, unknown>) ?? undefined,
              // 批量任务相关字段
              batchIndex: typeof data.batch_index === "number" ? data.batch_index : undefined,
              batchTotal: typeof data.batch_total === "number" ? data.batch_total : undefined,
            });
            break;
          }

          case "batch_progress": {
            // 批量任务进度事件
            const batchIndex = typeof data.batch_index === "number" ? data.batch_index : 0;
            const batchTotal = typeof data.batch_total === "number" ? data.batch_total : 1;
            const batchItemName = (data.batch_item_name as string) || `任务 ${batchIndex + 1}`;
            const batchStatus = (data.batch_status as string) || "running";
            const batchElapsed = typeof data.batch_elapsed_seconds === "number" ? data.batch_elapsed_seconds : 0;
            const batchMsg = (data.message as string) || "";

            S().setBatchProgress({
              batchIndex,
              batchTotal,
              batchItemName,
              batchStatus,
              batchElapsed,
              message: batchMsg,
            });
            break;
          }

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
              batcher.pushThinking((data.content as string) || "");
            } else {
              batcher.flush();
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
            if (!lastBlock || lastBlock.type !== "text") {
              S().appendBlock(msgId, { type: "text", content: "" });
            }
            batcher.pushText((data.content as string) || "");
            break;
          }

          case "tool_call_args_delta": {
            const _adId = (data.tool_call_id as string) || "";
            const _adName = (data.tool_name as string) || "";
            const _adDelta = (data.args_delta as string) || "";
            if (_adId && _adDelta) {
              useExcelStore.getState().appendStreamingArgs(_adId, _adDelta);
              const _adMsg = getLastAssistantMessage(S().messages, msgId);
              const _adHas = _adMsg?.blocks.some(
                (b) => b.type === "tool_call" && b.toolCallId === _adId,
              );
              if (!_adHas && _adName) {
                S().setPipelineStatus(null);
                S().appendBlock(msgId, {
                  type: "tool_call",
                  toolCallId: _adId,
                  name: _adName,
                  args: {},
                  status: "streaming" as "running",
                  iteration: undefined,
                });
              }
            }
            break;
          }

          case "tool_call_start": {
            S().setPipelineStatus(null);
            const toolCallIdRaw = data.tool_call_id;
            const toolCallIdVal = typeof toolCallIdRaw === "string" && toolCallIdRaw.length > 0
              ? toolCallIdRaw
              : undefined;
            const _sMsg = getLastAssistantMessage(S().messages, msgId);
            const _sExists = toolCallIdVal && _sMsg?.blocks.some(
              (b) => b.type === "tool_call" && b.toolCallId === toolCallIdVal && (b.status as string) === "streaming",
            );
            if (_sExists) {
              S().updateToolCallBlock(msgId, toolCallIdVal!, (b) => {
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
                toolCallId: toolCallIdVal,
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
            useExcelStore.getState().addDiff({
              toolCallId: (data.tool_call_id as string) || "",
              filePath: edFilePath,
              sheet: (data.sheet as string) || "",
              affectedRange: (data.affected_range as string) || "",
              changes: _mapDiffChanges(data.changes as unknown[]),
              mergeRanges: Array.isArray(data.merge_ranges) ? data.merge_ranges as MergeRange[] : undefined,
              oldMergeRanges: Array.isArray(data.old_merge_ranges) ? data.old_merge_ranges as MergeRange[] : undefined,
              metadataHints: Array.isArray(data.metadata_hints) ? data.metadata_hints as string[] : undefined,
              timestamp: Date.now(),
            });
            if (edFilePath) {
              const fn = edFilePath.split("/").pop() || edFilePath;
              useExcelStore.getState().addRecentFileIfNotDismissed({ path: edFilePath, filename: fn });
              S().addAffectedFiles(msgId, [edFilePath]);
            }
            break;
          }

          case "text_diff": {
            const tdFilePath3 = (data.file_path as string) || "";
            useExcelStore.getState().addTextDiff({
              toolCallId: (data.tool_call_id as string) || "",
              filePath: tdFilePath3,
              hunks: (data.hunks as string[]) || [],
              additions: (data.additions as number) || 0,
              deletions: (data.deletions as number) || 0,
              truncated: !!data.truncated,
              timestamp: Date.now(),
            });
            if (tdFilePath3) {
              S().addAffectedFiles(msgId, [tdFilePath3]);
            }
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
              // W7: 自动刷新备份列表
              if (sessionId) {
                excelStore.fetchBackups(sessionId);
              }
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

          case "mode_changed": {
            const uiMode = useUIStore.getState();
            const modeName = data.mode_name as string;
            const enabled = Boolean(data.enabled);
            if (modeName === "full_access") uiMode.setFullAccessEnabled(enabled);
            else if (modeName === "chat_mode") uiMode.setChatMode(data.value as "write" | "read" | "plan");
            S().appendBlock(msgId, {
              type: "status",
              label: `${enabled ? "已开启" : "已关闭"} ${modeName === "full_access" ? "Full Access" : modeName}`,
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
            const totalTokens = (data.total_tokens as number) || 0;
            if (totalTokens > 0 && !hasPendingInteraction) {
              S().appendBlock(msgId, {
                type: "token_stats",
                promptTokens: (data.prompt_tokens as number) || 0,
                completionTokens: (data.completion_tokens as number) || 0,
                totalTokens,
                iterations: (data.iterations as number) || 0,
              });
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
        content: `⚠️ 重连错误: ${(err as Error).message}`,
      });
    }
  } finally {
    _activeSubscribeSessionId = null;
    batcher.dispose();
    S().setPipelineStatus(null);
    S().saveCurrentSession();
    S().setStreaming(false);
    S().setAbortController(null);

    // 最终一致性：从后端加载权威消息，确保前端与后端状态完全同步。
    // 延迟执行以确保后端 release_for_chat 已完成消息持久化。
    const sid = sessionId;
    setTimeout(async () => {
      try {
        const { refreshSessionMessagesFromBackend } = await import("@/stores/chat-store");
        const chat = useChatStore.getState();
        if (chat.currentSessionId === sid && !chat.isStreaming && !chat.abortController) {
          await refreshSessionMessagesFromBackend(sid);
        }
      } catch {
        // 静默处理 — SessionSync 轮询最终会恢复
      }
    }, 1500);
  }
}

function getLastAssistantMessage(messages: ReturnType<typeof useChatStore.getState>["messages"], id: string) {
  const msg = messages.find((m) => m.id === id);
  if (msg && msg.role === "assistant") return msg;
  return null;
}
