import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useExcelStore } from "@/stores/excel-store";
import { useSessionStore } from "@/stores/session-store";
import { fetchOperations, undoOperation, type OperationRecord } from "@/lib/api";
import { hasPendingWorkbookEdits } from "@/lib/excel-cell-edit";

vi.mock("@/lib/api", () => ({ fetchOperations: vi.fn(), undoOperation: vi.fn(), invalidateWorkbookCaches: vi.fn() }));
vi.mock("@/lib/excel-cell-edit", () => ({ flushWorkbookEdits: vi.fn(), hasPendingWorkbookEdits: vi.fn(), isWorkbookEditPaused: vi.fn() }));

const operation: OperationRecord = {
  approval_id: "op1", tool_name: "edit_spreadsheet", arguments_summary: {}, session_turn: 1,
  created_at_utc: "2026-09-21T00:00:00Z", applied_at_utc: "2026-09-21T00:00:01Z",
  execution_status: "success", undoable: true, result_preview: "done",
  changes: [{ path: "book.xlsx", change_type: "modified", before_size: 1, after_size: 2, is_binary: true }],
};
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}
function session(id: string) {
  useSessionStore.setState({ activeSessionId: id, sessions: [{ id, title: id, workspaceId: id, messageCount: 0, inFlight: false }] });
}
beforeEach(() => {
  vi.resetAllMocks(); session("s1");
  useExcelStore.setState({ operations: [operation], operationsLoaded: true, operationsLoading: false, operationsError: null, refreshCounter: 7 });
});
afterEach(() => vi.restoreAllMocks());

describe("operation history scope and failure handling", () => {
  it("ignores a previous session's response", async () => {
    const old = deferred<Awaited<ReturnType<typeof fetchOperations>>>();
    vi.mocked(fetchOperations).mockReturnValueOnce(old.promise).mockResolvedValueOnce({ operations: [{ ...operation, approval_id: "op2" }], total: 1, has_more: false });
    const pending = useExcelStore.getState().fetchOperationHistory("s1");
    session("s2");
    await useExcelStore.getState().fetchOperationHistory("s2");
    old.resolve({ operations: [operation], total: 1, has_more: false });
    await pending;
    expect(useExcelStore.getState().operations[0].approval_id).toBe("op2");
  });

  it("keeps the latest response when refreshes finish out of order", async () => {
    const old = deferred<Awaited<ReturnType<typeof fetchOperations>>>();
    vi.mocked(fetchOperations).mockReturnValueOnce(old.promise).mockResolvedValueOnce({ operations: [], total: 0, has_more: false });
    const pending = useExcelStore.getState().fetchOperationHistory("s1");
    await useExcelStore.getState().fetchOperationHistory("s1");
    old.resolve({ operations: [operation], total: 1, has_more: false });
    await pending;
    expect(useExcelStore.getState().operations).toEqual([]);
  });

  it("reports a load error without clearing records or automatically looping", async () => {
    vi.mocked(fetchOperations).mockRejectedValue(new Error("网络不可用"));
    await useExcelStore.getState().fetchOperationHistory("s1");
    expect(useExcelStore.getState()).toMatchObject({ operations: [operation], operationsLoading: false, operationsLoaded: true, operationsError: "网络不可用" });
  });

  it("keeps new operations and refresh state when undo fails", async () => {
    const pending = deferred<Awaited<ReturnType<typeof undoOperation>>>();
    vi.mocked(undoOperation).mockReturnValue(pending.promise);
    const result = useExcelStore.getState().undoOperationById("s1", "op1");
    await vi.waitFor(() => expect(undoOperation).toHaveBeenCalledOnce());
    useExcelStore.getState().appendOperation({ ...operation, approval_id: "op2" });
    useExcelStore.setState({ refreshCounter: 9 });
    pending.resolve({ status: "error", message: "文件已被修改", approval_id: "op1" });
    expect(await result).toBe(false);
    expect(useExcelStore.getState().operations.map((op) => op.approval_id)).toEqual(["op2", "op1"]);
    expect(useExcelStore.getState()).toMatchObject({ refreshCounter: 9, operationsError: "文件已被修改" });
    expect(useExcelStore.getState().operations[1].undoable).toBe(true);
  });

  it("does not restore another session's list after a failed undo", async () => {
    const pending = deferred<Awaited<ReturnType<typeof undoOperation>>>();
    vi.mocked(undoOperation).mockReturnValue(pending.promise);
    const result = useExcelStore.getState().undoOperationById("s1", "op1");
    await vi.waitFor(() => expect(undoOperation).toHaveBeenCalledOnce());
    session("s2");
    useExcelStore.setState({ operations: [], operationsError: null });
    pending.resolve({ status: "error", message: "failed", approval_id: "op1" });
    await result;
    expect(useExcelStore.getState()).toMatchObject({ operations: [], operationsError: null });
  });

  it("blocks undo with pending local edits", async () => {
    vi.mocked(hasPendingWorkbookEdits).mockReturnValue(true);
    expect(await useExcelStore.getState().undoOperationById("s1", "op1")).toBe(false);
    expect(undoOperation).not.toHaveBeenCalled();
    expect(useExcelStore.getState().operationsError).toContain("未保存");
  });

  it("deduplicates undo and refreshes its captured workspace after a session switch", async () => {
    const pending = deferred<Awaited<ReturnType<typeof undoOperation>>>();
    vi.mocked(undoOperation).mockReturnValue(pending.promise);
    const notify = vi.spyOn(useExcelStore.getState(), "notifyWorkbookChanged").mockImplementation(() => {});
    const result = useExcelStore.getState().undoOperationById("s1", "op1");
    expect(await useExcelStore.getState().undoOperationById("s1", "op1")).toBe(false);
    await vi.waitFor(() => expect(undoOperation).toHaveBeenCalledOnce());
    session("s2");
    pending.resolve({ status: "ok", message: "已回滚", approval_id: "op1" });
    expect(await result).toBe(true);
    expect(notify).toHaveBeenCalledWith("book.xlsx", "id:s1", undefined, "refresh");
  });
});
