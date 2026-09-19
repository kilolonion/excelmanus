import { describe, expect, it } from "vitest";
import {
  classifyWorkspaceFile,
  isCodeFile,
  isExcelFile,
  isSpreadsheetFile,
  isTextPreviewableFile,
  isVisionImageFile,
  isVisionImageMime,
  isVisionImageUpload,
  isWorkspaceFileHref,
  workspaceFileOpenHint,
} from "@/lib/file-kind";

describe("classifyWorkspaceFile", () => {
  it("treats csv as spreadsheet to match Univer and inspect tools", () => {
    expect(classifyWorkspaceFile("sales.csv")).toBe("spreadsheet");
    expect(isSpreadsheetFile("广告.csv")).toBe(true);
    expect(isExcelFile("./uploads/a.csv")).toBe(true);
    expect(isTextPreviewableFile("sales.csv")).toBe(false);
    expect(isCodeFile("sales.csv")).toBe(false);
  });

  it("keeps tsv as text because Univer excel stream does not accept tsv", () => {
    expect(classifyWorkspaceFile("data.tsv")).toBe("text");
    expect(isSpreadsheetFile("data.tsv")).toBe(false);
    expect(isCodeFile("data.tsv")).toBe(true);
  });

  it("classifies workbook, word, image, text, and binary", () => {
    expect(classifyWorkspaceFile("book.xlsx")).toBe("spreadsheet");
    expect(classifyWorkspaceFile("book.xlsm")).toBe("spreadsheet");
    expect(classifyWorkspaceFile("book.xlsb")).toBe("spreadsheet");
    expect(classifyWorkspaceFile("report.docx")).toBe("word");
    expect(classifyWorkspaceFile("shot.png")).toBe("image");
    expect(classifyWorkspaceFile("logo.svg")).toBe("image");
    expect(classifyWorkspaceFile("notes.py")).toBe("text");
    expect(classifyWorkspaceFile(".env")).toBe("text");
    expect(classifyWorkspaceFile(".env.local")).toBe("text");
    expect(classifyWorkspaceFile(".gitignore")).toBe("text");
    expect(classifyWorkspaceFile("out.pdf")).toBe("binary");
    expect(classifyWorkspaceFile("pack.zip")).toBe("binary");
  });

  it("opens unknown utf-8 names as text and known binaries as download", () => {
    expect(classifyWorkspaceFile("Makefile")).toBe("text");
    expect(classifyWorkspaceFile("notes.xyz")).toBe("text");
    expect(classifyWorkspaceFile("app.exe")).toBe("binary");
    expect(classifyWorkspaceFile("logo.ico")).toBe("binary");
  });

  it("does not send svg to the vision attachment path", () => {
    expect(isVisionImageFile("a.png")).toBe(true);
    expect(isVisionImageFile("a.svg")).toBe(false);
    expect(isVisionImageMime("image/svg+xml")).toBe(false);
    expect(isVisionImageUpload({ name: "shot.png" })).toBe(true);
    expect(isVisionImageUpload({ name: "logo.svg", type: "image/svg+xml" })).toBe(false);
    expect(isVisionImageUpload({ name: "blob", type: "image/png" })).toBe(true);
  });

  it("writes open hints from kind, including word", () => {
    expect(workspaceFileOpenHint("a.xlsx")).toContain("侧边面板");
    expect(workspaceFileOpenHint("a.docx")).toContain("文档面板");
    expect(workspaceFileOpenHint("a.py")).toContain("预览");
    expect(workspaceFileOpenHint("a.pdf")).toContain("下载");
  });

  it("detects workspace hrefs for chat markdown and mentions", () => {
    expect(isWorkspaceFileHref("./区域汇总.xlsx")).toBe(true);
    expect(isWorkspaceFileHref("notes.py")).toBe(true);
    expect(isWorkspaceFileHref("shot.png")).toBe(true);
    expect(isWorkspaceFileHref("https://example.com/a.py")).toBe(false);
    expect(isWorkspaceFileHref("out.pdf")).toBe(true);
    expect(isWorkspaceFileHref("Makefile")).toBe(true);
    expect(isWorkspaceFileHref("hello")).toBe(false);
  });
});
