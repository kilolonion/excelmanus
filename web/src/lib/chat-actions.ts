import { consumeSSE, SSEError } from "./sse";
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

/** 在客户端本地构造 failure_guidance block（用于网络级错误，无 SSE 事件可达的场景）。 */
function _buildClientFailureGuidance(opts: {
  category?: "model" | "transport" | "config" | "quota" | "unknown";
  code: string;
  title: string;
  message: string;
  retryable: boolean;
  stage?: string;
}): Extract<AssistantBlock, { type: "failure_guidance" }> {
  const category = opts.category || "transport";
  return {
    type: "failure_guidance",
    category,
    code: opts.code,
    title: opts.title,
    message: opts.message,
    stage: opts.stage || "connecting",
    retryable: opts.retryable,
    diagnosticId: crypto.randomUUID?.() || uuid(),
    actions: opts.retryable
      ? [
          { type: "retry", label: "立即重试" },
          { type: "open_settings", label: "检查模型设置" },
        ]
      : [{ type: "open_settings", label: "检查模型设置" }],
  };
}

/** 根据 SSEError 的 HTTP 状态码生成对应的 failure_guidance block。 */
function _classifySSEError(err: SSEError): Extract<AssistantBlock, { type: "failure_guidance" }> {
  const status = err.statusCode;
  if (status === 401 || status === 403) {
    return _buildClientFailureGuidance({
      category: "model",
      code: "model_auth_failed",
      title: "认证失败",
      message: status === 401
        ? "API Key 无效或已过期，请在设置中检查模型配置。"
        : "无权访问该模型服务，请检查 API Key 权限。",
      retryable: false,
    });
  }
  if (status === 402) {
    return _buildClientFailureGuidance({
      category: "quota",
      code: "quota_exceeded",
      title: "额度不足",
      message: "模型 API 额度已用完，请充值后重试。",
      retryable: false,
    });
  }
  if (status === 404) {
    return _buildClientFailureGuidance({
      category: "model",
      code: "model_not_found",
      title: "模型不存在",
      message: "请求的模型不存在或已下线，请在设置中选择其他模型。",
      retryable: false,
    });
  }
  if (status === 409) {
    return _buildClientFailureGuidance({
      category: "transport",
      code: "session_busy",
      title: "会话忙碌",
      message: "当前会话正在处理另一个请求，请等待完成后再试。",
      retryable: true,
    });
  }
  if (status === 429) {
    return _buildClientFailureGuidance({
      category: "quota",
      code: "rate_limited",
      title: "请求频率受限",
      message: "API 调用频率超限，请稍后重试。",
      retryable: true,
    });
  }
  if (status >= 500) {
    return _buildClientFailureGuidance({
      category: "model",
      code: "provider_internal_error",
      title: "模型服务异常",
      message: `模型服务返回 ${status} 错误，请稍后重试。`,
      retryable: true,
    });
  }
  if (status === 0) {
    return _buildClientFailureGuidance({
      category: "transport",
      code: "stream_interrupted",
      title: "传输中断",
      message: err.message || "与服务端的连接意外断开",
      retryable: true,
    });
  }
  return _buildClientFailureGuidance({
    code: "http_error",
    title: "请求错误",
    message: err.message || `HTTP ${status}`,
    retryable: status >= 500,
  });
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

  // 空消息前置校验：无文本且无附件时直接拦截
  if (!text.trim() && (!files || files.length === 0)) {
    return;
  }

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
      }
      // 跳过上传失败的文件，避免空路径进入消息
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

      // 将上传成功的图片编码为 base64 用于 LLM 多模态载荷。
      // 仅在上传成功时发送 base64，避免模型"看到图"但附件状态不一致。
      // 跳过空 File 对象（保留附件使用 new File([], name) 创建，无实际内容）
      if (isImage && af.file.size > 0 && af.status === "success") {
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
        }
      },
      abortController.signal
    );
  } catch (err) {
    if ((err as Error).name !== "AbortError") {
      sseCtx.hadStreamError = true;
      if (err instanceof SSEError) {
        S().appendBlock(assistantMsgId, _classifySSEError(err));
      } else {
        S().appendBlock(assistantMsgId, _buildClientFailureGuidance({
          code: "network_error",
          title: "连接错误",
          message: (err as Error).message || "网络连接失败",
          retryable: true,
        }));
      }
    } else if (_connectionTimedOut) {
      sseCtx.hadStreamError = true;
      S().appendBlock(assistantMsgId, _buildClientFailureGuidance({
        code: "connect_timeout",
        title: "连接超时",
        message: "未能在 30 秒内建立连接，请检查模型配置或网络。",
        retryable: true,
      }));
    } else if (_stallTimedOut) {
      sseCtx.hadStreamError = true;
      S().appendBlock(assistantMsgId, _buildClientFailureGuidance({
        code: "stream_stalled",
        title: "响应停滞",
        message: "已连接但超过 90 秒未收到新数据，模型服务可能挂起。",
        retryable: true,
      }));
    }
  } finally {
    if (_stallTimer !== null) clearTimeout(_stallTimer);
    _stallTimer = null;
    try { batcher.dispose(); } catch (e) { console.error("[sendMessage] batcher dispose error:", e); }
    S().setPipelineStatus(null);
    try { S().saveCurrentSession(); } catch (e) { console.error("[sendMessage] save session error:", e); }
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
  let _contConnectionTimedOut = false;
  let _contStallTimedOut = false;
  let _contStallTimer: ReturnType<typeof setTimeout> | null = setTimeout(() => {
    _contConnectionTimedOut = true;
    abortController.abort();
  }, 30_000);
  const _resetContStall = () => {
    if (_contStallTimer !== null) clearTimeout(_contStallTimer);
    _contStallTimer = setTimeout(() => {
      _contStallTimedOut = true;
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
      if (err instanceof SSEError) {
        S().appendBlock(msgId, _classifySSEError(err));
      } else {
        S().appendBlock(msgId, _buildClientFailureGuidance({
          code: "network_error",
          title: "连接错误",
          message: (err as Error).message || "网络连接失败",
          retryable: true,
        }));
      }
    } else if (_contConnectionTimedOut) {
      sseCtx.hadStreamError = true;
      S().appendBlock(msgId, _buildClientFailureGuidance({
        code: "connect_timeout",
        title: "连接超时",
        message: "未能在 30 秒内建立连接，请检查模型配置或网络。",
        retryable: true,
      }));
    } else if (_contStallTimedOut) {
      sseCtx.hadStreamError = true;
      S().appendBlock(msgId, _buildClientFailureGuidance({
        code: "stream_stalled",
        title: "响应停滞",
        message: "已连接但超过 90 秒未收到新数据，模型服务可能挂起。",
        retryable: true,
      }));
    }
  } finally {
    if (_contStallTimer !== null) clearTimeout(_contStallTimer);
    _contStallTimer = null;
    try { batcher.dispose(); } catch (e) { console.error("[sendContinuation] batcher dispose error:", e); }
    S().setPipelineStatus(null);
    try { S().saveCurrentSession(); } catch (e) { console.error("[sendContinuation] save session error:", e); }
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
    // 向用户展示错误，而非静默失败
    const lastAssistant = [...messages].reverse().find((m) => m.role === "assistant");
    if (lastAssistant && lastAssistant.role === "assistant") {
      store.appendBlock(lastAssistant.id, _buildClientFailureGuidance({
        code: "network_error",
        title: "编辑重发失败",
        message: "回退对话时出错，请稍后重试或刷新页面。",
        retryable: true,
      }));
    }
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
  // 对于图片附件，需要从工作区重新下载内容，以便 sendMessage 能编码 base64 发给 LLM
  let retainedAttached: AttachedFile[] | undefined;
  if (userMessage.role === "user" && userMessage.files && userMessage.files.length > 0) {
    const { fetchFileBlob } = await import("./api");
    retainedAttached = await Promise.all(
      userMessage.files.map(async (f, i): Promise<AttachedFile> => {
        const id = `retry-retained-${Date.now()}-${i}`;
        const isImage = _isImageFile(f.filename);

        if (isImage) {
          // 图片需要重新获取内容，否则 sendMessage 因 file.size===0 跳过 base64 编码
          try {
            const blob = await fetchFileBlob(f.path, effectiveSessionId ?? undefined);
            const file = new File([blob], f.filename, { type: blob.type || "image/png" });
            return { id, file, status: "success" as const, uploadResult: { filename: f.filename, path: f.path, size: f.size } };
          } catch (err) {
            console.warn("[retryAssistantMessage] 图片重新获取失败，回退到路径通知:", f.path, err);
          }
        }

        return { id, file: new File([], f.filename), status: "success" as const, uploadResult: { filename: f.filename, path: f.path, size: f.size } };
      })
    );
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

  // 流停滞检测（与 sendMessage 一致）
  let _subConnectionTimedOut = false;
  let _subStallTimedOut = false;
  let _subStallTimer: ReturnType<typeof setTimeout> | null = setTimeout(() => {
    _subConnectionTimedOut = true;
    abortController.abort();
  }, 30_000);
  const _resetSubStall = () => {
    if (_subStallTimer !== null) clearTimeout(_subStallTimer);
    _subStallTimer = setTimeout(() => {
      _subStallTimedOut = true;
      abortController.abort();
    }, 90_000);
  };

  try {
    await consumeSSE(
      buildApiUrl("/chat/subscribe", { direct: true }),
      { session_id: sessionId, skip_replay: true },
      (event) => {
        // 收到事件，重置停滞检测计时器
        _resetSubStall();

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
      sseCtx.hadStreamError = true;
      if (err instanceof SSEError) {
        S().appendBlock(msgId, _classifySSEError(err));
      } else {
        S().appendBlock(msgId, _buildClientFailureGuidance({
          code: "network_error",
          title: "重连错误",
          message: (err as Error).message || "重连失败",
          retryable: true,
        }));
      }
    } else if (_subConnectionTimedOut) {
      sseCtx.hadStreamError = true;
      S().appendBlock(msgId, _buildClientFailureGuidance({
        code: "connect_timeout",
        title: "重连超时",
        message: "未能在 30 秒内重新连接，请检查网络或刷新页面。",
        retryable: true,
      }));
    } else if (_subStallTimedOut) {
      sseCtx.hadStreamError = true;
      S().appendBlock(msgId, _buildClientFailureGuidance({
        code: "stream_stalled",
        title: "响应停滞",
        message: "已连接但超过 90 秒未收到新数据，模型服务可能挂起。",
        retryable: true,
      }));
    }
  } finally {
    if (_subStallTimer !== null) clearTimeout(_subStallTimer);
    _subStallTimer = null;
    _activeSubscribeSessionId = null;
    try { batcher.dispose(); } catch (e) { console.error("[subscribeToSession] batcher dispose error:", e); }
    S().setPipelineStatus(null);
    S().saveCurrentSession();
    S().setStreaming(false);
    S().setAbortController(null);

    // subscribe 始终需要从后端刷新：可能连接中途丢失事件或缓冲区溢出
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
