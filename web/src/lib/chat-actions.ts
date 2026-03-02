import { consumeSSE } from "./sse";
import { buildApiUrl, fetchWorkspaceStorage } from "./api";
import { uuid } from "@/lib/utils";
import { useChatStore, type PipelineStatus } from "@/stores/chat-store";
import { useSessionStore } from "@/stores/session-store";
import { useAuthStore } from "@/stores/auth-store";
import { useUIStore } from "@/stores/ui-store";
import { useExcelStore, type ExcelCellDiff, type ExcelPreviewData, type MergeRange } from "@/stores/excel-store";
import type { AssistantBlock, TaskItem, AttachedFile, FileAttachment } from "@/lib/types";
import {
  dispatchSSEEvent,
  preDispatch,
  finalizeThinking,
  getLastAssistantMessage,
  _friendlyRouteMode,
  _mapDiffChanges,
  type SSEHandlerContext,
  type SSEEvent,
  type DeltaBatcher as DeltaBatcherInterface,
} from "./sse-event-handler";

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
  sessionId?: string | null,
) {
  const store = useChatStore.getState();
  const sessionStore = useSessionStore.getState();
  const uiState = useUIStore.getState();

  if (store.isStreaming) return;

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

  // 尽早创建 AbortController 并设置流式状态 - 在任何异步操作之前。
  // SessionSync 的 useEffect 通过检查 abortController 来决定是否调用 switchSession()。
  // 如果延迟到异步操作之后，SessionSync 的 effect 可能在 await 间隙触发，
  // 发现 abortController===null 后调用 switchSession，清空 addUserMessage 即将创建的消息。
  const abortController = new AbortController();
  store.setAbortController(abortController);
  store.setStreaming(true);
  store.setPipelineStatus({
    stage: "connecting",
    message: "正在连接...",
    startedAt: Date.now(),
  });

  // 清除上次可能残留的延迟 token 统计，避免跨会话泄漏
  _deferredTokenStats = null;

  // ── 乐观 UI：在任何 await 之前立即展示用户消息气泡 + 助手加载状态 ──

  // 同步收集文件上传结果（仅读取 ChatInput 预上传的结果，无需 await）
  const fileUploadResults: { filename: string; path: string; size: number }[] = [];
  if (files && files.length > 0) {
    for (const af of files) {
      if (af.status === "success" && af.uploadResult) {
        fileUploadResults.push(af.uploadResult);
      } else {
        fileUploadResults.push({ filename: af.file.name, path: "", size: af.file.size });
      }
    }
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

  // 立即添加用户消息和助手占位消息 — 用户瞬间看到自己的消息气泡 + "正在连接" 加载状态
  const userMsgId = uuid();
  store.addUserMessage(
    userMsgId,
    text,
    fileUploadResults.length > 0 ? fileUploadResults : undefined
  );

  const assistantMsgId = uuid();
  store.addAssistantMessage(assistantMsgId);

  // ── 以下为异步操作，消息气泡已可见 ──

  // 工作区配额前置检查：超限时直接在聊天中显示提示，避免 SSE 往返
  try {
    const wsStorage = await fetchWorkspaceStorage();
    if (wsStorage && (wsStorage.over_files || wsStorage.over_size)) {
      const parts: string[] = [];
      if (wsStorage.over_files) parts.push(`文件数 ${wsStorage.file_count}/${wsStorage.max_files}`);
      if (wsStorage.over_size) parts.push(`存储 ${wsStorage.size_mb.toFixed(1)} MB/${wsStorage.max_size_mb.toFixed(1)} MB`);
      store.appendBlock(assistantMsgId, {
        type: "text",
        content:
          `⚠️ **工作区已满**（${parts.join("、")}），无法继续对话。\n\n` +
          "请先清理工作区文件后再试：\n" +
          "- 在左侧文件列表中删除不需要的文件\n" +
          "- 或联系管理员调整配额",
      });
      store.saveCurrentSession();
      store.setStreaming(false);
      store.setAbortController(null);
      store.setPipelineStatus(null);
      return;
    }
  } catch {
    // 配额检查失败不阻断发送，交由后端兜底
  }

  // 文件已由 ChatInput 预先上传。这里收集路径和 base64 数据用于 SSE 载荷。
  const uploadedDocPaths: string[] = [];
  const uploadedImagePaths: string[] = [];
  const imageAttachments: { data: string; media_type: string }[] = [];
  if (files && files.length > 0) {
    for (const af of files) {
      const isImage = _isImageLike(af.file);

      // 将图片编码为 base64 用于 LLM 多模态载荷。
      // 即使文件上传失败，也能确保 agent 可以"看到"图片。
      // 跳过空 File 对象（保留附件使用 new File([], name) 创建，无实际内容）
      if (isImage && af.file.size > 0) {
        try {
          // 优先使用预编码的 base64（示例卡片预上传时已生成）
          const b64 = af.cachedBase64 ?? await _fileToBase64(af.file);
          imageAttachments.push({
            data: b64,
            media_type: af.file.type || "image/png",
          });
        } catch (b64Err) {
          console.error("Base64 encoding failed for image:", af.file.name, b64Err);
        }
      }

      // 收集上传成功的路径用于 agent 通知
      if (af.status === "success" && af.uploadResult) {
        if (isImage) {
          uploadedImagePaths.push(af.uploadResult.path);
        } else {
          uploadedDocPaths.push(af.uploadResult.path);
        }
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

  // ── SSE 事件处理上下文 ──
  const sseCtx: SSEHandlerContext = {
    assistantMsgId,
    batcher: batcher as unknown as DeltaBatcherInterface,
    effectiveSessionId: effectiveSessionId || "",
    isFirstSend: true,
    userText: text,
    thinkingInProgress: false,
    hadStreamError: false,
  };

  // 流停滞检测：初始连接 30 秒超时，之后每次收到事件重置为 90 秒。
  // 如果中途 LLM 或后端处理挂起超过 90 秒无任何事件，自动中止。
  let _connectionTimedOut = false;
  let _stallTimedOut = false;
  const _INITIAL_TIMEOUT_MS = 30_000;
  const _STALL_TIMEOUT_MS = 90_000;
  let _stallTimer: ReturnType<typeof setTimeout> | null = setTimeout(() => {
    _connectionTimedOut = true;
    abortController.abort();
  }, _INITIAL_TIMEOUT_MS);
  const _resetStallTimer = () => {
    if (_stallTimer !== null) clearTimeout(_stallTimer);
    _stallTimer = setTimeout(() => {
      _stallTimedOut = true;
      abortController.abort();
    }, _STALL_TIMEOUT_MS);
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
        // 收到事件，重置停滞检测计时器
        _resetStallTimer();

        const sseEvent = event as SSEEvent;
        preDispatch(sseEvent, sseCtx);
        dispatchSSEEvent(sseEvent, sseCtx);

        // ── sendMessage 独有的后分发逻辑 ──
        const data = event.data;

        if (sseEvent.event === "reply") {
          // Token 统计：sendMessage 有延迟累加逻辑
          const hasPendingInteraction =
            S().pendingApproval !== null || S().pendingQuestion !== null;
          const totalTokens = (data.total_tokens as number) || 0;
          if (totalTokens > 0) {
            if (hasPendingInteraction) {
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
        } else if (sseEvent.event === "error") {
          // sendMessage 有更丰富的错误分类（模型配置检测）
          const errMsg = (data.error as string) || "发生未知错误";
          if (data.error_code === "workspace_full") {
            S().appendBlock(assistantMsgId, { type: "text", content: errMsg });
          } else {
            const errLower = errMsg.toLowerCase();
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
          }
        }
      },
      abortController.signal
    );
  } catch (err) {
    if ((err as Error).name !== "AbortError") {
      sseCtx.hadStreamError = true;
      S().appendBlock(assistantMsgId, {
        type: "text",
        content: `⚠️ 连接错误: ${(err as Error).message}`,
      });
    } else if (_connectionTimedOut) {
      sseCtx.hadStreamError = true;
      S().appendBlock(assistantMsgId, {
        type: "text",
        content:
          "⚠️ **连接超时**\n\n" +
          "未能在 30 秒内与服务端建立连接，可能的原因：\n" +
          "- 模型 API 配置错误（API Key、Base URL 或模型名称）\n" +
          "- 后端服务未启动或网络不可达\n\n" +
          "> 请检查右上角 ⚙️ 设置中的模型配置，确认后重试。",
      });
    } else if (_stallTimedOut) {
      sseCtx.hadStreamError = true;
      S().appendBlock(assistantMsgId, {
        type: "text",
        content:
          "⚠️ **响应停滞**\n\n" +
          "已连接但超过 90 秒未收到新数据，可能的原因：\n" +
          "- 模型 API 服务响应缓慢或挂起\n" +
          "- 后端处理遇到阻塞\n\n" +
          "> 请稍后重试，或检查模型服务状态。",
      });
    }
  } finally {
    if (_stallTimer !== null) clearTimeout(_stallTimer);
    _stallTimer = null;
    batcher.dispose();
    S().setPipelineStatus(null);
    S().saveCurrentSession();
    S().setStreaming(false);
    S().setAbortController(null);

    if (sseCtx.hadStreamError && effectiveSessionId) {
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
 * 发送延续消息（审批/问答回复），复用最后一条 assistant 消息。
 * 不创建 user/assistant 气泡，绿线不会断开。
 */
export async function sendContinuation(
  text: string,
  sessionId?: string | null,
) {
  const store = useChatStore.getState();
  if (store.isStreaming) return;

  const messages = store.messages;
  let assistantMsgId: string | null = null;
  for (let i = messages.length - 1; i >= 0; i--) {
    if (messages[i].role === "assistant") {
      assistantMsgId = messages[i].id;
      break;
    }
  }
  if (!assistantMsgId) {
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
  const msgId = assistantMsgId;

  const batcher = new DeltaBatcher((textDelta, thinkingDelta) => {
    if (textDelta) {
      const msg = getLastAssistantMessage(S().messages, msgId);
      const lastBlock = msg?.blocks[msg.blocks.length - 1];
      if (lastBlock && lastBlock.type === "text") {
        S().updateLastBlock(msgId, (b) => {
          if (b.type === "text") return { ...b, content: b.content + textDelta };
          return b;
        });
      } else {
        S().appendBlock(msgId, { type: "text", content: textDelta });
      }
    }
    if (thinkingDelta) {
      S().updateBlockByType(msgId, "thinking", (b) => {
        if (b.type === "thinking") return { ...b, content: b.content + thinkingDelta };
        return b;
      });
    }
  });

  const sseCtx: SSEHandlerContext = {
    assistantMsgId: msgId,
    batcher: batcher as unknown as DeltaBatcherInterface,
    effectiveSessionId: effectiveSessionId || "",
    isFirstSend: false,
    thinkingInProgress: false,
    hadStreamError: false,
  };

  // 流停滞检测（与 sendMessage 一致）
  let _contStallTimer: ReturnType<typeof setTimeout> | null = setTimeout(() => {
    abortController.abort();
  }, 30_000);
  const _resetContStall = () => {
    if (_contStallTimer !== null) clearTimeout(_contStallTimer);
    _contStallTimer = setTimeout(() => {
      abortController.abort();
    }, 90_000);
  };

  try {
    await consumeSSE(
      buildApiUrl("/chat/stream", { direct: true }),
      { message: text, session_id: effectiveSessionId },
      (event) => {
        _resetContStall();
        const sseEvent = event as SSEEvent;
        preDispatch(sseEvent, sseCtx);
        dispatchSSEEvent(sseEvent, sseCtx);

        // ── sendContinuation 独有：reply 的 token 累加逻辑 ──
        if (sseEvent.event === "reply") {
          const data = event.data;
          const hasPendingInteraction =
            S().pendingApproval !== null || S().pendingQuestion !== null;
          const totalTokens = (data.total_tokens as number) || 0;
          if (totalTokens > 0) {
            if (hasPendingInteraction) {
              _deferredTokenStats = {
                promptTokens: (data.prompt_tokens as number) || 0,
                completionTokens: (data.completion_tokens as number) || 0,
                totalTokens,
                iterations: (data.iterations as number) || 0,
              };
            } else {
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
        }
      },
      abortController.signal,
    );
  } catch (err) {
    if ((err as Error).name !== "AbortError") {
      sseCtx.hadStreamError = true;
      S().appendBlock(msgId, {
        type: "text",
        content: `⚠️ 连接错误: ${(err as Error).message}`,
      });
    }
  } finally {
    if (_contStallTimer !== null) clearTimeout(_contStallTimer);
    _contStallTimer = null;
    batcher.dispose();
    S().setPipelineStatus(null);
    S().saveCurrentSession();
    S().setStreaming(false);
    S().setAbortController(null);

    if (sseCtx.hadStreamError && effectiveSessionId) {
      const sid = effectiveSessionId;
      setTimeout(async () => {
        try {
          const { refreshSessionMessagesFromBackend } = await import("@/stores/chat-store");
          const chat = useChatStore.getState();
          if (chat.currentSessionId === sid && !chat.isStreaming && !chat.abortController) {
            await refreshSessionMessagesFromBackend(sid);
          }
        } catch {
          // 静默处理
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
  rollbackFiles?: boolean,
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
      rollbackFiles: rollbackFiles ?? false,
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

  // 保留原始用户消息的文件附件（已上传，无需重传）
  let retainedAttached: AttachedFile[] | undefined;
  if (userMessage.role === "user" && userMessage.files && userMessage.files.length > 0) {
    retainedAttached = userMessage.files.map((f, i) => ({
      id: `retry-retained-${Date.now()}-${i}`,
      file: new File([], f.filename),
      status: "success" as const,
      uploadResult: { filename: f.filename, path: f.path, size: f.size },
    }));
  }

  // 重新发送（携带原始附件信息）
  await sendMessage(userContent, retainedAttached, effectiveSessionId);
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

  if (store.abortController) return;
  if (_activeSubscribeSessionId === sessionId) return;

  const messages = store.messages;
  let assistantMsgId: string | null = null;
  for (let i = messages.length - 1; i >= 0; i--) {
    if (messages[i].role === "assistant") {
      assistantMsgId = messages[i].id;
      break;
    }
  }
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
          if (b.type === "text") return { ...b, content: b.content + textDelta };
          return b;
        });
      } else {
        S().appendBlock(msgId, { type: "text", content: textDelta });
      }
    }
    if (thinkingDelta) {
      S().updateBlockByType(msgId, "thinking", (b) => {
        if (b.type === "thinking") return { ...b, content: b.content + thinkingDelta };
        return b;
      });
    }
  });

  const sseCtx: SSEHandlerContext = {
    assistantMsgId: msgId,
    batcher: batcher as unknown as DeltaBatcherInterface,
    effectiveSessionId: sessionId,
    isFirstSend: false,
    thinkingInProgress: false,
    hadStreamError: false,
  };

  try {
    await consumeSSE(
      buildApiUrl("/chat/subscribe", { direct: true }),
      { session_id: sessionId, skip_replay: true },
      (event) => {
        const sseEvent = event as SSEEvent;
        preDispatch(sseEvent, sseCtx);
        dispatchSSEEvent(sseEvent, sseCtx);

        // ── subscribe 独有：reply 的简单 token 统计 ──
        if (sseEvent.event === "reply") {
          const data = event.data;
          const hasPendingInteraction =
            S().pendingApproval !== null || S().pendingQuestion !== null;
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
