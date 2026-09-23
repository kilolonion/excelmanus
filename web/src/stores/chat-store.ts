import { create } from "zustand";
import type { Message, Approval, Question, AssistantBlock, FileAttachment, SubagentRun } from "@/lib/types";
import { isSubagentActive, subagentChangedFiles } from "@/lib/subagent-runs";
import { loadCachedMessages, saveCachedMessages, deleteCachedMessages, clearAllCachedMessages } from "@/lib/idb-cache";
import { fetchSessionMessages, fetchSessionExcelEvents, clearAllSessions } from "@/lib/api";
import { useSessionStore } from "@/stores/session-store";
import { useExcelStore } from "@/stores/excel-store";
import { useJevStore } from "@/stores/jev-store";
import type { ExcelDiffEntry } from "@/stores/excel-store";
import {
  deriveSessionTitleFromMessages,
  isFallbackSessionTitle,
} from "@/lib/session-title";
import { extractFileAttachmentsFromContent, stripImageSentPlaceholder } from "@/lib/upload-notice";
import {
  isHiddenBackendUserMessage,
  stripInjectedUserPromptBlocks,
} from "@/lib/injected-user-prompt";
import {
  collectHistoryAffectedFiles,
  displayFileName,
  mergeAffectedFiles,
  toPublicFileIdentity,
} from "@/lib/file-identity";
import { workspaceKeyForSessionId } from "@/lib/workspace-file-ref";
import {
  blocksHaveProgress,
  hydrateFailureGuidanceFromText,
  isFailureGuidanceBlock,
} from "@/lib/failure-recovery";

// 内存快速缓存（扩展 IndexedDB）
const _sessionMessages = new Map<string, Message[]>();
const _sessionMessageMeta = new Map<string, { total: number; offset: number; hasMore: boolean }>();
const _messageRefreshes = new Map<string, Promise<void>>();
const MESSAGE_PAGE_SIZE = 100;

// F5：switchSession 取消息令牌 —— 递归版本号，当 loadAndSwitch 检测到版本号变化后放送更新
let _switchSessionVersion = 0;

let _msgIdCounter = 0;
function _nextId(): string {
  return `restored-${++_msgIdCounter}-${Date.now()}`;
}

interface MessageEntities {
  messages: Message[];
  messageOrder: string[];
  messagesById: Record<string, Message>;
  messageIndexById: Record<string, number>;
}

function _buildMessageEntities(messages: Message[]): MessageEntities {
  const normalized: Message[] = [];
  const messageOrder: string[] = [];
  const messagesById: Record<string, Message> = {};
  const messageIndexById: Record<string, number> = {};

  for (const msg of messages) {
    const msgId = String(msg.id || "").trim();
    if (!msgId) continue;
    if (messageIndexById[msgId] != null) {
      const idx = messageIndexById[msgId];
      normalized[idx] = msg;
      messagesById[msgId] = msg;
      continue;
    }
    messageIndexById[msgId] = normalized.length;
    messageOrder.push(msgId);
    messagesById[msgId] = msg;
    normalized.push(msg);
  }

  return { messages: normalized, messageOrder, messagesById, messageIndexById };
}

function _setMessagesSnapshot(messages: Message[]): Pick<
  ChatState,
  "messages" | "messageOrder" | "messagesById" | "messageIndexById"
> {
  const entities = _buildMessageEntities(messages);
  return {
    messages: entities.messages,
    messageOrder: entities.messageOrder,
    messagesById: entities.messagesById,
    messageIndexById: entities.messageIndexById,
  };
}

function _patchMessageById(
  state: Pick<ChatState, "messages" | "messageOrder" | "messagesById" | "messageIndexById">,
  messageId: string,
  updater: (message: Message) => Message,
): Pick<ChatState, "messages" | "messagesById"> | null {
  const current = state.messagesById[messageId];
  if (!current) return null;
  const next = updater(current);
  if (next === current) return null;
  const messagesById = { ...state.messagesById, [messageId]: next };
  const index = state.messageIndexById[messageId];
  const messages = state.messages.slice();
  messages[index] = next;
  return {
    messages,
    messagesById,
  };
}

interface LoadMessagesOptions {
  preferCache?: boolean;
  replaceVisibleMessages?: boolean;
}

/**
 * 将后端 LLM 消息（role/content 字符）转换为前端 Message[]。
 * 将连续的 assistant/tool 消息合并为带 blocks 的单个 assistant 消息。
 */
const _EXCEL_WRITE_TOOL_NAMES = new Set([
  "edit_spreadsheet",
  "format_spreadsheet",
  "manage_spreadsheet_objects",
  "manage_spreadsheet_versions",
  "run_code",
]);

// 所有会修改工作区文件的工具（包括 Excel 写入 + 文本写入），用于恢复 affected files
const _ALL_WRITE_TOOL_NAMES = new Set([
  ...Array.from(_EXCEL_WRITE_TOOL_NAMES),
  "write_text_file", "edit_text_file",
]);

const _MAX_DIFFS_IN_STORE = 500;

// 仅由 SSE 事件产生的块类型，不持久化到后端消息存储。
// 从后端刷新时，必须从已有缓存消息中带出，避免视觉数据丢失（如 SessionSync 检测到 inFlight→false 时 thinking 块消失）。
const _SSE_ONLY_BLOCK_TYPES = new Set([
  "thinking", "iteration", "approval_action", "subagent", "task_list",
  // verification_report 仅出现在历史缓存中，保留以便刷新时不丢旧卡片
  "token_stats", "status", "verification_report", "staging_hint", "memory_extracted",
  "llm_retry", "failure_guidance",
  "tool_notice", "reasoning_notice",
]);

// failure_guidance 与其他 SSE-only 块不同：后端会将其后端渲染为 text block（failure_guidance_text）。
// 合并时需要移除对应的后端 text block，避免重复。
const _SSE_ONLY_HAS_BACKEND_COUNTERPART = new Set(["failure_guidance"]);

/**
 * 将仅由 SSE 产生的块（thinking、iteration、approval_action、subagent）从 oldMessages 合并到 newMessages，
 * 使后端刷新不会丢失块数据。在 assistant 消息间按位置保留。
 */
