import React from "react";
import { describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { RelatedFilesCard, isExcelFilename } from "@/components/chat/FileCapsule";

describe("RelatedFilesCard", () => {
  it("renders a grouped files-changed card", () => {
    const html = renderToStaticMarkup(
      React.createElement(RelatedFilesCard, {
        files: [
          { key: "a", filename: "收款收据.xlsx", filePath: "./收款收据.xlsx", onOpen: vi.fn(), onDownload: vi.fn() },
          { key: "b", filename: "verify_receipt.py", filePath: "./verify_receipt.py", onOpen: vi.fn(), onDownload: vi.fn() },
        ],
        onReview: vi.fn(),
      }),
    );

    expect(html).toContain("2 个相关文件");
    expect(html).toContain("查看");
    expect(html).toContain("收款收据.xlsx");
    expect(html).toContain("verify_receipt.py");
    expect(html).not.toContain("打开");
  });

  it("collapses after four files", () => {
    const files = ["a.py", "b.py", "c.py", "d.py", "e.py", "f.py"].map((filename) => ({
      key: filename,
      filename,
      onOpen: vi.fn(),
      onDownload: vi.fn(),
    }));
    const html = renderToStaticMarkup(React.createElement(RelatedFilesCard, { files }));

    expect(html).toContain("显示另外 2 个");
    expect(html).toContain("a.py");
    expect(html).not.toContain("e.py");
  });

  it("classifies spreadsheet names as excel", () => {
    expect(isExcelFilename("区域汇总.xlsx")).toBe(true);
    expect(isExcelFilename("notes.py")).toBe(false);
  });
});
