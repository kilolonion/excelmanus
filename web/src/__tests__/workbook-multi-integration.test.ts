import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";
import { useExcelStore } from "@/stores/excel-store";
import { useSessionStore } from "@/stores/session-store";
import { useWorkbookWorkspaceStore, workbookWorkspaceKey, visibleWorkbookPaths } from "@/stores/workbook-workspace-store";
import { useWorkbookConversationStore } from "@/stores/workbook-conversation-store";
import { prepareWorkbookRequest } from "@/lib/workbook-conversation";
import { currentWorkbookSendContext, currentWorkbookGroupContexts } from "@/lib/workbook-context";
import { enqueueExcelCellEdit, resetExcelCellEditStateForTests, setPersistExcelCellEditsForTests } from "@/lib/excel-cell-edit";
import { fileRefFromSession } from "@/lib/workspace-file-ref";
import { prepareWorkbookGroupAction } from "@/lib/workbook-group-actions";
import { useWorkbookWorkflowStore } from "@/stores/workbook-workflow-store";
import { activateChatWorkspaceTab } from "@/lib/chat-workspace-tabs";
import { toggleWorkbookPanelView } from "@/components/excel/WorkbookPanelButton";

const session = { id: "multi", workspaceId: "ws", title: "多表", messageCount: 0, inFlight: false };
const key = workbookWorkspaceKey(session.id, "id:ws");
function ready(path: string, sheet = "明细", version = "v1") {
  useWorkbookConversationStore.getState().observe(session.id, fileRefFromSession(path, session), { status: "ready", sheet, version, range: "A2:B6" });
}
beforeEach(() => {
  useSessionStore.setState({ activeSessionId: session.id, sessions: [session] });
  useWorkbookWorkspaceStore.setState({ workspaces: {} });
  useWorkbookConversationStore.setState({ targets: {}, views: {} });
  useExcelStore.getState().clearSession();
  useExcelStore.setState({ panelOpen: false, activeFilePath: null, activeWorkspaceKey: "id:ws" });
});
afterEach(() => resetExcelCellEditStateForTests());