function _preserveSseOnlyBlocks(
  oldMessages: Message[],
  newMessages: Message[],
): Message[] {
  // 鎸夐『搴忔敹闆嗘棫 assistant 娑堟伅
  const oldAssistant: AssistantBlock[][] = [];
  for (const m of oldMessages) {
    if (m.role === "assistant") oldAssistant.push(m.blocks);
  }
  if (oldAssistant.length === 0) return newMessages;

  // 同时保留用户消息的文件：内存中的消息可能含有更完整的 FileAttachment（含实际上传大小），
  // 而后端从通知字符串无法完全还原。
  const oldUserFiles: (FileAttachment[] | undefined)[] = [];
  for (const m of oldMessages) {
    if (m.role === "user") oldUserFiles.push(m.files);
  }

  let aIdx = 0;
  let uIdx = 0;
  const result = newMessages.map((msg) => {
    if (msg.role === "user") {
      const oldFiles = oldUserFiles[uIdx++];
      // 鑻ユ棫娑堟伅鏈夋枃浠惰€屾柊娑堟伅娌℃湁鎴栨洿灏戯紝浼樺厛淇濈暀鏃х殑
      if (oldFiles && oldFiles.length > 0 && (!msg.files || msg.files.length === 0)) {
        return { ...msg, files: oldFiles };
      }
      return msg;
    }
    if (msg.role !== "assistant") return msg;
    const oldBlocks = oldAssistant[aIdx++];
    if (!oldBlocks) return msg;

    // 鎸?toolCallId 寤虹珛鏃?tool_call 鍧楁槧灏勶紝鐢ㄤ簬鐘舵€佹仮澶嶃€?
    // SSE 浜嬩欢鎼哄甫鐨勭姸鎬侊紙error/result/status锛夋瘮鍚庣鎸佷箙鍖栨洿瀹屾暣锛屾鏄犲皠鐢ㄤ簬寤剁画璇ョ姸鎬併€?
    const oldToolCallMap = new Map<string, AssistantBlock>();
    for (const ob of oldBlocks) {
      if (ob.type === "tool_call" && ob.toolCallId) {
        oldToolCallMap.set(ob.toolCallId, ob);
      }
    }

    const recovered = blocksHaveProgress(msg.blocks);
    const sseBlocks = oldBlocks.filter((b) => {
      if (!_SSE_ONLY_BLOCK_TYPES.has(b.type)) return false;
      if (recovered && isFailureGuidanceBlock(b)) return false;
      return true;
    });
    if (sseBlocks.length === 0 && oldToolCallMap.size === 0) return msg;

    // 浠ユ棫鍧楅『搴忎负妯℃澘锛氫繚鐣欎粎 SSE 鐨勫潡涓嶅姩锛岀敤鍒锋柊鍚庣殑鍧楁浛鎹㈠悗绔寔涔呭寲鐨勫潡銆?
    const newBackendBlocks = msg.blocks.map((nb) => {
      // 寤剁画 SSE 浜х敓鐨?tool_call 鐘舵€侊紙閿欒鐘舵€併€佺粨鏋溿€侀敊璇俊鎭級锛屽悗绔浆鎹㈠彲鑳藉凡涓㈠け銆?
      if (
        nb.type === "tool_call"
        && nb.toolCallId
        && oldToolCallMap.has(nb.toolCallId)
      ) {
        const ob = oldToolCallMap.get(nb.toolCallId)!;
        if (ob.type === "tool_call") {
          // 鑻ユ棫鍧椾负閿欒鐘舵€佸垯淇濈暀锛堝悗绔宸茶В鏋愯皟鐢ㄦ€绘槸杩斿洖 "success"锛夈€?
          if (ob.status === "error" && nb.status === "success") {
            return { ...nb, status: ob.status, result: ob.result, error: ob.error };
          }
          // 鑻ユ柊鍧楃己灏戠粨鏋滄枃鏈垯娌跨敤鏃х殑
          if (!nb.result && ob.result) {
            return { ...nb, result: ob.result };
          }
        }
      }
      return nb;
    });

    if (sseBlocks.length === 0) {
      // 鏃犱粎 SSE 鐨勫潡闇€瑕佹寜浣嶅悎骞讹紝浣嗕笂闈㈠彲鑳藉凡淇ˉ浜?tool_call 鐘舵€併€?
      return { ...msg, blocks: newBackendBlocks };
    }

    let ni = 0;
    const merged: AssistantBlock[] = [];

    for (const ob of oldBlocks) {
      if (_SSE_ONLY_BLOCK_TYPES.has(ob.type)) {
        if (recovered && isFailureGuidanceBlock(ob)) continue;
        merged.push(ob);
        // failure_guidance 鏈夊悗绔寔涔呭寲瀵瑰簲鐨?text block锛屾秷璐瑰畠浠ラ伩鍏嶉噸澶?
        if (_SSE_ONLY_HAS_BACKEND_COUNTERPART.has(ob.type) && ni < newBackendBlocks.length) {
          ni++;
        }
      } else {
        if (ni < newBackendBlocks.length) {
          merged.push(newBackendBlocks[ni++]);
        }
      }
    }
    // 杩藉姞鍓╀綑鐨勬柊鍧楋紙濡傛棫娑堟伅涓渶鍚庝竴涓粎 SSE 鍧椾箣鍚庢柊澧炵殑 tool_calls锛夈€?
    while (ni < newBackendBlocks.length) {
      merged.push(newBackendBlocks[ni++]);
    }

    return { ...msg, blocks: merged };
  });

  // 鈹€鈹€ 杩藉姞鍚庣灏氭湭鎸佷箙鍖栫殑灏鹃儴鏃ф秷鎭?鈹€鈹€
  // 褰撳悗绔繑鍥炵殑娑堟伅灏戜簬鏈湴锛堝閿欒鍙戠敓鍚庡姪鎵嬫秷鎭湭琚寔涔呭寲锛夛紝
  // 棰濆鐨勬湰鍦版秷鎭紙鍚?failure_guidance 绛?SSE-only 鍧楋級浼氳涓婇潰鐨?map 涓㈠純銆?
  // 姝ゅ灏嗚繖浜涙湭鍖归厤鐨勫熬閮ㄦ棫娑堟伅杩藉姞鍥炵粨鏋滐紝閬垮厤閿欒鎻愮ず琚埛鏂版帀銆?
  if (aIdx < oldAssistant.length) {
    let consumedA = 0;
    let consumedU = 0;
    for (let i = 0; i < oldMessages.length; i++) {
      const m = oldMessages[i];
      if (m.role === "assistant") {
        if (consumedA < aIdx) { consumedA++; continue; }
      } else if (m.role === "user") {
        if (consumedU < uIdx) { consumedU++; continue; }
      }
      // 浠庣涓€鏉℃湭娑堣垂鐨勬秷鎭紑濮嬶紝妫€鏌ユ槸鍚︽湁鍊煎緱淇濈暀鐨?SSE-only 鍧?
      const trailing = oldMessages.slice(i);
      const hasPreservable = trailing.some(
        (tm) => tm.role === "assistant" && tm.blocks.some((b) => _SSE_ONLY_BLOCK_TYPES.has(b.type)),
      );
      if (hasPreservable) {
        const lastNew = [...newMessages].reverse().find((m) => m.role === "assistant");
        const dropStaleFailure = lastNew ? blocksHaveProgress(lastNew.blocks) : false;
        result.push(
          ...trailing.filter((tm) => {
            if (!dropStaleFailure || tm.role !== "assistant") return true;
            return !tm.blocks.some(isFailureGuidanceBlock) || blocksHaveProgress(tm.blocks);
          }),
        );
      }
      break;
    }
  }

  return result;
}

interface BackendConversionResult {
  messages: Message[];
  recoveredDiffs: ExcelDiffEntry[];
  recoveredFilePaths: string[];
}

function _normalizeRecoveredPath(rawPath: string): string | null {
  return toPublicFileIdentity(rawPath);
}

function _parseLooseJsonObject(raw: string): Record<string, unknown> | null {
  const text = String(raw || "").trim();
  if (!text || !text.startsWith("{")) return null;
  try {
    const parsed = JSON.parse(text);
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      return parsed as Record<string, unknown>;
    }
  } catch {
    // 缁х画鍚戜笅
  }
  for (let i = text.length - 1; i >= 1; i--) {
    if (text[i] !== "}") continue;
    try {
      const parsed = JSON.parse(text.slice(0, i + 1));
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
        return parsed as Record<string, unknown>;
      }
    } catch {
      // 缁х画鏌ユ壘鍚堟硶 JSON 鍚庣紑杈圭晫
    }
  }
  return null;
}

function _normalizeDiffCellValue(value: unknown): string | number | boolean | null {
  if (value === null || value === undefined) return null;
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return value;
  }
  return String(value);
}

function _buildRecoveredDiffFromToolResult(
  toolCallId: string,
  args: Record<string, unknown>,
  toolResultText: string,
): ExcelDiffEntry | null {
  const parsed = _parseLooseJsonObject(toolResultText);
  if (!parsed) return null;
  const diffData = parsed._excel_diff as Record<string, unknown> | undefined;
  if (!diffData || typeof diffData !== "object") return null;
  const rawChanges = diffData.changes;
  if (!Array.isArray(rawChanges) || rawChanges.length === 0) return null;

  const filePathFromDiff = typeof diffData.file_path === "string" ? diffData.file_path : "";
  const filePathFromArgs = typeof args.file_path === "string" ? args.file_path : "";
  const filePath = _normalizeRecoveredPath(filePathFromDiff || filePathFromArgs);
  if (!filePath) return null;

  const changes = rawChanges
    .map((item) => {
      const row = item as Record<string, unknown>;
      const cell = typeof row.cell === "string" ? row.cell : "";
      if (!cell) return null;
      return {
        cell,
        old: _normalizeDiffCellValue(row.old),
        new: _normalizeDiffCellValue(row.new),
      };
    })
    .filter((x): x is { cell: string; old: string | number | boolean | null; new: string | number | boolean | null } => Boolean(x));

  if (changes.length === 0) return null;

  return {
    toolCallId,
    filePath,
    sheet: typeof diffData.sheet === "string" ? diffData.sheet : "",
    affectedRange: typeof diffData.affected_range === "string" ? diffData.affected_range : "",
    changes,
    // 鍘嗗彶娑堟伅涓嶅寘鍚簿纭?diff 鏃堕棿鎴筹紝鎭㈠鏃朵娇鐢ㄥ綋鍓嶆椂闂淬€?
    timestamp: Date.now(),
  };
}

/**
 * 浠庡伐鍏风粨鏋?JSON 涓仮澶?_text_diff 鍒?ExcelStore锛坵rite_text_file / edit_text_file锛夈€?
 */
function _recoverTextDiffFromToolResult(
  toolCallId: string,
  toolResultText: string,
): void {
  const parsed = _parseLooseJsonObject(toolResultText);
  if (!parsed) return;
  const td = parsed._text_diff as Record<string, unknown> | undefined;
  if (!td || typeof td !== "object") return;
  const hunks = td.hunks;
  if (!Array.isArray(hunks) || hunks.length === 0) return;
  const filePath = typeof td.file_path === "string" ? td.file_path : "";
  if (!filePath) return;
  useExcelStore.getState().addTextDiff({
    toolCallId,
    filePath,
    hunks: hunks as string[],
    additions: (td.additions as number) || 0,
    deletions: (td.deletions as number) || 0,
    truncated: !!td.truncated,
    timestamp: Date.now(),
  });
}

