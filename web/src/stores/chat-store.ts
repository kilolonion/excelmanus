import { create } from "zustand";
import type { Message, Approval, Question, AssistantBlock, FileAttachment } from "@/lib/types";
import { loadCachedMessages, saveCachedMessages, deleteCachedMessages, clearAllCachedMessages } from "@/lib/idb-cache";
import { fetchSessionMessages, fetchSessionExcelEvents, clearAllSessions } from "@/lib/api";
import { useSessionStore } from "@/stores/session-store";
import { useExcelStore } from "@/stores/excel-store";
import type { ExcelDiffEntry } from "@/stores/excel-store";
import {
  deriveSessionTitleFromMessages,
  isFallbackSessionTitle,
} from "@/lib/session-title";

// 内存快速缓存（补充 IndexedDB）
const _sessionMessages = new Map<string, Message[]>();

// F5: switchSession 取消令牌 — 递增版本号，旧的 loadAndSwitch 检测到版本变化后放弃更新
let _switchSessionVersion = 0;

let _msgIdCounter = 0;
function _nextId(): string {
  return `restored-${++_msgIdCounter}-${Date.now()}`;
}

interface LoadMessagesOptions {
  preferCache?: boolean;
  replaceVisibleMessages?: boolean;
}

/**
 * 将后端 LLM 消息（role/content 字典）转换为前端 Message[]。
 * 将连续的 assistant/tool 消息合并为带 blocks 的单一 assistant 消息。
 */
const _EXCEL_WRITE_TOOL_NAMES = new Set([
  "write_cells", "insert_rows", "insert_columns",
  "create_sheet", "delete_sheet", "run_code",
]);

