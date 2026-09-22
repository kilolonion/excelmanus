import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { prepareWorkbookRequest } from "@/lib/workbook-conversation";
import { enqueueExcelCellEdit, enqueueWorkbookCommand, resetExcelCellEditStateForTests, setPersistExcelCellEditsForTests } from "@/lib/excel-cell-edit";
import { useSessionStore } from "@/stores/session-store";
import { useExcelStore } from "@/stores/excel-store";
import { useWordStore } from "@/stores/word-store";
import { useWorkbookConversationStore as conversations } from "@/stores/workbook-conversation-store";

const file = { relative: "main.xlsx", workspaceKey: "id:ws", workspaceId: "ws" };
const preview = { ...file, relative: "preview.xlsx" };
const ready = (target = file, sheet = "Sheet1", range = "A1:B3") => conversations.getState().observe("s1", target,
  { status: "ready", sheet, range, version: "sha256:aaaa" });
const edit = (target = file) => enqueueExcelCellEdit({ path: target.relative, file: target, sessionId: "s1", sheet: "Sheet1", cell: "A1", value: 42, expectedVersion: "sha256:aaaa" });

beforeEach(() => {
  useSessionStore.setState({ activeSessionId: "s1", sessions: [{ id: "s1", workspaceId: "ws", title: "Book", messageCount: 0, inFlight: false }] });
  useWordStore.setState({ fullViewPath: null });
  useExcelStore.setState({ fullViewPath: null, panelOpen: false, compareMode: false });
  conversations.setState({ targets: {}, views: {} });
  conversations.getState().bind("s1", file, "Sheet1");
  ready();
});
afterEach(resetExcelCellEditStateForTests);

describe("one snapshot for every workbook send", () => {
  it("uses the visible preview for both message and context, retaining the primary target when closed", async () => {
    useExcelStore.getState().openPanel(preview.relative, "Sheet2"); ready(preview, "Sheet2", "D4:E8");
    const request = await prepareWorkbookRequest("分析这里", "s1");
    expect(request.text).toContain("@file:preview.xlsx");
    expect(request.sheetContext).toMatchObject({ path: "preview.xlsx", sheet: "Sheet2", range: "D4:E8" });
    expect(conversations.getState().targets.s1.file.relative).toBe("main.xlsx");
    useExcelStore.getState().closePanel();
    expect((await prepareWorkbookRequest("继续", "s1")).sheetContext?.path).toBe("main.xlsx");
  });

  it("waits for saves from a side-panel-only workbook", async () => {
    conversations.setState({ targets: {} });
    useExcelStore.getState().openPanel(preview.relative, "Sheet2"); ready(preview, "Sheet2");
    const persist = vi.fn(async () => ({ kind: "ok" as const, contentVersion: "sha256:bbbb" }));
    setPersistExcelCellEditsForTests(persist); edit(preview);
    const request = await prepareWorkbookRequest("分析刚改的数字", "s1");
    expect(persist).toHaveBeenCalledTimes(1);
    expect(request.text).toContain("@sha256:bbbb");
    expect(request.sheetContext?.observed_version).toBe("sha256:bbbb");
  });

  it("flushes all explicit workbooks and advances only their acknowledged versions", async () => {
    const persist = vi.fn(async () => ({ kind: "ok" as const, contentVersion: "sha256:bbbb" }));
    setPersistExcelCellEditsForTests(persist); edit(file); edit(preview);
    const request = await prepareWorkbookRequest("比较 @file:main.xlsx[Sheet1!A1]@sha256:aaaa 与 @file:preview.xlsx@sha256:aaaa", "s1");
    expect(persist).toHaveBeenCalledTimes(2);
    expect(request.text).toContain("@file:main.xlsx[Sheet1!A1]@sha256:bbbb");
    expect(request.text).toContain("@file:preview.xlsx@sha256:bbbb");
    expect(request.sheetContext).toBeUndefined();
  });

  it("does not block an explicit reference because the unrelated primary workbook is loading", async () => {
    conversations.getState().observe("s1", file, { status: "loading" });
    expect((await prepareWorkbookRequest("分析 @file:preview.xlsx", "s1")).text).toBe("分析 @file:preview.xlsx");
  });

  it("retains sheet and selection captured before a save", async () => {
    let release!: () => void;
    setPersistExcelCellEditsForTests(async () => { await new Promise<void>((r) => { release = r; }); return { kind: "ok", contentVersion: "sha256:bbbb" }; });
    edit(); const sending = prepareWorkbookRequest("分析这里", "s1");
    await vi.waitFor(() => expect(release).toBeDefined());
    ready(file, "Other", "Z9"); release();
    const request = await sending;
    expect(request.sheetContext).toMatchObject({ sheet: "Sheet1", range: "A1:B3", observed_version: "sha256:bbbb" });
  });

  it.each(["session", "file"])("rejects when the %s changes during a save", async (change) => {
    let release!: () => void;
    setPersistExcelCellEditsForTests(async () => { await new Promise<void>((r) => { release = r; }); return { kind: "ok", contentVersion: "sha256:bbbb" }; });
    edit(); const sending = prepareWorkbookRequest("分析这里", "s1");
    const rejected = expect(sending).rejects.toThrow(/已切换对话|当前表格已改变/);
    await vi.waitFor(() => expect(release).toBeDefined());
    if (change === "session") useSessionStore.setState({ activeSessionId: "s2" });
    else useExcelStore.getState().openPanel(preview.relative);
    release(); await rejected;
  });

  it("rejects a failed save rather than sending unsaved data", async () => {
    setPersistExcelCellEditsForTests(async () => ({ kind: "conflict", code: "VERSION_CONFLICT" })); edit();
    await expect(prepareWorkbookRequest("分析这里", "s1")).rejects.toThrow("未保存");
  });

  it("requires a fresh selection after structural edits instead of attaching old coordinates to a new version", async () => {
    setPersistExcelCellEditsForTests(async () => ({ kind: "ok", contentVersion: "sha256:bbbb" }));
    enqueueWorkbookCommand({ path: file.relative, file, sessionId: "s1", expectedVersion: "sha256:aaaa", operations: [{ op: "insert_axis", sheet: "Sheet1", axis: "row", index: 1, count: 1 }] });
    await expect(prepareWorkbookRequest("分析 @file:main.xlsx[Sheet1!A1]@sha256:aaaa", "s1")).rejects.toThrow("结构已改变");
  });
});