function _isToolResultError(content: string): boolean {
  // 绠€鍗曞惎鍙戯細妫€鏌ュ伐鍏风粨鏋?JSON 鏄惁鍚《灞?"status": "error"銆?
  // 涓庡悗绔害瀹氫竴鑷达細澶辫触鐨勫伐鍏疯皟鐢ㄤ互 {"status": "error", "message": "..."} 浣滀负宸ュ叿娑堟伅鍐呭銆?
  const text = content.trim();
  if (!text.startsWith("{")) return false;
  try {
    const parsed = JSON.parse(text);
    return parsed && typeof parsed === "object" && parsed.status === "error";
  } catch {
    // 鍥為€€锛氬鎴柇鎴栧ぇ缁撴灉鍋氱畝鍗曞瓙涓叉鏌?
    return /"status"\s*:\s*"error"/.test(text.slice(0, 200));
  }
}

function _extractFileAttachmentsFromContent(
  rawContent: string,
): { content: string; files: FileAttachment[] } {
  return extractFileAttachmentsFromContent(rawContent);
}

function _resolveBackendMessageId(msg: Record<string, unknown>): string {
  const messageId =
    typeof msg.message_id === "string"
      ? msg.message_id.trim()
      : typeof msg.id === "string"
        ? msg.id.trim()
        : "";
  return messageId || _nextId();
}

function _resolveBackendTimestamp(msg: Record<string, unknown>): number | undefined {
  const raw = msg.created_at ?? msg.timestamp;
  if (typeof raw === "number" && Number.isFinite(raw)) {
    return raw > 10_000_000_000 ? raw : raw * 1000;
  }
  if (typeof raw === "string" && raw.trim()) {
    const parsed = Date.parse(raw);
    if (Number.isFinite(parsed)) return parsed;
  }
  return undefined;
}

function _convertBackendMessages(raw: unknown[]): BackendConversionResult {
  const resolvedToolCallIds = new Set<string>();
  const toolResultByCallId = new Map<string, string>();
  const toolErrorCallIds = new Set<string>();
  for (const item of raw) {
    const msg = item as Record<string, unknown>;
    if (msg.role === "tool" && typeof msg.tool_call_id === "string") {
      resolvedToolCallIds.add(msg.tool_call_id);
      const resultText = typeof msg.content === "string"
        ? msg.content
        : JSON.stringify(msg.content ?? "");
      toolResultByCallId.set(msg.tool_call_id, resultText);
      if (_isToolResultError(resultText)) {
        toolErrorCallIds.add(msg.tool_call_id);
      }
    }
  }

  const result: Message[] = [];
  const recoveredDiffs: ExcelDiffEntry[] = [];
  const recoveredFilePaths = new Set<string>();
  for (const item of raw) {
    const msg = item as Record<string, unknown>;
    if (msg.role === "assistant" && msg._ui_hidden === true) continue;
    const backendMessageId = _resolveBackendMessageId(msg);
    const role = msg.role as string;
    if (role === "user") {
      if (isHiddenBackendUserMessage(msg)) continue;
      let content: string;
      if (typeof msg.content === "string") {
        content = stripImageSentPlaceholder(msg.content);
        // 跳过仅包含系统注入图片的消息（C 通道降级），其整条内容仅为占位符时跳过。
        if (!content) continue;
      } else if (Array.isArray(msg.content)) {
        // 澶氭ā鎬佹秷鎭紙鏂囨湰 + image_url 閮ㄥ垎锛夈€備粎鎻愬彇鏂囨湰閮ㄥ垎锛涘皢 image_url 閮ㄥ垎鏇挎崲涓虹煭鍗犱綅绗︼紝閬垮厤鍘熷 base64 娉勯湶鍒?UI銆?
        const textParts: string[] = [];
        let imageCount = 0;
        for (const part of msg.content as Record<string, unknown>[]) {
          if (part.type === "text" && typeof part.text === "string") {
            textParts.push(part.text as string);
          } else if (part.type === "image_url" || part.type === "image") {
            imageCount++;
          }
        }
        const hasText = textParts.some((t) => t.trim().length > 0);
        // 璺宠繃绯荤粺娉ㄥ叆鐨勭函鍥剧墖娑堟伅锛圕 閫氶亾 add_user_message 浜х墿锛夛細
        // 杩欎簺娑堟伅浠呭惈 image_url 閮ㄥ垎銆佹棤鏂囨湰锛岀敱宸ュ叿鎵ц鏃惰嚜鍔ㄦ敞鍏ワ紝
        // 涓嶅簲鍦?UI 涓樉绀轰负鐢ㄦ埛鍙戦€佺殑姘旀场銆?
        if (imageCount > 0 && !hasText) {
          continue;
        }
        content = textParts.join("\n").trim() || "(多模态消息)";
      } else {
        content = JSON.stringify(msg.content ?? "");
      }
      // 从内容中嵌入的上传通知提取文件附件，并去掉后台注入的技能目录。
      const extracted = _extractFileAttachmentsFromContent(content);
      const visible = stripInjectedUserPromptBlocks(extracted.content);
      if (!visible && extracted.files.length === 0) continue;
      const userMsg: Message = {
        id: backendMessageId,
        role: "user",
        content: visible,
        timestamp: _resolveBackendTimestamp(msg),
      };
      const action = msg._workbook_action;
      if (action && typeof action === "object" && typeof (action as Record<string, unknown>).operation === "string") {
        userMsg.workbookAction = action as import("@/lib/workbook-handoff").WorkbookActionContext;
      }
      const context = msg._workbook_context as import("@/lib/workbook-context").WorkbookMessageContext | undefined;
      if (context && Array.isArray(context.sheet_contexts) && context.sheet_contexts.length <= 3) {
        userMsg.workbookContext = context;
      }
      if (extracted.files.length > 0) {
        userMsg.files = extracted.files;
      }
      result.push(userMsg);
    } else if (role === "assistant") {
      const blocks: AssistantBlock[] = [];
      const affectedFilePaths = new Set<string>();
      if (msg.content && typeof msg.content === "string") {
        const hydrated = hydrateFailureGuidanceFromText(msg.content);
        blocks.push(hydrated ?? { type: "text", content: msg.content });
      }
      if (Array.isArray(msg.tool_calls)) {
        for (const tc of msg.tool_calls as Record<string, unknown>[]) {
          const fn = tc.function as Record<string, unknown> | undefined;
          const tcId = tc.id as string | undefined;
          const hasResult = tcId ? resolvedToolCallIds.has(tcId) : true;
          const toolName = (fn?.name as string) ?? "unknown";
          let args: Record<string, unknown> = {};
          try {
            args = fn?.arguments ? JSON.parse(fn.arguments as string) : {};
          } catch {
            args = {};
          }
          const isError = tcId ? toolErrorCallIds.has(tcId) : false;
          const parentCallId = typeof tc.parent_call_id === "string" && tc.parent_call_id
            ? tc.parent_call_id
            : undefined;
          blocks.push({
            type: "tool_call",
            toolCallId: tcId,
            name: toolName,
            args,
            status: hasResult ? (isError ? "error" : "success") : "error",
            result: hasResult && tcId ? toolResultByCallId.get(tcId) : undefined,
            parentCallId,
          });
          // 从 offer_download 结果恢复 file_download 块（兼容旧 _file_download 与提升后的顶层字段）
          if (toolName === "offer_download" && hasResult && tcId) {
            const dlResultText = toolResultByCallId.get(tcId);
            if (dlResultText) {
              try {
                const dlParsed = JSON.parse(dlResultText) as Record<string, unknown> | null;
                const magic = dlParsed?._file_download as Record<string, unknown> | undefined;
                const filePath =
                  (typeof magic?.file_path === "string" && magic.file_path) ||
                  (typeof dlParsed?.file_path === "string" ? dlParsed.file_path : "");
                if (filePath) {
                  const filename =
                    (typeof magic?.filename === "string" && magic.filename) ||
                    (typeof dlParsed?.filename === "string" ? dlParsed.filename : "") ||
                    displayFileName(filePath) ||
                    "download";
                  const description =
                    (typeof magic?.description === "string" && magic.description) ||
                    (typeof dlParsed?.description === "string" ? dlParsed.description : "") ||
                    "";
                  blocks.push({
                    type: "file_download",
                    toolCallId: tcId,
                    filePath,
                    filename,
                    description,
                  });
                }
              } catch { /* ignore parse errors */ }
            }
          }
          if (_ALL_WRITE_TOOL_NAMES.has(toolName) && hasResult && tcId) {
            for (const ident of collectHistoryAffectedFiles(toolName, args, _ALL_WRITE_TOOL_NAMES)) {
              affectedFilePaths.add(ident);
              recoveredFilePaths.add(ident);
            }
            const toolResultText = toolResultByCallId.get(tcId);
            if (toolResultText) {
              // 不再从 tool JSON 正则捞所有 .xlsx（cow_mapping / 绝对 staging 路径会泄漏副本）
              const recoveredDiff = _buildRecoveredDiffFromToolResult(
                tcId,
                args,
                toolResultText,
              );
              if (recoveredDiff) {
                recoveredDiffs.push(recoveredDiff);
              }
              // 鎭㈠鏂囨湰鏂囦欢 diff锛坵rite_text_file / edit_text_file锛?
              _recoverTextDiffFromToolResult(tcId, toolResultText);
            }
          }
        }
      }
      const prev = result[result.length - 1];
      if (prev && prev.role === "assistant") {
        prev.blocks = [...prev.blocks, ...blocks];
        if (affectedFilePaths.size > 0) {
          prev.affectedFiles = mergeAffectedFiles(prev.affectedFiles ?? [], Array.from(affectedFilePaths));
        }
      } else {
        const newMsg: Message = {
          id: backendMessageId,
          role: "assistant",
          blocks,
          timestamp: _resolveBackendTimestamp(msg),
        };
        if (affectedFilePaths.size > 0) {
          (newMsg as Extract<Message, { role: "assistant" }>).affectedFiles = mergeAffectedFiles(
            [],
            Array.from(affectedFilePaths),
          );
        }
        result.push(newMsg);
      }
    }
  }
  // 鍚庣娑堟伅涓嶆惡甯︽椂闂存埑锛屼负鎭㈠鐨勬秷鎭悎鎴愯繎浼兼椂闂达紝浣挎椂闂村垎闅旂嚎鑳芥甯告樉绀恒€?
  // 姣忎釜瀵硅瘽杞锛坲ser鈫抋ssistant锛夐棿闅?6 鍒嗛挓锛? TIMESTAMP_GAP_MS 5 鍒嗛挓锛夛紝
  // 鍚屼竴杞鍐呭叡浜椂闂存埑銆?
  if (result.length > 0) {
    const TURN_GAP = 6 * 60 * 1000;
    // 缁熻杞鏁帮紙姣忎釜 user 娑堟伅寮€濮嬩竴涓柊杞锛?
    let turnCount = 0;
    for (const m of result) {
      if (m.role === "user") turnCount++;
    }
    const now = Date.now();
    const baseTime = now - Math.max(turnCount - 1, 0) * TURN_GAP;
    let currentTurn = -1;
    for (const m of result) {
      if (m.role === "user") currentTurn++;
      if (!m.timestamp) m.timestamp = baseTime + currentTurn * TURN_GAP;
    }
  }
  return {
    messages: result,
    recoveredDiffs,
    recoveredFilePaths: Array.from(recoveredFilePaths),
  };
}

