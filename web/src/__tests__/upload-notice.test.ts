import { describe, expect, it } from "vitest";
import {
  dedupeFileAttachments,
  extractFileAttachmentsFromContent,
  fileAttachmentMarker,
  formatUploadNotice,
  prepareUserMessageDisplay,
  stripImageSentPlaceholder,
} from "@/lib/upload-notice";

describe("formatUploadNotice", () => {
  it("uses the backend-facing Chinese labels with a colon", () => {
    expect(formatUploadNotice("file", "./uploads/a.csv")).toBe(
      "[已上传文件: ./uploads/a.csv]",
    );
    expect(formatUploadNotice("image", "./uploads/pic.png")).toBe(
      "[已上传图片: ./uploads/pic.png]",
    );
  });
});

describe("extractFileAttachmentsFromContent", () => {
  it("strips file notices and keeps the user text", () => {
    const raw = "[已上传文件: ./uploads/广告与销售数据.csv]\n\n读取数据并做回归分析";
    const { content, files } = extractFileAttachmentsFromContent(raw);
    expect(content).toBe("读取数据并做回归分析");
    expect(files).toEqual([
      {
        filename: "广告与销售数据.csv",
        path: "./uploads/广告与销售数据.csv",
        size: 0,
      },
    ]);
  });

  it("recovers historically garbled notices that still contain uploads/", () => {
    const raw =
      "[宸蹭筑浇狗枸浠? ./uploads/ec755659_广告与销售数据.csv]\n\n读取数据 @广告与销售数据.csv";
    const { content, files } = extractFileAttachmentsFromContent(raw);
    expect(content).toBe("读取数据 @广告与销售数据.csv");
    // path 保留磁盘规范名；filename 是剥掉 {8hex}_ 前缀的展示名。
    expect(files[0]?.filename).toBe("广告与销售数据.csv");
    expect(files[0]?.path).toBe("./uploads/ec755659_广告与销售数据.csv");
  });

  it("does not treat Excel range specs as upload notices", () => {
    const raw = "请看 @file:sales.xlsx[Sheet1!A1:C10]";
    const { content, files } = extractFileAttachmentsFromContent(raw);
    expect(content).toBe(raw);
    expect(files).toEqual([]);
  });
});

describe("stripImageSentPlaceholder", () => {
  it("removes the downgraded image placeholder suffix", () => {
    expect(
      stripImageSentPlaceholder("分析这张图\n[图片 #1 已在之前的对话中发送]"),
    ).toBe("分析这张图");
  });
});

describe("prepareUserMessageDisplay", () => {
  it("removes transport notices and generated workbook context from the bubble", () => {
    const result = prepareUserMessageDisplay(
      "[已上传文件: ./uploads/abcd1234_收款收据.xlsx]\n\n@file:uploads/收款收据.xlsx\n当前工作表：\"收款收据\"\n\n帮我把表格拉宽一点",
    );
    expect(result.content).toBe("帮我把表格拉宽一点");
    expect(result.files).toEqual([{ filename: "收款收据.xlsx", path: "./uploads/abcd1234_收款收据.xlsx", size: 0 }]);
    expect(prepareUserMessageDisplay("收款收据.xlsx\n当前工作表：\"收款收据\"\n\n帮我把表格拉宽一点").content)
      .toBe("帮我把表格拉宽一点");
  });

  it("dedupes attachment records by durable path instead of array position", () => {
    const files = [
      { filename: "sales.xlsx", path: "./uploads/abcd1234_sales.xlsx", size: 0 },
      { filename: "sales.xlsx", path: "uploads/abcd1234_sales.xlsx", size: 42 },
    ];
    expect(dedupeFileAttachments(files)).toEqual([files[1]]);
    expect(fileAttachmentMarker(files[0])).toBe(fileAttachmentMarker(files[1]));
  });
});
