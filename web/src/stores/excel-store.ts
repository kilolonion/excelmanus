import { create } from "zustand";
import { invalidateWorkbookCaches } from "@/lib/api";
import { persist } from "zustand/middleware";
import {
  fetchWorkspaceFiles,
  normalizeExcelPath,
  fetchOperations,
  undoOperation as apiUndoOperation,
  type ExcelFileListItem,
  type OperationRecord,
} from "@/lib/api";
import { useSessionStore } from "@/stores/session-store";
import {
  activeSession,
  isScopedWorkspaceKey,
  normalizeRelativePath,
  sanitizeRecentFiles,
  versionStoreKey,
  workspaceKeyFromSession,
  workspaceKeyForSessionId,
} from "@/lib/workspace-file-ref";
import { isSpreadsheetFile } from "@/lib/file-kind";
import { displayFileName, toPublicFileIdentity } from "@/lib/file-identity";
import { useWorkbookConversationStore } from "@/stores/workbook-conversation-store";
import { fileRefFromSession } from "@/lib/workspace-file-ref";
import type { WorkbookViewLayout } from "@/lib/workspace-surface";
import { useWorkbookWorkspaceStore, workbookWorkspaceKey } from "@/stores/workbook-workspace-store";

interface WorkspaceFilesRequest {
  sessionId: string | null;
  workspaceId: string | null;
  version: number;
  promise: Promise<void>;
}

// Keep one request per scope/version. A single global slot allowed an old
// session scan to cancel out a newer one even though both can run safely.
const workspaceFilesRequests = new Map<string, WorkspaceFilesRequest>();
let operationHistoryRequest = 0;
const undoRequests = new Set<string>();

function activeSessionId(): string | null {
  return useSessionStore.getState().activeSessionId;
}

function openWorkspaceWorkbook(path: string, sheet?: string) {
  const session = activeSession();
  const key = workbookWorkspaceKey(session?.id, workspaceKeyFromSession(session));
  const workspaces = useWorkbookWorkspaceStore.getState();
  const discussion = session ? useWorkbookConversationStore.getState().targets[session.id] : undefined;
  if (!workspaces.workspaces[key]?.files.length && discussion?.file.workspaceKey === workspaceKeyFromSession(session)) {
    workspaces.open(key, discussion.file.relative, discussion.sheet);
  }
  return workspaces.open(key, path, sheet);
}

/** Univer 兼容的单元格样式（轻量子集） */
export interface CellStyle {
  bl?: number;               // bold
  it?: number;               // italic
  ul?: { s: number };        // underline
  st?: { s: number };        // strikethrough
  fs?: number;               // font size
  ff?: string;               // font family
  cl?: { rgb: string };      // font color
  bg?: { rgb: string };      // background color
  ht?: number;               // horizontal alignment
  vt?: number;               // vertical alignment
  tb?: number;               // wrap text
  tr?: { a: number };        // text rotation
  pd?: { l?: number };       // padding / indent (l = left indent level)
  sk?: number;               // shrink to fit
  bd?: Record<string, { s: number; cl?: { rgb: string } }>; // borders
  n?: { pattern: string };   // number format
}

export interface MergeRange {
  min_row: number;
  min_col: number;
  max_row: number;
  max_col: number;
}

export interface ExcelCellDiff {
  cell: string;
  old: string | number | boolean | null;
  new: string | number | boolean | null;
  oldStyle?: CellStyle | null;
  newStyle?: CellStyle | null;
  styleOnly?: boolean;
}

export interface ExcelDiffSummary {
  totalCellsCompared: number;
  cellsDifferent: number;
  rowsAdded: number;
  rowsDeleted: number;
  rowsModified: number;
  columnsAdded: string[];
  columnsDeleted: string[];
}

export interface ExcelDiffEntry {
  toolCallId: string;
  filePath: string;
  sheet: string;
  affectedRange: string;
  changes: ExcelCellDiff[];
  mergeRanges?: MergeRange[];
  oldMergeRanges?: MergeRange[];
  metadataHints?: string[];
  timestamp: number;
  // 跨文件/跨 Sheet 对比扩展字段
  diffMode?: "inline" | "cross_file" | "cross_sheet";
  filePathB?: string;
  sheetB?: string;
  diffSummary?: ExcelDiffSummary;
}

export interface ExcelPreviewData {
  toolCallId: string;
  filePath: string;
  sheet: string;
  columns: string[];
  rows: (string | number | null)[][];
  totalRows: number;
  truncated: boolean;
  cellStyles?: (CellStyle | null)[][];
  mergeRanges?: MergeRange[];
  metadataHints?: string[];
}

export interface TextDiffEntry {
  toolCallId: string;
  filePath: string;
  hunks: string[];
  additions: number;
  deletions: number;
  truncated: boolean;
  timestamp: number;
}

export interface TextPreviewEntry {
  toolCallId: string;
  filePath: string;
  content: string;
  lineCount: number;
  truncated: boolean;
}

export interface ExcelFileRef {
  path: string;
  filename: string;
  lastUsedAt: number;
  workspaceKey?: string;
}

export interface SharedColumn {
  col_a: string;
  col_b: string;
  match_type: "exact" | "normalized" | "value_overlap";
  overlap_ratio: number;
}

export interface FileRelationship {
  fileA: string;
  fileB: string;
  sharedColumns: SharedColumn[];
}

export interface MergeResultInfo {
  sourceFiles: string[];
  outputFile: string;
  rowsMatched: number;
  rowsAdded: number;
  rowsUnmatched: number;
  keyColumns: string[];
  joinType: string;
  toolCallId: string;
}

const MAX_RECENT_FILES = 50;
const MAX_PERSISTED_DIFFS = 500;

export type ExcelPanelTab = "sheet" | "history";
export type HistorySubview = "revisions" | "operations";

export interface WorkbookChange {
  sequence: number;
  version?: string;
  source: "local" | "remote" | "refresh";
}

interface ExcelState {
  // 侧边面板
  panelOpen: boolean;
  activeFilePath: string | null;
  activeSheet: string | null;

  // Diff 历史（当前会话内累积）
  diffs: ExcelDiffEntry[];

  // 文本 Diff 历史
  textDiffs: TextDiffEntry[];

  // 聊天内嵌预览数据（按 toolCallId 索引）
  previews: Record<string, ExcelPreviewData>;

  // 文本文件预览数据（按 toolCallId 索引）
  textPreviews: Record<string, TextPreviewEntry>;

  // 历史列表的兼容刷新信号；工作簿视图只订阅 workbookChanges 中自己的文件。
  refreshCounter: number;

  // 当前打开文件的内容版本（sha256:...），按 workspaceKey|path 索引
  contentVersions: Record<string, string>;
  activeWorkspaceKey: string | null;
  viewGeneration: number;
  workbookChanges: Record<string, WorkbookChange>;

  // 快捷栏：最近使用的 Excel 文件（LRU，最多 5 个）
  recentFiles: ExcelFileRef[];

  // 全屏表格模式
  fullViewPath: string | null;
  fullViewSheet: string | null;
  fullViewLayout: WorkbookViewLayout;

  // 选区引用模式
  selectionMode: boolean;
  pendingSelection: { filePath: string; sheet: string; range: string; contentVersion?: string } | null;
  draftRange: { range: string; sheet: string; path?: string; contentVersion?: string } | null;

  // 普通模式下表格实时上报的当前选区（供 agent 上下文与对话芯片使用）
  liveSelection: { path: string; sheet: string; range: string; contentVersion?: string } | null;

  // 快速添加文件提及到聊天输入（由侧栏设置，ChatInput 消费）
  pendingFileMention: { path: string; filename: string } | null;