const _EXCEL_EXT_RE = /\.(xlsx|xlsm|xls|csv)$/i;
const _EXCEL_PATH_SCAN_RE = /(?:^|[\s`"'(（\[])([^ \t\r\n`"'()（）\[\]<>]+?\.(?:xlsx|xlsm|xls|csv))(?=$|[\s`"'.,;:!?)）\]])/gi;
const _MAX_DIFFS_IN_STORE = 500;

// 仅由 SSE 事件产生的块类型，不持久化到后端消息存储。
// 从后端刷新时，必须从现有内存消息中带出，避免视觉数据丢失（如 SessionSync 检测到 inFlight→false 后 thinking 块消失）。
const _SSE_ONLY_BLOCK_TYPES = new Set([
  "thinking", "iteration", "approval_action", "subagent",
]);

/**
 * 将仅由 SSE 产生的块（thinking、iteration、approval_action、subagent）从 oldMessages 合并到 newMessages，
 * 使后端刷新不会丢弃它们。在 assistant 消息间按位置匹配。
 */
function _preserveSseOnlyBlocks(
  oldMessages: Message[],
  newMessages: Message[],
): Message[] {
  // 按顺序收集旧 assistant 消息
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
  return newMessages.map((msg) => {
    if (msg.role === "user") {
      const oldFiles = oldUserFiles[uIdx++];
      // 若旧消息有文件而新消息没有或更少，优先保留旧的
      if (oldFiles && oldFiles.length > 0 && (!msg.files || msg.files.length === 0)) {
        return { ...msg, files: oldFiles };
      }
      return msg;
    }
    if (msg.role !== "assistant") return msg;
    const oldBlocks = oldAssistant[aIdx++];
    if (!oldBlocks) return msg;

    // 按 toolCallId 建立旧 tool_call 块映射，用于状态恢复。
    // SSE 事件携带的状态（error/result/status）比后端持久化更完整，此映射用于延续该状态。
    const oldToolCallMap = new Map<string, AssistantBlock>();
    for (const ob of oldBlocks) {
      if (ob.type === "tool_call" && ob.toolCallId) {
        oldToolCallMap.set(ob.toolCallId, ob);
      }
    }

    const sseBlocks = oldBlocks.filter((b) => _SSE_ONLY_BLOCK_TYPES.has(b.type));
    if (sseBlocks.length === 0 && oldToolCallMap.size === 0) return msg;

    // 以旧块顺序为模板：保留仅 SSE 的块不动，用刷新后的块替换后端持久化的块。
    const newBackendBlocks = msg.blocks.map((nb) => {
      // 延续 SSE 产生的 tool_call 状态（错误状态、结果、错误信息），后端转换可能已丢失。
      if (
        nb.type === "tool_call"
        && nb.toolCallId
        && oldToolCallMap.has(nb.toolCallId)
      ) {
        const ob = oldToolCallMap.get(nb.toolCallId)!;
        if (ob.type === "tool_call") {
          // 若旧块为错误状态则保留（后端对已解析调用总是返回 "success"）。
          if (ob.status === "error" && nb.status === "success") {
            return { ...nb, status: ob.status, result: ob.result, error: ob.error };
          }
          // 若新块缺少结果文本则沿用旧的
          if (!nb.result && ob.result) {
            return { ...nb, result: ob.result };
          }
        }
      }
      return nb;
    });

    if (sseBlocks.length === 0) {
      // 无仅 SSE 的块需要按位合并，但上面可能已修补了 tool_call 状态。
      return { ...msg, blocks: newBackendBlocks };
    }

    let ni = 0;
    const merged: AssistantBlock[] = [];

    for (const ob of oldBlocks) {
      if (_SSE_ONLY_BLOCK_TYPES.has(ob.type)) {
        merged.push(ob);
      } else {
        if (ni < newBackendBlocks.length) {
          merged.push(newBackendBlocks[ni++]);
        }
      }
    }
    // 追加剩余的新块（如旧消息中最后一个仅 SSE 块之后新增的 tool_calls）。
    while (ni < newBackendBlocks.length) {
      merged.push(newBackendBlocks[ni++]);
    }

    return { ...msg, blocks: merged };
  });
}

interface BackendConversionResult {
  messages: Message[];
  recoveredDiffs: ExcelDiffEntry[];
  recoveredFilePaths: string[];
}

function _normalizeRecoveredPath(rawPath: string): string | null {
  const text = String(rawPath || "").trim();
  if (!text) return null;
  if (text.startsWith("<path>/")) {
    const basename = text.slice("<path>/".length).trim();
    return basename ? `./${basename}` : null;
  }
  if (text.startsWith("./") || text.startsWith("/")) return text;
  if (_EXCEL_EXT_RE.test(text)) return `./${text}`;
  return null;
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
    // 继续向下
  }
  for (let i = text.length - 1; i >= 1; i--) {
    if (text[i] !== "}") continue;
    try {
      const parsed = JSON.parse(text.slice(0, i + 1));
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
        return parsed as Record<string, unknown>;
      }
    } catch {
      // 继续查找合法 JSON 后缀边界
    }
  }
  return null;
}

function _extractExcelPathsFromText(raw: string): string[] {
  const source = String(raw || "");
  const found = new Set<string>();
  let match: RegExpExecArray | null;
  _EXCEL_PATH_SCAN_RE.lastIndex = 0;
  while ((match = _EXCEL_PATH_SCAN_RE.exec(source)) !== null) {
    const normalized = _normalizeRecoveredPath(match[1]);
    if (normalized) found.add(normalized);
  }
  return Array.from(found);
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
    // 历史消息不包含精确 diff 时间戳，恢复时使用当前时间。
    timestamp: Date.now(),
  };
}

function _isToolResultError(content: string): boolean {
  // 简单启发：检查工具结果 JSON 是否含顶层 "status": "error"。
  // 与后端约定一致：失败的工具调用以 {"status": "error", "message": "..."} 作为工具消息内容。
  const text = content.trim();
  if (!text.startsWith("{")) return false;
  try {
    const parsed = JSON.parse(text);
    return parsed && typeof parsed === "object" && parsed.status === "error";
  } catch {
    // 回退：对截断或大结果做简单子串检查
    return /"status"\s*:\s*"error"/.test(text.slice(0, 200));
  }
}

