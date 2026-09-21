import { consumeSSE, SSEError } from "./sse";
import { apiPost, buildApiUrl } from "./api";
import { mapWithConcurrency } from "./concurrency";
import { uuid } from "@/lib/utils";
import { isVisionImageFile, isVisionImageUpload } from "@/lib/file-kind";
import { useChatStore, type PipelineStatus } from "@/stores/chat-store";
import { useSessionStore, getActiveSessionId } from "@/stores/session-store";
import { useUIStore } from "@/stores/ui-store";
import { useJevStore } from "@/stores/jev-store";
import { useExcelStore, type ExcelCellDiff, type ExcelPreviewData, type MergeRange } from "@/stores/excel-store";
import type { AssistantBlock, TaskItem, AttachedFile, FileAttachment } from "@/lib/types";
import { formatUploadNotice } from "./upload-notice";
import {
  dispatchSSEEvent,
  preDispatch,
  finalizeThinking,
  getLastAssistantMessage,
  type SSEHandlerContext,
  type SSEEvent,
  type DeltaBatcher as DeltaBatcherInterface,
} from "./sse-event-handler";
import { resolveFailureActions } from "./failure-recovery";
import { buildJevSheetContext } from "./jev-context";

type ChatImagePayload = {
  media_type: string;
  attachment_id: string;
  name?: string;
};

function workspaceIdForSession(sessionId?: string | null): string | undefined {
  if (!sessionId) return undefined;
  return useSessionStore.getState().sessions.find((item) => item.id === sessionId)?.workspaceId ?? undefined;
}

/**
 * A failed first request may never have reached the engine, so there is no
 * server-side user turn for rollback to remove.  The rollback endpoint then
 * returns 400/404 even though the session itself is still usable.  Treat only
 * these structural "target is missing" errors as a safe direct-resend case;
 * a transient/network rollback failure must keep the failed turn visible so we
 * do not risk duplicating a turn whose durable state is unknown.
 */
function isMissingRollbackTargetError(error: unknown): boolean {
  const message = error instanceof Error ? error.message : String(error ?? "");
  return /(?:API error:\s*)?(?:400|404)\b|不存在|超出范围|用户轮次索引/i.test(message);
}

async function _admitChatImage(
  data: string,
  media_type: string,
  name?: string,
): Promise<ChatImagePayload> {
  const res = await apiPost<{ attachment?: { attachmentId?: string; mediaType?: string } }>(
    "/attachments",
    { data, media_type, name },
    { direct: true, timeoutMs: 120_000 },
  );
  const attachmentId = res.attachment?.attachmentId;
  if (!attachmentId) {
    throw new Error("图片准入接口没有返回 attachmentId");
  }
  return {
    attachment_id: attachmentId,
    media_type: res.attachment?.mediaType || media_type,
    name,
  };
}

/** 在客户端本地构建 failure_guidance block（用于网络级错误，无 SSE 事件可达的场景）。*/
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
    actions: resolveFailureActions({ code: opts.code, retryable: opts.retryable }),
  };
}

/** 根据 SSEError 的 HTTP 状态码生成对应的 failure_guidance block。 */
function _classifySSEError(err: SSEError): Extract<AssistantBlock, { type: "failure_guidance" }> {
  const status = err.statusCode;
  if (status === 401 || status === 403) {
    return _buildClientFailureGuidance({
      category: "model",
      code: "model_auth_failed",
      title: "模型认证失败",
      message: status === 401
        ? "API Key 无效或已过期，请在模型设置中更新后重试。"
        : "无权访问该模型服务，请检查 API Key 权限后重试。",
      retryable: false,
    });
  }
  if (status === 402) {
    return _buildClientFailureGuidance({
      category: "quota",
      code: "quota_exceeded",
      title: "额度不足",
      message: "模型 API 额度已用尽，请充值后重试。",
      retryable: false,
    });
  }
  if (status === 404) {
    return _buildClientFailureGuidance({
      category: "model",
      code: "model_not_found",
      title: "模型不存在",
      message: "请求的模型不存在或已下线，请更换模型后重试。",
      retryable: false,
    });
  }
  if (status === 409) {
    return _buildClientFailureGuidance({
      category: "transport",
      code: "session_busy",
      title: "会话忙碌",
      message: "当前会话正在处理另一个请求，请稍后重试。",
      retryable: true,
    });
  }
  if (status === 429) {
    return _buildClientFailureGuidance({
      category: "quota",
      code: "rate_limited",
      title: "请求频率受限",
      message: "模型 API 调用频率超限，请稍后重试。",
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
      title: "连接中断",
      message: err.message || "与后端的连接已中断，请重试。",
      retryable: true,
    });
  }
  return _buildClientFailureGuidance({
    code: "http_error",
    title: "请求失败",
    message: err.message || `HTTP ${status}`,
    retryable: status >= 500,
  });
}