describe("workbook workspace compatibility", () => {
  it("keeps the primary stable but addresses the focused pane, then restores primary in chat", async () => {
    useExcelStore.getState().openFullView("main.xlsx", "明细"); ready("main.xlsx");
    useExcelStore.getState().openFullView("ref.xlsx", "预算"); ready("ref.xlsx", "预算");
    expect(useExcelStore.getState().fullViewPath).toBe("main.xlsx");
    expect(useWorkbookConversationStore.getState().targets.multi.file.relative).toBe("main.xlsx");
    const request = await prepareWorkbookRequest("比较这两份表格", session.id);
    expect(request.sheetContext).toMatchObject({ path: "ref.xlsx", sheet: "预算" });
    expect(request.sheetContexts?.map((view) => view.path)).toEqual(["main.xlsx", "ref.xlsx"]);
    useExcelStore.getState().closeFullView();
    expect(currentWorkbookSendContext(session.id)?.target.file.relative).toBe("main.xlsx");
  });

  it("promotes explicitly, removes a primary safely and never loses other open tabs", async () => {
    for (const path of ["a.xlsx", "b.xlsx", "c.xlsx", "d.xlsx"]) useExcelStore.getState().openFullView(path);
    expect(visibleWorkbookPaths(useWorkbookWorkspaceStore.getState().workspaces[key])).toEqual(["a.xlsx", "b.xlsx", "d.xlsx"]);
    useExcelStore.getState().setPrimaryWorkbook("c.xlsx");
    expect(useExcelStore.getState().fullViewPath).toBe("c.xlsx");
    expect(await useExcelStore.getState().closeWorkbook("c.xlsx")).toBe(true);
    expect(useExcelStore.getState().fullViewPath).toBe("a.xlsx");
    expect(useWorkbookWorkspaceStore.getState().workspaces[key].files).toHaveLength(3);
  });

  it("keeps reference focus when using the existing layout entry points", () => {
    useExcelStore.getState().openFullView("main.xlsx", "明细");
    useExcelStore.getState().openFullView("ref.xlsx", "预算");
    toggleWorkbookPanelView(false);
    expect(useExcelStore.getState()).toMatchObject({ fullViewPath: "main.xlsx", fullViewLayout: "split", activeFilePath: "ref.xlsx", activeSheet: "预算" });
    activateChatWorkspaceTab("sheet");
    expect(useExcelStore.getState()).toMatchObject({ fullViewPath: "main.xlsx", fullViewLayout: "embedded", activeFilePath: "ref.xlsx", activeSheet: "预算" });
  });

  it("blocks grouped sends and tab closing if a reference has an unsaved conflict", async () => {
    for (const path of ["a.xlsx", "b.xlsx"]) { useExcelStore.getState().openFullView(path); ready(path); }
    setPersistExcelCellEditsForTests(async () => ({ kind: "conflict", code: "VERSION_CONFLICT" }));
    enqueueExcelCellEdit({ path: "b.xlsx", file: fileRefFromSession("b.xlsx", session), sessionId: session.id, sheet: "明细", cell: "A2", value: 10, expectedVersion: "v1" });
    useExcelStore.getState().focusWorkbook("a.xlsx");
    await expect(prepareWorkbookRequest("分析这两张表", session.id)).rejects.toThrow("未保存");
    expect(await useExcelStore.getState().closeWorkbook("b.xlsx")).toBe(false);
    expect(useWorkbookWorkspaceStore.getState().workspaces[key].files).toHaveLength(2);
  });

  it("waits for all referenced saves and rejects a group changed during that wait", async () => {
    for (const path of ["a.xlsx", "b.xlsx"]) { useExcelStore.getState().openFullView(path); ready(path); }
    let finish!: () => void;
    setPersistExcelCellEditsForTests(() => new Promise((resolve) => { finish = () => resolve({ kind: "ok", contentVersion: "v2" }); }));
    enqueueExcelCellEdit({ path: "a.xlsx", file: fileRefFromSession("a.xlsx", session), sessionId: session.id, sheet: "明细", cell: "A2", value: 10, expectedVersion: "v1" });
    const pending = prepareWorkbookRequest("分析这些表", session.id);
    const rejected = expect(pending).rejects.toThrow("同屏表格已改变");
    await vi.waitFor(() => expect(finish).toBeTypeOf("function"));
    useExcelStore.getState().openFullView("c.xlsx"); ready("c.xlsx");
    useExcelStore.getState().focusWorkbook("b.xlsx");
    finish();
    await rejected;
  });

  it("never adds implicit references to an explicit file request", async () => {
    for (const path of ["a.xlsx", "b.xlsx"]) { useExcelStore.getState().openFullView(path); ready(path); }
    const request = await prepareWorkbookRequest("只分析 @file:a.xlsx", session.id);
    expect(request.sheetContexts).toBeUndefined();
    expect(request.sheetContext).toBeUndefined();
  });

  it("returns from compare to the same group and prepares a merge without changing any file", async () => {
    for (const path of ["a.xlsx", "b.xlsx"]) { useExcelStore.getState().openFullView(path); ready(path); }
    await prepareWorkbookGroupAction("compare", ["a.xlsx", "b.xlsx"]);
    useExcelStore.getState().closeCompare();
    expect(useExcelStore.getState().fullViewPath).toBe("a.xlsx");
    await prepareWorkbookGroupAction("merge", ["a.xlsx", "b.xlsx"]);
    expect(useWorkbookWorkflowStore.getState().handoff).toMatchObject({ operation: "merge-workbooks",
      parameters: { output: "new_workbook", primary_path: "a.xlsx", sources: [{ path: "a.xlsx" }, { path: "b.xlsx" }] } });
  });

  it("uses identity only for a hidden primary on narrow screens", () => {
    for (const path of ["a.xlsx", "b.xlsx"]) { useExcelStore.getState().openFullView(path); ready(path); }
    useWorkbookWorkspaceStore.getState().setVisible(key, ["b.xlsx"]);
    const contexts = currentWorkbookGroupContexts(session.id);
    expect(contexts[0]).toEqual({ workspace_id: "ws", path: "a.xlsx", sheet: "", range: "" });
    expect(contexts[1]).toMatchObject({ path: "b.xlsx", sheet: "明细", range: "A2:B6", observed_version: "v1" });
  });

  it("retries the captured group after changing panes, and respects an edited explicit target", async () => {
    for (const path of ["a.xlsx", "b.xlsx"]) { useExcelStore.getState().openFullView(path); ready(path); }
    const original = await prepareWorkbookRequest("比较这两份表", session.id);
    const captured = { sheet_context: original.sheetContext, sheet_contexts: original.sheetContexts! };
    useExcelStore.getState().openFullView("c.xlsx"); ready("c.xlsx");
    const retry = await prepareWorkbookRequest(original.text, session.id, captured);
    expect(retry.sheetContexts?.map((view) => view.path)).toEqual(["a.xlsx", "b.xlsx"]);
    expect(retry.sheetContext?.path).toBe("b.xlsx");
    const edited = await prepareWorkbookRequest("只看 @file:c.xlsx", session.id, captured);
    expect(edited.sheetContexts).toBeUndefined();
    setPersistExcelCellEditsForTests(async () => ({ kind: "conflict", code: "VERSION_CONFLICT" }));
    enqueueExcelCellEdit({ path: "a.xlsx", file: fileRefFromSession("a.xlsx", session), sessionId: session.id, sheet: "明细", cell: "A2", value: 10, expectedVersion: "v1" });
    await expect(prepareWorkbookRequest(original.text, session.id, captured)).rejects.toThrow("未保存");
  });

  it("isolates identical paths in different sessions and workspaces", () => {
    useExcelStore.getState().openFullView("a.xlsx");
    const other = { ...session, id: "other", workspaceId: "other-ws" };
    useSessionStore.setState({ activeSessionId: other.id, sessions: [session, other] });
    useExcelStore.getState().rebindSession("id:ws", "id:other-ws");
    useExcelStore.getState().openFullView("b.xlsx");
    expect(useWorkbookWorkspaceStore.getState().workspaces[key].files.map((file) => file.path)).toEqual(["a.xlsx"]);
    expect(useExcelStore.getState().fullViewPath).toBe("b.xlsx");
    expect(currentWorkbookSendContext(other.id)?.target.file.workspaceId).toBe("other-ws");
  });
});
