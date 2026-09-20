import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import {
  resolveChatWorkspaceTab,
  resolveSheetFullViewTarget,
} from "@/lib/chat-workspace-tabs";

const excelState = {
  fullViewPath: null as string | null,
  fullViewSheet: null as string | null,
  compareMode: false,
  activeFilePath: "./a.xlsx" as string | null,
  activeSheet: "Sheet1" as string | null,
  recentFiles: [] as { path: string; workspaceKey?: string }[],
  workspaceFiles: [] as { path: string; filename: string; is_dir?: boolean }[],
  activeWorkspaceKey: "id:ws" as string | null,
  closeFullView: vi.fn(),
  closeCompare: vi.fn(),
};

const wordState = {
  fullViewPath: null as string | null,
  closePanel: vi.fn(),
  closeFullView: vi.fn(),
};

vi.mock("@/stores/excel-store", () => ({
  useExcelStore: Object.assign((selector: (state: typeof excelState) => unknown) => selector(excelState), {
    getState: () => excelState,
  }),
}));

vi.mock("@/stores/word-store", () => ({
  useWordStore: Object.assign((selector: (state: typeof wordState) => unknown) => selector(wordState), {
    getState: () => wordState,
  }),
}));

vi.mock("@/stores/ui-store", () => ({
  useUIStore: (selector: (state: { sidebarOpen: boolean }) => unknown) =>
    selector({ sidebarOpen: true }),
}));

vi.mock("zustand/react/shallow", () => ({
  useShallow: (fn: unknown) => fn,
}));

vi.mock("@/lib/excel-view-prefetch", () => ({
  prefetchExcelView: vi.fn(),
}));

vi.mock("@/lib/open-workspace-file", () => ({
  openWorkspaceFile: vi.fn(),
}));

import { ChatWorkspaceTabs } from "@/components/chat/ChatWorkspaceTabs";

describe("resolveChatWorkspaceTab", () => {
  it("stays on chat when nothing occupies the chat area", () => {
    expect(resolveChatWorkspaceTab({ fullViewPath: null, compareMode: false })).toBe("chat");
  });

  it("selects sheet when the workbook is expanded into the chat area", () => {
    expect(resolveChatWorkspaceTab({ fullViewPath: "./a.xlsx", compareMode: false })).toBe("sheet");
  });

  it("selects sheet during compare mode", () => {
    expect(resolveChatWorkspaceTab({ fullViewPath: null, compareMode: true })).toBe("sheet");
  });
});

describe("resolveSheetFullViewTarget", () => {
  it("keeps the already expanded workbook", () => {
    expect(
      resolveSheetFullViewTarget({
        activeFilePath: "./side.xlsx",
        activeSheet: "侧栏",
        recentFiles: [{ path: "./recent.xlsx" }],
        fullViewPath: "./full.xlsx",
        fullViewSheet: "全屏",
      }),
    ).toEqual({ path: "./full.xlsx", sheet: "全屏" });
  });

  it("uses the side-panel workbook and sheet, matching expand-to-chat", () => {
    expect(
      resolveSheetFullViewTarget({
        activeFilePath: "./a.xlsx",
        activeSheet: "明细",
        recentFiles: [{ path: "./b.xlsx" }],
        fullViewPath: null,
        fullViewSheet: null,
      }),
    ).toEqual({ path: "./a.xlsx", sheet: "明细" });
  });

  it("falls back to the most recent workbook in the current workspace", () => {
    expect(
      resolveSheetFullViewTarget({
        activeFilePath: null,
        activeSheet: null,
        recentFiles: [{ path: "./recent.xlsx", workspaceKey: "id:ws" }],
        workspaceKey: "id:ws",
        fullViewPath: null,
        fullViewSheet: null,
      }),
    ).toEqual({ path: "./recent.xlsx", sheet: undefined });
  });

  it("ignores recent files without the current workspaceKey", () => {
    expect(
      resolveSheetFullViewTarget({
        activeFilePath: null,
        activeSheet: null,
        recentFiles: [
          { path: "./legacy.xlsx" },
          { path: "./other.xlsx", workspaceKey: "id:other" },
        ],
        workspaceKey: "id:ws",
        fullViewPath: null,
        fullViewSheet: null,
      }),
    ).toBeNull();
  });

  it("falls back to a spreadsheet in the workspace before it has been opened", () => {
    expect(
      resolveSheetFullViewTarget({
        activeFilePath: null,
        activeSheet: null,
        recentFiles: [],
        workspaceFiles: [
          { path: "./notes.md", filename: "notes.md" },
          { path: "./archive", filename: "archive", is_dir: true },
          { path: "./untouched.xlsx", filename: "untouched.xlsx" },
        ],
        workspaceKey: "id:ws",
        fullViewPath: null,
        fullViewSheet: null,
      }),
    ).toEqual({ path: "./untouched.xlsx", sheet: undefined });
  });

  it("returns null when no workbook is available", () => {
    expect(
      resolveSheetFullViewTarget({
        activeFilePath: null,
        activeSheet: null,
        recentFiles: [],
        fullViewPath: null,
        fullViewSheet: null,
      }),
    ).toBeNull();
  });
});

describe("ChatWorkspaceTabs", () => {
  beforeEach(() => {
    excelState.fullViewPath = null;
    excelState.compareMode = false;
    excelState.activeFilePath = "./a.xlsx";
    excelState.workspaceFiles = [];
    vi.clearAllMocks();
  });

  it("renders 对话 and 表格 tabs", () => {
    const html = renderToStaticMarkup(React.createElement(ChatWorkspaceTabs));
    expect(html).toContain("对话");
    expect(html).toContain("表格");
    expect(html).toContain('role="tablist"');
    expect(html).toContain('class="em-workspace-tab-glider"');
  });

  it("marks 表格 selected when the workbook occupies the chat area", () => {
    excelState.fullViewPath = "./a.xlsx";
    const html = renderToStaticMarkup(React.createElement(ChatWorkspaceTabs));
    expect(html).toMatch(/aria-selected="true"[^>]*><span[^>]*>表格/);
    expect(html).toMatch(/aria-selected="false"[^>]*><span[^>]*>对话/);
  });

  it("keeps 表格 enabled when the workspace has an unopened workbook", () => {
    excelState.activeFilePath = null;
    excelState.recentFiles = [];
    excelState.workspaceFiles = [
      { path: "./untouched.xlsx", filename: "untouched.xlsx" },
    ];
    const html = renderToStaticMarkup(React.createElement(ChatWorkspaceTabs));
    expect(html).toMatch(/aria-selected="false"[^>]*><span[^>]*>表格/);
    expect(html).not.toMatch(/disabled=""[^>]*><span[^>]*>表格/);
  });
});
