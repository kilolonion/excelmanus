import { create } from "zustand";
import { persist } from "zustand/middleware";
import {
  fetchBackupList,
  fetchWorkspaceFiles,
  applyBackup,
  discardBackup,
  undoBackup,
  normalizeExcelPath,
  fetchOperations,
  undoOperation as apiUndoOperation,
  fetchFileGroups,
  createFileGroup as apiCreateFileGroup,
  deleteFileGroup as apiDeleteFileGroup,
  type BackupFile,
  type AppliedFile,
  type ExcelFileListItem,
  type OperationRecord,
  type FileGroup,
} from "@/lib/api";

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

export interface RelationshipDiscovery {
  files_analyzed: number;
  file_pairs: { file_a: string; file_b: string; shared_columns: SharedColumn[] }[];
  summary: string;
  merge_hints?: { file_a: string; file_b: string; key_column_a: string; key_column_b: string; suggested_join: string }[];
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

function mergeAppliedFilesByOriginal(
  base: AppliedFile[],
  incoming: AppliedFile[]
): AppliedFile[] {
  const merged = new Map<string, AppliedFile>();
  for (const item of base) {
    merged.set(normalizeExcelPath(item.original), item);
  }
  for (const item of incoming) {
    merged.set(normalizeExcelPath(item.original), item);
  }
  return Array.from(merged.values());
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

  // 面板刷新计数器（每次 diff 后递增，触发 Univer 重新加载）
  refreshCounter: number;

  // 快捷栏：最近使用的 Excel 文件（LRU，最多 5 个）
  recentFiles: ExcelFileRef[];

  // 全屏表格模式
  fullViewPath: string | null;
  fullViewSheet: string | null;

  // 选区引用模式
  selectionMode: boolean;
  pendingSelection: { filePath: string; sheet: string; range: string } | null;

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

  // 工作区文件树刷新信号（在 files_changed 事件时递增）
  workspaceFilesVersion: number;

  // 工作区文件列表缓存（避免每次挂载组件都重新加载）
  workspaceFiles: { path: string; filename: string; is_dir?: boolean }[];
  wsFilesLoaded: boolean;

  // 引导演示文件（不真实存储，引导结束后自动消失）
  demoFile: { path: string; filename: string } | null;

  // 流式工具调用参数累积（用于实时预览文本写入内容）
  streamingToolContent: Record<string, string>;

  // 文本文件预览弹窗 tab 栏（最近打开的文件）
  previewTabs: { filePath: string; filename: string }[];

  // 备份应用
  pendingBackups: BackupFile[];
  backupEnabled: boolean;
  backupLoading: boolean;
  backupInFlight: boolean;
  appliedPaths: Set<string>;
  /** 最近 apply 的文件列表（支持 undo） */
  undoableApplies: AppliedFile[];

  // 操作历史时间线
  operations: OperationRecord[];
  operationsLoading: boolean;
  operationsLoaded: boolean;

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

  // 工作区文件关系缓存
  workspaceRelationships: RelationshipDiscovery | null;
  workspaceRelationshipsLoading: boolean;

  // 合并结果（最近一次）
  lastMergeResult: MergeResultInfo | null;

  // 操作
  openPanel: (filePath: string, sheet?: string) => void;
  closePanel: () => void;
  setActiveSheet: (sheet: string) => void;
  addDiff: (diff: ExcelDiffEntry) => void;
  addTextDiff: (diff: TextDiffEntry) => void;
  addTextPreview: (preview: TextPreviewEntry) => void;
  appendStreamingArgs: (toolCallId: string, delta: string) => void;
  clearStreamingArgs: (toolCallId: string) => void;
  addPreview: (preview: ExcelPreviewData) => void;
  /** Add a file explicitly opened/uploaded by the user (clears dismissal). */
  addRecentFile: (file: { path: string; filename: string }) => void;
  /** System-initiated add that respects dismissedPaths. */
  addRecentFileIfNotDismissed: (file: { path: string; filename: string }) => void;
  removeRecentFile: (path: string) => void;
  removeRecentFiles: (paths: string[]) => void;
  clearAllRecentFiles: () => void;
  mergeRecentFiles: (files: { path: string; filename: string; modifiedAt?: number }[]) => void;
  openFullView: (path: string, sheet?: string) => void;
  closeFullView: () => void;
  enterSelectionMode: () => void;
  exitSelectionMode: () => void;
  confirmSelection: (sel: { filePath: string; sheet: string; range: string }) => void;
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
  fetchBackups: (sessionId: string) => Promise<void>;
  applyFile: (sessionId: string, filePath: string) => Promise<boolean>;
  applyAll: (sessionId: string) => Promise<number>;
  discardFile: (sessionId: string, filePath: string) => Promise<void>;
  discardAll: (sessionId: string) => Promise<void>;
  isFileApplied: (filePath: string) => boolean;
  undoApply: (sessionId: string, item: AppliedFile) => Promise<boolean>;
  handleStagingUpdated: (action: string, files: { original_path: string; backup_path: string }[], pendingCount: number) => void;
  addPreviewTab: (tab: { filePath: string; filename: string }) => void;
  removePreviewTab: (filePath: string) => void;
  toggleShowSystemFiles: () => void;
  bumpWorkspaceFilesVersion: () => void;
  refreshWorkspaceFiles: () => Promise<void>;
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
  /** 加载工作区文件关系 */
  fetchWorkspaceRelationships: () => Promise<void>;
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
  recentFiles: [],
  fullViewPath: null,
  fullViewSheet: null,
  selectionMode: false,
  pendingSelection: null,
  pendingFileMention: null,
  pendingFileMentions: null,
  pendingTemplateMessage: null,
  draggingFileCount: 0,
  dismissedPaths: new Set<string>(),
  showSystemFiles: false,
  workspaceFilesVersion: 0,
  workspaceFiles: [],
  wsFilesLoaded: false,
  demoFile: null,
  streamingToolContent: {},
  previewTabs: [],
  pendingBackups: [],
  backupEnabled: false,
  backupLoading: false,
  backupInFlight: false,
  appliedPaths: new Set<string>(),
  undoableApplies: [],
  operations: [],
  operationsLoading: false,
  operationsLoaded: false,

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

  workspaceRelationships: null,
  workspaceRelationshipsLoading: false,

  lastMergeResult: null,

  openPanel: (filePath, sheet) =>
    set({
      panelOpen: true,
      activeFilePath: filePath,
      activeSheet: sheet ?? null,
    }),

  closePanel: () => set({ panelOpen: false }),

  setActiveSheet: (sheet) => set({ activeSheet: sheet }),

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
        // 如果面板打开且是同一文件 → 触发刷新
        refreshCounter:
          state.panelOpen && state.activeFilePath === diff.filePath
            ? state.refreshCounter + 1
            : state.refreshCounter,
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

  addRecentFile: (file) =>
    set((state) => {
      const normPath = normalizeExcelPath(file.path);
      const filtered = state.recentFiles.filter(
        (f) => normalizeExcelPath(f.path) !== normPath,
      );
      const entry: ExcelFileRef = {
        path: file.path,
        filename: file.filename,
        lastUsedAt: Date.now(),
      };
      const updated = [entry, ...filtered].slice(0, MAX_RECENT_FILES);
      const newDismissed = new Set(state.dismissedPaths);
      newDismissed.delete(file.path);
      newDismissed.delete(normPath);
      return { recentFiles: updated, dismissedPaths: newDismissed };
    }),

  addRecentFileIfNotDismissed: (file) =>
    set((state) => {
      if (state.dismissedPaths.has(file.path)) return {};
      const filtered = state.recentFiles.filter((f) => f.path !== file.path);
      const entry: ExcelFileRef = {
        path: file.path,
        filename: file.filename,
        lastUsedAt: Date.now(),
      };
      const updated = [entry, ...filtered].slice(0, MAX_RECENT_FILES);
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

  mergeRecentFiles: (files) =>
    set((state) => {
      const map = new Map<string, ExcelFileRef>();
      for (const f of state.recentFiles) {
        map.set(f.path, f);
      }
      for (const f of files) {
        if (!map.has(f.path) && !state.dismissedPaths.has(f.path)) {
          map.set(f.path, {
            path: f.path,
            filename: f.filename,
            lastUsedAt: f.modifiedAt ?? 0,
          });
        }
      }
      const merged = Array.from(map.values())
        .sort((a, b) => b.lastUsedAt - a.lastUsedAt)
        .slice(0, MAX_RECENT_FILES);
      return { recentFiles: merged };
    }),

  openFullView: (path, sheet) =>
    set({
      panelOpen: false,
      fullViewPath: path,
      fullViewSheet: sheet ?? null,
    }),

  closeFullView: () =>
    set({
      fullViewPath: null,
      fullViewSheet: null,
    }),

  enterSelectionMode: () => set({ selectionMode: true, pendingSelection: null }),

  exitSelectionMode: () => set({ selectionMode: false, pendingSelection: null }),

  confirmSelection: (sel) =>
    set({ selectionMode: false, pendingSelection: sel }),

  clearPendingSelection: () => set({ pendingSelection: null }),

  mentionFileToInput: (file) => set({ pendingFileMention: file }),

  clearPendingFileMention: () => set({ pendingFileMention: null }),

  mentionFilesToInput: (files) => set({ pendingFileMentions: files.length > 0 ? files : null }),

  clearPendingFileMentions: () => set({ pendingFileMentions: null }),

  setPendingTemplateMessage: (msg) => set({ pendingTemplateMessage: msg }),

  clearPendingTemplateMessage: () => set({ pendingTemplateMessage: null }),

  fetchBackups: async (sessionId) => {
    set({ backupLoading: true });
    try {
      const data = await fetchBackupList(sessionId);
      set({
        pendingBackups: data.files,
        backupEnabled: data.backup_enabled,
        backupInFlight: !!data.in_flight,
        backupLoading: false,
      });
    } catch {
      set({ backupLoading: false });
    }
  },

  applyFile: async (sessionId, filePath) => {
    const normPath = normalizeExcelPath(filePath);
    const snapshot = (() => {
      const state = get();
      return {
        pendingBackups: state.pendingBackups,
        appliedPaths: new Set(state.appliedPaths),
        undoableApplies: state.undoableApplies,
        refreshCounter: state.refreshCounter,
      };
    })();
    const pendingItem = snapshot.pendingBackups.find(
      (b) => normalizeExcelPath(b.original_path) === normPath
    );
    const optimisticApplied: AppliedFile = {
      original: pendingItem?.original_path ?? filePath,
      backup: pendingItem?.backup_path ?? filePath,
    };

    set((state) => {
      const newApplied = new Set(state.appliedPaths);
      newApplied.add(normPath);
      return {
        pendingBackups: state.pendingBackups.filter(
          (b) => normalizeExcelPath(b.original_path) !== normPath
        ),
        appliedPaths: newApplied,
        refreshCounter: state.refreshCounter + 1,
        undoableApplies: mergeAppliedFilesByOriginal(state.undoableApplies, [optimisticApplied]),
      };
    });

    try {
      const result = await applyBackup({ sessionId, files: [filePath] });
      if (result.count <= 0) {
        set({
          pendingBackups: snapshot.pendingBackups,
          appliedPaths: snapshot.appliedPaths,
          undoableApplies: snapshot.undoableApplies,
          refreshCounter: snapshot.refreshCounter,
        });
        return false;
      }

      set((state) => {
        const newApplied = new Set(state.appliedPaths);
        for (const item of result.applied) {
          newApplied.add(normalizeExcelPath(item.original));
        }
        const confirmedForPath = result.applied.filter(
          (item) => normalizeExcelPath(item.original) === normPath
        );
        return {
          appliedPaths: newApplied,
          undoableApplies: mergeAppliedFilesByOriginal(
            state.undoableApplies.filter(
              (item) => normalizeExcelPath(item.original) !== normPath
            ),
            confirmedForPath.length > 0 ? confirmedForPath : [optimisticApplied]
          ),
        };
      });
      return true;
    } catch {
      set({
        pendingBackups: snapshot.pendingBackups,
        appliedPaths: snapshot.appliedPaths,
        undoableApplies: snapshot.undoableApplies,
        refreshCounter: snapshot.refreshCounter,
      });
      return false;
    }
  },

  applyAll: async (sessionId) => {
    const snapshot = (() => {
      const state = get();
      return {
        pendingBackups: state.pendingBackups,
        appliedPaths: new Set(state.appliedPaths),
        undoableApplies: state.undoableApplies,
        refreshCounter: state.refreshCounter,
      };
    })();
    if (snapshot.pendingBackups.length === 0) return 0;

    const optimisticApplied = snapshot.pendingBackups.map<AppliedFile>((b) => ({
      original: b.original_path,
      backup: b.backup_path,
    }));
    const optimisticPathSet = new Set(
      snapshot.pendingBackups.map((b) => normalizeExcelPath(b.original_path))
    );

    set((state) => {
      const newApplied = new Set(state.appliedPaths);
      for (const path of optimisticPathSet) {
        newApplied.add(path);
      }
      return {
        pendingBackups: [],
        appliedPaths: newApplied,
        refreshCounter: state.refreshCounter + 1,
        undoableApplies: mergeAppliedFilesByOriginal(
          state.undoableApplies.filter(
            (item) => !optimisticPathSet.has(normalizeExcelPath(item.original))
          ),
          optimisticApplied
        ),
      };
    });

    try {
      const result = await applyBackup({ sessionId });
      if (result.count <= 0 && snapshot.pendingBackups.length > 0) {
        set({
          pendingBackups: snapshot.pendingBackups,
          appliedPaths: snapshot.appliedPaths,
          undoableApplies: snapshot.undoableApplies,
          refreshCounter: snapshot.refreshCounter,
        });
        return 0;
      }

      set((state) => {
        const newApplied = new Set(state.appliedPaths);
        for (const a of result.applied) {
          newApplied.add(normalizeExcelPath(a.original));
        }
        const confirmed = optimisticApplied.map((item) => {
          const match = result.applied.find(
            (applied) =>
              normalizeExcelPath(applied.original) === normalizeExcelPath(item.original)
          );
          return match ?? item;
        });
        return {
          appliedPaths: newApplied,
          undoableApplies: mergeAppliedFilesByOriginal(
            state.undoableApplies.filter(
              (item) => !optimisticPathSet.has(normalizeExcelPath(item.original))
            ),
            confirmed
          ),
        };
      });
      return result.count;
    } catch {
      set({
        pendingBackups: snapshot.pendingBackups,
        appliedPaths: snapshot.appliedPaths,
        undoableApplies: snapshot.undoableApplies,
        refreshCounter: snapshot.refreshCounter,
      });
      return 0;
    }
  },

  discardFile: async (sessionId, filePath) => {
    const snapshot = get().pendingBackups;
    const normPath = normalizeExcelPath(filePath);

    set((state) => ({
      pendingBackups: state.pendingBackups.filter(
        (b) => normalizeExcelPath(b.original_path) !== normPath
      ),
    }));

    try {
      await discardBackup({ sessionId, files: [filePath] });
    } catch {
      set({ pendingBackups: snapshot });
    }
  },

  discardAll: async (sessionId) => {
    const snapshot = get().pendingBackups;
    set({ pendingBackups: [] });
    try {
      await discardBackup({ sessionId });
    } catch {
      set({ pendingBackups: snapshot });
    }
  },

  isFileApplied: (filePath) => {
    return get().appliedPaths.has(normalizeExcelPath(filePath));
  },

  undoApply: async (sessionId, item) => {
    if (!item.undo_path) return false;
    const normOriginal = normalizeExcelPath(item.original);
    const snapshot = (() => {
      const state = get();
      return {
        appliedPaths: new Set(state.appliedPaths),
        undoableApplies: state.undoableApplies,
        refreshCounter: state.refreshCounter,
      };
    })();

    set((state) => {
      const newApplied = new Set(state.appliedPaths);
      newApplied.delete(normOriginal);
      return {
        appliedPaths: newApplied,
        undoableApplies: state.undoableApplies.filter(
          (a) => normalizeExcelPath(a.original) !== normOriginal
        ),
        refreshCounter: state.refreshCounter + 1,
      };
    });

    try {
      await undoBackup({
        sessionId,
        originalPath: item.original,
        undoPath: item.undo_path,
      });
      return true;
    } catch {
      set({
        appliedPaths: snapshot.appliedPaths,
        undoableApplies: snapshot.undoableApplies,
        refreshCounter: snapshot.refreshCounter,
      });
      return false;
    }
  },

  handleStagingUpdated: (action, files, pendingCount) => {
    if (action === "finish_hint" || action === "new") {
      // 触发备份列表刷新 — 前端收到后由 chat-actions 调用 fetchBackups
      set((state) => ({
        backupEnabled: true,
        pendingBackups: files.length > 0
          ? files.map((f) => ({
              original_path: f.original_path,
              backup_path: f.backup_path,
              exists: true,
              modified_at: Date.now() / 1000,
            }))
          : state.pendingBackups,
      }));
    } else if (action === "applied" || action === "discarded" || action === "undone") {
      // 服务端已处理完成，直接更新 pending count
      if (pendingCount === 0) {
        set({ pendingBackups: [] });
      }
    }
  },

  addPreviewTab: (tab) =>
    set((state) => {
      const exists = state.previewTabs.some((t) => t.filePath === tab.filePath);
      if (exists) return {};
      return { previewTabs: [...state.previewTabs, tab].slice(-10) };
    }),

  removePreviewTab: (filePath) =>
    set((state) => ({
      previewTabs: state.previewTabs.filter((t) => t.filePath !== filePath),
    })),

  toggleShowSystemFiles: () =>
    set((state) => ({ showSystemFiles: !state.showSystemFiles })),

  bumpWorkspaceFilesVersion: () =>
    set((state) => ({ workspaceFilesVersion: state.workspaceFilesVersion + 1 })),

  refreshWorkspaceFiles: async () => {
    try {
      const files = await fetchWorkspaceFiles();
      set({
        workspaceFiles: files.map((f) => ({ path: f.path, filename: f.filename, is_dir: f.is_dir })),
        wsFilesLoaded: true,
      });
    } catch {
      // silent
    }
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
      refreshCounter: state.refreshCounter + 1,
    }));

    try {
      const result = await apiUndoOperation(sessionId, approvalId);
      if (result.status === "ok") {
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
      const data = await fetchFileGroups();
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
      await apiDeleteFileGroup(groupId);
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

  fetchWorkspaceRelationships: async () => {
    set({ workspaceRelationshipsLoading: true });
    try {
      const { fetchFileRelationships } = await import("@/lib/api");
      const data = await fetchFileRelationships();
      set({ workspaceRelationships: data, workspaceRelationshipsLoading: false });
    } catch {
      set({ workspaceRelationshipsLoading: false });
    }
  },

  setMergeResult: (result) => set({ lastMergeResult: result }),

  injectDemoFile: () => {
    const demo = { path: "__demo__/示例销售数据.xlsx", filename: "示例销售数据.xlsx" };
    set({ demoFile: demo });
  },

  clearDemoFile: () =>
    set({ demoFile: null }),

  clearSession: () =>
    set({
      diffs: [],
      textDiffs: [],
      previews: {},
      streamingToolContent: {},
      previewTabs: [],
      refreshCounter: 0,
      fullViewPath: null,
      fullViewSheet: null,
      selectionMode: false,
      pendingSelection: null,
      pendingFileMentions: null,
      pendingTemplateMessage: null,
      pendingBackups: [],
      backupEnabled: false,
      backupLoading: false,
      backupInFlight: false,
      appliedPaths: new Set<string>(),
      undoableApplies: [],
      operations: [],
      operationsLoading: false,
      operationsLoaded: false,
      fileGroups: [],
      fileGroupsLoaded: false,
      activeGroupId: null,
      compareMode: false,
      compareFileA: null,
      compareFileB: null,
      compareSheetA: null,
      compareSheetB: null,
      compareRelationship: null,
      workspaceRelationships: null,
      workspaceRelationshipsLoading: false,
      lastMergeResult: null,
    }),
    }),
    {
      name: "excelmanus-excel-files",
      partialize: (state) => ({
        recentFiles: state.recentFiles,
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
        const { diffs: _d, textDiffs: _td, ...safeP } = (p ?? {}) as Record<string, unknown>;
        return {
          ...current,
          ...safeP,
          dismissedPaths: dismissed,
          appliedPaths: new Set<string>(),
        };
      },
    }
  )
);