function _fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      // result 格式为 "data:<mime>;base64,<data>" — 只提取 base64 部分
      const result = reader.result as string;
      const idx = result.indexOf(",");
      resolve(idx >= 0 ? result.slice(idx + 1) : result);
    };
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

// ---------------------------------------------------------------------------
// 基于 RAF 的增量特性处理器：缓存高频率 text_delta / thinking_delta 事件，
// 每个动画帧最多刷新一次，避免过度重复渲染。
// 改进：增加独立刷新机制，确保非特性事件不会与缓存的特性事件产生时序问题。
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
    // 先刷新剩余内容到 store，防止断开连接时丢失部分 delta
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
    if (text.length > 0 || thinking.length > 0) {
      try {
        this._onFlush(text, thinking);
      } catch (error) {
        console.error("[DeltaBatcher] flush error:", error);
      }
    }
  }
}

function applyTextDelta(messageId: string, textDelta: string) {
  if (textDelta.length === 0) return;
  const store = useChatStore.getState();
  const msg = getLastAssistantMessage(store.messages, messageId);
  const lastText = msg
    ? [...msg.blocks].reverse().find((b) => b.type === "text")
    : undefined;
  if (lastText) {
    store.updateBlockByType(messageId, "text", (b) => {
      if (b.type === "text") return { ...b, content: b.content + textDelta };
      return b;
    });
    return;
  }
  store.appendBlock(messageId, { type: "text", content: textDelta });
}

function applyThinkingDelta(messageId: string, thinkingDelta: string) {
  if (thinkingDelta.length === 0) return;
  useChatStore.getState().updateBlockByType(messageId, "thinking", (b) => {
    if (b.type === "thinking") return { ...b, content: b.content + thinkingDelta };
    return b;
  });
}

function makeDeltaBatcher(messageId: string) {
  return new DeltaBatcher((textDelta, thinkingDelta) => {
    if (textDelta.length > 0) applyTextDelta(messageId, textDelta);
    if (thinkingDelta.length > 0) applyThinkingDelta(messageId, thinkingDelta);
  });
}

function scheduleSessionResync(sessionId: string, delayMs: number) {
  setTimeout(async () => {
    try {
      const { refreshSessionMessagesFromBackend } = await import("@/stores/chat-store");
      const chat = useChatStore.getState();
      if (chat.loadedSessionId === sessionId && !chat.isStreaming && !chat.abortController) {
        await refreshSessionMessagesFromBackend(sessionId);
      }
    } catch {
      // SessionSync 轮询最终会恢复
    }
  }, delayMs);
}

function shouldResyncAfterStream(ctx: SSEHandlerContext, assistantMsgId: string): boolean {
  if (ctx.hadStreamError) return true;
  const lastAssistant = getLastAssistantMessage(useChatStore.getState().messages, assistantMsgId);
  const hasVisibleOutput = lastAssistant?.blocks.some((b) =>
    (b.type === "text" && Boolean(b.content.trim()))
    || b.type === "tool_call"
    || b.type === "subagent"
    || b.type === "failure_guidance"
  );
  if (!hasVisibleOutput) return true;
  if (!ctx.hadPersistedToolWork) return false;
  return !lastAssistant?.blocks.some((b) => b.type === "tool_call" || b.type === "subagent");
}

// 因延迟处理交互（askuser / approval）而累积的 Token 统计。
// sendContinuation 会将这些细节到最终统计中。
let _deferredTokenStats: {
  promptTokens: number;
  completionTokens: number;
  totalTokens: number;
  iterations: number;
} | null = null;

export function resumeAfterInteraction(sessionId: string, response: { resume_required?: boolean }) {
  if (!response.resume_required || getActiveSessionId() !== sessionId || useChatStore.getState().isStreaming) return;
  // 决策已持久化，使用既有聊天流接回执行；不让提交按钮等待下一道问题。
  void sendMessage("/resume", undefined, sessionId, "继续执行").catch(() => {
    if (getActiveSessionId() === sessionId) {
      useChatStore.getState().setPipelineStatus({ stage: "resume_failed", message: "回答或决策已保存，任务暂未继续，可使用 /resume 重试。", startedAt: Date.now() });
    }
  });
}

