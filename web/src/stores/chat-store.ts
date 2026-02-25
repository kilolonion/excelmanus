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

// In-memory fast cache (supplements IndexedDB)
const _sessionMessages = new Map<string, Message[]>();

let _msgIdCounter = 0;
function _nextId(): string {
  return `restored-${++_msgIdCounter}-${Date.now()}`;
}

interface LoadMessagesOptions {
  preferCache?: boolean;
  replaceVisibleMessages?: boolean;
}

/**
 * Convert backend LLM messages (role/content dicts) to frontend Message[].
 * Groups consecutive assistant/tool messages into a single assistant Message with blocks.
 */
const _EXCEL_WRITE_TOOL_NAMES = new Set([
  "write_cells", "insert_rows", "insert_columns",
  "create_sheet", "delete_sheet", "run_code",
]);

const _EXCEL_EXT_RE = /\.(xlsx|xlsm|xls|csv)$/i;
const _EXCEL_PATH_SCAN_RE = /(?:^|[\s`"'(（\[])([^ \t\r\n`"'()（）\[\]<>]+?\.(?:xlsx|xlsm|xls|csv))(?=$|[\s`"'.,;:!?)）\]])/gi;
const _MAX_DIFFS_IN_STORE = 500;

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
    // fall through
  }
  for (let i = text.length - 1; i >= 1; i--) {
    if (text[i] !== "}") continue;
    try {
      const parsed = JSON.parse(text.slice(0, i + 1));
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
        return parsed as Record<string, unknown>;
      }
    } catch {
      // continue searching a valid JSON suffix boundary
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

function _convertBackendMessages(raw: unknown[]): BackendConversionResult {
  const resolvedToolCallIds = new Set<string>();
  const toolResultByCallId = new Map<string, string>();
  for (const item of raw) {
    const msg = item as Record<string, unknown>;
    if (msg.role === "tool" && typeof msg.tool_call_id === "string") {
      resolvedToolCallIds.add(msg.tool_call_id);
      if (typeof msg.content === "string") {
        toolResultByCallId.set(msg.tool_call_id, msg.content);
      } else {
        toolResultByCallId.set(msg.tool_call_id, JSON.stringify(msg.content ?? ""));
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
      const content = typeof msg.content === "string" ? msg.content : JSON.stringify(msg.content ?? "");
      result.push({ id: _nextId(), role: "user", content });
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
          blocks.push({
            type: "tool_call",
            toolCallId: tcId,
            name: toolName,
            args,
            status: hasResult ? "success" : "running",
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
    // Session may have changed while async recovery was in flight.
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
 * Async loader: IDB → backend API fallback.
 * Updates the store only if the session is still active.
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

  // Try IndexedDB first
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
      // 消息已加载到 store，从后端恢复 Excel 事件并回填 affectedFiles
      _loadPersistedExcelEvents(sessionId).catch(() => {});
      return;
    }
  }

  // Fall back to backend API
  try {
    const raw = await fetchSessionMessages(sessionId, 200, 0);
    if (raw.length === 0) return;
    const {
      messages,
      recoveredDiffs,
      recoveredFilePaths,
    } = _convertBackendMessages(raw);
    _mergeRecoveredExcelState(recoveredDiffs, recoveredFilePaths);
    _sessionMessages.set(sessionId, messages);
    maybeBackfillTitle(messages);
    saveCachedMessages(sessionId, messages).catch(() => {});
    const store = useChatStore.getState();
    if (
      store.currentSessionId === sessionId
      && !store.isStreaming
      && !store.abortController
      && (store.messages.length === 0 || shouldReplaceVisibleMessages)
    ) {
      useChatStore.setState({ messages });
    }
    // 消息已加载，从后端恢复 Excel 事件并回填 affectedFiles
    _loadPersistedExcelEvents(sessionId).catch(() => {});
  } catch {
    // silently ignore
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
  pendingQuestion: Question | null;
  abortController: AbortController | null;
  pipelineStatus: PipelineStatus | null;

  setMessages: (messages: Message[]) => void;
  addUserMessage: (id: string, content: string, files?: FileAttachment[]) => void;
  addAssistantMessage: (id: string) => void;
  appendBlock: (messageId: string, block: AssistantBlock) => void;
  updateLastBlock: (messageId: string, updater: (block: AssistantBlock) => AssistantBlock) => void;
  updateBlockByType: (messageId: string, blockType: string, updater: (block: AssistantBlock) => AssistantBlock) => void;
  updateToolCallBlock: (
    messageId: string,
    toolCallId: string | null,
    updater: (block: AssistantBlock) => AssistantBlock,
  ) => void;
  addAffectedFiles: (messageId: string, files: string[]) => void;
  setStreaming: (streaming: boolean) => void;
  setPendingApproval: (approval: Approval | null) => void;
  setPendingQuestion: (question: Question | null) => void;
  setAbortController: (controller: AbortController | null) => void;
  setPipelineStatus: (status: PipelineStatus | null) => void;
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
  pendingQuestion: null,
  abortController: null,
  pipelineStatus: null,

  setMessages: (messages) => set({ messages }),
  addUserMessage: (id, content, files) =>
    set((state) => ({
      messages: [...state.messages, { id, role: "user" as const, content, files }],
    })),
  addAssistantMessage: (id) =>
    set((state) => ({
      messages: [...state.messages, { id, role: "assistant" as const, blocks: [] }],
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
  updateToolCallBlock: (messageId, toolCallId, updater) =>
    set((state) => ({
      messages: state.messages.map((m) => {
        if (m.id !== messageId || m.role !== "assistant") return m;
        const blocks = [...m.blocks];
        const isActive = (s: string) => s === "running" || s === "pending";

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
  setStreaming: (streaming) => set({ isStreaming: streaming }),
  setPendingApproval: (approval) => set({ pendingApproval: approval }),
  setPendingQuestion: (question) => set({ pendingQuestion: question }),
  setAbortController: (controller) => set({ abortController: controller }),
  setPipelineStatus: (status) => set({ pipelineStatus: status }),
  clearMessages: () => {
    const { currentSessionId } = get();
    // 清除内存缓存
    if (currentSessionId) {
      _sessionMessages.delete(currentSessionId);
      // 清除 IndexedDB 缓存
      deleteCachedMessages(currentSessionId).catch(() => {});
    }
    set({ messages: [], pendingApproval: null, pendingQuestion: null, pipelineStatus: null });
  },

  removeSessionCache: (sessionId) => {
    _sessionMessages.delete(sessionId);
    deleteCachedMessages(sessionId).catch(() => {});

    const state = get();
    if (state.currentSessionId === sessionId) {
      set({
        currentSessionId: null,
        messages: [],
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

    // Only skip if we're on this session AND actively streaming.
    // Previously this also skipped when messages.length > 0, which
    // caused clicks to be silently ignored after async message load.
    if (sessionId && sessionId === state.currentSessionId) {
      if (state.abortController) return;
      // Already loaded — no need to re-fetch
      if (state.messages.length > 0) return;
    }

    // Save current session messages to both caches
    if (state.currentSessionId && state.messages.length > 0) {
      _sessionMessages.set(state.currentSessionId, [...state.messages]);
      saveCachedMessages(state.currentSessionId, state.messages).catch(() => {});
    }
    // Load target session messages from memory cache first
    const memCached = sessionId ? _sessionMessages.get(sessionId) : undefined;
    if (memCached && memCached.length > 0) {
      set({
        currentSessionId: sessionId,
        messages: memCached,
        pendingApproval: null,
        pendingQuestion: null,
        pipelineStatus: null,
      });
      return;
    }
    // Set empty immediately, then try IDB → backend async
    set({
      currentSessionId: sessionId,
      messages: [],
      pendingApproval: null,
      pendingQuestion: null,
      pipelineStatus: null,
    });
    if (sessionId) {
      _loadMessagesAsync(sessionId).catch(() => {});
    }
  },
}));
