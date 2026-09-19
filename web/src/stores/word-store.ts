import { create } from "zustand";

import { isWordFile } from "@/lib/file-kind";

/** 与 excel-store 的最近文件保留上限一致。 */
const MAX_RECENT_FILES = 50;

function normalizeWordPath(path: string): string {
  return path.replace(/\\/g, "/").trim();
}

function getWordPathKey(path: string): string {
  return normalizeWordPath(path).toLowerCase();
}

export function isWordDocumentPath(path: string): boolean {
  return isWordFile(path);
}

function mergeRecentWordFiles(existing: string[], incoming: string[]): string[] {
  const seen = new Set<string>();
  const merged: string[] = [];

  for (const candidate of [...incoming, ...existing]) {
    const normalized = normalizeWordPath(candidate);
    const key = getWordPathKey(normalized);
    if (!normalized || !isWordDocumentPath(normalized) || seen.has(key)) continue;
    seen.add(key);
    merged.push(normalized);
    if (merged.length >= MAX_RECENT_FILES) break;
  }

  return merged;
}

export interface WordParagraphSnapshot {
  text: string;
  style: string;
  heading_level?: number;
  alignment?: string;
  runs?: {
    text: string;
    bold?: boolean;
    italic?: boolean;
    underline?: boolean;
    size_pt?: number;
    font_name?: string;
    color?: string;
  }[];
}

export interface WordTableSnapshot {
  index: number;
  rows: number;
  columns: number;
  data: string[][];
}

export interface WordSnapshot {
  file: string;
  content_version?: string;
  total_paragraphs: number;
  returned_paragraphs: number;
  truncated: boolean;
  paragraphs: WordParagraphSnapshot[];
  tables: WordTableSnapshot[];
  total_tables: number;
  sections: number;
  properties: {
    title?: string;
    author?: string;
  };
}

export type WordPanelTab = "doc" | "history";

interface WordState {
  panelOpen: boolean;
  panelTab: WordPanelTab;
  activeDocPath: string | null;
  activeWorkspaceKey: string | null;
  fullViewPath: string | null;
  docSnapshot: WordSnapshot | null;
  refreshCounter: number;
  recentFiles: string[];

  openPanel: (path: string) => void;
  openHistory: (path?: string) => void;
  setPanelTab: (tab: WordPanelTab) => void;
  closePanel: () => void;
  openFullView: (path?: string) => void;
  closeFullView: () => void;
  setSnapshot: (snapshot: WordSnapshot) => void;
  triggerRefresh: () => void;
  addRecentFile: (path: string) => void;
  removeRecentFile: (path: string) => void;
  handleFilesChanged: (paths: string[]) => void;
  rebindWorkspace: (nextWorkspaceKey: string | null) => void;
}

export const useWordStore = create<WordState>((set) => ({
  panelOpen: false,
  panelTab: "doc",
  activeDocPath: null,
  activeWorkspaceKey: null,
  fullViewPath: null,
  docSnapshot: null,
  refreshCounter: 0,
  recentFiles: [],

  openPanel: (path) =>
    set((s) => {
      const normalizedPath = normalizeWordPath(path);
      return {
        panelOpen: true,
        panelTab: "doc",
        activeDocPath: normalizedPath,
        recentFiles: mergeRecentWordFiles(s.recentFiles, [normalizedPath]),
      };
    }),

  openHistory: (path) =>
    set((s) => {
      const normalizedPath = normalizeWordPath(path ?? s.activeDocPath ?? "");
      if (!normalizedPath) return s;
      return {
        panelOpen: true,
        panelTab: "history",
        activeDocPath: normalizedPath,
        recentFiles: mergeRecentWordFiles(s.recentFiles, [normalizedPath]),
      };
    }),

  setPanelTab: (tab) => set({ panelTab: tab }),

  closePanel: () => set({ panelOpen: false }),

  openFullView: (path) =>
    set((s) => {
      const resolvedPath = normalizeWordPath(path ?? s.activeDocPath ?? "");
      if (!resolvedPath) return s;
      return {
        activeDocPath: resolvedPath,
        fullViewPath: resolvedPath,
        recentFiles: mergeRecentWordFiles(s.recentFiles, [resolvedPath]),
      };
    }),

  closeFullView: () => set({ fullViewPath: null }),

  setSnapshot: (snapshot) =>
    set((s) => ({
      docSnapshot: snapshot,
      recentFiles: mergeRecentWordFiles(s.recentFiles, [snapshot.file]),
    })),

  triggerRefresh: () =>
    set((s) => ({ refreshCounter: s.refreshCounter + 1 })),

  addRecentFile: (path) =>
    set((s) => ({
      recentFiles: mergeRecentWordFiles(s.recentFiles, [path]),
    })),

  removeRecentFile: (path) =>
    set((s) => ({
      recentFiles: s.recentFiles.filter((f) => getWordPathKey(f) !== getWordPathKey(path)),
    })),

  handleFilesChanged: (paths) =>
    set((s) => {
      const changedWordFiles = mergeRecentWordFiles([], paths);
      if (changedWordFiles.length === 0) return s;

      const trackedPaths = [
        s.activeDocPath,
        s.fullViewPath,
        s.docSnapshot?.file ?? null,
      ]
        .filter((path): path is string => Boolean(path))
        .map(getWordPathKey);

      const shouldRefresh = changedWordFiles.some((path) =>
        trackedPaths.includes(getWordPathKey(path)),
      );
      const snapshotFile = s.docSnapshot?.file ?? null;
      const shouldInvalidateSnapshot = Boolean(
        snapshotFile && changedWordFiles.some(
          (path) => getWordPathKey(path) === getWordPathKey(snapshotFile),
        ),
      );

      return {
        recentFiles: mergeRecentWordFiles(s.recentFiles, changedWordFiles),
        refreshCounter: shouldRefresh ? s.refreshCounter + 1 : s.refreshCounter,
        docSnapshot: shouldInvalidateSnapshot ? null : s.docSnapshot,
      };
    }),

  rebindWorkspace: (nextWorkspaceKey) =>
    set((s) => {
      if (s.activeWorkspaceKey && nextWorkspaceKey && s.activeWorkspaceKey === nextWorkspaceKey) {
        return s;
      }
      return {
        activeWorkspaceKey: nextWorkspaceKey,
        activeDocPath: null,
        fullViewPath: null,
        docSnapshot: null,
        panelOpen: false,
      };
    }),
}));
