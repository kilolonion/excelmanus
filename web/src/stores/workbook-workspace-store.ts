import { create } from "zustand";
import { persist } from "zustand/middleware";
import { isSpreadsheetFile } from "@/lib/file-kind";
import { normalizeRelativePath } from "@/lib/workspace-file-ref";

export type WorkbookPaneCount = 1 | 2 | 3;
export interface OpenWorkbook { path: string; sheet?: string }
export interface LinkedWorkbookSelection { sequence: number; source: string; sheet: string; range: string }
export interface WorkbookWorkspace {
  /** Opening order is stable; entry zero is the primary workbook. */
  files: OpenWorkbook[];
  focused: string | null;
  slots: string[];
  paneCount: WorkbookPaneCount;
  linkSelection: boolean;
  navigation?: LinkedWorkbookSelection;
  /** Measured, currently visible panes. Never restored from browser storage. */
  visible?: string[];
}
export const EMPTY_WORKBOOK_WORKSPACE: WorkbookWorkspace = {
  files: [], focused: null, slots: [], paneCount: 3, linkSelection: false,
};

export function workbookWorkspaceKey(sessionId: string | null | undefined, workspaceKey: string) {
  return JSON.stringify([sessionId ?? "", workspaceKey]);
}

/** Primary stays visible in split mode; opening a fourth file replaces only the last reference pane. */
export function visibleWorkbookPaths(workspace: WorkbookWorkspace, capacity: WorkbookPaneCount = workspace.paneCount): string[] {
  const primary = workspace.files[0]?.path;
  if (!primary) return [];
  const count = Math.min(capacity, workspace.paneCount);
  if (count === 1) return [workspace.focused ?? primary];
  const candidates = [...new Set([primary, ...workspace.slots, ...workspace.files.map((file) => file.path)])]
    .filter((path) => workspace.files.some((file) => file.path === path));
  const visible = candidates.slice(0, count);
  if (workspace.focused && !visible.includes(workspace.focused)) visible[visible.length - 1] = workspace.focused;
  return visible;
}

function reveal(workspace: WorkbookWorkspace, path: string): WorkbookWorkspace {
  const primary = workspace.files[0]?.path;
  const slots = [...new Set([primary, ...workspace.slots].filter((p): p is string => Boolean(p)))];
  if (!slots.includes(path)) {
    if (slots.length >= 3) slots[2] = path;
    else slots.push(path);
  }
  return { ...workspace, focused: path, slots, navigation: undefined };
}

interface State {
  workspaces: Record<string, WorkbookWorkspace>;
  open: (key: string, path: string, sheet?: string) => WorkbookWorkspace;
  focus: (key: string, path: string) => void;
  promote: (key: string, path: string) => void;
  close: (key: string, path: string) => void;
  observeSheet: (key: string, path: string, sheet: string) => void;
  setPaneCount: (key: string, count: WorkbookPaneCount) => void;
  setLinkSelection: (key: string, enabled: boolean) => void;
  navigate: (key: string, source: string, sheet: string, range: string) => void;
  setVisible: (key: string, paths: string[]) => void;
}

