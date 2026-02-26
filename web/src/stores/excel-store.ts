import { create } from "zustand";
import { persist } from "zustand/middleware";
import {
  fetchBackupList,
  applyBackup,
  discardBackup,
  normalizeExcelPath,
  type BackupFile,
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
  bd?: Record<string, { s: number; cl?: { rgb: string } }>; // borders
  n?: { pattern: string };   // number format
}

export interface ExcelCellDiff {
  cell: string;
  old: string | number | boolean | null;
  new: string | number | boolean | null;
  oldStyle?: CellStyle | null;
  newStyle?: CellStyle | null;
}

export interface ExcelDiffEntry {
  toolCallId: string;
  filePath: string;
  sheet: string;
  affectedRange: string;
  changes: ExcelCellDiff[];
  timestamp: number;
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

export interface ExcelFileRef {
  path: string;
  filename: string;
  lastUsedAt: number;
}

const MAX_RECENT_FILES = 50;
const MAX_PERSISTED_DIFFS = 500;

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

  // 用户明确关闭的路径，在重挂载间保留，以便自动发现（工作区扫描/会话恢复）不会再次加入。
  dismissedPaths: Set<string>;

  // 工作区文件树刷新信号（在 files_changed 事件时递增）
  workspaceFilesVersion: number;

  // 流式工具调用参数累积（用于实时预览文本写入内容）
  streamingToolContent: Record<string, string>;

  // 备份应用
  pendingBackups: BackupFile[];
  backupEnabled: boolean;
  backupLoading: boolean;
  appliedPaths: Set<string>;

  // 操作
  openPanel: (filePath: string, sheet?: string) => void;
  closePanel: () => void;
  setActiveSheet: (sheet: string) => void;
  addDiff: (diff: ExcelDiffEntry) => void;
  addTextDiff: (diff: TextDiffEntry) => void;
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
  fetchBackups: (sessionId: string) => Promise<void>;
  applyFile: (sessionId: string, filePath: string) => Promise<boolean>;
  applyAll: (sessionId: string) => Promise<number>;
  discardFile: (sessionId: string, filePath: string) => Promise<void>;
  discardAll: (sessionId: string) => Promise<void>;
  isFileApplied: (filePath: string) => boolean;
  bumpWorkspaceFilesVersion: () => void;
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
  refreshCounter: 0,
  recentFiles: [],
  fullViewPath: null,
  fullViewSheet: null,
  selectionMode: false,
  pendingSelection: null,
  pendingFileMention: null,
  dismissedPaths: new Set<string>(),
  workspaceFilesVersion: 0,
  streamingToolContent: {},
  pendingBackups: [],
  backupEnabled: false,
  backupLoading: false,
  appliedPaths: new Set<string>(),

  openPanel: (filePath, sheet) =>
    set({
      panelOpen: true,
      activeFilePath: filePath,
      activeSheet: sheet ?? null,
    }),

  closePanel: () => set({ panelOpen: false }),

  setActiveSheet: (sheet) => set({ activeSheet: sheet }),

  addDiff: (diff) =>
    set((state) => ({
      diffs: [...state.diffs, diff],
      // 如果面板打开且是同一文件 → 触发刷新
      refreshCounter:
        state.panelOpen && state.activeFilePath === diff.filePath
          ? state.refreshCounter + 1
          : state.refreshCounter,
    })),

  addTextDiff: (diff) =>
    set((state) => ({
      textDiffs: [...state.textDiffs, diff],
    })),

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

  fetchBackups: async (sessionId) => {
    set({ backupLoading: true });
    try {
      const data = await fetchBackupList(sessionId);
      set({
        pendingBackups: data.files,
        backupEnabled: data.backup_enabled,
        backupLoading: false,
      });
    } catch {
      set({ backupLoading: false });
    }
  },

  applyFile: async (sessionId, filePath) => {
    try {
      const result = await applyBackup({ sessionId, files: [filePath] });
      if (result.count > 0) {
        const normPath = normalizeExcelPath(filePath);
        set((state) => {
          const newApplied = new Set(state.appliedPaths);
          newApplied.add(normPath);
          return {
            pendingBackups: state.pendingBackups.filter(
              (b) => normalizeExcelPath(b.original_path) !== normPath
            ),
            appliedPaths: newApplied,
            refreshCounter: state.refreshCounter + 1,
          };
        });
        return true;
      }
      return false;
    } catch {
      return false;
    }
  },

  applyAll: async (sessionId) => {
    try {
      const result = await applyBackup({ sessionId });
      set((state) => {
        const newApplied = new Set(state.appliedPaths);
        for (const a of result.applied) {
          newApplied.add(a.original);
        }
        return {
          pendingBackups: [],
          appliedPaths: newApplied,
          refreshCounter: state.refreshCounter + 1,
        };
      });
      return result.count;
    } catch {
      return 0;
    }
  },

  discardFile: async (sessionId, filePath) => {
    try {
      await discardBackup({ sessionId, files: [filePath] });
      const normPath = normalizeExcelPath(filePath);
      set((state) => ({
        pendingBackups: state.pendingBackups.filter(
          (b) => normalizeExcelPath(b.original_path) !== normPath
        ),
      }));
    } catch {
      // 忽略
    }
  },

  discardAll: async (sessionId) => {
    try {
      await discardBackup({ sessionId });
      set({ pendingBackups: [] });
    } catch {
      // 忽略
    }
  },

  isFileApplied: (filePath) => {
    return get().appliedPaths.has(normalizeExcelPath(filePath));
  },

  bumpWorkspaceFilesVersion: () =>
    set((state) => ({ workspaceFilesVersion: state.workspaceFilesVersion + 1 })),

  clearSession: () =>
    set({
      diffs: [],
      textDiffs: [],
      previews: {},
      streamingToolContent: {},
      refreshCounter: 0,
      fullViewPath: null,
      fullViewSheet: null,
      selectionMode: false,
      pendingSelection: null,
      pendingBackups: [],
      backupEnabled: false,
      backupLoading: false,
      appliedPaths: new Set<string>(),
    }),
    }),
    {
      name: "excelmanus-excel-files",
      partialize: (state) => ({
        recentFiles: state.recentFiles,
        diffs: state.diffs.slice(-MAX_PERSISTED_DIFFS),
        dismissedPaths: Array.from(state.dismissedPaths),
      }),
      merge: (persisted, current) => {
        const p = persisted as Record<string, unknown> | undefined;
        const dismissed = Array.isArray(p?.dismissedPaths)
          ? new Set<string>(p.dismissedPaths as string[])
          : new Set<string>();
        return {
          ...current,
          ...(p ?? {}),
          dismissedPaths: dismissed,
          appliedPaths: new Set<string>(),
        };
      },
    }
  )
);