// 匹配 sendMessage 注入的文件上传通知的正则
// "[已上传文件: ./path/to/file.xlsx]" or "[已上传图片: ./path/to/image.png]"
const _UPLOAD_NOTICE_RE = /\[已上传(?:文件|图片):\s*([^\]]+)\]/g;

/**
 * 从用户消息内容中的上传通知行提取 FileAttachment[]，
 * 返回去除通知后的内容及附件列表。
 */
function _extractFileAttachmentsFromContent(
  rawContent: string,
): { content: string; files: FileAttachment[] } {
  const files: FileAttachment[] = [];
  _UPLOAD_NOTICE_RE.lastIndex = 0;
  let match: RegExpExecArray | null;
  while ((match = _UPLOAD_NOTICE_RE.exec(rawContent)) !== null) {
    const filePath = match[1].trim();
    if (!filePath) continue;
    const filename = filePath.split("/").pop() || filePath;
    files.push({ filename, path: filePath, size: 0 });
  }
  // 为展示去掉内容中所有通知行；通知在开头，每行一条，后接 \n\n。
  const cleaned = rawContent
    .replace(/\[已上传(?:文件|图片):\s*[^\]]+\]\n?/g, "")
    .replace(/^\n+/, "")
    .trim();
  return { content: cleaned || rawContent.trim(), files };
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
    const role = msg.role as string;
    if (role === "user") {
      let content: string;
      if (typeof msg.content === "string") {
        // 去掉 mark_images_sent() 注入的尾部降级图片占位符（如 "\n[图片 #1 已在之前的对话中发送]"）。
        content = msg.content.replace(/\n?\[图片 #\d+ 已在之前的对话中发送\]\s*$/g, "").trim();
        // 跳过仅包含系统注入图片的消息（C 通道降级），其整条内容仅为占位符时跳过。
        if (!content) continue;
      } else if (Array.isArray(msg.content)) {
        // 多模态消息（文本 + image_url 部分）。仅提取文本部分；将 image_url 部分替换为短占位符，避免原始 base64 泄露到 UI。
        const textParts: string[] = [];
        let imageCount = 0;
        for (const part of msg.content as Record<string, unknown>[]) {
          if (part.type === "text" && typeof part.text === "string") {
            textParts.push(part.text as string);
          } else if (part.type === "image_url") {
            imageCount++;
          }
        }
        const hasText = textParts.some((t) => t.trim().length > 0);
        // 跳过系统注入的纯图片消息（C 通道 add_image_message 产物）：
        // 这些消息仅含 image_url 部分、无文本，由工具执行时自动注入，
        // 不应在 UI 中显示为用户发送的气泡。
        if (imageCount > 0 && !hasText) {
          continue;
        }
        content = textParts.join("\n").trim() || "(多模态消息)";
      } else {
        content = JSON.stringify(msg.content ?? "");
      }
      // 从内容中嵌入的上传通知提取文件附件
      const extracted = _extractFileAttachmentsFromContent(content);
      const userMsg: Message = { id: _nextId(), role: "user", content: extracted.content };
      if (extracted.files.length > 0) {
        userMsg.files = extracted.files;
      }
      result.push(userMsg);
    } else if (role === "assistant") {
      const blocks: AssistantBlock[] = [];
      const affectedFilePaths = new Set<string>();
      if (msg.content && typeof msg.content === "string") {
        blocks.push({ type: "text", content: msg.content });
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
          blocks.push({
            type: "tool_call",
            toolCallId: tcId,
            name: toolName,
            args,
            status: hasResult ? (isError ? "error" : "success") : "error",
            result: hasResult && tcId ? toolResultByCallId.get(tcId) : undefined,
          });
          if (_EXCEL_WRITE_TOOL_NAMES.has(toolName) && hasResult && tcId) {
            const argFilePath = typeof args.file_path === "string" ? args.file_path : "";
            const normalizedArgPath = _normalizeRecoveredPath(argFilePath);
            if (normalizedArgPath) {
              affectedFilePaths.add(normalizedArgPath);
              recoveredFilePaths.add(normalizedArgPath);
            }
            const toolResultText = toolResultByCallId.get(tcId);
            if (toolResultText) {
              for (const p of _extractExcelPathsFromText(toolResultText)) {
                affectedFilePaths.add(p);
                recoveredFilePaths.add(p);
              }
              const recoveredDiff = _buildRecoveredDiffFromToolResult(
                tcId,
                args,
                toolResultText,
              );
              if (recoveredDiff) {
                recoveredDiffs.push(recoveredDiff);
              }
            }
          }
        }
      }
      const prev = result[result.length - 1];
      if (prev && prev.role === "assistant") {
        prev.blocks = [...prev.blocks, ...blocks];
        if (affectedFilePaths.size > 0) {
          const existing = new Set(prev.affectedFiles ?? []);
          for (const f of affectedFilePaths) existing.add(f);
          prev.affectedFiles = Array.from(existing);
        }
      } else {
        const newMsg: Message = { id: _nextId(), role: "assistant", blocks };
        if (affectedFilePaths.size > 0) {
          (newMsg as Extract<Message, { role: "assistant" }>).affectedFiles = Array.from(affectedFilePaths);
        }
        result.push(newMsg);
      }
    }
  }
  return {
    messages: result,
    recoveredDiffs,
    recoveredFilePaths: Array.from(recoveredFilePaths),
  };
}