/** Merge two chronological UI pages without duplicating overlap rows. */
function _mergeMessagePages(older: Message[], newer: Message[]): Message[] {
  const merged = [...older];
  const indexById = new Map(merged.map((message, index) => [message.id, index]));
  for (const message of newer) {
    const existingIndex = indexById.get(message.id);
    if (existingIndex !== undefined) {
      // The newer page has the authoritative tool-call status/result.
      merged[existingIndex] = message;
      continue;
    }
    const previous = merged[merged.length - 1];
    if (previous?.role === "assistant" && message.role === "assistant") {
      merged[merged.length - 1] = {
        ...previous,
        blocks: [...previous.blocks, ...message.blocks],
        timestamp: previous.timestamp ?? message.timestamp,
        affectedFiles: mergeAffectedFiles(previous.affectedFiles ?? [], message.affectedFiles ?? []),
      };
      indexById.set(message.id, merged.length - 1);
      continue;
    }
    indexById.set(message.id, merged.length);
    merged.push(message);
  }
  return merged;
}

function _mergeRecoveredExcelState(
  recoveredDiffs: ExcelDiffEntry[],
  recoveredFilePaths: string[],
  sessionId: string,
): void {
  // ExcelStore is global, so an old history request must never publish its
  // recovered diffs into the newly selected conversation.
  if (useChatStore.getState().loadedSessionId !== sessionId) return;
  if (recoveredDiffs.length === 0 && recoveredFilePaths.length === 0) return;
  const excelStore = useExcelStore.getState();
  // 文件路径相对来源会话的工作区，按来源会话键入桶，避免会话切换期间错挂。
  const sourceWorkspaceKey = workspaceKeyForSessionId(sessionId);
  for (const filePath of recoveredFilePaths) {
    const normalized = _normalizeRecoveredPath(filePath);
    if (!normalized) continue;
    const filename = normalized.split("/").pop() || normalized;
    excelStore.addRecentFileIfNotDismissed({ path: normalized, filename }, sourceWorkspaceKey);
  }
  if (recoveredDiffs.length === 0) return;
  useExcelStore.setState((state) => {
    const existing = state.diffs ?? [];
    const seen = new Set(
      existing.map((d) => `${d.toolCallId}::${d.filePath}::${d.sheet}::${d.affectedRange}`),
    );
    const merged = [...existing];
    for (const diff of recoveredDiffs) {
      const key = `${diff.toolCallId}::${diff.filePath}::${diff.sheet}::${diff.affectedRange}`;
      if (seen.has(key)) continue;
      seen.add(key);
      merged.push(diff);
    }
    if (merged.length === existing.length) return {} as Partial<typeof state>;
    return { diffs: merged.slice(-_MAX_DIFFS_IN_STORE) };
  });
}

/**
 * 浠庡悗绔寔涔呭寲鐨?excel-events 绔偣鎭㈠ diff 鍜屾敼鍔ㄦ枃浠跺埌 store銆?
 * 杩斿洖鐨勬暟鎹潵鑷?SQLite锛屼笉渚濊禆鍓嶇鎺ㄦ柇锛岄噸鍚悗 100% 鍙仮澶嶃€?
 */
async function _loadPersistedExcelEvents(sessionId: string): Promise<void> {
  try {
    const { diffs, previews, affected_files } = await fetchSessionExcelEvents(sessionId);
    if (diffs.length === 0 && previews.length === 0 && affected_files.length === 0) return;
    if (useChatStore.getState().loadedSessionId !== sessionId) return;

    const excelStore = useExcelStore.getState();
    const sourceWorkspaceKey = workspaceKeyForSessionId(sessionId);
    for (const fp of affected_files) {
      if (!fp) continue;
      const filename = fp.split("/").pop() || fp;
      excelStore.addRecentFileIfNotDismissed({ path: fp, filename }, sourceWorkspaceKey);
    }

    if (diffs.length > 0) {
      const converted: ExcelDiffEntry[] = diffs.map((d) => ({
        toolCallId: d.tool_call_id,
        filePath: d.file_path,
        sheet: d.sheet,
        affectedRange: d.affected_range,
        changes: d.changes.map((c) => ({
          cell: c.cell,
          old: c.old,
          new: c.new,
        })),
        timestamp: d.timestamp ? new Date(d.timestamp).getTime() : Date.now(),
      }));
      _mergeRecoveredExcelState(converted, [], sessionId);
    }

    // 鎭㈠棰勮鏁版嵁鍒?excel-store
    if (previews.length > 0) {
      for (const p of previews) {
        excelStore.addPreview({
          toolCallId: p.tool_call_id,
          filePath: p.file_path,
          sheet: p.sheet,
          columns: p.columns,
          rows: p.rows,
          totalRows: p.total_rows,
          truncated: p.truncated,
        });
      }
    }

    // 鎭㈠娑堟伅涓婄殑 affectedFiles 寰界珷
    _restoreAffectedFilesOnMessages(diffs, affected_files, sessionId);
  } catch {
    // 绔偣涓嶅彲鐢ㄦ垨鏍煎紡閿欒鏃堕潤榛橀檷绾?
  }
}

/**
 * 灏嗘寔涔呭寲鐨?excel 浜嬩欢涓殑鏂囦欢璺緞锛屽洖濉埌瀵瑰簲 assistant 娑堟伅鐨?affectedFiles銆?
 * 注意重试"涉及文件"块眼能正确显示。
 */
