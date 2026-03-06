import { create } from "zustand";

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

export interface WordDiff {
  paragraph_index: number;
  old_text: string;
  new_text: string;
}

interface WordState {
  panelOpen: boolean;
  activeDocPath: string | null;
  fullViewPath: string | null;
  docSnapshot: WordSnapshot | null;
  wordDiffs: WordDiff[];
  refreshCounter: number;
  recentFiles: string[];

  openPanel: (path: string) => void;
  closePanel: () => void;
  openFullView: (path?: string) => void;
  closeFullView: () => void;
  setSnapshot: (snapshot: WordSnapshot) => void;
  addDiff: (diff: WordDiff) => void;
  clearDiffs: () => void;
  triggerRefresh: () => void;
  addRecentFile: (path: string) => void;
  removeRecentFile: (path: string) => void;
}

export const useWordStore = create<WordState>((set, get) => ({
  panelOpen: false,
  activeDocPath: null,
  fullViewPath: null,
  docSnapshot: null,
  wordDiffs: [],
  refreshCounter: 0,
  recentFiles: [],

  openPanel: (path) =>
    set((s) => {
      const recent = [path, ...s.recentFiles.filter((f) => f !== path)].slice(0, 10);
      return { panelOpen: true, activeDocPath: path, recentFiles: recent };
    }),

  closePanel: () => set({ panelOpen: false }),

  openFullView: (path) =>
    set((s) => ({ fullViewPath: path ?? s.activeDocPath })),

  closeFullView: () => set({ fullViewPath: null }),

  setSnapshot: (snapshot) => set({ docSnapshot: snapshot }),

  addDiff: (diff) =>
    set((s) => ({ wordDiffs: [...s.wordDiffs, diff] })),

  clearDiffs: () => set({ wordDiffs: [] }),

  triggerRefresh: () =>
    set((s) => ({ refreshCounter: s.refreshCounter + 1 })),

  addRecentFile: (path) =>
    set((s) => ({
      recentFiles: [path, ...s.recentFiles.filter((f) => f !== path)].slice(0, 10),
    })),

  removeRecentFile: (path) =>
    set((s) => ({
      recentFiles: s.recentFiles.filter((f) => f !== path),
    })),
}));