function _mergeRecoveredExcelState(
  recoveredDiffs: ExcelDiffEntry[],
  recoveredFilePaths: string[],
): void {
  if (recoveredDiffs.length === 0 && recoveredFilePaths.length === 0) return;
  const excelStore = useExcelStore.getState();
  for (const filePath of recoveredFilePaths) {
    const normalized = _normalizeRecoveredPath(filePath);
    if (!normalized) continue;
    const filename = normalized.split("/").pop() || normalized;
    excelStore.addRecentFileIfNotDismissed({ path: normalized, filename });
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
 * 从后端持久化的 excel-events 端点恢复 diff 和改动文件到 store。
 * 返回的数据来自 SQLite，不依赖前端推断，重启后 100% 可恢复。
 */
async function _loadPersistedExcelEvents(sessionId: string): Promise<void> {
  try {
    const { diffs, previews, affected_files } = await fetchSessionExcelEvents(sessionId);
    if (diffs.length === 0 && previews.length === 0 && affected_files.length === 0) return;
    if (useChatStore.getState().currentSessionId !== sessionId) return;

    const excelStore = useExcelStore.getState();
    for (const fp of affected_files) {
      if (!fp) continue;
      const filename = fp.split("/").pop() || fp;
      excelStore.addRecentFileIfNotDismissed({ path: fp, filename });
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
      _mergeRecoveredExcelState(converted, []);
    }

    // 恢复预览数据到 excel-store
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

    // 恢复消息上的 affectedFiles 徽章
    _restoreAffectedFilesOnMessages(diffs, affected_files, sessionId);
  } catch {
    // 端点不可用或格式错误时静默降级
  }
}

/**
 * 将持久化的 excel 事件中的文件路径，回填到对应 assistant 消息的 affectedFiles。
 * 保证重启后 "涉及文件" 徽章能正确显示。
 */
function _restoreAffectedFilesOnMessages(
  diffs: { tool_call_id: string; file_path: string }[],
  allAffectedFiles: string[],
  sessionId: string,
): void {
  const current = useChatStore.getState().currentSessionId;
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
    const existing = new Set(m.affectedFiles ?? []);
    const beforeSize = existing.size;

    for (const block of m.blocks) {
      if (block.type !== "tool_call" || !block.toolCallId) continue;
      const files = toolCallFileMap.get(block.toolCallId);
      if (files) {
        for (const f of files) {
          if (f.length <= 260 && !/[\n\r\t]/.test(f)) existing.add(f);
        }
      }
    }

    if (existing.size === beforeSize) return m;
    changed = true;
    return { ...m, affectedFiles: Array.from(existing) };
  });

  if (changed) {
    // 异步恢复期间会话可能已切换。
    const latest = useChatStore.getState().currentSessionId;
    if (latest !== sessionId) return;

    useChatStore.setState({ messages: updated });
    const store = useChatStore.getState();
    if (store.currentSessionId === sessionId) {
      saveCachedMessages(sessionId, updated).catch(() => {});
    }
  }
}