/** Persist identities and layout only. Readiness, selections and versions must be observed afresh. */
export const useWorkbookWorkspaceStore = create<State>()(persist((set, get) => {
  const update = (key: string, transform: (workspace: WorkbookWorkspace) => WorkbookWorkspace) => set((state) => {
    const previous = state.workspaces[key] ?? EMPTY_WORKBOOK_WORKSPACE;
    const next = transform(previous);
    return next === previous ? state : { workspaces: { ...state.workspaces, [key]: next } };
  });
  return {
    workspaces: {},
    open: (key, rawPath, sheet) => {
      const path = normalizeRelativePath(rawPath);
      if (!isSpreadsheetFile(path)) return get().workspaces[key] ?? EMPTY_WORKBOOK_WORKSPACE;
      update(key, (workspace) => reveal({ ...workspace,
        files: workspace.files.some((file) => file.path === path)
          ? workspace.files.map((file) => file.path === path && sheet !== undefined ? { path, sheet } : file)
          : [...workspace.files, { path, ...(sheet ? { sheet } : {}) }],
      }, path));
      return get().workspaces[key];
    },
    focus: (key, rawPath) => update(key, (workspace) => {
      const path = normalizeRelativePath(rawPath);
      return workspace.focused === path || !workspace.files.some((file) => file.path === path) ? workspace : reveal(workspace, path);
    }),
    promote: (key, rawPath) => update(key, (workspace) => {
      const path = normalizeRelativePath(rawPath);
      const file = workspace.files.find((entry) => entry.path === path);
      if (!file || workspace.files[0] === file) return workspace;
      return reveal({ ...workspace, files: [file, ...workspace.files.filter((entry) => entry !== file)],
        slots: [path, ...workspace.slots.filter((entry) => entry !== path)].slice(0, 3) }, path);
    }),
    close: (key, rawPath) => update(key, (workspace) => {
      const path = normalizeRelativePath(rawPath);
      const files = workspace.files.filter((file) => file.path !== path);
      const next = { ...workspace, files, slots: workspace.slots.filter((entry) => entry !== path), navigation: undefined,
        focused: workspace.focused === path ? files[0]?.path ?? null : workspace.focused };
      return files.length ? reveal(next, next.focused ?? files[0].path) : { ...EMPTY_WORKBOOK_WORKSPACE, paneCount: workspace.paneCount };
    }),
    observeSheet: (key, path, sheet) => update(key, (workspace) => {
      const file = workspace.files.find((entry) => entry.path === normalizeRelativePath(path));
      if (!file || file.sheet === sheet) return workspace;
      return { ...workspace, files: workspace.files.map((entry) => entry === file ? { ...entry, sheet } : entry) };
    }),
    setPaneCount: (key, paneCount) => update(key, (workspace) => ({ ...workspace, paneCount })),
    setLinkSelection: (key, linkSelection) => update(key, (workspace) => ({ ...workspace, linkSelection, navigation: undefined })),
    setVisible: (key, paths) => update(key, (workspace) => {
      const visible = [...new Set(paths)].filter((path) => workspace.files.some((file) => file.path === path)).slice(0, 3);
      return workspace.visible?.join("\n") === visible.join("\n") ? workspace : { ...workspace, visible };
    }),
    navigate: (key, source, sheet, range) => update(key, (workspace) => {
      if (!workspace.linkSelection || workspace.focused !== source || !/^[A-Z]+[1-9]\d*(?::[A-Z]+[1-9]\d*)?$/.test(range)) return workspace;
      if (workspace.navigation?.source === source && workspace.navigation.sheet === sheet && workspace.navigation.range === range) return workspace;
      return { ...workspace, navigation: { source, sheet, range, sequence: (workspace.navigation?.sequence ?? 0) + 1 } };
    }),
  };
}, {
  name: "excelmanus-workbook-workspaces",
  partialize: (state) => ({ workspaces: Object.fromEntries(Object.entries(state.workspaces).slice(-100)
    .map(([key, workspace]) => [key, { ...workspace, navigation: undefined, visible: undefined }])) }),
  merge: (persisted, current) => {
    const raw = (persisted as { workspaces?: Record<string, Partial<WorkbookWorkspace>> } | undefined)?.workspaces;
    const workspaces: State["workspaces"] = {};
    for (const [key, value] of Object.entries(raw ?? {}).slice(-100)) {
      if (!value || !Array.isArray(value.files)) continue;
      const paths = new Set<string>();
      const files = value.files.flatMap((file) => {
        if (!file || typeof file.path !== "string" || !isSpreadsheetFile(file.path)) return [];
        const path = normalizeRelativePath(file.path);
        if (paths.has(path) || path.startsWith("/") || path.includes(":") || path.split("/").includes("..")) return [];
        paths.add(path);
        return [{ path, ...(typeof file.sheet === "string" ? { sheet: file.sheet } : {}) }];
      });
      workspaces[key] = { files, focused: value.focused && paths.has(value.focused) ? value.focused : files[0]?.path ?? null,
        slots: Array.isArray(value.slots) ? [...new Set(value.slots.filter((path) => paths.has(path)))].slice(0, 3) : [],
        paneCount: value.paneCount === 1 || value.paneCount === 2 ? value.paneCount : 3, linkSelection: value.linkSelection === true };
    }
    return { ...current, workspaces };
  },
}));