  // 批量添加文件提及到聊天输入（多选引用，ChatInput 消费）
  pendingFileMentions: { path: string; filename: string }[] | null;

  // 模板消息注入（右键“合并/对比”操作预填到聊天输入框）
  pendingTemplateMessage: string | null;

  // 拖拽中的文件数量（用于 ChatInput 拖拽覆盖层显示计数）
  draggingFileCount: number;

  // 用户明确关闭的路径，在重挂载间保留，以便自动发现（工作区扫描/会话恢复）不会再次加入。
  dismissedPaths: Set<string>;

  // Closing a view is a navigation choice, not removal from the file list.
  // Keep it for this conversation until the next user message is accepted.
  autoOpenSuppressedSessionId: string | null;

  // 工作区系统文件可见性（默认隐藏，用户可在侧栏开关切换）
  showSystemFiles: boolean;

  // 工作区文件树刷新信号（在 mutation 事件时递增）
  workspaceFilesVersion: number;

  // 工作区文件列表缓存（避免每次挂载组件都重新加载）
  workspaceFiles: { path: string; filename: string; is_dir?: boolean }[];
  wsFilesLoaded: boolean;
  /** 当前作用域正在扫描；重新验证时保留旧行，避免列表闪空。 */
  workspaceFilesLoading: boolean;
  workspaceFilesSessionId: string | null | undefined;
  /** 与 workspaceFilesSessionId 一起标记已加载列表的工作区作用域 */
  workspaceFilesWorkspaceId: string | null;
  workspaceFilesLoadedVersion: number;
  workspaceFilesLoadedAt: number;
  workspaceFilesError: string | null;
  /** 后端按上限截断时为 true：workspaceFiles 不是完整清单，不可用于存在性校验 */
  workspaceFilesTruncated: boolean;

  // 引导演示文件（不真实存储，引导结束后自动消失）
  demoFile: { path: string; filename: string } | null;

  // 流式工具调用参数累积（用于实时预览文本写入内容）
  streamingToolContent: Record<string, string>;

  // 操作历史时间线
  operations: OperationRecord[];
  operationsLoading: boolean;
  operationsLoaded: boolean;
  operationsError: string | null;

  // 侧栏：表格 / 历史
  panelTab: ExcelPanelTab;
  historySubview: HistorySubview;


  // 跨文件对比模式
  compareMode: boolean;
  compareFileA: string | null;
  compareFileB: string | null;
  compareSheetA: string | null;
  compareSheetB: string | null;
  compareRelationship: FileRelationship | null;
  compareReturnPath: string | null;

  // 合并结果（最近一次）
  lastMergeResult: MergeResultInfo | null;

  // 操作
  openPanel: (filePath?: string, sheet?: string) => void;
  openHistory: (filePath?: string, view?: HistorySubview) => void;
  setPanelTab: (tab: ExcelPanelTab) => void;
  setHistorySubview: (view: HistorySubview) => void;
  closePanel: () => void;
  setActiveSheet: (sheet: string) => void;
  setContentVersion: (path: string, version: string | null | undefined, workspaceKey?: string | null) => void;
  getContentVersion: (path: string, workspaceKey?: string | null) => string | null;
  notifyWorkbookChanged: (path: string, workspaceKey: string, version?: string, source?: WorkbookChange["source"]) => void;
  rebindSession: (prevWorkspaceKey: string | null, nextWorkspaceKey: string | null) => void;
  addDiff: (diff: ExcelDiffEntry) => void;
  addTextDiff: (diff: TextDiffEntry) => void;
  addTextPreview: (preview: TextPreviewEntry) => void;
  appendStreamingArgs: (toolCallId: string, delta: string) => void;
  clearStreamingArgs: (toolCallId: string) => void;
  addPreview: (preview: ExcelPreviewData) => void;
  /** Add a file explicitly opened/uploaded by the user (clears dismissal). */
  addRecentFile: (file: { path: string; filename: string }, workspaceKey?: string) => void;
  /** System-initiated add that respects dismissedPaths. */
  addRecentFileIfNotDismissed: (file: { path: string; filename: string }, workspaceKey?: string) => void;
  removeRecentFile: (path: string, workspaceKey?: string | null) => void;
  removeRecentFiles: (paths: string[], workspaceKey?: string | null) => void;
  clearAllRecentFiles: () => void;
  mergeRecentFiles: (
    files: { path: string; filename: string; modifiedAt?: number }[],
    workspaceKey?: string,
    options?: { pruneMissing?: boolean },
  ) => void;
  /** Evict a cached entry the backend reported missing. 不写 dismissedPaths，文件重建后仍可重新出现。 */
  evictRecentFile: (path: string, workspaceKey?: string | null) => void;
  /**
   * 后端已确认路径不再可读时退役该表格目标：关闭陈旧标签、清掉最近打开与视图
   * 缓存，并在面板/全屏视图正指向它时回到空状态。不写 dismissedPaths，
   * 文件重新出现后仍可再次打开。
   */
  dropMissingWorkbook: (path: string, workspaceKey?: string | null) => void;
  /**
   * 文件（或文件夹）删除后的统一清理，所有删除入口共用：
   * 侧边栏文件面板、最近打开、已打开的表格标签、全屏视图、对比视图、
   * 对话绑定与视图缓存同步剔除，保证各入口口径一致。
   */
  handleFilesDeleted: (paths: string[], workspaceKey?: string | null) => void;
  openFullView: (path: string, sheet?: string, layout?: WorkbookViewLayout) => void;
  closeFullView: () => void;
  focusWorkbook: (path: string) => void;
  setPrimaryWorkbook: (path: string, sheet?: string) => void;
  closeWorkbook: (path: string) => Promise<boolean>;
  enterSelectionMode: () => void;
  exitSelectionMode: () => void;
  confirmSelection: (sel: { filePath: string; sheet: string; range: string; contentVersion?: string }) => void;
  setDraftRange: (range: { range: string; sheet: string; path?: string; contentVersion?: string } | null) => void;
  setLiveSelection: (sel: ExcelState["liveSelection"]) => void;
  clearPendingSelection: () => void;
  /** Insert @file:filename into chat input from sidebar click. */
  mentionFileToInput: (file: { path: string; filename: string }) => void;
  clearPendingFileMention: () => void;
  /** Batch insert multiple @file:filename into chat input (multi-select reference). */
  mentionFilesToInput: (files: { path: string; filename: string }[]) => void;
  clearPendingFileMentions: () => void;
  /** Set a template message to inject into chat input (e.g. merge/compare prompt). */
  setPendingTemplateMessage: (msg: string) => void;
  clearPendingTemplateMessage: () => void;
  toggleShowSystemFiles: () => void;
  bumpWorkspaceFilesVersion: () => void;
  refreshWorkspaceFiles: (sessionId?: string | null, options?: { cached?: boolean; workspaceId?: string | null }) => Promise<void>;
  fetchOperationHistory: (sessionId: string) => Promise<void>;
  undoOperationById: (sessionId: string, approvalId: string) => Promise<boolean>;
  appendOperation: (op: OperationRecord) => void;
  /** 打开跨文件对比视图 */
  openCompare: (fileA: string, fileB: string, relationship?: FileRelationship) => void;
  closeCompare: () => void;
  setCompareSheetA: (sheet: string) => void;
  setCompareSheetB: (sheet: string) => void;
  setCompareRelationship: (rel: FileRelationship | null) => void;
  /** 设置合并结果摘要 */
  setMergeResult: (result: MergeResultInfo | null) => void;
  injectDemoFile: () => void;
  clearDemoFile: () => void;
  clearSession: () => void;
}

