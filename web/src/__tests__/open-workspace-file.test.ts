import { beforeEach, describe, expect, it, vi } from "vitest";

const downloadFile = vi.hoisted(() => vi.fn().mockResolvedValue(undefined));

const excel = {
  addRecentFile: vi.fn(),
  openPanel: vi.fn(),
  openFullView: vi.fn(),
  closePanel: vi.fn(),
  closeFullView: vi.fn(),
  closeCompare: vi.fn(),
};

const word = {
  openPanel: vi.fn(),
  openFullView: vi.fn(),
  closePanel: vi.fn(),
  closeFullView: vi.fn(),
};

const preview = {
  openText: vi.fn(),
  openImage: vi.fn(),
  closeText: vi.fn(),
  closeImage: vi.fn(),
};

vi.mock("@/stores/excel-store", () => ({
  useExcelStore: { getState: () => excel },
}));

vi.mock("@/stores/word-store", () => ({
  useWordStore: { getState: () => word },
}));

vi.mock("@/stores/file-preview-store", () => ({
  useFilePreviewStore: { getState: () => preview },
}));

vi.mock("@/stores/session-store", () => ({
  useSessionStore: { getState: () => ({ activeSessionId: "s1" }) },
}));

vi.mock("@/lib/api", () => ({
  downloadFile: (...args: unknown[]) => downloadFile(...args),
}));

import { openWorkspaceFile } from "@/lib/open-workspace-file";

describe("openWorkspaceFile", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("opens csv in the spreadsheet panel, not as text", () => {
    expect(openWorkspaceFile("./sales.csv")).toBe("spreadsheet");
    expect(excel.openPanel).toHaveBeenCalledWith("./sales.csv", undefined);
    expect(preview.openText).not.toHaveBeenCalled();
  });

  it("opens docx in the word panel", () => {
    expect(openWorkspaceFile("report.docx")).toBe("word");
    expect(word.openPanel).toHaveBeenCalledWith("report.docx");
    expect(excel.closePanel).toHaveBeenCalled();
  });

  it("opens python in the shared text preview host", () => {
    expect(openWorkspaceFile("tools/run.py")).toBe("text");
    expect(preview.openText).toHaveBeenCalledWith("tools/run.py", "run.py");
  });

  it("opens png in the shared image preview host", () => {
    expect(openWorkspaceFile("shot.png")).toBe("image");
    expect(preview.openImage).toHaveBeenCalledWith("shot.png", "shot.png");
  });

  it("downloads pdf", () => {
    expect(openWorkspaceFile("out.pdf")).toBe("binary");
    expect(downloadFile).toHaveBeenCalledWith("out.pdf", "out.pdf", "s1");
  });

  it("opens Makefile as text instead of downloading", () => {
    expect(openWorkspaceFile("Makefile")).toBe("text");
    expect(preview.openText).toHaveBeenCalledWith("Makefile", "Makefile");
  });

  it("downloads exe", () => {
    expect(openWorkspaceFile("tool.exe")).toBe("binary");
    expect(downloadFile).toHaveBeenCalledWith("tool.exe", "tool.exe", "s1");
  });

  it("uses full intent for spreadsheet and word", () => {
    openWorkspaceFile("book.xlsx", { intent: "full", sheet: "明细" });
    expect(excel.openFullView).toHaveBeenCalledWith("book.xlsx", "明细");
    openWorkspaceFile("a.docx", { intent: "full" });
    expect(word.openFullView).toHaveBeenCalledWith("a.docx");
  });
});
