import { create } from "zustand";
import { persist } from "zustand/middleware";
import {
  fetchBackupList,
  applyBackup,
  discardBackup,
  type BackupFile,
} from "@/lib/api";

export interface ExcelCellDiff {
  cell: string;
  old: string | number | boolean | null;
  new: string | number | boolean | null;
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

  // Backup apply
  pendingBackups: BackupFile[];
  backupEnabled: boolean;
  backupLoading: boolean;
  appliedPaths: Set<string>;

  // Actions
  openPanel: (filePath: string, sheet?: string) => void;
  closePanel: () => void;
  setActiveSheet: (sheet: string) => void;
  addDiff: (diff: ExcelDiffEntry) => void;
  addPreview: (preview: ExcelPreviewData) => void;
  addRecentFile: (file: { path: string; filename: string }) => void;
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
  fetchBackups: (sessionId: string) => Promise<void>;
  applyFile: (sessionId: string, filePath: string) => Promise<boolean>;
  applyAll: (sessionId: string) => Promise<number>;
  discardFile: (sessionId: string, filePath: string) => Promise<void>;
  discardAll: (sessionId: string) => Promise<void>;
  isFileApplied: (filePath: string) => boolean;
  clearSession: () => void;
}

export const useExcelStore = create<ExcelState>()(
  persist(
    (set, get) => ({
  panelOpen: false,
  activeFilePath: null,
  activeSheet: null,
  diffs: [],
  previews: {},
  refreshCounter: 0,
  recentFiles: [],
  fullViewPath: null,
  fullViewSheet: null,
  selectionMode: false,
  pendingSelection: null,
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

  addPreview: (preview) =>
    set((state) => ({
      previews: { ...state.previews, [preview.toolCallId]: preview },
    })),

  addRecentFile: (file) =>
    set((state) => {
      const filtered = state.recentFiles.filter((f) => f.path !== file.path);
      const entry: ExcelFileRef = {
        path: file.path,
        filename: file.filename,
        lastUsedAt: Date.now(),
      };
      const updated = [entry, ...filtered].slice(0, MAX_RECENT_FILES);
      return { recentFiles: updated };
    }),

  removeRecentFile: (path) =>
    set((state) => ({
      recentFiles: state.recentFiles.filter((f) => f.path !== path),
    })),

  removeRecentFiles: (paths) =>
    set((state) => {
      const pathSet = new Set(paths);
      return { recentFiles: state.recentFiles.filter((f) => !pathSet.has(f.path)) };
    }),

  clearAllRecentFiles: () => set({ recentFiles: [] }),

  mergeRecentFiles: (files) =>
    set((state) => {
      const map = new Map<string, ExcelFileRef>();
      // existing local entries first (higher priority for lastUsedAt)
      for (const f of state.recentFiles) {
        map.set(f.path, f);
      }
      // merge backend files — only add if not already present
      for (const f of files) {
        if (!map.has(f.path)) {
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
        set((state) => {
          const newApplied = new Set(state.appliedPaths);
          newApplied.add(filePath);
          return {
            pendingBackups: state.pendingBackups.filter(
              (b) => b.original_path !== filePath
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
      set((state) => ({
        pendingBackups: state.pendingBackups.filter(
          (b) => b.original_path !== filePath
        ),
      }));
    } catch {
      // ignore
    }
  },

  discardAll: async (sessionId) => {
    try {
      await discardBackup({ sessionId });
      set({ pendingBackups: [] });
    } catch {
      // ignore
    }
  },

  isFileApplied: (filePath) => {
    return get().appliedPaths.has(filePath);
  },

  clearSession: () =>
    set({
      diffs: [],
      previews: {},
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
      }),
    }
  )
);