export async function sendMessage(
  text: string,
  files?: AttachedFile[],
  sessionId?: string | null,
  displayText?: string,
) {
  const store = useChatStore.getState();
  const sessionStore = useSessionStore.getState();
  const uiState = useUIStore.getState();

  if (store.isStreaming) return;

  // 空消息前置拦截：无文本且无附件时直接拦截
  if (!text.trim() && (!files || files.length === 0)) {
    return;
  }

  if (uiState.configReady === false || uiState.configReady === null) {
    const userMsgId = uuid();
    store.addUserMessage(userMsgId, displayText ?? text);
    const assistantMsgId = uuid();
    store.addAssistantMessage(assistantMsgId);
    const items = uiState.configPlaceholderItems;
    store.appendBlock(assistantMsgId, {
      type: "config_error",
      items: items.length > 0 ? items : [{ name: "active", field: "api_key", model: "" }],
    });
    store.saveCurrentSession();
    return;
  }

  // 尽早创建 AbortController 并设置流状态 —— 在任何异常操作之前。
  // SessionSync 的 useEffect 通过检查 abortController 来决定是否调用 switchSession()。
  // 如果延迟到异常操作之后，SessionSync 的 effect 可能在 await 间歇检查时，
  // 发现 abortController===null 后调用 switchSession，清空 addUserMessage 即将创建的消息。
  const abortController = new AbortController();
  store.setAbortController(abortController);
  store.setStreaming(true);
  useJevStore.getState().beginTurn(sessionId);
  store.setPipelineStatus({
    stage: "connecting",
    message: "正在连接...",
    startedAt: Date.now(),
  });

  // 娓呴櫎涓婃鍙兘娈嬬暀鐨勫欢杩?token 缁熻锛岄伩鍏嶈法浼氳瘽娉勬紡
  _deferredTokenStats = null;

  // ... 提升UI：在任何 await 之前立即显示用户消息占位 + 手手加载状态 ...

  // 同步收集文件上传结果（仅读取 ChatInput 预上传的结果，无需 await）
  const fileUploadResults: { filename: string; path: string; size: number }[] = [];
  if (files && files.length > 0) {
    for (const af of files) {
      if (af.status === "success" && af.uploadResult) {
        fileUploadResults.push(af.uploadResult);
      } else if (
        af.status === "success" &&
        isVisionImageUpload(af.file) &&
        (af.cachedBase64 || af.file.size > 0)
      ) {
        // 示例卡片极速路径：图片可能只有本地 file / cachedBase64，没有 uploadResult
        fileUploadResults.push({
          filename: af.file.name,
          path: af.file.name,
          size: af.file.size,
        });
      }
    }
  }

  const effectiveSessionId = sessionId || getActiveSessionId();
  const sheetContext = buildJevSheetContext(effectiveSessionId);

  // 选中态只写 session-store。bindLoadedSession 避免 SessionSync 在消息占位后误切会话清空。
  if (effectiveSessionId && sessionStore.activeSessionId !== effectiveSessionId) {
    sessionStore.setActiveSession(effectiveSessionId);
  }
  if (effectiveSessionId) {
    store.bindLoadedSession(effectiveSessionId);
  }

  // 立即添加用户消息和助手占位消息 —— 用户界面看到自己的消息 + "正在连接" 加载状态
  const userMsgId = uuid();
  store.addUserMessage(
    userMsgId,
    displayText ?? text,
    fileUploadResults.length > 0 ? fileUploadResults : undefined
  );

  const assistantMsgId = uuid();
  store.addAssistantMessage(assistantMsgId);

  // 鈹€鈹€ 浠ヤ笅涓哄紓姝ユ搷浣滐紝娑堟伅姘旀场宸插彲瑙?鈹€鈹€


  // 鏂囦欢宸茬敱 ChatInput 棰勫厛涓婁紶銆傝繖閲屾敹闆嗚矾寰勫拰 base64 鏁版嵁鐢ㄤ簬 SSE 杞借嵎銆?
  const uploadedDocPaths: string[] = [];
  const uploadedImagePaths: string[] = [];
  const imageAttachments: ChatImagePayload[] = [];
  if (files && files.length > 0) {
    const successfulFiles = files.filter(
      (af) =>
        af.status === "success" &&
        (af.uploadResult || (isVisionImageUpload(af.file) && (af.cachedBase64 || af.file.size > 0))),
    );
    for (const af of successfulFiles) {
      const isImage = isVisionImageUpload(af.file);
      if (af.uploadResult && !af.fromWorkspace) {
        if (isImage) uploadedImagePaths.push(af.uploadResult.path);
        else uploadedDocPaths.push(af.uploadResult.path);
      }
    }

    const imageCandidates = successfulFiles.filter(
      (af) => isVisionImageUpload(af.file) && (af.file.size > 0 || !!af.cachedBase64),
    );
    let imageAdmitError: unknown = null;
    const encodedImages = await mapWithConcurrency(
      imageCandidates,
      async (af) => {
        try {
          const b64 = af.cachedBase64 ?? await _fileToBase64(af.file);
          return _admitChatImage(
            b64,
            af.file.type || "image/png",
            af.file.name,
          );
        } catch (b64Err) {
          console.error("Image attachment admission failed:", af.file.name, b64Err);
          throw b64Err;
        }
      },
      4,
    ).catch((err) => {
      imageAdmitError = err;
      return [] as ChatImagePayload[];
    });
    if (imageAdmitError) {
      useChatStore.getState().appendBlock(assistantMsgId, _buildClientFailureGuidance({
        category: "transport",
        code: "attachment_admit_failed",
        title: "图片上传失败",
        message: (imageAdmitError as Error).message || "图片未能完成准入，请重试。",
        retryable: true,
      }));
      return;
    }
    imageAttachments.push(...encodedImages);
  }

  let messageContent = text;
  const notices: string[] = [];
  for (const p of uploadedDocPaths) {
    notices.push(formatUploadNotice("file", p));
  }
  for (const p of uploadedImagePaths) {
    notices.push(formatUploadNotice("image", p));
  }
  if (notices.length > 0) {
    messageContent = `${notices.join("\n")}\n\n${text}`;
  }

  // 辅助函数：获取最新的 store 状态
  const S = () => useChatStore.getState();

  // RAF 批量增量刷新器：累积 text_delta / thinking_delta，
  // 每个动画帧最多应用到 store 一次。
  const batcher = makeDeltaBatcher(assistantMsgId);

  // ── SSE 事件处理上下文 ───────────────────────────────────
  const sseCtx: SSEHandlerContext = {
    assistantMsgId,
    batcher: batcher as unknown as DeltaBatcherInterface,
    effectiveSessionId: effectiveSessionId || "",
    isFirstSend: true,
    userText: text,
    thinkingInProgress: false,
    hadStreamError: false,
  };

  // 娴佸仠婊炴娴嬶細鍒濆杩炴帴 30 绉掕秴鏃讹紝涔嬪悗姣忔鏀跺埌浜嬩欢閲嶇疆涓?90 绉掋€?
  // 濡傛灉涓€?LLM 鎴栧悗绔鐞嗘寕璧疯秴杩?90 绉掓棤浠讳綍浜嬩欢锛岃嚜鍔ㄤ腑姝€?
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

  // 璇婃柇鏃ュ織锛氳褰曞浘鐗囬檮浠剁姸鎬?
  if (files && files.length > 0) {
    console.log(
      "[sendMessage] files=%d, imageAttachments=%d, uploadedImagePaths=%o",
      files.length,
      imageAttachments.length,
      uploadedImagePaths,
    );
    for (const att of imageAttachments) {
      console.log(
        "[sendMessage] image: media_type=%s, attachment_id=%s",
        att.media_type,
        att.attachment_id || "",
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
        sheet_context: sheetContext,
        ...(imageAttachments.length > 0 ? { images: imageAttachments } : {}),
      },
      (event) => {
        // 鏀跺埌浜嬩欢锛岄噸缃仠婊炴娴嬭鏃跺櫒
        _resetStallTimer();

        const sseEvent = event as SSEEvent;
        preDispatch(sseEvent, sseCtx);
        dispatchSSEEvent(sseEvent, sseCtx);

        // 鈹€鈹€ sendMessage 鐙湁鐨勫悗鍒嗗彂閫昏緫 鈹€鈹€
        const data = event.data;

        if (sseEvent.event === "reply") {
          // Token 缁熻锛歴endMessage 鏈夊欢杩熺疮鍔犻€昏緫
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
        message: "30 秒内未能建立连接，请检查模型设置或网络后重试。",
        retryable: true,
      }));
    } else if (_stallTimedOut) {
      sseCtx.hadStreamError = true;
      S().appendBlock(assistantMsgId, _buildClientFailureGuidance({
        code: "stream_stalled",
        title: "响应停滞",
        message: "已连接但超过 90 秒没有新数据，服务可能已停滞，请重试。",
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
    useJevStore.getState().finishTurn();

    if (effectiveSessionId && shouldResyncAfterStream(sseCtx, assistantMsgId)) {
      scheduleSessionResync(effectiveSessionId, sseCtx.hadStreamError ? 1500 : 400);
    }
  }
}

/**
 * 鍙戦€佸欢缁秷鎭紙瀹℃壒/闂瓟鍥炲锛夛紝澶嶇敤鏈€鍚庝竴鏉?assistant 娑堟伅銆?
 * 涓嶅垱寤?user/assistant 姘旀场锛岀豢绾夸笉浼氭柇寮€銆?
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

  const effectiveSessionId = sessionId || getActiveSessionId();

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

  const batcher = makeDeltaBatcher(msgId);

  const sseCtx: SSEHandlerContext = {
    assistantMsgId: msgId,
    batcher: batcher as unknown as DeltaBatcherInterface,
    effectiveSessionId: effectiveSessionId || "",
    isFirstSend: false,
    thinkingInProgress: false,
    hadStreamError: false,
  };

  // 娴佸仠婊炴娴嬶紙涓?sendMessage 涓€鑷达級
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
      { message: text, session_id: effectiveSessionId, chat_mode: useUIStore.getState().chatMode },
      (event) => {
        _resetContStall();
        const sseEvent = event as SSEEvent;
        preDispatch(sseEvent, sseCtx);
        dispatchSSEEvent(sseEvent, sseCtx);

        // 鈹€鈹€ sendContinuation 鐙湁锛歳eply 鐨?token 绱姞閫昏緫 鈹€鈹€
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
                  S().updateAssistantMessage(msgId, (m) => ({
                    ...m,
                    blocks: m.blocks.filter((b) => b.type !== "token_stats"),
                  }));
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
        message: "30 秒内未能建立连接，请检查模型设置或网络后重试。",
        retryable: true,
      }));
    } else if (_contStallTimedOut) {
      sseCtx.hadStreamError = true;
      S().appendBlock(msgId, _buildClientFailureGuidance({
        code: "stream_stalled",
        title: "响应停滞",
        message: "已连接但超过 90 秒没有新数据，服务可能已停滞，请重试。",
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
    useJevStore.getState().finishTurn();

    if (effectiveSessionId && shouldResyncAfterStream(sseCtx, assistantMsgId)) {
      scheduleSessionResync(effectiveSessionId, sseCtx.hadStreamError ? 1500 : 400);
    }
  }
}

/**
 * 回滚对话到指定用户消息并重新发送（编辑后的内容）。
 * 1. 调用后端 rollback API（resend_mode=true 会移除目标用户消息）；
 * 2. 截断前端消息列表到目标消息之前；
 * 3. 用 sendMessage 重新发送（在前端后端各添加一条用户消息）
 */
export async function rollbackAndResend(
  messageId: string,
  newContent: string,
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

  // 鐩爣蹇呴』鏄?user 娑堟伅
  if (messages[msgIndex].role !== "user") return;

  // 璁＄畻 turn_index锛堢鍑犱釜 user 娑堟伅锛?
  let turnIndex = 0;
  for (let i = 0; i < msgIndex; i++) {
    if (messages[i].role === "user") turnIndex++;
  }

  const effectiveSessionId = sessionId || getActiveSessionId() || store.loadedSessionId;
  if (!effectiveSessionId) return;
  const workspaceId = workspaceIdForSession(effectiveSessionId);

  // 璋冪敤鍚庣 rollback API锛坮esend_mode 浼氱Щ闄ょ洰鏍囩敤鎴锋秷鎭級
  try {
    const { rollbackChat } = await import("./api");
    await rollbackChat({
      sessionId: effectiveSessionId,
      turnIndex,
      resendMode: true,
    });
  } catch (err) {
    console.warn("Rollback failed, attempting to resync session:", err);
    // 鍚戠敤鎴峰睍绀洪敊璇紝鑰岄潪闈欓粯澶辫触
    const lastAssistant = [...messages].reverse().find((m) => m.role === "assistant");
    if (lastAssistant && lastAssistant.role === "assistant") {
      store.appendBlock(lastAssistant.id, _buildClientFailureGuidance({
        code: "network_error",
        title: "重新发送失败",
        message: "回滚失败，请稍后重试或刷新页面。",
        retryable: true,
      }));
    }
    // 鍚庣浼氳瘽鍙兘宸茶繃鏈?閲嶅缓锛屽皾璇曞埛鏂板墠绔秷鎭互閲嶆柊鍚屾
    try {
      const { refreshSessionMessagesFromBackend } = await import("@/stores/chat-store");
      await refreshSessionMessagesFromBackend(effectiveSessionId);
    } catch { /* SessionSync 杞鏈€缁堜細鎭㈠ */ }
    return;
  }

  // 鍓嶇鎴柇鍒扮洰鏍囩敤鎴锋秷鎭箣鍓嶏紙涓庡悗绔?resend_mode 涓€鑷达級
  const truncated = messages.slice(0, msgIndex);
  store.setMessages(truncated);

  // 涓轰繚鐣欑殑鍘熸湁闄勪欢鍒涘缓鍚堟垚 AttachedFile锛堝凡涓婁紶锛屾棤闇€閲嶄紶锛?
  const retainedAttached: AttachedFile[] = (retainedFiles ?? []).map((f, i) => ({
    id: `retained-${Date.now()}-${i}`,
    file: new File([], f.filename),
    status: "success" as const,
    uploadResult: { filename: f.filename, path: f.path, size: f.size },
  }));

  // 棰勫厛涓婁紶鏂版枃浠讹紙涓?ChatInput 鐨?triggerUpload 鐩稿悓锛夛紝
  // 纭繚 sendMessage 鏀跺埌甯︽湁 uploadResult 鐨勬纭?AttachedFile 瀵硅薄銆?
  let newAttached: AttachedFile[] = [];
  if (files && files.length > 0) {
    const { uploadFile } = await import("./api");
    newAttached = await Promise.all(
      files.map(async (f, i): Promise<AttachedFile> => {
        const id = `resend-${Date.now()}-${i}`;
        try {
          const uploadResult = await uploadFile(f, effectiveSessionId, workspaceId);
          return { id, file: f, status: "success" as const, uploadResult };
        } catch (err) {
          console.error("Edit-resend upload failed:", f.name, err);
          return { id, file: f, status: "failed" as const, error: String(err) };
        }
      }),
    );
  }

  const allAttached = [...retainedAttached, ...newAttached];

  // sendMessage 浼氬湪鍓嶅悗绔悇娣诲姞鐢ㄦ埛娑堟伅 + 瑙﹀彂娴佸紡鍥炲
  await sendMessage(newContent, allAttached.length > 0 ? allAttached : undefined, effectiveSessionId);
}

/**
 * 閲嶈瘯鎸囧畾 assistant 娑堟伅锛氬洖婊氬埌鍏跺墠涓€鏉?user 娑堟伅锛岀劧鍚庨噸鏂板彂閫併€?
 * 濡傛灉鎸囧畾浜?switchToModel锛屼細鍏堝垏鎹㈡ā鍨嬪啀閲嶆柊鍙戦€併€?
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

  // 鎵惧埌璇?assistant 娑堟伅鍓嶉潰鏈€杩戠殑 user 娑堟伅
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

  // 璁＄畻 turn_index锛堢鍑犱釜 user 娑堟伅锛?
  let turnIndex = 0;
  for (let i = 0; i < userIdx; i++) {
    if (messages[i].role === "user") turnIndex++;
  }

  const effectiveSessionId = sessionId || getActiveSessionId() || store.loadedSessionId;
  if (!effectiveSessionId) return;
  const workspaceId = workspaceIdForSession(effectiveSessionId);

  // 濡傛灉闇€瑕佸垏鎹㈡ā鍨嬶紝鍏堝垏鎹?
  if (switchToModel) {
    try {
      const { apiPut } = await import("./api");
      await apiPut("/models/active", { name: switchToModel });
      useUIStore.getState().setCurrentModel(switchToModel);
      useUIStore.getState().bumpModelProfiles();
    } catch (err) {
      console.error("Model switch failed:", err);
      return;
    }
  }

  // 璋冪敤鍚庣 rollback API
  try {
    const { rollbackChat } = await import("./api");
    await rollbackChat({
      sessionId: effectiveSessionId,
      turnIndex,
      resendMode: true,
    });
  } catch (err) {
    if (!isMissingRollbackTargetError(err)) {
      console.warn("Rollback failed, attempting to resync session:", err);
      try {
        const { refreshSessionMessagesFromBackend } = await import("@/stores/chat-store");
        await refreshSessionMessagesFromBackend(effectiveSessionId);
      } catch { /* SessionSync 杞鏈€缁堜細鎭㈠ */ }
      return;
    }
    // The first request can fail before the backend records its user turn.
    // There is nothing to roll back in that case; remove the optimistic failed
    // turn locally and let sendMessage submit the original content again.
    console.info("Rollback target is absent; retrying the original turn directly", err);
  }

  // 鍓嶇鎴柇鍒?user 娑堟伅涔嬪墠
  const truncated = messages.slice(0, userIdx);
  store.setMessages(truncated);

  // 淇濈暀鍘熷鐢ㄦ埛娑堟伅鐨勬枃浠堕檮浠讹紙宸蹭笂浼狅紝鏃犻渶閲嶄紶锛?
  // 瀵逛簬鍥剧墖闄勪欢锛岄渶瑕佷粠宸ヤ綔鍖洪噸鏂颁笅杞藉唴瀹癸紝浠ヤ究 sendMessage 鑳界紪鐮?base64 鍙戠粰 LLM
  let retainedAttached: AttachedFile[] | undefined;
  if (userMessage.role === "user" && userMessage.files && userMessage.files.length > 0) {
    const { fetchFileBlob } = await import("./api");
    retainedAttached = await Promise.all(
      userMessage.files.map(async (f, i): Promise<AttachedFile> => {
        const id = `retry-retained-${Date.now()}-${i}`;
        const isImage = isVisionImageFile(f.filename);

        if (isImage) {
          // 鍥剧墖闇€瑕侀噸鏂拌幏鍙栧唴瀹癸紝鍚﹀垯 sendMessage 鍥?file.size===0 璺宠繃 base64 缂栫爜
          try {
            const blob = await fetchFileBlob(f.path, effectiveSessionId, workspaceId);
            const file = new File([blob], f.filename, { type: blob.type || "image/png" });
            return { id, file, status: "success" as const, uploadResult: { filename: f.filename, path: f.path, size: f.size } };
          } catch (err) {
            console.warn("[retryAssistantMessage] 鍥剧墖閲嶆柊鑾峰彇澶辫触锛屽洖閫€鍒拌矾寰勯€氱煡:", f.path, err);
          }
        }

        return { id, file: new File([], f.filename), status: "success" as const, uploadResult: { filename: f.filename, path: f.path, size: f.size } };
      })
    );
  }

  // 閲嶆柊鍙戦€侊紙鎼哄甫鍘熷闄勪欢淇℃伅锛?
  await sendMessage(userContent, retainedAttached, effectiveSessionId);
}

