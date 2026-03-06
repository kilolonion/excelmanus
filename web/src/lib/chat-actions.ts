import { consumeSSE, SSEError } from "./sse";
import { buildApiUrl } from "./api";
import { mapWithConcurrency } from "./concurrency";
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

/** 鍦ㄥ鎴风鏈湴鏋勯€?failure_guidance block锛堢敤浜庣綉缁滅骇閿欒锛屾棤 SSE 浜嬩欢鍙揪鐨勫満鏅級銆?*/
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
          { type: "retry", label: "Retry Now" },
          { type: "open_settings", label: "Check Settings" },
        ]
      : [{ type: "open_settings", label: "Check Settings" }],
  };
}

/** 鏍规嵁 SSEError 鐨?HTTP 鐘舵€佺爜鐢熸垚瀵瑰簲鐨?failure_guidance block銆?*/
function _classifySSEError(err: SSEError): Extract<AssistantBlock, { type: "failure_guidance" }> {
  const status = err.statusCode;
  if (status === 401 || status === 403) {
    return _buildClientFailureGuidance({
      category: "model",
      code: "model_auth_failed",
      title: "Authentication Failed",
      message: status === 401
        ? "API key is invalid or expired. Check your model settings."
        : "Unauthorized to access the model provider. Check API key permissions.",
      retryable: false,
    });
  }
  if (status === 402) {
    return _buildClientFailureGuidance({
      category: "quota",
      code: "quota_exceeded",
      title: "Quota Exceeded",
      message: "Model API quota has been exhausted. Recharge and retry.",
      retryable: false,
    });
  }
  if (status === 404) {
    return _buildClientFailureGuidance({
      category: "model",
      code: "model_not_found",
      title: "Model Not Found",
      message: "Requested model does not exist or is offline. Choose another model.",
      retryable: false,
    });
  }
  if (status === 409) {
    return _buildClientFailureGuidance({
      category: "transport",
      code: "session_busy",
      title: "Session Busy",
      message: "This session is processing another request. Wait and try again.",
      retryable: true,
    });
  }
  if (status === 429) {
    return _buildClientFailureGuidance({
      category: "quota",
      code: "rate_limited",
      title: "Rate Limited",
      message: "API rate limit exceeded. Retry later.",
      retryable: true,
    });
  }
  if (status >= 500) {
    return _buildClientFailureGuidance({
      category: "model",
      code: "provider_internal_error",
      title: "Provider Internal Error",
      message: `Model provider returned ${status}. Retry later.`,
      retryable: true,
    });
  }
  if (status === 0) {
    return _buildClientFailureGuidance({
      category: "transport",
      code: "stream_interrupted",
      title: "Connection Interrupted",
      message: err.message || "Connection to backend was interrupted.",
      retryable: true,
    });
  }
  return _buildClientFailureGuidance({
    code: "http_error",
    title: "Request Error",
    message: err.message || `HTTP ${status}`,
    retryable: status >= 500,
  });
}

function _fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      // result 鏍煎紡涓?"data:<mime>;base64,<data>" 鈥?浠呮彁鍙?base64 閮ㄥ垎
      const result = reader.result as string;
      const idx = result.indexOf(",");
      resolve(idx >= 0 ? result.slice(idx + 1) : result);
    };
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

// ---------------------------------------------------------------------------
// 鍩轰簬 RAF 鐨勫閲忔壒澶勭悊鍣細缂撳啿楂橀 text_delta / thinking_delta 浜嬩欢锛?
// 姣忎釜鍔ㄧ敾甯ф渶澶氬埛鏂颁竴娆★紝閬垮厤杩囧害閲嶆覆鏌撱€?
// 鏀硅繘锛氬鍔犵珛鍗冲埛鏂版満鍒讹紝纭繚闈炲閲忎簨浠朵笉浼氫笌缂撳啿鐨勫閲忎簨浠朵骇鐢熸椂搴忛棶棰?
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
    // 鍏堝埛鏂版畫浣欏唴瀹瑰埌 store锛岄槻姝㈡柇杩?鐑噸杞芥椂涓㈠け灏鹃儴 delta
    if (this._rafId !== null) {
      cancelAnimationFrame(this._rafId);
      this._rafId = null;
    }
    this._doFlush();
    this._disposed = true;
    this._textBuf = "";
    this._thinkingBuf = "";
  }

  // 妫€鏌ユ槸鍚︽湁寰呭鐞嗙殑鍐呭
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