function _restoreAffectedFilesOnMessages(
  diffs: { tool_call_id: string; file_path: string }[],
  allAffectedFiles: string[],
  sessionId: string,
): void {
  const current = useChatStore.getState().loadedSessionId;
  if (current !== sessionId) return;

  const toolCallFileMap = new Map<string, Set<string>>();
  for (const d of diffs) {
    if (!d.tool_call_id || !d.file_path) continue;
    let set = toolCallFileMap.get(d.tool_call_id);
    if (!set) {
      set = new Set();
      toolCallFileMap.set(d.tool_call_id, set);
    }
    set.add(d.file_path);
  }
  if (toolCallFileMap.size === 0 && allAffectedFiles.length === 0) return;

  const { messages } = useChatStore.getState();
  let changed = false;
  const updated = messages.map((m) => {
    if (m.role !== "assistant") return m;
    const incoming: string[] = [];
    for (const block of m.blocks) {
      if (block.type !== "tool_call" || !block.toolCallId) continue;
      const files = toolCallFileMap.get(block.toolCallId);
      if (files) incoming.push(...files);
    }
    const merged = mergeAffectedFiles(m.affectedFiles ?? [], incoming);
    const before = m.affectedFiles ?? [];
    if (merged.length === before.length && merged.every((f, i) => f === before[i])) return m;
    changed = true;
    return { ...m, affectedFiles: merged };
  });

  if (changed) {
    // 寮傛鎭㈠鏈熼棿浼氳瘽鍙兘宸插垏鎹€?
    const latest = useChatStore.getState().loadedSessionId;
    if (latest !== sessionId) return;

    useChatStore.getState().setMessages(updated);
    const store = useChatStore.getState();
    if (store.loadedSessionId === sessionId) {
      saveCachedMessages(sessionId, updated).catch(() => {});
    }
  }
}

/**
 * 寮傛鍔犺浇锛氫紭鍏?IDB锛屽洖閫€鍒板悗绔?API銆備粎褰撲細璇濅粛涓哄綋鍓嶄細璇濇椂鏇存柊 store銆?
 */
async function _loadMessagesAsyncWithOptions(
  sessionId: string,
  opts: LoadMessagesOptions,
): Promise<void> {
  const maybeBackfillTitle = (messages: Message[]) => {
    const store = useSessionStore.getState();
    const current = store.sessions.find((s) => s.id === sessionId);
    if (current && !isFallbackSessionTitle(current.title, sessionId)) {
      return;
    }
    const title = deriveSessionTitleFromMessages(messages);
    if (title) {
      store.updateSessionTitle(sessionId, title);
    }
  };

  const shouldPreferCache = opts.preferCache !== false;
  const shouldReplaceVisibleMessages = opts.replaceVisibleMessages === true;

  // 浼樺厛灏濊瘯 IndexedDB
  if (shouldPreferCache) {
    const cached = await loadCachedMessages(sessionId);
    if (cached && cached.length > 0) {
      _sessionMessages.set(sessionId, cached);
      maybeBackfillTitle(cached);
      const store = useChatStore.getState();
      if (
        store.loadedSessionId === sessionId
        && !store.isStreaming
        && !store.abortController
        && (store.messages.length === 0 || shouldReplaceVisibleMessages)
      ) {
        useChatStore.getState().setMessages(cached);
      }
      // 娑堟伅宸插姞杞藉埌 store锛岀珛鍗虫仮澶?Excel 浜嬩欢骞跺洖濉?affectedFiles
      _loadPersistedExcelEvents(sessionId).catch(() => {});
      return;
    }
  }

  // 鍥為€€鍒板悗绔?API
  try {
    const result = await fetchSessionMessages(sessionId, MESSAGE_PAGE_SIZE, 0, {
      tail: true,
      withMeta: true,
    });
    const page = Array.isArray(result)
      ? { messages: result, total: result.length, offset: 0, hasMore: false }
      : result;
    const raw = page.messages;
    _sessionMessageMeta.set(sessionId, {
      total: page.total,
      offset: page.offset,
      hasMore: page.hasMore,
    });
    if (raw.length === 0) {
      // An empty response is a valid blank conversation. When this is a
      // revalidation, clear a stale local cache instead of leaving the old
      // transcript visible forever.
      if (shouldReplaceVisibleMessages) {
        const current = useChatStore.getState();
        if (current.loadedSessionId === sessionId && !current.abortController) {
          _sessionMessages.set(sessionId, []);
          _sessionMessageMeta.set(sessionId, { total: 0, offset: 0, hasMore: false });
          saveCachedMessages(sessionId, []).catch(() => {});
          current.setMessages([]);
        }
      }
      if (useChatStore.getState().loadedSessionId === sessionId) {
        useChatStore.setState({ messageLoadError: null });
      }
      return;
    }
    const {
      messages,
      recoveredDiffs,
      recoveredFilePaths,
    } = _convertBackendMessages(raw);
    if (useChatStore.getState().loadedSessionId !== sessionId) {
      // Keep the per-session cache warm, but do not mutate visible/global UI
      // state after the user has switched conversations.
      return;
    }
    _mergeRecoveredExcelState(recoveredDiffs, recoveredFilePaths, sessionId);
    // 鏇挎崲鍙娑堟伅鏃讹紝浠庡綋鍓?store 甯﹀嚭浠?SSE 鐨勫潡锛坱hinking銆乮teration銆乤pproval_action锛夛紝
    // 閬垮厤鍚庣鍒锋柊鏃惰涓㈠純銆?
    const store = useChatStore.getState();
    const shouldReplace =
      store.loadedSessionId === sessionId
      && !store.isStreaming
      && !store.abortController
      && (store.messages.length === 0 || shouldReplaceVisibleMessages);
    let finalMessages =
      shouldReplace && shouldReplaceVisibleMessages && store.messages.length > 0
        ? _preserveSseOnlyBlocks(store.messages, messages)
        : messages;
    // A previous visit may have a complete transcript in memory/IndexedDB,
    // while the revalidation intentionally fetched only the newest page. Keep
    // the cached prefix when it can still fit the server total so the first
    // background refresh does not make older visible messages disappear.
    if (
      shouldReplace
      && shouldReplaceVisibleMessages
      && page.total >= store.messages.length
      && store.messages.length > messages.length
    ) {
      const freshIds = new Set(messages.map((message) => message.id));
      const cachedPrefix = store.messages.filter((message) => !freshIds.has(message.id));
      finalMessages = _mergeMessagePages(cachedPrefix, messages);
    }
    const visibleHasMore = page.hasMore;
    _sessionMessageMeta.set(sessionId, {
      total: page.total,
      offset: visibleHasMore ? page.offset : 0,
      hasMore: visibleHasMore,
    });
    _sessionMessages.set(sessionId, finalMessages);
    // A tail page may not contain the first user turn; deriving a title from
    // it would rename an existing conversation to an unrelated recent prompt.
    if (!visibleHasMore) maybeBackfillTitle(finalMessages);
    saveCachedMessages(sessionId, finalMessages).catch(() => {});
    if (shouldReplace) {
      // 鑻ュ悎骞剁粨鏋滀笌 store 褰撳墠鍐呭璇箟绛変环鍒欓伩鍏嶈瑙夐棯鐑併€?
      // 瀹屾暣娣卞害姣旇緝鎴愭湰楂橈紝鏁呭仛杞婚噺缁撴瀯妫€鏌ワ細娑堟伅鏁般€佽鑹层€佸潡鏁颁笌绫诲瀷銆佹枃鏈唴瀹逛竴鑷淬€?
      const cur = useChatStore.getState().messages;
      let equiv = cur.length === finalMessages.length;
      if (equiv) {
        for (let i = 0; i < cur.length && equiv; i++) {
          const cm = cur[i];
          const fm = finalMessages[i];
          if (cm.role !== fm.role) { equiv = false; break; }
          if (cm.role === "user" && fm.role === "user") {
            equiv = cm.content === fm.content;
          } else if (cm.role === "assistant" && fm.role === "assistant") {
            if (cm.blocks.length !== fm.blocks.length) { equiv = false; break; }
            for (let j = 0; j < cm.blocks.length && equiv; j++) {
              const cb = cm.blocks[j];
              const fb = fm.blocks[j];
              if (cb.type !== fb.type) { equiv = false; break; }
              if (cb.type === "tool_call" && fb.type === "tool_call") {
                equiv = cb.status === fb.status && cb.toolCallId === fb.toolCallId;
              } else if (cb.type === "text" && fb.type === "text") {
                equiv = cb.content === fb.content;
              } else if (cb.type === "thinking" && fb.type === "thinking") {
                equiv = cb.content === fb.content;
              }
            }
          }
        }
      }
      if (!equiv) {
        useChatStore.getState().setMessages(finalMessages);
      }
      useChatStore.setState({
        loadedMessageTotal: page.total,
        hasMoreMessages: visibleHasMore,
      });
    }
    if (useChatStore.getState().loadedSessionId === sessionId) {
      useChatStore.setState({ messageLoadError: null });
    }
    // 娑堟伅宸插姞杞斤紝绔嬪嵆鎭㈠ Excel 浜嬩欢骞跺洖濉?affectedFiles
    _loadPersistedExcelEvents(sessionId).catch(() => {});
  } catch (error) {
    // Keep the cached/optimistic transcript visible, but expose the failure so
    // the UI can tell the user why a refresh is taking time and offer retry.
    const current = useChatStore.getState();
    if (current.loadedSessionId === sessionId) {
      const detail = error instanceof Error ? error.message : "网络请求失败";
      useChatStore.setState({ messageLoadError: `历史消息加载失败：${detail}` });
    }
  }
}