export const useExcelStore = create<ExcelState>()(
  persist(
    (set, get) => ({
  panelOpen: false,
  activeFilePath: null,
  activeSheet: null,
  diffs: [],
  textDiffs: [],
  previews: {},
  textPreviews: {},
  refreshCounter: 0,
  contentVersions: {},
  activeWorkspaceKey: null,
  viewGeneration: 0,
  workbookChanges: {},
  recentFiles: [],
  fullViewPath: null,
  fullViewSheet: null,
  fullViewLayout: "embedded",
  selectionMode: false,
  pendingSelection: null,
  draftRange: null,
  liveSelection: null,
  pendingFileMention: null,
  pendingFileMentions: null,
  pendingTemplateMessage: null,
  draggingFileCount: 0,
  dismissedPaths: new Set<string>(),
  autoOpenSuppressedSessionId: null,
  showSystemFiles: false,
  workspaceFilesVersion: 0,
  workspaceFiles: [],
  wsFilesLoaded: false,
  workspaceFilesLoading: false,
  workspaceFilesSessionId: undefined,
  workspaceFilesWorkspaceId: null,
  workspaceFilesLoadedVersion: -1,
  workspaceFilesLoadedAt: 0,
  workspaceFilesError: null,
  workspaceFilesTruncated: false,
  demoFile: null,
  streamingToolContent: {},
  operations: [],
  operationsLoading: false,
  operationsLoaded: false,
  operationsError: null,
  panelTab: "sheet",
  historySubview: "revisions",


  compareMode: false,
  compareFileA: null,
  compareFileB: null,
  compareSheetA: null,
  compareSheetB: null,
  compareRelationship: null,
  compareReturnPath: null,

  lastMergeResult: null,

  openPanel: (filePath, sheet) =>
    set(() => {
      if (filePath && !isSpreadsheetFile(filePath)) return {};
      const workspaceKey = workspaceKeyFromSession(activeSession());
      if (!filePath) {
        return { panelOpen: true, panelTab: "sheet", activeWorkspaceKey: workspaceKey };
      }
      openWorkspaceWorkbook(filePath, sheet);
      return {
        panelOpen: true,
        panelTab: "sheet",
        activeFilePath: filePath,
        activeSheet: sheet ?? null,
        activeWorkspaceKey: workspaceKey,
      };
    }),

  openHistory: (filePath, view = "revisions") => {
    if (filePath && !isSpreadsheetFile(filePath)) return;
    get().openPanel(filePath);
    set({ panelTab: "history", historySubview: view });
  },

  setPanelTab: (tab) => set({ panelTab: tab }),

  setHistorySubview: (view) => set({ historySubview: view }),

  closePanel: () => set((state) => ({
    panelOpen: false,
    ...(state.panelOpen ? { autoOpenSuppressedSessionId: activeSessionId() } : {}),
  })),

  setActiveSheet: (sheet) => set({ activeSheet: sheet }),

  setContentVersion: (path, version, workspaceKey) => {
    if (!isSpreadsheetFile(path)) return;
    set((state) => {
      const ws = workspaceKey ?? state.activeWorkspaceKey ?? "_";
      const key = versionStoreKey(path, ws);
      if (!key.endsWith("|") && !normalizeExcelPath(path)) return state;
      const next = { ...state.contentVersions };
      if (!version) {
        delete next[key];
      } else {
        next[key] = version;
      }
      return { contentVersions: next };
    });
  },

  getContentVersion: (path, workspaceKey) => {
    const ws = workspaceKey ?? get().activeWorkspaceKey ?? "_";
    return get().contentVersions[versionStoreKey(path, ws)] ?? null;
  },

  notifyWorkbookChanged: (path, workspaceKey, version, source = "remote") => {
    if (!isSpreadsheetFile(path)) return;
    const key = versionStoreKey(path, workspaceKey);
    const previous = get().workbookChanges[key];
    if (source !== "refresh" && version && previous?.version === version) return;
    invalidateWorkbookCaches({ workspaceKey, relative: path });
    set((state) => {
      const contentVersions = { ...state.contentVersions };
      if (version) contentVersions[key] = version;
      else delete contentVersions[key];
      return { contentVersions, workbookChanges: { ...state.workbookChanges,
        [key]: { sequence: (previous?.sequence ?? 0) + 1, version, source },
      } };
    });
  },

  rebindSession: (prevWorkspaceKey, nextWorkspaceKey) =>
    set((state) => {
      const nextKey = nextWorkspaceKey || "_";
      const prevKey = prevWorkspaceKey || state.activeWorkspaceKey;
      if (prevKey === nextKey) {
        return { activeWorkspaceKey: nextKey };
      }
      const contentVersions: Record<string, string> = {};
      for (const [key, value] of Object.entries(state.contentVersions)) {
        if (key.startsWith(`${nextKey}|`)) contentVersions[key] = value;
      }
      const recentWorkspaceFiles = sanitizeRecentFiles(state.recentFiles)
        .filter((file) => file.workspaceKey === nextKey)
        .map((file) => ({ path: file.path, filename: file.filename }));
      return {
        activeWorkspaceKey: nextKey,
        viewGeneration: state.viewGeneration + 1,
        // Seed the new scope from its persisted recent bucket. The backend
        // scan will replace this projection with the complete tree.
        workspaceFiles: recentWorkspaceFiles,
        wsFilesLoaded: recentWorkspaceFiles.length > 0,
        workspaceFilesLoading: false,
        workspaceFilesSessionId: undefined,
        workspaceFilesWorkspaceId: null,
        workspaceFilesLoadedVersion: -1,
        workspaceFilesLoadedAt: 0,
        workspaceFilesError: null,
        workspaceFilesTruncated: false,
        workbookChanges: {},
        contentVersions,
        fullViewPath: null,
        fullViewSheet: null,
        activeFilePath: null,
        activeSheet: null,
        selectionMode: false,
        pendingSelection: null,
        draftRange: null,
        liveSelection: null,
        compareMode: false,
        compareReturnPath: null,
        compareFileA: null,
        compareFileB: null,
        compareSheetA: null,
        compareSheetB: null,
        compareRelationship: null,
        diffs: [],
        textDiffs: [],
        previews: {},
        streamingToolContent: {},
        refreshCounter: state.refreshCounter + 1,
      };
    }),

  addDiff: (diff) =>
    set((state) => {
      if (
        !isSpreadsheetFile(diff.filePath)
        || (diff.filePathB && !isSpreadsheetFile(diff.filePathB))
      ) return state;
      // 按 toolCallId + filePath + sheet 去重，避免重放/多路径发射导致重复
      const dupKey = `${diff.toolCallId}|${diff.filePath}|${diff.sheet}`;
      const isDup = state.diffs.some(
        (d) => `${d.toolCallId}|${d.filePath}|${d.sheet}` === dupKey,
      );
      if (isDup) return state;
      const newDiffs = [...state.diffs, diff].slice(-MAX_PERSISTED_DIFFS);
      return {
        diffs: newDiffs,
      };
    }),

  addTextPreview: (preview) =>
    set((state) => ({
      textPreviews: { ...state.textPreviews, [preview.toolCallId]: preview },
    })),

  addTextDiff: (diff) =>
    set((state) => {
      const dupKey = `${diff.toolCallId}|${diff.filePath}`;
      const isDup = state.textDiffs.some(
        (d) => `${d.toolCallId}|${d.filePath}` === dupKey,
      );
      if (isDup) return state;
      const newTextDiffs = [...state.textDiffs, diff].slice(-MAX_PERSISTED_DIFFS);
      return { textDiffs: newTextDiffs };
    }),

  appendStreamingArgs: (toolCallId, delta) =>
    set((state) => ({
      streamingToolContent: {
        ...state.streamingToolContent,
        [toolCallId]: (state.streamingToolContent[toolCallId] || "") + delta,
      },
    })),

  clearStreamingArgs: (toolCallId) =>
    set((state) => {
      const next = { ...state.streamingToolContent };
      delete next[toolCallId];
      return { streamingToolContent: next };
    }),

  addPreview: (preview) =>
    set((state) => {
      if (!isSpreadsheetFile(preview.filePath)) return state;
      return {
        previews: { ...state.previews, [preview.toolCallId]: preview },
      };
    }),

  addRecentFile: (file, explicitWorkspaceKey) =>
    set((state) => {
      const workspaceKey = explicitWorkspaceKey ?? workspaceKeyFromSession(activeSession());
      // Mutation/recovery events can contain images, documents, or text files.
      // This store is the workbook LRU, so reject anything outside the
      // spreadsheet kind before it can reach persistence or the workbook UI.
      if (
        !isScopedWorkspaceKey(workspaceKey)
        || !toPublicFileIdentity(file.path)
        || !isSpreadsheetFile(displayFileName(file.path) || file.filename)
      ) return {};
      const normPath = normalizeExcelPath(file.path);
      const filtered = sanitizeRecentFiles(state.recentFiles).filter(
        (f) => !(normalizeExcelPath(f.path) === normPath && f.workspaceKey === workspaceKey),
      );
      const entry: ExcelFileRef = {
        path: file.path,
        filename: displayFileName(file.path) || file.filename,
        lastUsedAt: Date.now(),
        workspaceKey,
      };
      const sameWs = filtered.filter((f) => f.workspaceKey === workspaceKey);
      const otherWs = filtered.filter((f) => f.workspaceKey !== workspaceKey);
      const updated = [entry, ...sameWs, ...otherWs].slice(0, MAX_RECENT_FILES);
      const newDismissed = new Set(state.dismissedPaths);
      newDismissed.delete(file.path);
      newDismissed.delete(normPath);
      newDismissed.delete(`${workspaceKey}|${file.path}`);
      newDismissed.delete(`${workspaceKey}|${normPath}`);
      return { recentFiles: updated, dismissedPaths: newDismissed };
    }),

  addRecentFileIfNotDismissed: (file, explicitWorkspaceKey) =>
    set((state) => {
      const workspaceKey = explicitWorkspaceKey ?? workspaceKeyFromSession(activeSession());
      if (state.dismissedPaths.has(file.path)
        || state.dismissedPaths.has(`${workspaceKey}|${file.path}`)
        || state.dismissedPaths.has(`${workspaceKey}|${normalizeExcelPath(file.path)}`)) return {};
      if (
        !isScopedWorkspaceKey(workspaceKey)
        || !toPublicFileIdentity(file.path)
        || !isSpreadsheetFile(displayFileName(file.path) || file.filename)
      ) return {};
      const normPath = normalizeExcelPath(file.path);
      const filtered = sanitizeRecentFiles(state.recentFiles).filter(
        (f) => !(normalizeExcelPath(f.path) === normPath && f.workspaceKey === workspaceKey),
      );
      const entry: ExcelFileRef = {
        path: file.path,
        filename: displayFileName(file.path) || file.filename,
        lastUsedAt: Date.now(),
        workspaceKey,
      };
      const sameWs = filtered.filter((f) => f.workspaceKey === workspaceKey);
      const otherWs = filtered.filter((f) => f.workspaceKey !== workspaceKey);
      const updated = [entry, ...sameWs, ...otherWs].slice(0, MAX_RECENT_FILES);
      return { recentFiles: updated, workspaceFilesVersion: state.workspaceFilesVersion + 1 };
    }),

  removeRecentFile: (path, workspaceKey) =>
    set((state) => {
      const normalized = normalizeExcelPath(path);
      const newDismissed = new Set(state.dismissedPaths);
      // A relative path can legitimately exist in multiple workspaces. Keep
      // deletion scoped to the workspace that issued it.
      newDismissed.add(workspaceKey == null ? path : `${workspaceKey}|${normalized}`);
      return {
        recentFiles: state.recentFiles.filter((f) =>
          !(normalizeExcelPath(f.path) === normalized
            && (workspaceKey == null || f.workspaceKey === workspaceKey))),
        dismissedPaths: newDismissed,
      };
    }),

  removeRecentFiles: (paths, workspaceKey) =>
    set((state) => {
      const pathSet = new Set(paths.map((path) => normalizeExcelPath(path)));
      const newDismissed = new Set(state.dismissedPaths);
      for (const p of paths) {
        const normalized = normalizeExcelPath(p);
        newDismissed.add(workspaceKey == null ? p : `${workspaceKey}|${normalized}`);
      }
      return {
        recentFiles: state.recentFiles.filter((f) => {
          const matchesPath = pathSet.has(normalizeExcelPath(f.path));
          const matchesScope = workspaceKey == null || f.workspaceKey === workspaceKey;
          return !(matchesPath && matchesScope);
        }),
        dismissedPaths: newDismissed,
      };
    }),

  clearAllRecentFiles: () =>
    set((state) => {
      const newDismissed = new Set(state.dismissedPaths);
      for (const f of state.recentFiles) newDismissed.add(f.path);
      return { recentFiles: [], dismissedPaths: newDismissed };
    }),

  evictRecentFile: (path, workspaceKey) =>
    set((state) => {
      const normPath = normalizeExcelPath(path);
      const filtered = state.recentFiles.filter(
        (f) =>
          !(
            normalizeExcelPath(f.path) === normPath
            && (workspaceKey == null || f.workspaceKey === workspaceKey)
          ),
      );
      if (filtered.length === state.recentFiles.length) return {};
      return { recentFiles: filtered };
    }),

  dropMissingWorkbook: (path, workspaceKey) => {
    if (!isSpreadsheetFile(path)) return;
    const norm = normalizeRelativePath(path);
    if (!norm) return;
    const wsKey = workspaceKey ?? get().activeWorkspaceKey;
    // 1. 关闭已打开标签：任何仍指向该路径的面板都会在挂载时重新发起 observe，
    //    留着标签只会让同一个 404 反复把面板钉在加载壳上。
    const workspaceStore = useWorkbookWorkspaceStore.getState();
    for (const [groupKey, workspace] of Object.entries(workspaceStore.workspaces)) {
      let scope: unknown = null;
      try { scope = JSON.parse(groupKey); } catch { continue; }
      const groupWorkspaceKey = Array.isArray(scope) && scope.length > 1 ? String(scope[1]) : "";
      if (wsKey != null && groupWorkspaceKey !== wsKey) continue;
      if (!workspace.files.some((file) => normalizeRelativePath(file.path) === norm)) continue;
      workspaceStore.close(groupKey, norm);
    }
    // 2. 对话绑定不能继续指向读不出来的文件：改绑本组剩余主表，没有剩余就解绑。
    const conversation = useWorkbookConversationStore.getState();
    for (const [targetSessionId, target] of Object.entries(conversation.targets)) {
      if (!target?.file?.relative || normalizeRelativePath(target.file.relative) !== norm) continue;
      if (wsKey != null && target.file.workspaceKey !== wsKey) continue;
      const targetSession = useSessionStore.getState().sessions?.find((item) => item.id === targetSessionId);
      const remaining = useWorkbookWorkspaceStore.getState().workspaces[
        workbookWorkspaceKey(targetSessionId, wsKey ?? workspaceKeyFromSession(targetSession))
      ]?.files[0];
      if (remaining && targetSession) {
        conversation.bind(targetSessionId, fileRefFromSession(remaining.path, targetSession), remaining.sheet, get().fullViewLayout);
      } else {
        conversation.detach(targetSessionId);
      }
    }
    // 3. 视图缓存按同一身份清理，避免下一次打开命中失效快照。
    invalidateWorkbookCaches(wsKey == null ? { relative: norm } : { workspaceKey: wsKey, relative: norm });
    set((state) => {
      const samePath = (candidate: string | null | undefined) =>
        Boolean(candidate) && normalizeRelativePath(candidate as string) === norm;
      const contentVersions = { ...state.contentVersions };
      const workbookChanges = { ...state.workbookChanges };
      delete contentVersions[versionStoreKey(norm, wsKey ?? "_")];
      delete workbookChanges[versionStoreKey(norm, wsKey ?? "_")];
      return {
        recentFiles: state.recentFiles.filter((file) => !(normalizeRelativePath(file.path) === norm
          && (wsKey == null || file.workspaceKey === wsKey))),
        contentVersions,
        workbookChanges,
        // 面板与全屏视图正指向这个文件时回到空状态，而不是继续渲染死表格。
        ...(samePath(state.activeFilePath)
          ? { activeFilePath: null, activeSheet: null, selectionMode: false, draftRange: null, liveSelection: null }
          : {}),
        ...(samePath(state.fullViewPath) ? { fullViewPath: null, fullViewSheet: null } : {}),
      };
    });
  },

  handleFilesDeleted: (paths, workspaceKey) => {
    const targets = [...new Set(
      paths.map((path) => normalizeExcelPath(path)).filter((path) => path.length > 0),
    )];
    if (targets.length === 0) return;
    const wsKey = workspaceKey ?? get().activeWorkspaceKey;
    // 删除目标可能是文件夹：其全部后代一并视为已删除。
    const isDeletedPath = (candidate: string | null | undefined): boolean => {
      if (!candidate) return false;
      const normalized = normalizeExcelPath(candidate);
      return targets.some((target) => normalized === target || normalized.startsWith(`${target}/`));
    };
    const scopedBy = (entryKey: string): boolean => {
      if (wsKey == null) return true;
      const separator = entryKey.indexOf("|");
      const keyWs = separator >= 0 ? entryKey.slice(0, separator) : "_";
      return keyWs === wsKey;
    };

    const state = get();
    const session = activeSession();
    const sessionKey = workspaceKeyFromSession(session);

    // 1. 收集所有会失效的具体路径（含文件夹后代），先清视图缓存，
    //    避免任何面板继续命中已删除文件的旧快照。
    const doomed = new Set<string>();
    for (const file of state.workspaceFiles) {
      if (isDeletedPath(file.path)) doomed.add(normalizeExcelPath(file.path));
    }
    for (const file of state.recentFiles) {
      if (isDeletedPath(file.path) && (wsKey == null || file.workspaceKey === wsKey)) {
        doomed.add(normalizeExcelPath(file.path));
      }
    }
    const workbookStore = useWorkbookWorkspaceStore.getState();
    const currentGroupKey = workbookWorkspaceKey(session?.id, sessionKey);
    const affectedGroups: string[] = [];
    for (const [key, workspace] of Object.entries(workbookStore.workspaces)) {
      let scope: unknown = null;
      try { scope = JSON.parse(key); } catch { continue; }
      const groupWorkspaceKey = Array.isArray(scope) && scope.length > 1 ? String(scope[1]) : "";
      if (wsKey != null && groupWorkspaceKey !== wsKey) continue;
      let groupAffected = false;
      for (const file of workspace.files) {
        if (!isDeletedPath(file.path)) continue;
        doomed.add(normalizeExcelPath(file.path));
        // 2. 关闭已打开的表格标签：表格处不再保留已删除文件的标签。
        workbookStore.close(key, file.path);
        groupAffected = true;
      }
      if (groupAffected) affectedGroups.push(key);
    }
    for (const target of targets) doomed.add(target);
    for (const relative of doomed) {
      invalidateWorkbookCaches(wsKey == null ? { relative } : { workspaceKey: wsKey, relative });
    }

    // 3. 对话绑定：主文件被删的会话改绑本组剩余主表；没有剩余则解绑。
    //    覆盖所有受影响会话（含未激活会话的持久化绑定），不只是当前会话。
    const replacementBySession = new Map<string, { path: string; sheet?: string }>();
    for (const key of affectedGroups) {
      let scope: unknown = null;
      try { scope = JSON.parse(key); } catch { continue; }
      const groupSessionId = Array.isArray(scope) ? String(scope[0] ?? "") : "";
      if (!groupSessionId) continue;
      const nextPrimary = useWorkbookWorkspaceStore.getState().workspaces[key]?.files[0] ?? null;
      const previous = replacementBySession.get(groupSessionId);
      if (!previous || (!previous.path && nextPrimary)) {
        if (nextPrimary) replacementBySession.set(groupSessionId, { path: nextPrimary.path, sheet: nextPrimary.sheet });
        else if (!previous) replacementBySession.set(groupSessionId, { path: "" });
      }
    }
    const conversation = useWorkbookConversationStore.getState();
    for (const [targetSessionId, target] of Object.entries(conversation.targets)) {
      if (!target?.file?.relative || !isDeletedPath(target.file.relative)) continue;
      if (wsKey != null && target.file.workspaceKey !== wsKey) continue;
      const replacement = replacementBySession.get(targetSessionId);
      const targetSession = useSessionStore.getState().sessions?.find((item) => item.id === targetSessionId);
      if (replacement?.path && targetSession) {
        useWorkbookConversationStore.getState().bind(
          targetSessionId,
          fileRefFromSession(replacement.path, targetSession),
          replacement.sheet,
          get().fullViewLayout,
        );
      } else {
        useWorkbookConversationStore.getState().detach(targetSessionId);
      }
    }
    const activeGroup = useWorkbookWorkspaceStore.getState().workspaces[currentGroupKey];
    const primary = activeGroup?.files[0] ?? null;
    const focused = activeGroup?.files.find((file) => file.path === activeGroup?.focused) ?? primary;

    // 4. 本 store：文件列表、最近打开（写 dismissed 防 mutation 回声重新加入）、
    //    版本/变更记录、当前面板、全屏视图、对比视图与选区。
    set((current) => {
      const filesScopeKey = current.workspaceFilesWorkspaceId
        ? `id:${current.workspaceFilesWorkspaceId}`
        : workspaceKeyForSessionId(current.workspaceFilesSessionId);
      const touchesFilesScope = wsKey == null || wsKey === filesScopeKey;
      const touchesActiveScope = wsKey == null || wsKey === (current.activeWorkspaceKey ?? sessionKey);
      const recentFiles = sanitizeRecentFiles(current.recentFiles)
        .filter((file) => !(isDeletedPath(file.path) && (wsKey == null || file.workspaceKey === wsKey)));
      const dismissedPaths = new Set(current.dismissedPaths);
      const dismiss = (path: string) => {
        const normalized = normalizeExcelPath(path);
        if (normalized.length === 0) return;
        dismissedPaths.add(wsKey == null ? normalized : `${wsKey}|${normalized}`);
      };
      for (const target of targets) dismiss(target);
      // 文件夹删除：已知的每个后代也补 dismissal 键，否则 SSE 回声以
      // 子路径（./a/one.xlsx）形式到达时会被重新加回最近列表。
      for (const file of current.workspaceFiles) {
        if (isDeletedPath(file.path)) dismiss(file.path);
      }
      for (const file of current.recentFiles) {
        if (isDeletedPath(file.path) && (wsKey == null || file.workspaceKey === wsKey)) dismiss(file.path);
      }
      const contentVersions: Record<string, string> = {};
      for (const [key, value] of Object.entries(current.contentVersions)) {
        const path = key.slice(key.indexOf("|") + 1);
        if (isDeletedPath(path) && scopedBy(key)) continue;
        contentVersions[key] = value;
      }
      const workbookChanges: Record<string, WorkbookChange> = {};
      for (const [key, value] of Object.entries(current.workbookChanges)) {
        const path = key.slice(key.indexOf("|") + 1);
        if (isDeletedPath(path) && scopedBy(key)) continue;
        workbookChanges[key] = value;
      }
      const activeDeleted = touchesActiveScope && isDeletedPath(current.activeFilePath);
      const fullViewDeleted = touchesActiveScope && isDeletedPath(current.fullViewPath);
      const compareDeleted = touchesActiveScope && current.compareMode
        && (isDeletedPath(current.compareFileA) || isDeletedPath(current.compareFileB));
      return {
        workspaceFiles: touchesFilesScope
          ? current.workspaceFiles.filter((file) => !isDeletedPath(file.path))
          : current.workspaceFiles,
        recentFiles,
        dismissedPaths,
        contentVersions,
        workbookChanges,
        ...(activeDeleted ? {
          activeFilePath: focused?.path ?? null,
          activeSheet: focused?.sheet ?? null,
          panelOpen: focused ? current.panelOpen : false,
        } : {}),
        ...(fullViewDeleted ? {
          fullViewPath: primary?.path ?? null,
          fullViewSheet: primary?.sheet ?? null,
        } : {}),
        ...(compareDeleted ? {
          compareMode: false,
          compareFileA: null,
          compareFileB: null,
          compareSheetA: null,
          compareSheetB: null,
          compareRelationship: null,
          compareReturnPath: null,
        } : {}),
        liveSelection: current.liveSelection && isDeletedPath(current.liveSelection.path)
          ? null : current.liveSelection,
        draftRange: current.draftRange?.path && isDeletedPath(current.draftRange.path)
          ? null : current.draftRange,
        pendingSelection: current.pendingSelection && isDeletedPath(current.pendingSelection.filePath)
          ? null : current.pendingSelection,
        workspaceFilesVersion: current.workspaceFilesVersion + 1,
      };
    });
  },

  mergeRecentFiles: (files, explicitWorkspaceKey, options) =>
    set((state) => {
      const workspaceKey = explicitWorkspaceKey ?? workspaceKeyFromSession(activeSession());
      const map = new Map<string, ExcelFileRef>();
      for (const f of sanitizeRecentFiles(state.recentFiles)) {
        map.set(`${f.workspaceKey}|${normalizeExcelPath(f.path)}`, f);
      }
      const present = new Set<string>();
      if (isScopedWorkspaceKey(workspaceKey)) {
        for (const f of files) {
          if (
            !toPublicFileIdentity(f.path)
            || !isSpreadsheetFile(displayFileName(f.path) || f.filename)
          ) continue;
          const key = `${workspaceKey}|${normalizeExcelPath(f.path)}`;
          present.add(normalizeExcelPath(f.path));
          if (!map.has(key)
            && !state.dismissedPaths.has(f.path)
            && !state.dismissedPaths.has(`${workspaceKey}|${f.path}`)
            && !state.dismissedPaths.has(`${workspaceKey}|${normalizeExcelPath(f.path)}`)) {
            map.set(key, {
              path: f.path,
              filename: displayFileName(f.path) || f.filename,
              lastUsedAt: f.modifiedAt ?? 0,
              workspaceKey,
            });
          }
        }
        // A complete scan is authoritative. Drop deleted/renamed entries from
        // the recent bucket so the history picker does not offer dead paths.
        if (options?.pruneMissing) {
          for (const key of map.keys()) {
            if (key.startsWith(`${workspaceKey}|`) && !present.has(key.slice(workspaceKey.length + 1))) {
              map.delete(key);
            }
          }
        }
      }
      const merged = Array.from(map.values())
        .sort((a, b) => b.lastUsedAt - a.lastUsedAt)
        .slice(0, MAX_RECENT_FILES);
      const unchanged = merged.length === state.recentFiles.length
        && merged.every((file, index) => {
          const previous = state.recentFiles[index];
          return previous?.path === file.path
            && previous.filename === file.filename
            && previous.lastUsedAt === file.lastUsedAt
            && previous.workspaceKey === file.workspaceKey;
        });
      return unchanged ? state : { recentFiles: merged };
    }),

  openFullView: (path, sheet, layout = "embedded") => {
    if (!isSpreadsheetFile(path)) return;
    const session = activeSession();
    const workspace = openWorkspaceWorkbook(path, sheet);
    const primary = workspace.files[0];
    if (session) useWorkbookConversationStore.getState().bind(session.id, fileRefFromSession(primary.path, session), primary.sheet, layout);
    set({
      panelOpen: false,
      panelTab: "sheet",
      compareMode: false,
      compareReturnPath: null,
      fullViewPath: primary.path,
      fullViewSheet: primary.sheet ?? null,
      fullViewLayout: layout,
      activeFilePath: path,
      activeSheet: sheet ?? null,
      activeWorkspaceKey: workspaceKeyFromSession(session),
      liveSelection: null,
      draftRange: null,
    });
  },

  focusWorkbook: (path) => {
    const session = activeSession();
    const key = workbookWorkspaceKey(session?.id, workspaceKeyFromSession(session));
    const workspace = useWorkbookWorkspaceStore.getState().workspaces[key];
    const file = workspace?.files.find((entry) => normalizeExcelPath(entry.path) === normalizeExcelPath(path));
    if (!file || (workspace.focused === file.path && get().activeFilePath === file.path)) return;
    useWorkbookWorkspaceStore.getState().focus(key, file.path);
    set({ activeFilePath: file.path, activeSheet: file.sheet ?? null, liveSelection: null, draftRange: null, panelTab: "sheet" });
  },

  setPrimaryWorkbook: (path, sheet) => {
    if (!isSpreadsheetFile(path)) return;
    const session = activeSession();
    const key = workbookWorkspaceKey(session?.id, workspaceKeyFromSession(session));
    openWorkspaceWorkbook(path, sheet);
    useWorkbookWorkspaceStore.getState().promote(key, path);
    const primary = useWorkbookWorkspaceStore.getState().workspaces[key].files[0];
    if (session) {
      const conversation = useWorkbookConversationStore.getState();
      const layout = get().fullViewPath ? get().fullViewLayout : conversation.targets[session.id]?.layout ?? get().fullViewLayout;
      conversation.bind(session.id, fileRefFromSession(primary.path, session), primary.sheet, layout);
      conversation.setShowSheet(session.id, Boolean(get().fullViewPath));
    }
    set({ activeFilePath: primary.path, activeSheet: primary.sheet ?? null, liveSelection: null, draftRange: null,
      ...(get().fullViewPath ? { fullViewPath: primary.path, fullViewSheet: primary.sheet ?? null } : {}) });
  },

  closeWorkbook: async (path) => {
    const session = activeSession();
    const key = workbookWorkspaceKey(session?.id, workspaceKeyFromSession(session));
    const file = fileRefFromSession(path, session);
    const edits = await import("@/lib/excel-cell-edit");
    await edits.flushWorkbookEdits(file);
    if (edits.isWorkbookEditPaused(file) || edits.hasPendingWorkbookEdits(file)
      || activeSessionId() !== (session?.id ?? null) || workspaceKeyFromSession(activeSession()) !== file.workspaceKey) return false;
    useWorkbookWorkspaceStore.getState().close(key, path);
    const workspace = useWorkbookWorkspaceStore.getState().workspaces[key];
    const primary = workspace?.files[0];
    const focused = workspace?.files.find((file) => file.path === workspace.focused) ?? primary;
    if (session) {
      const conversation = useWorkbookConversationStore.getState();
      if (primary) {
        conversation.bind(session.id, fileRefFromSession(primary.path, session), primary.sheet, get().fullViewLayout);
        conversation.setShowSheet(session.id, Boolean(get().fullViewPath));
      } else conversation.detach(session.id);
    }
    set({ autoOpenSuppressedSessionId: session?.id ?? null,
      activeFilePath: focused?.path ?? null, activeSheet: focused?.sheet ?? null,
      fullViewPath: get().fullViewPath ? primary?.path ?? null : null,
      fullViewSheet: get().fullViewPath ? primary?.sheet ?? null : null,
      panelOpen: primary ? get().panelOpen : false, liveSelection: null, draftRange: null, selectionMode: false });
    return true;
  },

  closeFullView: () => {
    const sessionId = activeSessionId();
    if (sessionId) useWorkbookConversationStore.getState().setShowSheet(sessionId, false);
    set({
      ...(get().fullViewPath ? { autoOpenSuppressedSessionId: sessionId } : {}),
      fullViewPath: null,
      fullViewSheet: null,
    });
  },

  enterSelectionMode: () =>
    set({ selectionMode: true, pendingSelection: null, draftRange: null }),

  exitSelectionMode: () =>
    set({ selectionMode: false, pendingSelection: null, draftRange: null }),

  confirmSelection: (sel) =>
    set({ selectionMode: false, pendingSelection: sel, draftRange: null }),

  setDraftRange: (range) => {
    if (!range) {
      set({ draftRange: null });
      return;
    }
    const path = range.path;
    const known = path ? get().getContentVersion(path) : null;
    set({
      draftRange: {
        ...range,
        contentVersion: range.contentVersion ?? known ?? undefined,
      },
    });
  },

  setLiveSelection: (sel) =>
    set((state) => {
      const prev = state.liveSelection;
      if (prev === sel) return state;
      if (!prev || !sel) return { liveSelection: sel ?? null };
      if (prev.path === sel.path && prev.sheet === sel.sheet && prev.range === sel.range
        && prev.contentVersion === sel.contentVersion) return state;
      return { liveSelection: sel };
    }),

  clearPendingSelection: () => set({ pendingSelection: null }),

  mentionFileToInput: (file) => set({ pendingFileMention: file }),

  clearPendingFileMention: () => set({ pendingFileMention: null }),

  mentionFilesToInput: (files) => set({ pendingFileMentions: files.length > 0 ? files : null }),

  clearPendingFileMentions: () => set({ pendingFileMentions: null }),

  setPendingTemplateMessage: (msg) => set({ pendingTemplateMessage: msg }),

  clearPendingTemplateMessage: () => set({ pendingTemplateMessage: null }),

  toggleShowSystemFiles: () =>
    set((state) => ({ showSystemFiles: !state.showSystemFiles })),

  bumpWorkspaceFilesVersion: () =>
    set((state) => ({ workspaceFilesVersion: state.workspaceFilesVersion + 1 })),

  refreshWorkspaceFiles: (sessionId, options) => {
    const sid = sessionId === undefined ? activeSessionId() : sessionId;
    const session = sid == null
      ? undefined
      : useSessionStore.getState().sessions?.find((item) => item.id === sid);
    // Callers usually know the session but not its workspace id. Resolve it
    // here so a session rebind cannot reuse a snapshot from the old folder.
    const wid = options?.workspaceId === undefined
      ? session?.workspaceId ?? null
      : options.workspaceId ?? null;
    const state = get();
    const version = state.workspaceFilesVersion;
    const sameWorkspace = wid ? state.workspaceFilesWorkspaceId === wid : state.workspaceFilesSessionId === sid;
    if (options?.cached && state.wsFilesLoaded && !state.workspaceFilesError && sameWorkspace
      && state.workspaceFilesWorkspaceId === wid
      && state.workspaceFilesLoadedVersion === version && Date.now() - state.workspaceFilesLoadedAt < 30_000) {
      if (state.workspaceFilesSessionId !== sid) set({ workspaceFilesSessionId: sid });
      return Promise.resolve();
    }
    if (state.workspaceFilesSessionId !== sid || state.workspaceFilesWorkspaceId !== wid) {
      // A session rebind may have seeded the list from the scoped recent
      // bucket. Keep those rows visible while the authoritative scan runs.
      const seeded = state.workspaceFiles.length > 0;
      set({ workspaceFiles: seeded ? state.workspaceFiles : [], wsFilesLoaded: seeded, workspaceFilesSessionId: sid, workspaceFilesWorkspaceId: wid,
        workspaceFilesLoadedVersion: -1, workspaceFilesLoadedAt: 0, workspaceFilesLoading: false,
        workspaceFilesTruncated: false });
    }
    const requestKey = `${sid ?? "_"}|${wid ?? "_"}|${version}`;
    const existingRequest = workspaceFilesRequests.get(requestKey);
    if (existingRequest) {
      if (!get().workspaceFilesLoading) set({ workspaceFilesLoading: true });
      return existingRequest.promise;
    }
    if (state.workspaceFilesError) set({ workspaceFilesError: null });
    const request: WorkspaceFilesRequest = { sessionId: sid, workspaceId: wid, version, promise: Promise.resolve() };
    workspaceFilesRequests.set(requestKey, request);
    set({ workspaceFilesLoading: true });
    request.promise = (async () => {
      try {
        const { files, truncated } = await fetchWorkspaceFiles(sid, wid);
        // An older scan must not replace a newer scan or another scope's files.
        const current = get();
        const currentScope = current.workspaceFilesSessionId === sid
          && current.workspaceFilesWorkspaceId === wid;
        const currentKey = `${sid ?? "_"}|${wid ?? "_"}|${current.workspaceFilesVersion}`;
        if (workspaceFilesRequests.get(requestKey) !== request || activeSessionId() !== sid
          || current.workspaceFilesVersion !== version || !currentScope) {
          if (currentScope && !workspaceFilesRequests.has(currentKey)) set({ workspaceFilesLoading: false });
          return;
        }
        const next = files.map((f) => ({ path: f.path, filename: f.filename, is_dir: f.is_dir }));
        // A capped or partially unreadable scan is not authoritative. Keep
        // scoped historical entries visible even when they fall outside the
        // returned page; opening a deleted one will evict it safely.
        if (truncated) {
          const present = new Set(next.map((file) => normalizeExcelPath(file.path)));
          for (const recent of sanitizeRecentFiles(current.recentFiles)) {
            if (recent.workspaceKey !== workspaceKeyForSessionId(sid)) continue;
            const normalized = normalizeExcelPath(recent.path);
            if (present.has(normalized)) continue;
            present.add(normalized);
            next.push({ path: recent.path, filename: recent.filename, is_dir: false });
          }
        }
        const previous = current.workspaceFiles;
        const unchanged = previous.length === next.length && previous.every((file, index) =>
          file.path === next[index].path && file.filename === next[index].filename && file.is_dir === next[index].is_dir);
        set({ workspaceFiles: unchanged ? previous : next, wsFilesLoaded: true, workspaceFilesSessionId: sid,
          workspaceFilesWorkspaceId: wid, workspaceFilesLoading: false,
          workspaceFilesLoadedVersion: version, workspaceFilesLoadedAt: Date.now(), workspaceFilesTruncated: truncated });
        // Reuse the same scan for recent workbooks instead of walking the workspace twice.
        get().mergeRecentFiles(files.filter((file) => !file.is_dir && isSpreadsheetFile(file.filename)).map((file) => ({
          path: file.path, filename: file.filename, modifiedAt: (file.modified_at || 0) * 1000,
        })), workspaceKeyForSessionId(sid), { pruneMissing: !truncated });
      } catch (error) {
        // Keep the previous snapshot; a failed scan is not an empty workspace.
        const current = get();
        const currentScope = current.workspaceFilesSessionId === sid
          && current.workspaceFilesWorkspaceId === wid;
        const currentKey = `${sid ?? "_"}|${wid ?? "_"}|${current.workspaceFilesVersion}`;
        if (workspaceFilesRequests.get(requestKey) === request && activeSessionId() === sid
          && current.workspaceFilesVersion === version && currentScope) {
          set({ workspaceFilesLoading: false, workspaceFilesError: error instanceof Error ? error.message : "文件列表加载失败" });
        } else if (currentScope && !workspaceFilesRequests.has(currentKey)) {
          set({ workspaceFilesLoading: false });
        }
      } finally {
        if (workspaceFilesRequests.get(requestKey) === request) workspaceFilesRequests.delete(requestKey);
      }
    })();
    return request.promise;
  },

  fetchOperationHistory: async (sessionId) => {
    if (activeSessionId() !== sessionId) return;
    const request = ++operationHistoryRequest;
    set({ operationsLoading: true, operationsError: null });
    try {
      const data = await fetchOperations(sessionId, { limit: 100 });
      if (request !== operationHistoryRequest || activeSessionId() !== sessionId) return;
      set({
        operations: data.operations,
        operationsLoaded: true,
        operationsLoading: false,
      });
    } catch (error) {
      if (request !== operationHistoryRequest || activeSessionId() !== sessionId) return;
      set({ operationsLoading: false, operationsLoaded: true,
        operationsError: error instanceof Error ? error.message : "操作记录加载失败，请重试" });
    }
  },

  undoOperationById: async (sessionId, approvalId) => {
    const key = `${sessionId}|${approvalId}`;
    const original = get().operations.find((op) => op.approval_id === approvalId);
    if (activeSessionId() !== sessionId || undoRequests.has(key) || !original?.undoable) return false;
    undoRequests.add(key);
    const source = useSessionStore.getState().sessions.find((s) => s.id === sessionId);
    const workspaceKey = workspaceKeyFromSession(source);

    set((state) => ({
      operationsError: null,
      operations: state.operations.map((op) =>
        op.approval_id === approvalId
          ? { ...op, undoable: false }
          : op
      ),
    }));

    try {
      const { flushWorkbookEdits, hasPendingWorkbookEdits, isWorkbookEditPaused } = await import("@/lib/excel-cell-edit");
      for (const change of original.changes) {
        const file = { workspaceKey, relative: change.path };
        await flushWorkbookEdits(file);
        if (hasPendingWorkbookEdits(file) || isWorkbookEditPaused(file)) throw new Error("仍有未保存的编辑，请处理后再撤销。");
      }
      const result = await apiUndoOperation(sessionId, approvalId);
      if (result.status === "ok") {
        if (activeSessionId() === sessionId) set((state) => ({ operations: state.operations.map((op) =>
          op.approval_id === approvalId ? { ...op, undoable: false } : op) }));
        for (const change of original.changes) {
          get().notifyWorkbookChanged(change.path, workspaceKey, undefined, "refresh");
        }
        get().bumpWorkspaceFilesVersion();
        return true;
      }
      throw new Error(result.message || "撤销失败，请检查文件版本");
    } catch (error) {
      if (activeSessionId() === sessionId) set((state) => ({
        operations: state.operations.map((op) => op.approval_id === approvalId ? { ...op, undoable: original.undoable } : op),
        operationsError: error instanceof Error ? error.message : "撤销失败，请重试",
      }));
      return false;
    } finally {
      undoRequests.delete(key);
    }
  },

  appendOperation: (op) =>
    set((state) => {
      const exists = state.operations.some((o) => o.approval_id === op.approval_id);
      if (exists) return state;
      return { operations: [op, ...state.operations] };
    }),

  openCompare: (fileA, fileB, relationship) => {
    if (!isSpreadsheetFile(fileA) || !isSpreadsheetFile(fileB)) return;
    set({
      compareReturnPath: get().compareMode ? get().compareReturnPath : get().fullViewPath,
      compareMode: true,
      compareFileA: fileA,
      compareFileB: fileB,
      compareSheetA: null,
      compareSheetB: null,
      compareRelationship: relationship ?? null,
      fullViewPath: null,
      fullViewSheet: null,
      panelOpen: false,
    });
  },

  closeCompare: () => {
    const session = activeSession();
    const key = workbookWorkspaceKey(session?.id, workspaceKeyFromSession(session));
    const primary = useWorkbookWorkspaceStore.getState().workspaces[key]?.files[0];
    set((state) => ({
      ...(state.compareMode ? { autoOpenSuppressedSessionId: session?.id ?? null } : {}),
      ...(state.compareMode && state.compareReturnPath ? {
        fullViewPath: primary?.path ?? state.compareReturnPath, fullViewSheet: primary?.sheet ?? null,
      } : {}),
      compareReturnPath: null,
      compareMode: false,
      compareFileA: null,
      compareFileB: null,
      compareSheetA: null,
      compareSheetB: null,
      compareRelationship: null,
    }));
  },

  setCompareSheetA: (sheet) => set({ compareSheetA: sheet }),

  setCompareSheetB: (sheet) => set({ compareSheetB: sheet }),

  setCompareRelationship: (rel) => set({ compareRelationship: rel }),

  setMergeResult: (result) => set({ lastMergeResult: result }),

  injectDemoFile: () => {
    const demo = { path: "__demo__/示例销售数据.xlsx", filename: "示例销售数据.xlsx" };
    set({ demoFile: demo });
  },

  clearDemoFile: () =>
    set({ demoFile: null }),

  clearSession: () =>
    set({
      workspaceFiles: [],
      wsFilesLoaded: false,
      workspaceFilesLoading: false,
      workspaceFilesSessionId: undefined,
      workspaceFilesWorkspaceId: null,
      workspaceFilesLoadedVersion: -1,
      workspaceFilesLoadedAt: 0,
      workspaceFilesError: null,
      workspaceFilesTruncated: false,
      diffs: [],
      textDiffs: [],
      previews: {},
      streamingToolContent: {},
      refreshCounter: 0,
      workbookChanges: {},
      contentVersions: {},
      fullViewPath: null,
      fullViewSheet: null,
      selectionMode: false,
      pendingSelection: null,
      draftRange: null,
      liveSelection: null,
      pendingFileMentions: null,
      pendingTemplateMessage: null,
      operations: [],
      operationsLoading: false,
      operationsLoaded: false,
      operationsError: null,
      panelTab: "sheet",
      historySubview: "revisions",
      compareMode: false,
      compareReturnPath: null,
      compareFileA: null,
      compareFileB: null,
      compareSheetA: null,
      compareSheetB: null,
      compareRelationship: null,
      lastMergeResult: null,
    }),
    }),
    {
      name: "excelmanus-excel-files",
      partialize: (state) => ({
        recentFiles: sanitizeRecentFiles(state.recentFiles),
        dismissedPaths: Array.from(state.dismissedPaths),
        showSystemFiles: state.showSystemFiles,
      }),
      merge: (persisted, current) => {
        const p = persisted as Record<string, unknown> | undefined;
        const dismissed = Array.isArray(p?.dismissedPaths)
          ? new Set<string>(p.dismissedPaths as string[])
          : new Set<string>();
        // diffs / textDiffs 是会话级瞬态数据，不从 localStorage 恢复
        const { diffs: _d, textDiffs: _td, pendingBackups: _pb, appliedPaths: _ap, undoableApplies: _ua, backupEnabled: _be, backupLoading: _bl, backupInFlight: _bi, ...safeP } = (p ?? {}) as Record<string, unknown>;
        const rawRecent = Array.isArray(safeP.recentFiles) ? safeP.recentFiles as ExcelFileRef[] : [];
        return {
          ...current,
          ...safeP,
          recentFiles: sanitizeRecentFiles(rawRecent),
          dismissedPaths: dismissed,
        };
      },
    }
  )
);
