import { beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "@/lib/api";
import { useExcelStore } from "@/stores/excel-store";
import { useSessionStore } from "@/stores/session-store";
import { useWorkbookConversationStore, workbookViewKey } from "@/stores/workbook-conversation-store";
import { fileRefFromSession } from "@/lib/workspace-file-ref";
import { openWorkbookForConversation } from "@/lib/open-workbook";
import { formatWorkbookMessage, prepareWorkbookMessage } from "@/lib/workbook-conversation";
import type { Session } from "@/lib/types";
import type { WorkbookViewSnapshot } from "@/lib/workbook-view";

const edits = vi.hoisted(() => ({ flush: vi.fn(), pending: vi.fn(), paused: vi.fn() }));
vi.mock("@/lib/excel-view-prefetch", () => ({ prefetchExcelView: vi.fn() }));
vi.mock("@/lib/excel-cell-edit", () => ({
  flushWorkbookEdits: edits.flush, hasPendingWorkbookEdits: edits.pending, isWorkbookEditPaused: edits.paused,
  acknowledgedWorkbookVersion: (_file: unknown, version?: string) => version,
}));

const session: Session = { id: "s1", title: "Sales", workspaceId: "w1", messageCount: 0, inFlight: false };
const other: Session = { ...session, id: "s2", workspaceId: "w2" };
const file = fileRefFromSession("uploads/sales.xlsx", session);
const view: WorkbookViewSnapshot = {
  file, content_version: "sha256:aaaa", active_sheet: "销售明细", sheets: [{ name: "销售明细", sheet_id: "s", used: { rows: 3, cols: 2 } }],
  windows: [{ sheet: "销售明细", rect: { r0: 1, c0: 1, r1: 3, c1: 2 }, cells: {} }], coverage: { loaded: [], unloaded: [] },
};

function ready() {
  const store = useWorkbookConversationStore.getState();
  store.bind(session.id, file, "销售明细");
  store.observe(session.id, file, { status: "ready", sheet: "销售明细", version: view.content_version });
}

beforeEach(() => {
  vi.restoreAllMocks();
  useSessionStore.setState({ activeSessionId: session.id, sessions: [session, other] });
  useExcelStore.setState({ activeWorkspaceKey: "id:w1", fullViewPath: null, fullViewSheet: null, panelOpen: false, recentFiles: [], contentVersions: {} });
  useWorkbookConversationStore.setState({ targets: {}, views: {}, pickerOpen: false });
  edits.flush.mockReset().mockResolvedValue(undefined);
  edits.pending.mockReset().mockReturnValue(false);
  edits.paused.mockReset().mockReturnValue(false);
});

describe("open an existing workbook without an agent turn", () => {
  it("reads the selected upload and binds the grid and conversation without uploading again", async () => {
    const read = vi.spyOn(api, "fetchWorkbookView").mockResolvedValue(view);
    const upload = vi.spyOn(api, "uploadFile");
    await openWorkbookForConversation(file.relative, session);
    expect(read).toHaveBeenCalledWith(expect.objectContaining({ path: file.relative, sessionId: session.id, workspaceKey: "id:w1", withStyles: false }));
    expect(upload).not.toHaveBeenCalled();
    expect(useExcelStore.getState().fullViewPath).toBe(file.relative);
    expect(useExcelStore.getState().panelOpen).toBe(false);
    expect(useWorkbookConversationStore.getState().targets[session.id].file).toMatchObject(file);
  });

  it("does not open a late response after the user switches workspace", async () => {
    let resolve!: (value: WorkbookViewSnapshot) => void;
    vi.spyOn(api, "fetchWorkbookView").mockImplementation(() => new Promise((r) => { resolve = r; }));
    const pending = openWorkbookForConversation(file.relative, session);
    useSessionStore.getState().setActiveSession(other.id);
    resolve(view);
    await expect(pending).rejects.toThrow("已切换对话");
    expect(useExcelStore.getState().fullViewPath).toBeNull();
    expect(useWorkbookConversationStore.getState().targets).toEqual({});
  });

  it("does not open a cancelled file after its read finishes", async () => {
    let resolve!: (value: WorkbookViewSnapshot) => void;
    vi.spyOn(api, "fetchWorkbookView").mockImplementation(() => new Promise((r) => { resolve = r; }));
    const controller = new AbortController();
    const pending = openWorkbookForConversation(file.relative, session, { signal: controller.signal });
    controller.abort(); resolve(view);
    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
    expect(useExcelStore.getState().fullViewPath).toBeNull();
  });

  it("keeps the current file when the newly selected file cannot be read", async () => {
    useExcelStore.setState({ fullViewPath: "old.xlsx" });
    vi.spyOn(api, "fetchWorkbookView").mockRejectedValue(new Error("文件已删除"));
    await expect(openWorkbookForConversation(file.relative, session)).rejects.toThrow("文件已删除");
    expect(useExcelStore.getState().fullViewPath).toBe("old.xlsx");
  });
});

describe("workbook discussion context", () => {
  it("changes the primary file from chat without opening the sheet and uses it for the next question", async () => {
    const oldFile = { ...file, relative: "uploads/old.xlsx" };
    useWorkbookConversationStore.getState().bind(session.id, oldFile, "旧表", "split");
    useExcelStore.getState().closeFullView();
    vi.spyOn(api, "fetchWorkbookView").mockResolvedValue(view);
    await openWorkbookForConversation(file.relative, session, { layout: "split", showSheet: false });
    expect(useExcelStore.getState().fullViewPath).toBeNull();
    expect(useExcelStore.getState().activeFilePath).toBe(file.relative);
    expect(useWorkbookConversationStore.getState().targets.s1).toMatchObject({ file, showSheet: false, layout: "split", sheet: "销售明细" });
    const message = await prepareWorkbookMessage("汇总这张表", session.id);
    expect(message).toContain("@file:uploads/sales.xlsx@sha256:aaaa");
    expect(message).not.toContain("old.xlsx");
  });

  it("keeps the split layout when replacing the visible main workbook", async () => {
    useExcelStore.getState().openFullView("uploads/old.xlsx", "旧表", "split");
    vi.spyOn(api, "fetchWorkbookView").mockResolvedValue(view);
    await openWorkbookForConversation(file.relative, session, { layout: "split", showSheet: true });
    expect(useExcelStore.getState()).toMatchObject({ fullViewPath: file.relative, fullViewLayout: "split", panelOpen: false });
    expect(useWorkbookConversationStore.getState().targets.s1.file.relative).toBe(file.relative);
  });

  it("retains the original discussion target if a replacement cannot be read", async () => {
    ready();
    useExcelStore.getState().closeFullView();
    vi.spyOn(api, "fetchWorkbookView").mockRejectedValue(new Error("文件已删除"));
    await expect(openWorkbookForConversation("missing.xlsx", session, { showSheet: false })).rejects.toThrow("文件已删除");
    expect(useWorkbookConversationStore.getState().targets.s1.file.relative).toBe(file.relative);
  });

  it("opens the replacement picker with the current layout and resets its intent for normal opens", () => {
    useWorkbookConversationStore.getState().bind(session.id, file, "销售明细", "split");
    useWorkbookConversationStore.getState().setShowSheet(session.id, false);
    useWorkbookConversationStore.getState().openSwitchPicker(session.id);
    expect(useWorkbookConversationStore.getState()).toMatchObject({ pickerOpen: true, pickerMode: "switch", pickerLayout: "split", pickerShowSheet: false });
    useWorkbookConversationStore.getState().openPicker();
    expect(useWorkbookConversationStore.getState()).toMatchObject({ pickerMode: "open", pickerLayout: "embedded", pickerShowSheet: true });
  });

  it("keeps the discussion target when hiding the sheet, without resetting readiness on reopen", () => {
    ready();
    useExcelStore.getState().closeFullView();
    expect(useWorkbookConversationStore.getState().targets.s1.showSheet).toBe(false);
    useExcelStore.getState().openFullView(file.relative, "销售明细");
    expect(useWorkbookConversationStore.getState().views[workbookViewKey(session.id, file)].status).toBe("ready");
  });

  it("ignores late observations for a different file or workspace", () => {
    ready();
    const store = useWorkbookConversationStore.getState();
    store.observe(session.id, fileRefFromSession(file.relative, other), { status: "error", error: "wrong workspace" });
    store.observe(session.id, { ...file, relative: "other.xlsx" }, { status: "error", error: "wrong file" });
    expect(useWorkbookConversationStore.getState().views[workbookViewKey(session.id, file)].status).toBe("ready");
  });

  it("adds the viewed file and version when asking about this spreadsheet", async () => {
    ready();
    const text = await prepareWorkbookMessage("这张表有哪些字段？", session.id);
    expect(text).toContain("@file:uploads/sales.xlsx@sha256:aaaa");
    expect(text).toContain('当前工作表："销售明细"');
    expect(text).not.toContain("已上传");
    expect(edits.flush).toHaveBeenCalledWith(expect.objectContaining({ relative: file.relative }));
  });

  it("preserves explicit range references and command semantics", () => {
    ready();
    const target = useWorkbookConversationStore.getState().targets.s1;
    const explicit = "@file:other.xlsx[明细!A2:B5] 分析这些行";
    expect(formatWorkbookMessage(explicit, target)).toBe(explicit);
    expect(formatWorkbookMessage("/resume", target)).toBe("/resume");
  });

  it("preserves literal paths with spaces rather than generating a truncated mention", () => {
    const target = { file: { ...file, relative: "uploads/Q3 sales.xlsx" }, sheet: "销售 明细", showSheet: true };
    const text = formatWorkbookMessage("分析这份表", target, "sha256:aaaa");
    expect(text).toContain('"path":"uploads/Q3 sales.xlsx"');
    expect(text).not.toContain("@file:");
  });

  it("blocks sending while the file is still loading or has unsaved edits", async () => {
    useWorkbookConversationStore.getState().bind(session.id, file);
    await expect(prepareWorkbookMessage("分析", session.id)).rejects.toThrow("正在加载");
    ready(); edits.paused.mockReturnValue(true);
    await expect(prepareWorkbookMessage("分析", session.id)).rejects.toThrow("未保存");
  });

  it("keeps the original sheet while waiting for a save", async () => {
    ready();
    let resolve!: () => void;
    edits.flush.mockImplementation(() => new Promise<void>((r) => { resolve = r; }));
    const pending = prepareWorkbookMessage("分析", session.id);
    useWorkbookConversationStore.getState().observe(session.id, file, { status: "ready", sheet: "其他工作表", version: "sha256:bbbb" });
    resolve();
    expect(await pending).toContain('当前工作表："销售明细"');
  });

  it("requires confirmation through a fresh send if the file changes while saving", async () => {
    ready();
    let resolve!: () => void;
    edits.flush.mockImplementation(() => new Promise<void>((r) => { resolve = r; }));
    const pending = prepareWorkbookMessage("分析", session.id);
    useWorkbookConversationStore.getState().bind(session.id, { ...file, relative: "other.xlsx" });
    resolve();
    await expect(pending).rejects.toThrow("当前表格已改变");
  });
});
