/**
 * 文本/图片预览宿主状态。弹窗只挂一次，所有入口走 openWorkspaceFile。
 */
import { create } from "zustand";
import { fileNameOf } from "@/lib/file-kind";

export interface PreviewTab {
  filePath: string;
  filename: string;
  sessionId?: string;
  workspaceId?: string;
}

export interface PreviewScope {
  sessionId?: string | null;
  workspaceId?: string | null;
}

export type PreviewTarget = PreviewTab;

interface FilePreviewState {
  textOpen: boolean;
  imageOpen: boolean;
  textTarget: { path: string; filename: string; sessionId?: string; workspaceId?: string } | null;
  imageTarget: { path: string; filename: string; sessionId?: string; workspaceId?: string } | null;
  previewTabs: PreviewTab[];

  openText: (path: string, filename?: string, scope?: PreviewScope) => void;
  openImage: (path: string, filename?: string, scope?: PreviewScope) => void;
  closeText: () => void;
  closeImage: () => void;
  clearForSessionChange: () => void;
  addPreviewTab: (tab: PreviewTab) => void;
  removePreviewTab: (tab: Pick<PreviewTab, "filePath" | "sessionId" | "workspaceId">) => void;
}

function tabOf(path: string, filename?: string, scope?: PreviewScope): PreviewTab {
  return {
    filePath: path,
    filename: filename || fileNameOf(path),
    ...(scope?.sessionId ? { sessionId: scope.sessionId } : {}),
    ...(scope?.workspaceId ? { workspaceId: scope.workspaceId } : {}),
  };
}

export function previewTabKey(tab: Pick<PreviewTab, "filePath" | "sessionId" | "workspaceId">): string {
  return `${tab.workspaceId || tab.sessionId || "_"}|${tab.filePath}`;
}

export const useFilePreviewStore = create<FilePreviewState>((set) => ({
  textOpen: false,
  imageOpen: false,
  textTarget: null,
  imageTarget: null,
  previewTabs: [],

  openText: (path, filename, scope) =>
    set((state) => {
      const tab = tabOf(path, filename, scope);
      const key = previewTabKey(tab);
      const exists = state.previewTabs.some((item) => previewTabKey(item) === key);
      return {
        textOpen: true,
        imageOpen: false,
        textTarget: {
          path: tab.filePath,
          filename: tab.filename,
          sessionId: tab.sessionId,
          workspaceId: tab.workspaceId,
        },
        previewTabs: exists ? state.previewTabs : [...state.previewTabs, tab].slice(-10),
      };
    }),

  openImage: (path, filename, scope) =>
    set({
      imageOpen: true,
      textOpen: false,
      imageTarget: {
        path,
        filename: filename || fileNameOf(path),
        ...(scope?.sessionId ? { sessionId: scope.sessionId } : {}),
        ...(scope?.workspaceId ? { workspaceId: scope.workspaceId } : {}),
      },
    }),

  closeText: () => set({ textOpen: false }),

  closeImage: () => set({ imageOpen: false }),

  clearForSessionChange: () => set({
    textOpen: false,
    imageOpen: false,
    textTarget: null,
    imageTarget: null,
    previewTabs: [],
  }),

  addPreviewTab: (tab) =>
    set((state) => {
      if (state.previewTabs.some((item) => previewTabKey(item) === previewTabKey(tab))) return state;
      return { previewTabs: [...state.previewTabs, tab].slice(-10) };
    }),

  removePreviewTab: (tab) =>
    set((state) => ({
      previewTabs: state.previewTabs.filter((item) => previewTabKey(item) !== previewTabKey(tab)),
    })),
}));