// 鍥犲緟澶勭悊浜や簰锛坅skuser / approval锛夎€屽欢杩熺殑 Token 缁熻銆?
// sendContinuation 浼氬皢杩欎簺绱Н鍒版渶缁堢粺璁′腑銆?
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

  // 绌烘秷鎭墠缃牎楠岋細鏃犳枃鏈笖鏃犻檮浠舵椂鐩存帴鎷︽埅
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

  // 灏芥棭鍒涘缓 AbortController 骞惰缃祦寮忕姸鎬?- 鍦ㄤ换浣曞紓姝ユ搷浣滀箣鍓嶃€?
  // SessionSync 鐨?useEffect 閫氳繃妫€鏌?abortController 鏉ュ喅瀹氭槸鍚﹁皟鐢?switchSession()銆?
  // 濡傛灉寤惰繜鍒板紓姝ユ搷浣滀箣鍚庯紝SessionSync 鐨?effect 鍙兘鍦?await 闂撮殭瑙﹀彂锛?
  // 鍙戠幇 abortController===null 鍚庤皟鐢?switchSession锛屾竻绌?addUserMessage 鍗冲皢鍒涘缓鐨勬秷鎭€?
  const abortController = new AbortController();
  store.setAbortController(abortController);
  store.setStreaming(true);
  store.setPipelineStatus({
    stage: "connecting",
    message: "姝ｅ湪杩炴帴...",
    startedAt: Date.now(),
  });

  // 娓呴櫎涓婃鍙兘娈嬬暀鐨勫欢杩?token 缁熻锛岄伩鍏嶈法浼氳瘽娉勬紡
  _deferredTokenStats = null;

  // 鈹€鈹€ 涔愯 UI锛氬湪浠讳綍 await 涔嬪墠绔嬪嵆灞曠ず鐢ㄦ埛娑堟伅姘旀场 + 鍔╂墜鍔犺浇鐘舵€?鈹€鈹€

  // 鍚屾鏀堕泦鏂囦欢涓婁紶缁撴灉锛堜粎璇诲彇 ChatInput 棰勪笂浼犵殑缁撴灉锛屾棤闇€ await锛?
  const fileUploadResults: { filename: string; path: string; size: number }[] = [];
  if (files && files.length > 0) {
    for (const af of files) {
      if (af.status === "success" && af.uploadResult) {
        fileUploadResults.push(af.uploadResult);
      }
      // 璺宠繃涓婁紶澶辫触鐨勬枃浠讹紝閬垮厤绌鸿矾寰勮繘鍏ユ秷鎭?
    }
  }

  const effectiveSessionId = sessionId || sessionStore.activeSessionId;

  // 鍦ㄦ坊鍔犳秷鎭箣鍓嶅悓姝?currentSessionId锛岀‘淇?SessionSync 鐨?useEffect
  //锛堝湪涓嬫娓叉煋鍚庤Е鍙戯級鐪嬪埌 currentSessionId === activeSessionId锛?
  // 浠庤€岃烦杩囦細娓呯┖鎴戜滑鍗冲皢娣诲姞鐨勬秷鎭殑 switchSession() 璋冪敤銆?
  if (effectiveSessionId && store.currentSessionId !== effectiveSessionId) {
    if (store.currentSessionId && store.messages.length > 0) {
      store.saveCurrentSession();
    }
    useChatStore.setState({ currentSessionId: effectiveSessionId });
  }

  // 绔嬪嵆娣诲姞鐢ㄦ埛娑堟伅鍜屽姪鎵嬪崰浣嶆秷鎭?鈥?鐢ㄦ埛鐬棿鐪嬪埌鑷繁鐨勬秷鎭皵娉?+ "姝ｅ湪杩炴帴" 鍔犺浇鐘舵€?
  const userMsgId = uuid();
  store.addUserMessage(
    userMsgId,
    text,
    fileUploadResults.length > 0 ? fileUploadResults : undefined
  );

  const assistantMsgId = uuid();
  store.addAssistantMessage(assistantMsgId);

  // 鈹€鈹€ 浠ヤ笅涓哄紓姝ユ搷浣滐紝娑堟伅姘旀场宸插彲瑙?鈹€鈹€


  // 鏂囦欢宸茬敱 ChatInput 棰勫厛涓婁紶銆傝繖閲屾敹闆嗚矾寰勫拰 base64 鏁版嵁鐢ㄤ簬 SSE 杞借嵎銆?
  const uploadedDocPaths: string[] = [];
  const uploadedImagePaths: string[] = [];
  const imageAttachments: { data: string; media_type: string }[] = [];
  if (files && files.length > 0) {
    const successfulFiles = files.filter((af) => af.status === "success" && af.uploadResult);
    for (const af of successfulFiles) {
      const isImage = _isImageLike(af.file);
      if (isImage) uploadedImagePaths.push(af.uploadResult!.path);
      else uploadedDocPaths.push(af.uploadResult!.path);
    }

    const imageCandidates = successfulFiles.filter(
      (af) => _isImageLike(af.file) && af.file.size > 0,
    );
    const encodedImages = await mapWithConcurrency(
      imageCandidates,
      async (af) => {
        try {
          const b64 = af.cachedBase64 ?? await _fileToBase64(af.file);
          return {
            data: b64,
            media_type: af.file.type || "image/png",
          };
        } catch (b64Err) {
          console.error("Base64 encoding failed for image:", af.file.name, b64Err);
          return null;
        }
      },
      4,
    );
    imageAttachments.push(
      ...encodedImages.filter((item): item is { data: string; media_type: string } => item !== null),
    );
  }

  // 涓?agent 鏋勫缓缁撴瀯鍖栨枃浠堕€氱煡
  let messageContent = text;
  const notices: string[] = [];
  for (const p of uploadedDocPaths) {
    notices.push(`[宸蹭笂浼犳枃浠? ${p}]`);
  }
  for (const p of uploadedImagePaths) {
    notices.push(`[宸蹭笂浼犲浘鐗? ${p}]`);
  }
  if (notices.length > 0) {
    messageContent = `${notices.join("\n")}\n\n${text}`;
  }

  // 杈呭姪鍑芥暟锛氳幏鍙栨渶鏂扮殑 store 鐘舵€?
  const S = () => useChatStore.getState();

  // RAF 鎵归噺澧為噺鍒锋柊鍣細绱Н text_delta / thinking_delta锛?
  // 姣忎釜鍔ㄧ敾甯ф渶澶氬簲鐢ㄤ竴娆″埌 store銆?
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

  // 鈹€鈹€ SSE 浜嬩欢澶勭悊涓婁笅鏂?鈹€鈹€
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
          title: "杩炴帴閿欒",
          message: (err as Error).message || "缃戠粶杩炴帴澶辫触",
          retryable: true,
        }));
      }
    } else if (_connectionTimedOut) {
      sseCtx.hadStreamError = true;
      S().appendBlock(assistantMsgId, _buildClientFailureGuidance({
        code: "connect_timeout",
        title: "Connection Timeout",
        message: "Unable to establish connection within 30 seconds. Check model settings or network.",
        retryable: true,
      }));
    } else if (_stallTimedOut) {
      sseCtx.hadStreamError = true;
      S().appendBlock(assistantMsgId, _buildClientFailureGuidance({
        code: "stream_stalled",
        title: "Stream Stalled",
        message: "Connected but no new data received for over 90 seconds. Provider may be stalled.",
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
          // 闈欓粯澶勭悊 鈥?SessionSync 杞鏈€缁堜細鎭㈠
        }
      }, 1500);
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

  const sessionStore = useSessionStore.getState();
  const effectiveSessionId = sessionId || sessionStore.activeSessionId;

  const abortController = new AbortController();
  store.setAbortController(abortController);
  store.setStreaming(true);
  store.setPipelineStatus({
    stage: "connecting",
    message: "姝ｅ湪杩炴帴...",
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
      { message: text, session_id: effectiveSessionId },
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
          title: "杩炴帴閿欒",
          message: (err as Error).message || "缃戠粶杩炴帴澶辫触",
          retryable: true,
        }));
      }
    } else if (_contConnectionTimedOut) {
      sseCtx.hadStreamError = true;
      S().appendBlock(msgId, _buildClientFailureGuidance({
        code: "connect_timeout",
        title: "Connection Timeout",
        message: "Unable to establish connection within 30 seconds. Check model settings or network.",
        retryable: true,
      }));
    } else if (_contStallTimedOut) {
      sseCtx.hadStreamError = true;
      S().appendBlock(msgId, _buildClientFailureGuidance({
        code: "stream_stalled",
        title: "Stream Stalled",
        message: "Connected but no new data received for over 90 seconds. Provider may be stalled.",
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
          // 闈欓粯澶勭悊
        }
      }, 1500);
    }
  }
}