export async function refreshSessionMessagesFromBackend(
  sessionId: string,
): Promise<void> {
  const existing = _messageRefreshes.get(sessionId);
  if (existing) return existing;

  const refresh = (async () => {
    const current = useChatStore.getState();
    if (current.loadedSessionId === sessionId) {
      useChatStore.setState({ isLoadingMessages: true, messageLoadError: null });
    }
    try {
      await _loadMessagesAsyncWithOptions(sessionId, {
        preferCache: false,
        replaceVisibleMessages: true,
      });
    } finally {
      const latest = useChatStore.getState();
      if (latest.loadedSessionId === sessionId) {
        useChatStore.setState({ isLoadingMessages: false });
      }
    }
  })();
  _messageRefreshes.set(sessionId, refresh);
  try {
    await refresh;
  } finally {
    if (_messageRefreshes.get(sessionId) === refresh) _messageRefreshes.delete(sessionId);
  }
}

async function _loadOlderMessages(): Promise<void> {
  const state = useChatStore.getState();
  const sessionId = state.loadedSessionId;
  const meta = sessionId ? _sessionMessageMeta.get(sessionId) : undefined;
  if (!sessionId || !meta || !meta.hasMore || state.isLoadingOlderMessages) return;
  if (meta.offset <= 0) {
    useChatStore.setState({ hasMoreMessages: false });
    _sessionMessageMeta.set(sessionId, { ...meta, hasMore: false });
    return;
  }

  useChatStore.setState({ isLoadingOlderMessages: true });
  try {
    const nextLimit = Math.min(MESSAGE_PAGE_SIZE, meta.offset);
    const nextOffset = Math.max(0, meta.offset - nextLimit);
    const result = await fetchSessionMessages(sessionId, nextLimit, nextOffset, {
      withMeta: true,
    });
    const page = Array.isArray(result)
      ? { messages: result, total: result.length, offset: nextOffset, hasMore: nextOffset > 0 }
      : result;
    const latest = useChatStore.getState();
    if (latest.loadedSessionId !== sessionId) return;
    const converted = _convertBackendMessages(page.messages);
    const merged = _mergeMessagePages(converted.messages, latest.messages);
    _sessionMessages.set(sessionId, merged);
    _sessionMessageMeta.set(sessionId, {
      total: page.total,
      offset: page.offset,
      hasMore: page.hasMore,
    });
    useChatStore.setState({
      ..._setMessagesSnapshot(merged),
      loadedMessageTotal: page.total,
      hasMoreMessages: page.hasMore,
    });
    saveCachedMessages(sessionId, merged).catch(() => {});
    _mergeRecoveredExcelState(converted.recoveredDiffs, converted.recoveredFilePaths, sessionId);
  } finally {
    if (useChatStore.getState().loadedSessionId === sessionId) {
      useChatStore.setState({ isLoadingOlderMessages: false });
    }
  }
}

export interface PipelineStatus {
  stage: string;
  message: string;
  startedAt: number;
}

export interface BatchProgress {
  batchIndex: number;
  batchTotal: number;
  batchItemName: string;
  batchStatus: "running" | "completed" | "failed";
  batchElapsed: number;
  message: string;
}

interface ChatState {
  messages: Message[];
  messageOrder: string[];
  messagesById: Record<string, Message>;
  messageIndexById: Record<string, number>;
  /** 当前 messages 绑定的会话。不是选中态事实源；选中态见 session-store.activeSessionId。 */
  loadedSessionId: string | null;
  activeStreamId: string | null;
  latestSeq: number;
  resumeFailedReason: string | null;
  isStreaming: boolean;
  pendingApproval: Approval | null;
  _lastDismissedApprovalId: string | null;
  pendingQuestion: Question | null;
  abortController: AbortController | null;
  pipelineStatus: PipelineStatus | null;
  batchProgress: BatchProgress | null;
  toolProgress: Record<string, { stage: string; message: string }>;
  isLoadingMessages: boolean;
  loadedMessageTotal: number | null;
  hasMoreMessages: boolean;
  isLoadingOlderMessages: boolean;
  /** Human-readable error from the latest history load/revalidation. */
  messageLoadError: string | null;

  setMessages: (messages: Message[]) => void;
  updateAssistantMessage: (
    messageId: string,
    updater: (message: Extract<Message, { role: "assistant" }>) => Extract<Message, { role: "assistant" }>,
  ) => void;
  addUserMessage: (id: string, content: string, files?: FileAttachment[], workbookAction?: import("@/lib/workbook-handoff").WorkbookActionContext, workbookContext?: import("@/lib/workbook-context").WorkbookMessageContext) => void;
  addAssistantMessage: (id: string) => void;
  appendBlock: (messageId: string, block: AssistantBlock) => void;
  updateLastBlock: (messageId: string, updater: (block: AssistantBlock) => AssistantBlock) => void;
  updateBlockByType: (messageId: string, blockType: string, updater: (block: AssistantBlock) => AssistantBlock) => void;
  upsertBlockByType: (messageId: string, blockType: string, block: AssistantBlock) => void;
  updateSubagentBlock: (
    messageId: string,
    conversationId: string | null,
    updater: (block: AssistantBlock) => AssistantBlock,
  ) => void;
  syncSubagentRuns: (sessionId: string, runs: SubagentRun[]) => void;
  updateToolCallBlock: (
    messageId: string,
    toolCallId: string | null,
    updater: (block: AssistantBlock) => AssistantBlock,
  ) => void;
  addAffectedFiles: (messageId: string, files: string[]) => void;
  retractLastThinking: (messageId: string) => void;
  setStreaming: (streaming: boolean) => void;
  setPendingApproval: (approval: Approval | null) => void;
  dismissApproval: (approvalId: string) => void;
  setPendingQuestion: (question: Question | null) => void;
  setAbortController: (controller: AbortController | null) => void;
  setPipelineStatus: (status: PipelineStatus | null) => void;
  setStreamState: (streamId: string | null, latestSeq: number) => void;
  markResumeFailed: (reason: string) => void;
  clearResumeFailed: () => void;
  setBatchProgress: (progress: BatchProgress | null) => void;
  setToolProgress: (toolCallId: string, progress: { stage: string; message: string }) => void;
  clearToolProgress: (toolCallId: string) => void;
  clearMessages: () => void;
  removeSessionCache: (sessionId: string) => void;
  switchSession: (sessionId: string | null) => void;
  clearAllHistory: () => Promise<void>;
  saveCurrentSession: () => void;
  bindLoadedSession: (sessionId: string | null) => void;
  loadOlderMessages: () => Promise<void>;
}