export function stopGeneration() {
  const store = useChatStore.getState();
  if (!store.abortController) return;

  // 1. 閫氱煡鍚庣鍙栨秷鏈嶅姟绔换鍔?
  const sessionId = getActiveSessionId() || store.loadedSessionId;
  if (sessionId) {
    import("./api").then(({ abortChat }) => abortChat(sessionId)).catch(() => {});
  }

  // 2. 涓柇鍓嶇 SSE 杩炴帴
  store.abortController.abort();
  store.setAbortController(null);
  store.setStreaming(false);

  // 3. 淇ˉ鏈€鍚庝竴鏉?assistant 娑堟伅锛氬皢杩涜涓殑 block 鏍囪涓哄け璐ワ紝
  //    骞惰拷鍔犲彲瑙佺殑"宸插仠姝?鎸囩ず鍣ㄣ€?
  const messages = store.messages;
  const lastMsg = [...messages].reverse().find((m) => m.role === "assistant");
  if (lastMsg && lastMsg.role === "assistant") {
    let blocksChanged = false;
    const patchedBlocks = lastMsg.blocks.map((block): AssistantBlock => {
      if (block.type === "tool_call" && block.status === "running") {
        blocksChanged = true;
        return { ...block, status: "error", error: "已被用户停止" };
      }
      if (block.type === "subagent" && block.status === "running" && !block.background) {
        blocksChanged = true;
        return { ...block, status: "done", summary: "已被用户停止" };
      }
      return block;
    });

    patchedBlocks.push({
      type: "status",
      label: "对话已停止",
      detail: "已手动停止生成",
      variant: "info",
    });

    store.updateAssistantMessage(lastMsg.id, (m) => ({ ...m, blocks: patchedBlocks }));
    store.saveCurrentSession();
  }
}

