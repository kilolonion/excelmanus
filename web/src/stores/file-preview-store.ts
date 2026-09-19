/**
 * 文本/图片预览宿主状态。弹窗只挂一次，所有入口走 openWorkspaceFile。
 */
import { create } from "zustand";
import { fileNameOf } from "@/lib/file-kind";

export interface PreviewTab {
  filePath: string;
  filename: string;
}

interface FilePreviewState {
  textOpen: boolean;
  imageOpen: boolean;
  textTarget: { path: string; filename: string } | null;
  imageTarget: { path: string; filename: string } | null;
  previewTabs: PreviewTab[];

  openText: (path: string, filename?: string) => void;
  openImage: (path: string, filename?: string) => void;
  closeText: () => void;
  closeImage: () => void;
  addPreviewTab: (tab: PreviewTab) => void;
  removePreviewTab: (filePath: string) => void;
}

function tabOf(path: string, filename?: string): PreviewTab {
  return { filePath: path, filename: filename || fileNameOf(path) };
}

export const useFilePreviewStore = create<FilePreviewState>((set) => ({
  textOpen: false,
  imageOpen: false,
  textTarget: null,
  imageTarget: null,
  previewTabs: [],

  openText: (path, filename) =>
    set((state) => {
      const tab = tabOf(path, filename);
      const exists = state.previewTabs.some((item) => item.filePath === tab.filePath);
      return {
        textOpen: true,
        imageOpen: false,
        textTarget: { path: tab.filePath, filename: tab.filename },
        previewTabs: exists ? state.previewTabs : [...state.previewTabs, tab].slice(-10),
      };
    }),

  openImage: (path, filename) =>
    set({
      imageOpen: true,
      textOpen: false,
      imageTarget: { path, filename: filename || fileNameOf(path) },
    }),

  closeText: () => set({ textOpen: false }),

  closeImage: () => set({ imageOpen: false }),

  addPreviewTab: (tab) =>
    set((state) => {
      if (state.previewTabs.some((item) => item.filePath === tab.filePath)) return state;
      return { previewTabs: [...state.previewTabs, tab].slice(-10) };
    }),

  removePreviewTab: (filePath) =>
    set((state) => ({
      previewTabs: state.previewTabs.filter((item) => item.filePath !== filePath),
    })),
}));