/**
 * 异步加载：优先 IDB，回退到后端 API。仅当会话仍为当前会话时更新 store。
 */
async function _loadMessagesAsync(sessionId: string): Promise<void> {
  const opts: LoadMessagesOptions = {};
  return _loadMessagesAsyncWithOptions(sessionId, opts);
}

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

  // 优先尝试 IndexedDB
  if (shouldPreferCache) {
    const cached = await loadCachedMessages(sessionId);
    if (cached && cached.length > 0) {
      _sessionMessages.set(sessionId, cached);
      maybeBackfillTitle(cached);
      const store = useChatStore.getState();
      if (
        store.currentSessionId === sessionId
        && !store.isStreaming
        && !store.abortController
        && (store.messages.length === 0 || shouldReplaceVisibleMessages)
      ) {
        useChatStore.setState({ messages: cached });
      }
      // 消息已加载到 store，立即恢复 Excel 事件并回填 affectedFiles
      _loadPersistedExcelEvents(sessionId).catch(() => {});
      return;
    }
  }

  // 回退到后端 API
  try {
    const raw = await fetchSessionMessages(sessionId, 200, 0);
    if (raw.length === 0) return;
    const {
      messages,
      recoveredDiffs,
      recoveredFilePaths,
    } = _convertBackendMessages(raw);
    _mergeRecoveredExcelState(recoveredDiffs, recoveredFilePaths);
    // 替换可见消息时，从当前 store 带出仅 SSE 的块（thinking、iteration、approval_action），
    // 避免后端刷新时被丢弃。
    const store = useChatStore.getState();
    const shouldReplace =
      store.currentSessionId === sessionId
      && !store.isStreaming
      && !store.abortController
      && (store.messages.length === 0 || shouldReplaceVisibleMessages);
    const finalMessages =
      shouldReplace && shouldReplaceVisibleMessages && store.messages.length > 0
        ? _preserveSseOnlyBlocks(store.messages, messages)
        : messages;
    _sessionMessages.set(sessionId, finalMessages);
    maybeBackfillTitle(finalMessages);
    saveCachedMessages(sessionId, finalMessages).catch(() => {});
    if (shouldReplace) {
      // 若合并结果与 store 当前内容语义等价则避免视觉闪烁。
      // 完整深度比较成本高，故做轻量结构检查：消息数、角色、块数与类型、文本内容一致。
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
        useChatStore.setState({ messages: finalMessages });
      }
    }
    // 消息已加载，立即恢复 Excel 事件并回填 affectedFiles
    _loadPersistedExcelEvents(sessionId).catch(() => {});
  } catch {
    // 静默忽略
  }
}

export async function refreshSessionMessagesFromBackend(
  sessionId: string,
): Promise<void> {
  await _loadMessagesAsyncWithOptions(sessionId, {
    preferCache: false,
    replaceVisibleMessages: true,
  });
}

export interface VlmPhaseEntry {
  stage: string;
  message: string;
  startedAt: number;
  duration?: number;
  diff?: PipelineStatus["diff"];
  specPath?: string;
  phaseIndex: number;
  totalPhases: number;
}

export interface PipelineStatus {
  stage: string;
  message: string;
  startedAt: number;
  phaseIndex?: number;
  totalPhases?: number;
  specPath?: string;
  diff?: {
    changes: Array<{
      type: string;
      sheet?: string;
      cells_added?: number;
      cells_modified?: number;
      modified_details?: Array<{
        cell: string;
        old_value: unknown;
        new_value: unknown;
      }>;
      merges_added?: number;
      styles_added?: number;
    }>;
    summary: string;
  };
  checkpoint?: Record<string, unknown>;
}

