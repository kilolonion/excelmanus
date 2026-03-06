import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";

const refreshWorkspaceFiles = vi.fn().mockResolvedValue(undefined);
const excelStoreState = {
  recentFiles: [],
  addRecentFile: vi.fn(),
  removeRecentFile: vi.fn(),
  removeRecentFiles: vi.fn(),
  mergeRecentFiles: vi.fn(),
  openPanel: vi.fn(),
  openFullView: vi.fn(),
  closePanel: vi.fn(),
  closeFullView: vi.fn(),
  closeCompare: vi.fn(),
  panelOpen: false,
  activeFilePath: null,
  pendingBackups: [],
  applyFile: vi.fn(),
  workspaceFilesVersion: 0,
  workspaceFiles: [],
  wsFilesLoaded: true,
  refreshWorkspaceFiles,
  showSystemFiles: false,
  toggleShowSystemFiles: vi.fn(),
  demoFile: null,
  groupViewMode: false,
  toggleGroupViewMode: vi.fn(),
  createGroupFromSelected: vi.fn(),
};

vi.mock("@/stores/excel-store", () => ({
  useExcelStore: Object.assign((selector: (state: typeof excelStoreState) => unknown) => selector(excelStoreState), {
    getState: () => excelStoreState,
    setState: vi.fn(),
  }),
}));

vi.mock("@/stores/session-store", () => ({
  useSessionStore: (selector: (state: { activeSessionId: string | null }) => unknown) =>
    selector({ activeSessionId: null }),
}));

vi.mock("@/stores/auth-store", () => ({
  useAuthStore: (selector: (state: { user: { id: string } | null }) => unknown) =>
    selector({ user: { id: "user-1" } }),
}));

vi.mock("@/stores/word-store", () => ({
  useWordStore: (selector: (state: Record<string, ReturnType<typeof vi.fn>>) => unknown) =>
    selector({
      openPanel: vi.fn(),
      openFullView: vi.fn(),
      closePanel: vi.fn(),
      closeFullView: vi.fn(),
    }),
}));

vi.mock("@/lib/api", () => ({
  uploadFile: vi.fn(),
  uploadFileToFolder: vi.fn(),
  fetchExcelFiles: vi.fn().mockResolvedValue([]),
  normalizeExcelPath: (path: string) => path,
  downloadFile: vi.fn(),
  workspaceMkdir: vi.fn(),
  workspaceDeleteItem: vi.fn(),
}));

vi.mock("@/lib/concurrency", () => ({
  mapWithConcurrency: vi.fn(),
}));

vi.mock("@/lib/file-preview", () => ({
  isExcelFile: vi.fn().mockReturnValue(false),
  isImageFile: vi.fn().mockReturnValue(false),
  isTextPreviewableFile: vi.fn().mockReturnValue(false),
  isWordFile: vi.fn().mockReturnValue(false),
}));

vi.mock("@/components/chat/CodePreviewModal", () => ({
  CodePreviewModal: () => null,
}));

vi.mock("@/components/chat/ImagePreviewModal", () => ({
  ImagePreviewModal: () => null,
}));

vi.mock("@/components/sidebar/file-tree-helpers", () => ({
  buildTree: vi.fn(() => ({ children: [] })),
  filterWorkspaceFiles: vi.fn((files: typeof excelStoreState.workspaceFiles) => files),
  removeWorkspaceEntries: vi.fn(),
  upsertWorkspaceEntry: vi.fn(),
}));

vi.mock("@/components/sidebar/InlineInputs", () => ({
  InlineCreateInput: () => null,
}));

vi.mock("@/components/sidebar/TreeNodeItem", () => ({
  TreeNodeItem: () => null,
}));

vi.mock("@/components/sidebar/FlatFileListView", () => ({
  FlatFileListView: () => React.createElement("div", null, "No files yet, click + upload above"),
}));

vi.mock("@/components/sidebar/FileGroupListView", () => ({
  FileGroupListView: () => null,
}));

vi.mock("@/components/sidebar/ExcelFilesDialogs", () => ({
  ExcelFilesDialog: () => null,
  RemoveConfirmDialog: () => null,
}));

vi.mock("@/components/sidebar/StorageBar", () => ({
  StorageBar: () => null,
}));

vi.mock("@/components/sidebar/FileRelationshipGraph", () => ({
  FileRelationshipGraph: () => null,
}));

import { ExcelFilesBar } from "@/components/sidebar/ExcelFilesBar";

describe("ExcelFilesBar", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    excelStoreState.workspaceFiles = [];
    excelStoreState.wsFilesLoaded = true;
    excelStoreState.groupViewMode = false;
  });

  it("renders the empty embedded state only once", () => {
    const html = renderToStaticMarkup(React.createElement(ExcelFilesBar, { embedded: true }));
    const matches = html.match(/No files yet, click \+ upload above/g) ?? [];

    expect(matches).toHaveLength(1);
  });

  it("renders a dedicated sidebar upload picker input", () => {
    const html = renderToStaticMarkup(React.createElement(ExcelFilesBar, { embedded: true }));

    expect(html).toContain('data-file-picker="sidebar-upload"');
    expect(html).toContain('class="sr-only"');
  });
});
