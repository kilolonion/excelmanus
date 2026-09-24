// @vitest-environment jsdom
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { useRef, useState, type ClipboardEvent } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  fetchFileBlob: vi.fn(),
  uploadFileFromUrl: vi.fn().mockResolvedValue({
    filename: "remote.xlsx",
    path: "uploads/ab12_remote.xlsx",
    size: 1,
  }),
}));

vi.mock("@/lib/open-workbook", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/open-workbook")>()),
  ensureWorkbookSession: vi.fn().mockResolvedValue({ id: "s1", workspaceId: "w1" }),
  importWorkspaceFile: vi.fn().mockImplementation(async (file: File) => ({
    filename: file.name,
    path: `uploads/hash_${file.name}`,
    size: file.size,
  })),
}));

import { useChatUpload } from "@/components/chat/use-chat-upload";
import { uploadFileFromUrl } from "@/lib/api";
import { importWorkspaceFile } from "@/lib/open-workbook";
import { useExcelStore } from "@/stores/excel-store";

function setup(initialText = "") {
  return renderHook(() => {
    const [text, setText] = useState(initialText);
    const [, setConfirmedTokens] = useState<Set<string>>(new Set());
    const textareaRef = useRef<HTMLTextAreaElement | null>(null);
    const tokenMapRef = useRef(new Map<string, string>());
    const upload = useChatUpload({ text, setText, textareaRef, tokenMapRef, setConfirmedTokens });
    return { text, tokenMap: tokenMapRef.current, upload };
  });
}

function pasteEvent({ files = [], items = [], text = "", uriList = "" }: { files?: File[]; items?: DataTransferItem[]; text?: string; uriList?: string }) {
  return {
    clipboardData: {
      files: files as unknown as FileList,
      items: items as unknown as DataTransferItemList,
      getData: (type: string) => type === "text/plain" ? text : type === "text/uri-list" ? uriList : "",
    },
    preventDefault: vi.fn(),
  } as unknown as ClipboardEvent & { preventDefault: ReturnType<typeof vi.fn> };
}

beforeEach(() => {
  vi.clearAllMocks();
  useExcelStore.setState({ recentFiles: [], dismissedPaths: new Set(), activeWorkspaceKey: null });
});
afterEach(cleanup);

describe("handlePaste 文件粘贴", () => {
  it("粘贴本地文件：上传并在输入框留下 @file 引用，上传成功后回填路径", async () => {
    const { result } = setup();
    const evt = pasteEvent({ files: [new File(["x"], "报告.xlsx")] });

    act(() => result.current.upload.handlePaste(evt));

    expect(evt.preventDefault).toHaveBeenCalled();
    expect(result.current.upload.files).toHaveLength(1);
    expect(result.current.upload.files[0].file.name).toBe("报告.xlsx");
    expect(result.current.text).toBe("报告.xlsx ");
    expect(result.current.tokenMap.get("报告.xlsx")).toBe("@file:报告.xlsx");

    await waitFor(() => expect(importWorkspaceFile).toHaveBeenCalled());
    await waitFor(() =>
      expect(result.current.tokenMap.get("报告.xlsx")).toBe("@file:uploads/hash_报告.xlsx")
    );
    await waitFor(() =>
      expect(result.current.upload.files[0].status).toBe("success")
    );
  });

  it("文件名含空格或引用分隔符时：仍生成可解析的占位引用", async () => {
    const { result } = setup();
    const evt = pasteEvent({ files: [new File(["x"], "报告 final[1].pdf")] });

    act(() => result.current.upload.handlePaste(evt));

    expect(result.current.text).toBe("报告_final_1_.pdf ");
    expect(result.current.tokenMap.get("报告_final_1_.pdf")).toBe("@file:报告_final_1_.pdf");
    await waitFor(() =>
      expect(result.current.tokenMap.get("报告_final_1_.pdf")).toBe("@file:uploads/hash_报告 final[1].pdf")
    );
  });

  it("粘贴截图：生成视觉附件但不插入文本引用", async () => {
    const { result } = setup();
    const evt = pasteEvent({ files: [new File(["png"], "image.png", { type: "image/png" })] });

    act(() => result.current.upload.handlePaste(evt));

    expect(evt.preventDefault).toHaveBeenCalled();
    expect(result.current.upload.files).toHaveLength(1);
    expect(result.current.text).toBe("");

    await waitFor(() => expect(importWorkspaceFile).toHaveBeenCalled());
  });

  it("剪贴板仅通过 items 暴露图片时：仍按视觉附件处理", async () => {
    const { result } = setup();
    const image = new File(["png"], "clipboard", { type: "image/png" });
    const item = { kind: "file", getAsFile: () => image } as unknown as DataTransferItem;
    const evt = pasteEvent({ items: [item] });

    act(() => result.current.upload.handlePaste(evt));

    expect(evt.preventDefault).toHaveBeenCalled();
    expect(result.current.text).toBe("");
    expect(result.current.upload.files[0].file).toBe(image);
    await waitFor(() => expect(importWorkspaceFile).toHaveBeenCalled());
  });

  it("文件复制附带 Finder 路径元数据时：不把路径文字粘进输入框", async () => {
    const { result } = setup();
    const file = new File(["pdf"], "报告.pdf", { type: "application/pdf" });
    const evt = pasteEvent({
      files: [file],
      text: "/Users/test/报告.pdf",
      uriList: "file:///Users/test/%E6%8A%A5%E5%91%8A.pdf",
    });

    act(() => result.current.upload.handlePaste(evt));

    expect(evt.preventDefault).toHaveBeenCalled();
    expect(result.current.text).toBe("报告.pdf ");
    await waitFor(() => expect(importWorkspaceFile).toHaveBeenCalled());
  });

  it("文件与文本并存（如 Excel 复制单元格）：不拦截默认粘贴，延迟追加文件引用", async () => {
    vi.useFakeTimers();
    try {
      const { result } = setup("已输入");
      const evt = pasteEvent({
        files: [new File(["x"], "数据.xlsx")],
        text: "1\t2\n3\t4",
      });

      act(() => result.current.upload.handlePaste(evt));
      expect(evt.preventDefault).not.toHaveBeenCalled();
      expect(result.current.upload.files).toHaveLength(0);

      await act(async () => {
        vi.runAllTimers();
        await Promise.resolve();
      });
      expect(result.current.upload.files).toHaveLength(1);
      expect(result.current.text).toBe("已输入 数据.xlsx ");
    } finally {
      vi.useRealTimers();
    }
  });

  it("粘贴文本 URL：维持原有 URL 上传行为", async () => {
    const { result } = setup();
    const evt = pasteEvent({ text: "https://example.com/数据.xlsx" });

    act(() => result.current.upload.handlePaste(evt));

    expect(evt.preventDefault).toHaveBeenCalled();
    await waitFor(() =>
      expect(uploadFileFromUrl).toHaveBeenCalledWith("https://example.com/数据.xlsx", "s1", "w1")
    );
  });

  it("粘贴普通文本：不触发任何上传", () => {
    const { result } = setup();
    const evt = pasteEvent({ text: "随便一句话" });

    act(() => result.current.upload.handlePaste(evt));

    expect(evt.preventDefault).not.toHaveBeenCalled();
    expect(result.current.upload.files).toHaveLength(0);
    expect(importWorkspaceFile).not.toHaveBeenCalled();
    expect(uploadFileFromUrl).not.toHaveBeenCalled();
  });
});