interface ChatState {
  messages: Message[];
  currentSessionId: string | null;
  isStreaming: boolean;
  pendingApproval: Approval | null;
  _lastDismissedApprovalId: string | null;
  pendingQuestion: Question | null;
  abortController: AbortController | null;
  pipelineStatus: PipelineStatus | null;
  vlmPhases: VlmPhaseEntry[];
  isLoadingMessages: boolean;

  setMessages: (messages: Message[]) => void;
  addUserMessage: (id: string, content: string, files?: FileAttachment[]) => void;
  addAssistantMessage: (id: string) => void;
  appendBlock: (messageId: string, block: AssistantBlock) => void;
  updateLastBlock: (messageId: string, updater: (block: AssistantBlock) => AssistantBlock) => void;
  updateBlockByType: (messageId: string, blockType: string, updater: (block: AssistantBlock) => AssistantBlock) => void;
  updateSubagentBlock: (
    messageId: string,
    conversationId: string | null,
    updater: (block: AssistantBlock) => AssistantBlock,
  ) => void;
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
  pushVlmPhase: (entry: VlmPhaseEntry) => void;
  clearVlmPhases: () => void;
  clearMessages: () => void;
  removeSessionCache: (sessionId: string) => void;
  switchSession: (sessionId: string | null) => void;
  clearAllHistory: () => Promise<void>;
  saveCurrentSession: () => void;
}

