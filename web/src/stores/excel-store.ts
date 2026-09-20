import { create } from "zustand";
import { invalidateWorkbookCaches } from "@/lib/api";
import { persist } from "zustand/middleware";
import {
  fetchWorkspaceFiles,
  normalizeExcelPath,
  fetchOperations,
  undoOperation as apiUndoOperation,
  fetchFileGroups,
  createFileGroup as apiCreateFileGroup,
  deleteFileGroup as apiDeleteFileGroup,
  type ExcelFileListItem,
  type OperationRecord,
  type FileGroup,
} from "@/lib/api";
import { useSessionStore } from "@/stores/session-store";
import {
  activeSession,
  isScopedWorkspaceKey,
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

let workspaceFilesRequest: { sessionId: string | null; workspaceId: string | null; version: number; promise: Promise<void> } | null = null;

function activeSessionId(): string | null {
  return useSessionStore.getState().activeSessionId;
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

  // 工作区系统文件可见性（默认隐藏，用户可在侧栏开关切换）
  showSystemFiles: boolean;

  // 工作区文件树刷新信号（在 mutation 事件时递增）
  workspaceFilesVersion: number;

  // 工作区文件列表缓存（避免每次挂载组件都重新加载）
  workspaceFiles: { path: string; filename: string; is_dir?: boolean }[];
  wsFilesLoaded: boolean;
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

  // 侧栏：表格 / 历史
  panelTab: ExcelPanelTab;
  historySubview: HistorySubview;

  // 文件组
  fileGroups: FileGroup[];
  fileGroupsLoaded: boolean;
  activeGroupId: string | null;
  groupViewMode: boolean;

  // 跨文件对比模式
  compareMode: boolean;
  compareFileA: string | null;
  compareFileB: string | null;
  compareSheetA: string | null;
  compareSheetB: string | null;
  compareRelationship: FileRelationship | null;

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
  removeRecentFile: (path: string) => void;
  removeRecentFiles: (paths: string[]) => void;
  clearAllRecentFiles: () => void;
  mergeRecentFiles: (files: { path: string; filename: string; modifiedAt?: number }[], workspaceKey?: string) => void;
  /** Evict a cached entry the backend reported missing. 不写 dismissedPaths，文件重建后仍可重新出现。 */
  evictRecentFile: (path: string, workspaceKey?: string | null) => void;
  openFullView: (path: string, sheet?: string, layout?: WorkbookViewLayout) => void;
  closeFullView: () => void;
  enterSelectionMode: () => void;
  exitSelectionMode: () => void;
  confirmSelection: (sel: { filePath: string; sheet: string; range: string; contentVersion?: string }) => void;
  setDraftRange: (range: { range: string; sheet: string; path?: string; contentVersion?: string } | null) => void;
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
  loadFileGroups: () => Promise<void>;
  createGroupFromSelected: (name: string, fileIds: string[]) => Promise<string | null>;
  deleteGroup: (groupId: string) => Promise<void>;
  setActiveGroup: (groupId: string | null) => void;
  toggleGroupViewMode: () => void;
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
  pendingFileMention: null,
  pendingFileMentions: null,
  pendingTemplateMessage: null,
  draggingFileCount: 0,
  dismissedPaths: new Set<string>(),
  showSystemFiles: false,
  workspaceFilesVersion: 0,
  workspaceFiles: [],
  wsFilesLoaded: false,
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
  panelTab: "sheet",
  historySubview: "revisions",

  fileGroups: [],
  fileGroupsLoaded: false,
  activeGroupId: null,
  groupViewMode: false,

  compareMode: false,
  compareFileA: null,
  compareFileB: null,
  compareSheetA: null,
  compareSheetB: null,
  compareRelationship: null,

  lastMergeResult: null,

  openPanel: (filePath, sheet) =>
    set(() => {
      const workspaceKey = workspaceKeyFromSession(activeSession());
      if (!filePath) {
        return { panelOpen: true, panelTab: "sheet", activeWorkspaceKey: workspaceKey };
      }
      return {
        panelOpen: true,
        panelTab: "sheet",
        activeFilePath: filePath,
        activeSheet: sheet ?? null,
        activeWorkspaceKey: workspaceKey,
      };
    }),

  openHistory: (filePath, view = "revisions") =>
    set((s) => ({
      panelOpen: true,
      panelTab: "history",
      historySubview: view ?? s.historySubview,
      ...(filePath ? { activeFilePath: filePath } : {}),
    })),

  setPanelTab: (tab) => set({ panelTab: tab }),

  setHistorySubview: (view) => set({ historySubview: view }),

  closePanel: () => set({ panelOpen: false }),

  setActiveSheet: (sheet) => set({ activeSheet: sheet }),

  setContentVersion: (path, version, workspaceKey) =>
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
    }),

  getContentVersion: (path, workspaceKey) => {
    const ws = workspaceKey ?? get().activeWorkspaceKey ?? "_";
    return get().contentVersions[versionStoreKey(path, ws)] ?? null;
  },

  notifyWorkbookChanged: (path, workspaceKey, version, source = "remote") => {
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
      return {
        activeWorkspaceKey: nextKey,
        viewGeneration: state.viewGeneration + 1,
        workspaceFiles: [],
        wsFilesLoaded: false,
        workspaceFilesSessionId: undefined,
        workspaceFilesWorkspaceId: null,
        workspaceFilesLoadedVersion: -1,
        workspaceFilesLoadedAt: 0,
        workspaceFilesError: null,
        workspaceFilesTruncated: false,
        fileGroups: [],
        fileGroupsLoaded: false,
        workbookChanges: {},
        contentVersions,
        fullViewPath: null,
        fullViewSheet: null,
        activeFilePath: null,
        activeSheet: null,
        selectionMode: false,
        pendingSelection: null,
        draftRange: null,
        compareMode: false,
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
    set((state) => ({
      previews: { ...state.previews, [preview.toolCallId]: preview },
    })),

  addRecentFile: (file, explicitWorkspaceKey) =>
    set((state) => {
      const workspaceKey = explicitWorkspaceKey ?? workspaceKeyFromSession(activeSession());
      if (!isScopedWorkspaceKey(workspaceKey) || !toPublicFileIdentity(file.path)) return {};
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
      return { recentFiles: updated, dismissedPaths: newDismissed };
    }),

  addRecentFileIfNotDismissed: (file, explicitWorkspaceKey) =>
    set((state) => {
      if (state.dismissedPaths.has(file.path)) return {};
      const workspaceKey = explicitWorkspaceKey ?? workspaceKeyFromSession(activeSession());
      if (!isScopedWorkspaceKey(workspaceKey) || !toPublicFileIdentity(file.path)) return {};
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

  removeRecentFile: (path) =>
    set((state) => {
      const newDismissed = new Set(state.dismissedPaths);
      newDismissed.add(path);
      return {
        recentFiles: state.recentFiles.filter((f) => f.path !== path),
        dismissedPaths: newDismissed,
      };
    }),

  removeRecentFiles: (paths) =>
    set((state) => {
      const pathSet = new Set(paths);
      const newDismissed = new Set(state.dismissedPaths);
      for (const p of paths) newDismissed.add(p);
      return {
        recentFiles: state.recentFiles.filter((f) => !pathSet.has(f.path)),
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

  mergeRecentFiles: (files, explicitWorkspaceKey) =>
    set((state) => {
      const workspaceKey = explicitWorkspaceKey ?? workspaceKeyFromSession(activeSession());
      const map = new Map<string, ExcelFileRef>();
      for (const f of sanitizeRecentFiles(state.recentFiles)) {
        map.set(`${f.workspaceKey}|${normalizeExcelPath(f.path)}`, f);
      }
      if (isScopedWorkspaceKey(workspaceKey)) {
        for (const f of files) {
          if (!toPublicFileIdentity(f.path)) continue;
          const key = `${workspaceKey}|${normalizeExcelPath(f.path)}`;
          if (!map.has(key) && !state.dismissedPaths.has(f.path)) {
            map.set(key, {
              path: f.path,
              filename: displayFileName(f.path) || f.filename,
              lastUsedAt: f.modifiedAt ?? 0,
              workspaceKey,
            });
          }
        }
      }
      const merged = Array.from(map.values())
        .sort((a, b) => b.lastUsedAt - a.lastUsedAt)
        .slice(0, MAX_RECENT_FILES);
      return { recentFiles: merged };
    }),

  openFullView: (path, sheet, layout = "embedded") => {
    const session = activeSession();
    if (session) useWorkbookConversationStore.getState().bind(session.id, fileRefFromSession(path, session), sheet, layout);
    set({
      panelOpen: false,
      panelTab: "sheet",
      fullViewPath: path,
      fullViewSheet: sheet ?? null,
      fullViewLayout: layout,
      activeFilePath: path,
      activeSheet: sheet ?? null,
      activeWorkspaceKey: workspaceKeyFromSession(session),
    });
  },

  closeFullView: () => {
    const sessionId = activeSessionId();
    if (sessionId) useWorkbookConversationStore.getState().setShowSheet(sessionId, false);
    set({
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
    const wid = options?.workspaceId ?? null;
    const state = get();
    const version = state.workspaceFilesVersion;
    if (options?.cached && state.wsFilesLoaded && !state.workspaceFilesError && state.workspaceFilesSessionId === sid
      && state.workspaceFilesWorkspaceId === wid
      && state.workspaceFilesLoadedVersion === version && Date.now() - state.workspaceFilesLoadedAt < 30_000) {
      return Promise.resolve();
    }
    if (workspaceFilesRequest?.sessionId === sid && workspaceFilesRequest.workspaceId === wid
      && workspaceFilesRequest.version === version) {
      return workspaceFilesRequest.promise;
    }
    if (state.workspaceFilesError) set({ workspaceFilesError: null });
    if (state.workspaceFilesSessionId !== sid) {
      set({ workspaceFiles: [], wsFilesLoaded: false, workspaceFilesSessionId: sid, workspaceFilesWorkspaceId: wid,
        fileGroups: [], fileGroupsLoaded: false, workspaceFilesTruncated: false });
    }
    const request = { sessionId: sid, workspaceId: wid, version, promise: Promise.resolve() };
    workspaceFilesRequest = request;
    request.promise = (async () => {
      try {
        const { files, truncated } = await fetchWorkspaceFiles(sid, options?.workspaceId);
        // An older scan must not replace a newer scan or another session's files.
        if (workspaceFilesRequest !== request || activeSessionId() !== sid) return;
        const next = files.map((f) => ({ path: f.path, filename: f.filename, is_dir: f.is_dir }));
        const previous = get().workspaceFiles;
        const unchanged = previous.length === next.length && previous.every((file, index) =>
          file.path === next[index].path && file.filename === next[index].filename && file.is_dir === next[index].is_dir);
        set({ workspaceFiles: unchanged ? previous : next, wsFilesLoaded: true, workspaceFilesSessionId: sid,
          workspaceFilesWorkspaceId: wid,
          workspaceFilesLoadedVersion: version, workspaceFilesLoadedAt: Date.now(), workspaceFilesTruncated: truncated });
        // Reuse the same scan for recent workbooks instead of walking the workspace twice.
        get().mergeRecentFiles(files.filter((file) => !file.is_dir && isSpreadsheetFile(file.filename)).map((file) => ({
          path: file.path, filename: file.filename, modifiedAt: (file.modified_at || 0) * 1000,
        })), workspaceKeyForSessionId(sid));
      } catch (error) {
        // Keep the previous snapshot; a failed scan is not an empty workspace.
        if (workspaceFilesRequest === request && activeSessionId() === sid) {
          set({ workspaceFilesError: error instanceof Error ? error.message : "文件列表加载失败" });
        }
      } finally {
        if (workspaceFilesRequest === request) workspaceFilesRequest = null;
      }
    })();
    return request.promise;
  },

  fetchOperationHistory: async (sessionId) => {
    set({ operationsLoading: true });
    try {
      const data = await fetchOperations(sessionId, { limit: 100 });
      set({
        operations: data.operations,
        operationsLoaded: true,
        operationsLoading: false,
      });
    } catch {
      set({ operationsLoading: false });
    }
  },

  undoOperationById: async (sessionId, approvalId) => {
    const snapshot = (() => {
      const state = get();
      return {
        operations: state.operations,
        refreshCounter: state.refreshCounter,
      };
    })();

    set((state) => ({
      operations: state.operations.map((op) =>
        op.approval_id === approvalId
          ? { ...op, undoable: false }
          : op
      ),
    }));

    try {
      const result = await apiUndoOperation(sessionId, approvalId);
      if (result.status === "ok") {
        const source = useSessionStore.getState().sessions.find((s) => s.id === sessionId);
        const workspaceKey = workspaceKeyFromSession(source);
        for (const change of snapshot.operations.find((op) => op.approval_id === approvalId)?.changes || []) {
          get().notifyWorkbookChanged(change.path, workspaceKey, undefined, "refresh");
        }
        get().bumpWorkspaceFilesVersion();
        return true;
      }
      set({
        operations: snapshot.operations,
        refreshCounter: snapshot.refreshCounter,
      });
      return false;
    } catch {
      set({
        operations: snapshot.operations,
        refreshCounter: snapshot.refreshCounter,
      });
      return false;
    }
  },

  appendOperation: (op) =>
    set((state) => {
      const exists = state.operations.some((o) => o.approval_id === op.approval_id);
      if (exists) return state;
      return { operations: [op, ...state.operations] };
    }),

  loadFileGroups: async () => {
    try {
      const data = await fetchFileGroups(activeSessionId());
      set({ fileGroups: data.groups, fileGroupsLoaded: true });
    } catch {
      set({ fileGroupsLoaded: true });
    }
  },

  createGroupFromSelected: async (name, fileIds) => {
    try {
      const group = await apiCreateFileGroup({
        name,
        file_ids: fileIds.map((id) => ({ id })),
        sessionId: activeSessionId(),
      });
      set((state) => ({
        fileGroups: [...state.fileGroups, group],
      }));
      return group.id;
    } catch {
      return null;
    }
  },

  deleteGroup: async (groupId) => {
    const snapshot = get().fileGroups;
    set((state) => ({
      fileGroups: state.fileGroups.filter((g) => g.id !== groupId),
      activeGroupId: state.activeGroupId === groupId ? null : state.activeGroupId,
    }));
    try {
      await apiDeleteFileGroup(groupId, activeSessionId());
    } catch {
      set({ fileGroups: snapshot });
    }
  },

  setActiveGroup: (groupId) => set({ activeGroupId: groupId }),

  toggleGroupViewMode: () =>
    set((state) => ({ groupViewMode: !state.groupViewMode })),

  openCompare: (fileA, fileB, relationship) =>
    set({
      compareMode: true,
      compareFileA: fileA,
      compareFileB: fileB,
      compareSheetA: null,
      compareSheetB: null,
      compareRelationship: relationship ?? null,
      fullViewPath: null,
      fullViewSheet: null,
      panelOpen: false,
    }),

  closeCompare: () =>
    set({
      compareMode: false,
      compareFileA: null,
      compareFileB: null,
      compareSheetA: null,
      compareSheetB: null,
      compareRelationship: null,
    }),

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
      pendingFileMentions: null,
      pendingTemplateMessage: null,
      operations: [],
      operationsLoading: false,
      operationsLoaded: false,
      panelTab: "sheet",
      historySubview: "revisions",
      fileGroups: [],
      fileGroupsLoaded: false,
      activeGroupId: null,
      compareMode: false,
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
        groupViewMode: state.groupViewMode,
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