// ---------------------------------------------------------------------------
// 娲昏穬璁㈤槄瀹堝崼锛氶槻姝㈠涓苟鍙戠殑 subscribe 杩炴帴銆?
// ---------------------------------------------------------------------------
let _activeSubscribeSessionId: string | null = null;

/**
 * SSE 閲嶈繛锛氶〉闈㈠埛鏂板悗閲嶆柊鎺ュ叆姝ｅ湪鎵ц鐨勮亰澶╀换鍔′簨浠舵祦銆?
 * 澶嶇敤鏈€鍚庝竴鏉?assistant 娑堟伅锛堣嫢瀛樺湪锛夛紝涓嶅垱寤烘柊鐨勭敤鎴锋秷鎭€?
 *
 * 鐢?SessionSync 鍦ㄦ娴嬪埌 in_flight && !hasLocalLiveStream 鏃惰皟鐢ㄣ€?
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
  const subscribeStreamId = store.activeStreamId;
  const subscribeAfterSeq = Math.max(0, store.latestSeq || 0);
  store.clearResumeFailed();

  if (!subscribeStreamId) {
    _activeSubscribeSessionId = null;
    store.setPipelineStatus(null);
    store.setStreaming(false);
    store.setAbortController(null);
    try {
      const { refreshSessionMessagesFromBackend } = await import("@/stores/chat-store");
      await refreshSessionMessagesFromBackend(sessionId);
    } catch {
      // ignore
    }
    return;
  }

  const abortController = new AbortController();
  store.setAbortController(abortController);
  store.setStreaming(true);
  store.setPipelineStatus({
    stage: "reconnecting",
    message: "姝ｅ湪閲嶈繛...",
    startedAt: Date.now(),
  });

  const S = () => useChatStore.getState();

  const batcher = makeDeltaBatcher(msgId);

  const sseCtx: SSEHandlerContext = {
    assistantMsgId: msgId,
    batcher: batcher as unknown as DeltaBatcherInterface,
    effectiveSessionId: sessionId,
    isFirstSend: false,
    thinkingInProgress: false,
    hadStreamError: false,
  };

  // 娴佸仠婊炴娴嬶紙涓?sendMessage 涓€鑷达級
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
      {
        session_id: sessionId,
        stream_id: subscribeStreamId,
        after_seq: subscribeAfterSeq,
      },
      (event) => {
        // 鏀跺埌浜嬩欢锛岄噸缃仠婊炴娴嬭鏃跺櫒
        _resetSubStall();

        const sseEvent = event as SSEEvent;
        preDispatch(sseEvent, sseCtx);
        dispatchSSEEvent(sseEvent, sseCtx);

        // 鈹€鈹€ subscribe 鐙湁锛歳eply 鐨勭畝鍗?token 缁熻 鈹€鈹€
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
        message: "30 秒内未能重新连接，请检查网络或刷新页面后重试。",
        retryable: true,
      }));
    } else if (_subStallTimedOut) {
      sseCtx.hadStreamError = true;
      S().appendBlock(msgId, _buildClientFailureGuidance({
        code: "stream_stalled",
        title: "响应停滞",
        message: "已连接但超过 90 秒没有新数据，服务可能已停滞，请重试。",
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
    useJevStore.getState().finishTurn();

    if (S().resumeFailedReason) {
      const sid = sessionId;
      setTimeout(async () => {
        try {
          const { refreshSessionMessagesFromBackend } = await import("@/stores/chat-store");
          const chat = useChatStore.getState();
          if (chat.loadedSessionId === sid && !chat.isStreaming && !chat.abortController) {
            await refreshSessionMessagesFromBackend(sid);
            chat.clearResumeFailed();
          }
        } catch {
          // 静默处理 —— SessionSync 轮询最终会恢复
        }
      }, 200);
    }
  }
}