export const useChatStore = create<ChatState>((set, get) => ({
  messages: [],
  messageOrder: [],
  messagesById: {},
  messageIndexById: {},
  loadedSessionId: null,
  activeStreamId: null,
  latestSeq: 0,
  resumeFailedReason: null,
  isStreaming: false,
  pendingApproval: null,
  _lastDismissedApprovalId: null,
  pendingQuestion: null,
  abortController: null,
  pipelineStatus: null,
  batchProgress: null,
  toolProgress: {},
  isLoadingMessages: false,
  loadedMessageTotal: null,
  hasMoreMessages: false,
  isLoadingOlderMessages: false,
  messageLoadError: null,

  setMessages: (messages) => set(() => _setMessagesSnapshot(messages)),
  updateAssistantMessage: (messageId, updater) =>
    set((state) => {
      const patch = _patchMessageById(state, messageId, (message) => {
        if (message.role !== "assistant") return message;
        return updater(message);
      });
      return patch ?? {};
    }),
  addUserMessage: (id, content, files, workbookAction, workbookContext) =>
    set((state) => {
      const message: Message = { id, role: "user", content, files, timestamp: Date.now(), ...(workbookAction ? { workbookAction } : {}), ...(workbookContext ? { workbookContext } : {}) };
      const newOrder = [...state.messageOrder, id];
      const newById = { ...state.messagesById, [id]: message };
      return {
        messages: newOrder.map(mid => newById[mid]),
        messageOrder: newOrder,
        messageIndexById: { ...state.messageIndexById, [id]: state.messageOrder.length },
        messagesById: newById,
        loadedMessageTotal: state.loadedMessageTotal == null ? null : state.loadedMessageTotal + 1,
      };
    }),
  addAssistantMessage: (id) =>
    set((state) => {
      const message: Message = { id, role: "assistant", blocks: [], timestamp: Date.now() };
      const newOrder = [...state.messageOrder, id];
      const newById = { ...state.messagesById, [id]: message };
      return {
        messages: newOrder.map(mid => newById[mid]),
        messageOrder: newOrder,
        messageIndexById: { ...state.messageIndexById, [id]: state.messageOrder.length },
        messagesById: newById,
        loadedMessageTotal: state.loadedMessageTotal == null ? null : state.loadedMessageTotal + 1,
      };
    }),
  appendBlock: (messageId, block) =>
    set((state) => {
      const patch = _patchMessageById(state, messageId, (message) => {
        if (message.role !== "assistant") return message;
        return { ...message, blocks: [...message.blocks, block] };
      });
      return patch ?? {};
    }),
  updateLastBlock: (messageId, updater) =>
    set((state) => {
      const patch = _patchMessageById(state, messageId, (message) => {
        if (message.role !== "assistant" || message.blocks.length === 0) return message;
        const blocks = [...message.blocks];
        blocks[blocks.length - 1] = updater(blocks[blocks.length - 1]);
        return { ...message, blocks };
      });
      return patch ?? {};
    }),
  updateBlockByType: (messageId, blockType, updater) =>
    set((state) => {
      const patch = _patchMessageById(state, messageId, (message) => {
        if (message.role !== "assistant") return message;
        const idx = [...message.blocks].reverse().findIndex((b) => b.type === blockType);
        if (idx === -1) return message;
        const realIdx = message.blocks.length - 1 - idx;
        const blocks = [...message.blocks];
        blocks[realIdx] = updater(blocks[realIdx]);
        return { ...message, blocks };
      });
      return patch ?? {};
    }),
  upsertBlockByType: (messageId, blockType, block) =>
    set((state) => {
      const patch = _patchMessageById(state, messageId, (message) => {
        if (message.role !== "assistant") return message;
        const idx = [...message.blocks].reverse().findIndex((b) => b.type === blockType);
        if (idx === -1) {
          return { ...message, blocks: [...message.blocks, block] };
        }
        const realIdx = message.blocks.length - 1 - idx;
        const blocks = [...message.blocks];
        blocks[realIdx] = block;
        return { ...message, blocks };
      });
      return patch ?? {};
    }),
  updateSubagentBlock: (messageId, conversationId, updater) =>
    set((state) => {
      const patch = _patchMessageById(state, messageId, (message) => {
        if (message.role !== "assistant") return message;
        let targetIdx = -1;
        if (conversationId) {
          for (let i = message.blocks.length - 1; i >= 0; i--) {
            const b = message.blocks[i];
            if (b.type === "subagent" && b.conversationId === conversationId) {
              targetIdx = i;
              break;
            }
          }
          if (targetIdx === -1) return message;
        }
        if (targetIdx === -1) {
          for (let i = message.blocks.length - 1; i >= 0; i--) {
            const b = message.blocks[i];
            if (b.type === "subagent" && b.status === "running") {
              targetIdx = i;
              break;
            }
          }
        }
        if (targetIdx === -1) {
          for (let i = message.blocks.length - 1; i >= 0; i--) {
            if (message.blocks[i].type === "subagent") { targetIdx = i; break; }
          }
        }
        if (targetIdx === -1) return message;
        const blocks = [...message.blocks];
        blocks[targetIdx] = updater(blocks[targetIdx]);
        return { ...message, blocks };
      });
      return patch ?? {};
    }),
  syncSubagentRuns: (sessionId, runs) =>
    set((state) => {
      if (state.loadedSessionId !== sessionId) return {};
      const byId = new Map(runs.filter((run) => run.background).map((run) => [run.run_id, run]));
      let changed = false;
      const messages = state.messages.map((message) => {
        if (message.role !== "assistant") return message;
        let messageChanged = false;
        let affectedFiles = message.affectedFiles;
        const blocks = message.blocks.map((block): AssistantBlock => {
          if (block.type !== "subagent" || !block.conversationId) return block;
          const run = byId.get(block.conversationId);
          if (!run) return block;
          // 对话事件已收到终态时，较早发出的活动状态查询不能把它变回进行中。
          if (isSubagentActive(run.status) && block.status === "done" && block.stopReason) return block;
          const next: typeof block = {
            ...block,
            background: true,
            runStatus: run.status,
            status: isSubagentActive(run.status) ? "running" : "done",
            iterations: Math.max(block.iterations, run.iteration),
            toolCalls: Math.max(block.toolCalls, run.tool_calls),
            summary: run.result?.output || block.summary,
            success: run.result ? run.result.stop_reason === "completed" : undefined,
            stopReason: run.result?.stop_reason,
            diagnostic: run.result?.diagnostic || undefined,
          };
          const files = subagentChangedFiles(run);
          const merged = mergeAffectedFiles(affectedFiles ?? [], files);
          if (merged.length !== (affectedFiles?.length ?? 0)) {
            affectedFiles = merged;
            messageChanged = true;
          }
          if (next.background === block.background && next.runStatus === block.runStatus
            && next.status === block.status && next.iterations === block.iterations
            && next.toolCalls === block.toolCalls && next.summary === block.summary
            && next.success === block.success && next.stopReason === block.stopReason
            && next.diagnostic === block.diagnostic) return block;
          messageChanged = true;
          return next;
        });
        if (!messageChanged) return message;
        changed = true;
        return { ...message, blocks, affectedFiles };
      });
      return changed ? _setMessagesSnapshot(messages) : {};
    }),
  updateToolCallBlock: (messageId, toolCallId, updater) =>
    set((state) => {
      const patch = _patchMessageById(state, messageId, (message) => {
        if (message.role !== "assistant") return message;
        const blocks = [...message.blocks];
        const isActive = (s: string) => s === "running" || s === "pending" || s === "streaming";

        let targetIndex = -1;
        if (toolCallId) {
          for (let i = blocks.length - 1; i >= 0; i--) {
            const block = blocks[i];
            if (
              block.type === "tool_call"
              && isActive(block.status)
              && block.toolCallId === toolCallId
            ) {
              targetIndex = i;
              break;
            }
          }
        }

        if (targetIndex === -1) {
          for (let i = blocks.length - 1; i >= 0; i--) {
            const block = blocks[i];
            if (block.type === "tool_call" && isActive(block.status)) {
              targetIndex = i;
              break;
            }
          }
        }

        if (targetIndex === -1) return message;
        blocks[targetIndex] = updater(blocks[targetIndex]);
        return { ...message, blocks };
      });
      return patch ?? {};
    }),
  addAffectedFiles: (messageId, files) =>
    set((state) => {
      const patch = _patchMessageById(state, messageId, (message) => {
        if (message.role !== "assistant") return message;
        const merged = mergeAffectedFiles(message.affectedFiles ?? [], files);
        return { ...message, affectedFiles: merged };
      });
      return patch ?? {};
    }),
  retractLastThinking: (messageId) =>
    set((state) => {
      const patch = _patchMessageById(state, messageId, (message) => {
        if (message.role !== "assistant" || message.blocks.length === 0) return message;
        const blocks = [...message.blocks];
        const lastIdx = blocks.length - 1;
        if (blocks[lastIdx].type === "thinking" && blocks[lastIdx].duration == null) {
          blocks.pop();
          if (blocks.length > 0 && blocks[blocks.length - 1].type === "iteration") {
            blocks.pop();
          }
        }
        return { ...message, blocks };
      });
      return patch ?? {};
    }),
  setStreaming: (streaming) => set({ isStreaming: streaming }),
  setPendingApproval: (approval) => set({ pendingApproval: approval }),
  dismissApproval: (approvalId) => set({ pendingApproval: null, _lastDismissedApprovalId: approvalId }),
  setPendingQuestion: (question) => set({ pendingQuestion: question }),
  setAbortController: (controller) => set({ abortController: controller }),
  setPipelineStatus: (status) => set({ pipelineStatus: status }),
  setStreamState: (streamId, latestSeq) => set({
    activeStreamId: streamId,
    latestSeq: Math.max(0, latestSeq || 0),
  }),
  markResumeFailed: (reason) => set({ resumeFailedReason: reason || "unknown" }),
  clearResumeFailed: () => set({ resumeFailedReason: null }),
  setBatchProgress: (progress) => set({ batchProgress: progress }),
  setToolProgress: (toolCallId, progress) =>
    set((state) => ({
      toolProgress: { ...state.toolProgress, [toolCallId]: progress },
    })),
  clearToolProgress: (toolCallId) =>
    set((state) => {
      const { [toolCallId]: _, ...rest } = state.toolProgress;
      return { toolProgress: rest };
    }),
  clearMessages: () => {
    const { loadedSessionId } = get();
    if (loadedSessionId) {
      _sessionMessages.delete(loadedSessionId);
      _sessionMessageMeta.delete(loadedSessionId);
      deleteCachedMessages(loadedSessionId).catch(() => {});
    }
    set({
      ..._setMessagesSnapshot([]),
      isLoadingMessages: false,
      loadedMessageTotal: 0,
      hasMoreMessages: false,
      isLoadingOlderMessages: false,
      messageLoadError: null,
      pendingApproval: null,
      pendingQuestion: null,
      pipelineStatus: null,
      activeStreamId: null,
      latestSeq: 0,
      resumeFailedReason: null,
    });
  },

  removeSessionCache: (sessionId) => {
    _sessionMessages.delete(sessionId);
    _sessionMessageMeta.delete(sessionId);
    deleteCachedMessages(sessionId).catch(() => {});

    const state = get();
    if (state.loadedSessionId === sessionId) {
      set({
        loadedSessionId: null,
        ..._setMessagesSnapshot([]),
        isLoadingMessages: false,
        loadedMessageTotal: 0,
        hasMoreMessages: false,
        isLoadingOlderMessages: false,
        messageLoadError: null,
        pendingApproval: null,
        pendingQuestion: null,
        pipelineStatus: null,
        activeStreamId: null,
        latestSeq: 0,
        resumeFailedReason: null,
      });
    }
  },

  /** 清空所有会话历史：后端 + IndexedDB + 内存 + 本地 session 列表。 */
  clearAllHistory: async () => {
    const { stopGeneration } = await import("@/lib/chat-actions");
    if (get().abortController) {
      stopGeneration();
    }
    await clearAllSessions();
    _sessionMessages.clear();
    _sessionMessageMeta.clear();
    _messageRefreshes.clear();
    await clearAllCachedMessages();
    useSessionStore.getState().setSessions([]);
    useSessionStore.getState().setActiveSession(null);
    set({
      loadedSessionId: null,
      ..._setMessagesSnapshot([]),
      isLoadingMessages: false,
      loadedMessageTotal: null,
      hasMoreMessages: false,
      isLoadingOlderMessages: false,
      messageLoadError: null,
      pendingApproval: null,
      pendingQuestion: null,
      activeStreamId: null,
      latestSeq: 0,
      resumeFailedReason: null,
    });
  },

  saveCurrentSession: () => {
    const { loadedSessionId, messages } = get();
    if (loadedSessionId && messages.length > 0) {
      _sessionMessages.set(loadedSessionId, [...messages]);
      saveCachedMessages(loadedSessionId, messages).catch(() => {});
    }
  },

  bindLoadedSession: (sessionId) => {
    const state = get();
    if (state.loadedSessionId === sessionId) return;
    if (state.loadedSessionId && state.messages.length > 0) {
      _sessionMessages.set(state.loadedSessionId, [...state.messages]);
      saveCachedMessages(state.loadedSessionId, state.messages).catch(() => {});
    }
    set({ loadedSessionId: sessionId });
  },

  loadOlderMessages: _loadOlderMessages,

  switchSession: (sessionId) => {
    const state = get();

    // Do not bind a different session while an SSE stream is still writing to
    // the current one. SessionSync retries the switch when the stream closes;
    // changing loadedSessionId here would make that retry look like a no-op
    // and could leave the old transcript attached to the new session.
    if (state.abortController) {
      if (state.isLoadingMessages) useChatStore.setState({ isLoadingMessages: false });
      return;
    }

    // 已绑定该会话：流式输出中跳过；已有消息由 SessionSync 的显式
    // revalidation 负责更新，避免每次轮询都重建历史。
    if (sessionId === state.loadedSessionId) {
      if (!sessionId || state.messages.length > 0) return;
    }

    if (state.loadedSessionId && state.messages.length > 0) {
      _sessionMessages.set(state.loadedSessionId, [...state.messages]);
      saveCachedMessages(state.loadedSessionId, state.messages).catch(() => {});
    }

    useJevStore.getState().reset();

    const myVersion = ++_switchSessionVersion;

    const finishLoad = () => {
      if (_switchSessionVersion === myVersion && get().loadedSessionId === sessionId) {
        useChatStore.setState({ isLoadingMessages: false });
      }
    };

    // Cache is only the fast first paint. Always revalidate it from SQLite/API
    // in the background so a stale tab cannot hide messages until the user
    // sends another message or clicks another control.
    const revalidateFromBackend = () => {
      if (!sessionId || _switchSessionVersion !== myVersion) return;
      if (get().abortController) {
        finishLoad();
        return;
      }
      _loadMessagesAsyncWithOptions(sessionId, {
        preferCache: false,
        replaceVisibleMessages: true,
      }).finally(finishLoad);
    };

    const memCached = sessionId ? _sessionMessages.get(sessionId) : undefined;
    if (sessionId && memCached && memCached.length > 0) {
      set({
        loadedSessionId: sessionId,
        ..._setMessagesSnapshot(memCached),
        isLoadingMessages: true,
        loadedMessageTotal: _sessionMessageMeta.get(sessionId)?.total ?? memCached.length,
        hasMoreMessages: _sessionMessageMeta.get(sessionId)?.hasMore ?? false,
        isLoadingOlderMessages: false,
        messageLoadError: null,
        pendingApproval: null,
        pendingQuestion: null,
        pipelineStatus: null,
        resumeFailedReason: null,
      });
      _loadPersistedExcelEvents(sessionId).catch(() => {});
      revalidateFromBackend();
      return;
    }

    const loadAndSwitch = async () => {
      if (!sessionId) {
        set({
          loadedSessionId: null,
          ..._setMessagesSnapshot([]),
          isLoadingMessages: false,
          loadedMessageTotal: 0,
          hasMoreMessages: false,
          isLoadingOlderMessages: false,
          messageLoadError: null,
          pendingApproval: null,
          pendingQuestion: null,
          pipelineStatus: null,
          activeStreamId: null,
          latestSeq: 0,
          resumeFailedReason: null,
        });
        return;
      }

      try {
        const cached = await loadCachedMessages(sessionId);
        if (cached && cached.length > 0) {
          if (_switchSessionVersion !== myVersion) return;
          if (get().abortController) {
            finishLoad();
            return;
          }
          _sessionMessages.set(sessionId, cached);
          set({
            loadedSessionId: sessionId,
            ..._setMessagesSnapshot(cached),
            isLoadingMessages: true,
            loadedMessageTotal: _sessionMessageMeta.get(sessionId)?.total ?? cached.length,
            hasMoreMessages: _sessionMessageMeta.get(sessionId)?.hasMore ?? false,
            isLoadingOlderMessages: false,
            messageLoadError: null,
            pendingApproval: null,
            pendingQuestion: null,
            pipelineStatus: null,
            resumeFailedReason: null,
          });
          _loadPersistedExcelEvents(sessionId).catch(() => {});
          revalidateFromBackend();
          return;
        }
      } catch {
        // IndexedDB 失败，继续后续流程
      }

      if (_switchSessionVersion !== myVersion) return;
      if (get().abortController) {
        finishLoad();
        return;
      }

      set({
        loadedSessionId: sessionId,
        ..._setMessagesSnapshot([]),
        loadedMessageTotal: null,
        hasMoreMessages: false,
        isLoadingOlderMessages: false,
        messageLoadError: null,
        pendingApproval: null,
        pendingQuestion: null,
        pipelineStatus: null,
        activeStreamId: null,
        latestSeq: 0,
        resumeFailedReason: null,
      });
      _loadMessagesAsyncWithOptions(sessionId, { preferCache: false }).finally(finishLoad);
    };

    // 立即绑定 loadedSessionId，但保持当前消息直到新消息加载完成。
    // 不写 session-store：选中态由 setActiveSession 单源更新。
    set({
      loadedSessionId: sessionId,
      isLoadingMessages: true,
      messageLoadError: null,
      pendingApproval: null,
      pendingQuestion: null,
      pipelineStatus: null,
      activeStreamId: null,
      latestSeq: 0,
      resumeFailedReason: null,
      loadedMessageTotal: sessionId ? null : 0,
      hasMoreMessages: false,
      isLoadingOlderMessages: false,
    });

    loadAndSwitch();
  },
}));