export const useChatStore = create<ChatState>((set, get) => ({
  messages: [],
  currentSessionId: null,
  isStreaming: false,
  pendingApproval: null,
  _lastDismissedApprovalId: null,
  pendingQuestion: null,
  abortController: null,
  pipelineStatus: null,
  vlmPhases: [],
  isLoadingMessages: false,

  setMessages: (messages) => set({ messages }),
  addUserMessage: (id, content, files) =>
    set((state) => ({
      messages: [...state.messages, { id, role: "user" as const, content, files, timestamp: Date.now() }],
    })),
  addAssistantMessage: (id) =>
    set((state) => ({
      messages: [...state.messages, { id, role: "assistant" as const, blocks: [], timestamp: Date.now() }],
    })),
  appendBlock: (messageId, block) =>
    set((state) => ({
      messages: state.messages.map((m) =>
        m.id === messageId && m.role === "assistant"
          ? { ...m, blocks: [...m.blocks, block] }
          : m
      ),
    })),
  updateLastBlock: (messageId, updater) =>
    set((state) => ({
      messages: state.messages.map((m) => {
        if (m.id !== messageId || m.role !== "assistant" || m.blocks.length === 0)
          return m;
        const blocks = [...m.blocks];
        blocks[blocks.length - 1] = updater(blocks[blocks.length - 1]);
        return { ...m, blocks };
      }),
    })),
  updateBlockByType: (messageId, blockType, updater) =>
    set((state) => ({
      messages: state.messages.map((m) => {
        if (m.id !== messageId || m.role !== "assistant") return m;
        const idx = [...m.blocks].reverse().findIndex((b) => b.type === blockType);
        if (idx === -1) return m;
        const realIdx = m.blocks.length - 1 - idx;
        const blocks = [...m.blocks];
        blocks[realIdx] = updater(blocks[realIdx]);
        return { ...m, blocks };
      }),
    })),
  updateSubagentBlock: (messageId, conversationId, updater) =>
    set((state) => ({
      messages: state.messages.map((m) => {
        if (m.id !== messageId || m.role !== "assistant") return m;
        // 按 conversationId 精准匹配；无 id 时回退到最后一个 running subagent block
        let targetIdx = -1;
        if (conversationId) {
          for (let i = m.blocks.length - 1; i >= 0; i--) {
            const b = m.blocks[i];
            if (b.type === "subagent" && b.conversationId === conversationId) {
              targetIdx = i;
              break;
            }
          }
        }
        if (targetIdx === -1) {
          for (let i = m.blocks.length - 1; i >= 0; i--) {
            const b = m.blocks[i];
            if (b.type === "subagent" && b.status === "running") {
              targetIdx = i;
              break;
            }
          }
        }
        if (targetIdx === -1) {
          // 最终回退：最后一个 subagent block
          for (let i = m.blocks.length - 1; i >= 0; i--) {
            if (m.blocks[i].type === "subagent") { targetIdx = i; break; }
          }
        }
        if (targetIdx === -1) return m;
        const blocks = [...m.blocks];
        blocks[targetIdx] = updater(blocks[targetIdx]);
        return { ...m, blocks };
      }),
    })),
  updateToolCallBlock: (messageId, toolCallId, updater) =>
    set((state) => ({
      messages: state.messages.map((m) => {
        if (m.id !== messageId || m.role !== "assistant") return m;
        const blocks = [...m.blocks];
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

        // 向后兼容：旧事件不带 tool_call_id 时，回退到"最近 running/pending 工具调用"。
        if (targetIndex === -1) {
          for (let i = blocks.length - 1; i >= 0; i--) {
            const block = blocks[i];
            if (block.type === "tool_call" && isActive(block.status)) {
              targetIndex = i;
              break;
            }
          }
        }

        if (targetIndex === -1) return m;
        blocks[targetIndex] = updater(blocks[targetIndex]);
        return { ...m, blocks };
      }),
    })),
  addAffectedFiles: (messageId, files) =>
    set((state) => ({
      messages: state.messages.map((m) => {
        if (m.id !== messageId || m.role !== "assistant") return m;
        const existing = new Set(m.affectedFiles ?? []);
        for (const f of files) {
          if (f && f.length <= 260 && !/[\n\r\t]/.test(f)) existing.add(f);
        }
        return { ...m, affectedFiles: Array.from(existing) };
      }),
    })),
  retractLastThinking: (messageId) =>
    set((state) => ({
      messages: state.messages.map((m) => {
        if (m.id !== messageId || m.role !== "assistant" || m.blocks.length === 0)
          return m;
        const blocks = [...m.blocks];
        // Remove the last unclosed thinking block
        const lastIdx = blocks.length - 1;
        if (blocks[lastIdx].type === "thinking" && blocks[lastIdx].duration == null) {
          blocks.pop();
          // Also remove a preceding iteration divider if it's now the last block
          if (blocks.length > 0 && blocks[blocks.length - 1].type === "iteration") {
            blocks.pop();
          }
        }
        return { ...m, blocks };
      }),
    })),
  setStreaming: (streaming) => set({ isStreaming: streaming }),
  setPendingApproval: (approval) => set({ pendingApproval: approval }),
  dismissApproval: (approvalId) => set({ pendingApproval: null, _lastDismissedApprovalId: approvalId }),
  setPendingQuestion: (question) => set({ pendingQuestion: question }),
  setAbortController: (controller) => set({ abortController: controller }),
  setPipelineStatus: (status) => set({ pipelineStatus: status }),
  pushVlmPhase: (entry) =>
    set((state) => ({
      vlmPhases: [...state.vlmPhases.filter((p) => p.stage !== entry.stage), entry],
    })),
  clearVlmPhases: () => set({ vlmPhases: [] }),
  clearMessages: () => {
    const { currentSessionId } = get();
    // 清除内存缓存
    if (currentSessionId) {
      _sessionMessages.delete(currentSessionId);
      // 清除 IndexedDB 缓存
      deleteCachedMessages(currentSessionId).catch(() => {});
    }
    set({ messages: [], isLoadingMessages: false, pendingApproval: null, pendingQuestion: null, pipelineStatus: null });
  },

  removeSessionCache: (sessionId) => {
    _sessionMessages.delete(sessionId);
    deleteCachedMessages(sessionId).catch(() => {});

    const state = get();
    if (state.currentSessionId === sessionId) {
      set({
        currentSessionId: null,
        messages: [],
        isLoadingMessages: false,
        pendingApproval: null,
        pendingQuestion: null,
        pipelineStatus: null,
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
    await clearAllCachedMessages();
    useSessionStore.getState().setSessions([]);
    useSessionStore.getState().setActiveSession(null);
    set({
      currentSessionId: null,
      messages: [],
      isLoadingMessages: false,
      pendingApproval: null,
      pendingQuestion: null,
    });
  },

  saveCurrentSession: () => {
    const { currentSessionId, messages } = get();
    if (currentSessionId && messages.length > 0) {
      _sessionMessages.set(currentSessionId, [...messages]);
      saveCachedMessages(currentSessionId, messages).catch(() => {});
    }
  },

  switchSession: (sessionId) => {
    const state = get();

    // 仅当处于当前会话且正在流式输出时跳过。此前在 messages.length > 0 时也会跳过，导致异步加载后点击被静默忽略。
    if (sessionId && sessionId === state.currentSessionId) {
      if (state.abortController) return;
      // 已加载，无需重新拉取
      if (state.messages.length > 0) return;
    }

    // 将当前会话消息写入两处缓存
    if (state.currentSessionId && state.messages.length > 0) {
      _sessionMessages.set(state.currentSessionId, [...state.messages]);
      saveCachedMessages(state.currentSessionId, state.messages).catch(() => {});
    }
    
    // 先从内存缓存加载目标会话消息
    const memCached = sessionId ? _sessionMessages.get(sessionId) : undefined;
    if (memCached && memCached.length > 0) {
      // F5：即使命中同步缓存也递增版本，以取消未完成的异步加载
      ++_switchSessionVersion;
      set({
        currentSessionId: sessionId,
        messages: memCached,
        isLoadingMessages: false,
        pendingApproval: null,
        pendingQuestion: null,
        pipelineStatus: null,
      });
      return;
    }

    // F5: 递增版本号，取消之前的 loadAndSwitch 异步操作
    const myVersion = ++_switchSessionVersion;

    // 改进：不立即清空消息，而是先尝试从 IndexedDB 加载
    // 只有在确实没有缓存时才清空，减少消息闪烁
    const loadAndSwitch = async () => {
      if (!sessionId) {
        set({
          currentSessionId: null,
          messages: [],
          isLoadingMessages: false,
          pendingApproval: null,
          pendingQuestion: null,
          pipelineStatus: null,
        });
        return;
      }

      // 先尝试从 IndexedDB 快速加载
      try {
        const cached = await loadCachedMessages(sessionId);
        if (cached && cached.length > 0) {
          // F5: 检查版本号 — 若已被更新的 switchSession 调用取代则放弃
          if (_switchSessionVersion !== myVersion) return;
          _sessionMessages.set(sessionId, cached);
          set({
            currentSessionId: sessionId,
            messages: cached,
            isLoadingMessages: false,
            pendingApproval: null,
            pendingQuestion: null,
            pipelineStatus: null,
          });
          // 立即恢复 Excel 事件，确保 diff 数据及时显示
          _loadPersistedExcelEvents(sessionId).catch(() => {});
          return;
        }
      } catch {
        // IndexedDB 失败，继续后续流程
      }

      // F5: 再次检查版本号
      if (_switchSessionVersion !== myVersion) return;

      // IndexedDB 没有缓存，现在才清空并异步加载
      set({
        currentSessionId: sessionId,
        messages: [],
        pendingApproval: null,
        pendingQuestion: null,
        pipelineStatus: null,
      });
      _loadMessagesAsync(sessionId).catch(() => {}).finally(() => {
        if (_switchSessionVersion === myVersion) {
          useChatStore.setState({ isLoadingMessages: false });
        }
      });
    };

    // 立即更新 currentSessionId，但保持当前消息直到新消息加载完成
    set({
      currentSessionId: sessionId,
      isLoadingMessages: true,
      pendingApproval: null,
      pendingQuestion: null,
      pipelineStatus: null,
    });
    
    loadAndSwitch();
  },
}));