/**
 * 鍥為€€瀵硅瘽鍒版寚瀹氱敤鎴锋秷鎭苟閲嶆柊鍙戦€侊紙缂栬緫鍚庣殑鍐呭锛夈€?
 * 1. 璋冪敤鍚庣 rollback API锛坮esend_mode=true 绉婚櫎鐩爣鐢ㄦ埛娑堟伅锛?
 * 2. 鎴柇鍓嶇娑堟伅鍒楄〃鍒扮洰鏍囨秷鎭箣鍓?
 * 3. 鐢?sendMessage 閲嶆柊鍙戦€侊紙鍦ㄥ墠鍚庣鍚勬坊鍔犱竴鏉＄敤鎴锋秷鎭級
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

  // 鎵惧埌鐩爣鐢ㄦ埛娑堟伅鍦ㄥ墠绔秷鎭垪琛ㄤ腑鐨勪綅缃?
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

  const effectiveSessionId = sessionId || store.currentSessionId;
  if (!effectiveSessionId) return;

  // 璋冪敤鍚庣 rollback API锛坮esend_mode 浼氱Щ闄ょ洰鏍囩敤鎴锋秷鎭級
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
    // 鍚戠敤鎴峰睍绀洪敊璇紝鑰岄潪闈欓粯澶辫触
    const lastAssistant = [...messages].reverse().find((m) => m.role === "assistant");
    if (lastAssistant && lastAssistant.role === "assistant") {
      store.appendBlock(lastAssistant.id, _buildClientFailureGuidance({
        code: "network_error",
        title: "Edit Resend Failed",
        message: "Rollback failed. Retry later or refresh the page.",
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
  rollbackFiles?: boolean,
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

  const effectiveSessionId = sessionId || store.currentSessionId;
  if (!effectiveSessionId) return;

  // 濡傛灉闇€瑕佸垏鎹㈡ā鍨嬶紝鍏堝垏鎹?
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

  // 璋冪敤鍚庣 rollback API
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
    } catch { /* SessionSync 杞鏈€缁堜細鎭㈠ */ }
    return;
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
        const isImage = _isImageFile(f.filename);

        if (isImage) {
          // 鍥剧墖闇€瑕侀噸鏂拌幏鍙栧唴瀹癸紝鍚﹀垯 sendMessage 鍥?file.size===0 璺宠繃 base64 缂栫爜
          try {
            const blob = await fetchFileBlob(f.path, effectiveSessionId ?? undefined);
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
  const sessionId = store.currentSessionId;
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
        return { ...block, status: "error", error: "宸茶鐢ㄦ埛鍋滄" };
      }
      if (block.type === "subagent" && block.status === "running") {
        blocksChanged = true;
        return { ...block, status: "done", summary: "宸茶鐢ㄦ埛鍋滄" };
      }
      return block;
    });

    patchedBlocks.push({
      type: "status",
      label: "Conversation Stopped",
      detail: "Generation was manually stopped by the user.",
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
          title: "閲嶈繛閿欒",
          message: (err as Error).message || "閲嶈繛澶辫触",
          retryable: true,
        }));
      }
    } else if (_subConnectionTimedOut) {
      sseCtx.hadStreamError = true;
      S().appendBlock(msgId, _buildClientFailureGuidance({
        code: "connect_timeout",
        title: "Reconnect Timeout",
        message: "Unable to reconnect within 30 seconds. Check network or refresh the page.",
        retryable: true,
      }));
    } else if (_subStallTimedOut) {
      sseCtx.hadStreamError = true;
      S().appendBlock(msgId, _buildClientFailureGuidance({
        code: "stream_stalled",
        title: "Stream Stalled",
        message: "Connected but no new data received for over 90 seconds. Provider may be stalled.",
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

    if (S().resumeFailedReason) {
      const sid = sessionId;
      setTimeout(async () => {
        try {
          const { refreshSessionMessagesFromBackend } = await import("@/stores/chat-store");
          const chat = useChatStore.getState();
          if (chat.currentSessionId === sid && !chat.isStreaming && !chat.abortController) {
            await refreshSessionMessagesFromBackend(sid);
            chat.clearResumeFailed();
          }
        } catch {
          // 闈欓粯澶勭悊 鈥?SessionSync 杞鏈€缁堜細鎭㈠
        }
      }, 200);
    }
  }
}
