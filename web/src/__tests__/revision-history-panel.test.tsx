// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { RevisionTimelinePanel } from "@/components/chat/CheckpointTimeline";
import { RevisionWorkbookPreview } from "@/components/excel/RevisionWorkbookPreview";
import { useSessionStore } from "@/stores/session-store";
import { useExcelStore } from "@/stores/excel-store";
import { fetchRevisions, fetchRevisionPreview, restoreRevision, deleteRevision, type RevisionPreviewResponse, type RevisionListResponse } from "@/lib/api";
import { flushWorkbookEdits, hasPendingWorkbookEdits, isWorkbookEditPaused } from "@/lib/excel-cell-edit";

vi.mock("@/hooks/use-mobile", () => ({ useIsMobile: () => false }));
vi.mock("@/lib/api", () => ({ fetchRevisions: vi.fn(), fetchRevisionPreview: vi.fn(), restoreRevision: vi.fn(), deleteRevision: vi.fn() }));
vi.mock("@/lib/excel-cell-edit", async (original) => ({
  ...await original<typeof import("@/lib/excel-cell-edit")>(),
  flushWorkbookEdits: vi.fn(), hasPendingWorkbookEdits: vi.fn(), isWorkbookEditPaused: vi.fn(),
}));

const revision = { revision_id: "saved", sequence: 1, content_version: "saved-version", reason: "checkpoint", transaction_id: "tx", label: "发版前", parent_revision_id: null };
const history: RevisionListResponse = { path: "book.xlsx", content_version: "live-version", revisions: [revision], total: 1 };
const preview: RevisionPreviewResponse = {
  content_version: "saved-version", revision_id: "saved", revision_reason: "checkpoint", revision_label: "发版前",
  sheets: [{ name: "收据", sheet_id: "s1", used: { rows: 3, cols: 3 } }, { name: "明细", sheet_id: "s2", used: { rows: 2, cols: 2 } }],
  windows: [{ sheet: "收据", rect: { r0: 1, c0: 1, r1: 50, c1: 26 }, cells: {
    "1,1": { t: "s", v: "收款收据", cached: "yes", s: { bg: { rgb: "#195D85" }, cl: { rgb: "#FFFFFF" }, bl: 1 } },
    "2,1": { t: "n", v: 128, cached: "yes", s: { n: { pattern: "0.00" } } },
    "2,2": { t: "z", v: null, cached: "no", f: "=A2*6" },
    "3,3": { t: "z", v: null, cached: "yes", s: { bg: { rgb: "#FF0000" }, bd: { b: { s: 7, cl: { rgb: "#000000" } } } } },
  }, merges: [{ min_row: 1, max_row: 1, min_col: 1, max_col: 3 }], col_widths: { A: 25 }, row_heights: { "1": 36 } }],
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

beforeEach(() => {
  vi.resetAllMocks();
  useSessionStore.setState({ activeSessionId: "s1", sessions: [{ id: "s1", title: "book", workspaceId: "w1", messageCount: 0, inFlight: false }] });
  useExcelStore.setState({ activeWorkspaceKey: "id:w1" });
  vi.mocked(fetchRevisions).mockResolvedValue(history);
  vi.mocked(fetchRevisionPreview).mockResolvedValue(preview);
  vi.mocked(flushWorkbookEdits).mockResolvedValue(undefined);
  vi.mocked(hasPendingWorkbookEdits).mockReturnValue(false);
  vi.mocked(isWorkbookEditPaused).mockReturnValue(false);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

async function openConfirmation() {
  fireEvent.click(await screen.findByRole("button", { name: "恢复" }));
  return screen.findByRole("button", { name: "确认恢复" });
}

describe("revision timeline asynchronous safety", () => {
  it("holds the confirmed version while a background refresh sees newer edits", async () => {
    vi.mocked(restoreRevision).mockResolvedValue({ status: "ok", path: "book.xlsx", content_version: "saved-version", restored_revision: "saved" });
    render(<RevisionTimelinePanel filePath="book.xlsx" />);
    const confirm = await openConfirmation();
    expect(flushWorkbookEdits).toHaveBeenCalledWith({ workspaceKey: "id:w1", relative: "book.xlsx" });
    const requests = vi.mocked(fetchRevisions).mock.calls.length;
    vi.mocked(fetchRevisions).mockResolvedValue({ ...history, content_version: "newer-version" });
    act(() => useExcelStore.getState().bumpWorkspaceFilesVersion());
    await waitFor(() => expect(fetchRevisions).toHaveBeenCalledTimes(requests + 1));
    fireEvent.click(confirm);
    await waitFor(() => expect(restoreRevision).toHaveBeenCalledTimes(1));
    expect(restoreRevision).toHaveBeenCalledWith(expect.objectContaining({ expectedVersion: "live-version", sessionId: "s1", workspaceId: "w1" }));
  });

  it.each(["pending", "paused", "flush failure"])("does not offer restore with %s edits", async (mode) => {
    if (mode === "pending") vi.mocked(hasPendingWorkbookEdits).mockReturnValue(true);
    if (mode === "paused") vi.mocked(isWorkbookEditPaused).mockReturnValue(true);
    if (mode === "flush failure") vi.mocked(flushWorkbookEdits).mockRejectedValue(new Error("保存失败"));
    render(<RevisionTimelinePanel filePath="book.xlsx" />);
    fireEvent.click(await screen.findByRole("button", { name: "恢复" }));
    await screen.findByText(mode === "flush failure" ? "保存失败" : /仍有未保存的编辑/);
    expect(screen.queryByRole("button", { name: "确认恢复" })).toBeNull();
    expect(restoreRevision).not.toHaveBeenCalled();
  });

  it("blocks an edit arriving after confirmation opens", async () => {
    render(<RevisionTimelinePanel filePath="book.xlsx" />);
    const confirm = await openConfirmation();
    vi.mocked(hasPendingWorkbookEdits).mockReturnValue(true);
    fireEvent.click(confirm);
    await screen.findByText(/仍有未保存的编辑/);
    expect(restoreRevision).not.toHaveBeenCalled();
  });

  it("discards late preview results when the selected file changes", async () => {
    const oldPreview = deferred<RevisionPreviewResponse>();
    vi.mocked(fetchRevisionPreview).mockReturnValue(oldPreview.promise);
    const view = render(<RevisionTimelinePanel filePath="book.xlsx" />);
    fireEvent.click(await screen.findByRole("button", { name: "预览" }));
    view.rerender(<RevisionTimelinePanel filePath="other.xlsx" />);
    await act(async () => oldPreview.resolve(preview));
    expect(screen.queryByRole("table", { name: "历史工作表预览" })).toBeNull();
    expect(vi.mocked(fetchRevisionPreview).mock.calls[0][0].signal?.aborted).toBe(true);
  });

  it("does not open a late confirmation after leaving the history tab", async () => {
    const latest = deferred<RevisionListResponse>();
    const view = render(<RevisionTimelinePanel filePath="book.xlsx" />);
    const restore = await screen.findByRole("button", { name: "恢复" });
    vi.mocked(fetchRevisions).mockReturnValue(latest.promise);
    fireEvent.click(restore);
    await waitFor(() => expect(fetchRevisions).toHaveBeenCalledTimes(2));
    view.rerender(<RevisionTimelinePanel filePath="book.xlsx" active={false} />);
    await act(async () => latest.resolve(history));
    expect(screen.queryByRole("button", { name: "确认恢复" })).toBeNull();
  });

  it("refreshes the original workspace if the user switches during restoration", async () => {
    const pending = deferred<Awaited<ReturnType<typeof restoreRevision>>>();
    vi.mocked(restoreRevision).mockReturnValue(pending.promise);
    const notify = vi.spyOn(useExcelStore.getState(), "notifyWorkbookChanged");
    render(<RevisionTimelinePanel filePath="book.xlsx" />);
    const confirm = await openConfirmation();
    fireEvent.click(confirm);
    fireEvent.click(confirm);
    expect(restoreRevision).toHaveBeenCalledTimes(1);
    act(() => {
      useSessionStore.setState({ activeSessionId: "s2", sessions: [{ id: "s2", title: "other", workspaceId: "w2", messageCount: 0, inFlight: false }] });
      useExcelStore.setState({ activeWorkspaceKey: "id:w2" });
    });
    await act(async () => pending.resolve({ status: "ok", path: "book.xlsx", content_version: "saved-version", restored_revision: "saved" }));
    expect(notify).toHaveBeenCalledWith("book.xlsx", "id:w1", "saved-version", "refresh");
    expect(screen.queryByText(/已恢复到/)).toBeNull();
  });

  it("allows preview and checkpoint removal for the current version", async () => {
    vi.mocked(fetchRevisions).mockResolvedValue({ ...history, content_version: revision.content_version });
    vi.mocked(deleteRevision).mockResolvedValue({ status: "ok", path: "book.xlsx", deleted_revision: "saved" });
    vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<RevisionTimelinePanel filePath="book.xlsx" />);
    expect((await screen.findByRole("button", { name: "恢复" }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "预览" }) as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(screen.getByTitle("删除检查点"));
    await waitFor(() => expect(deleteRevision).toHaveBeenCalledWith({ path: "book.xlsx", revisionId: "saved", sessionId: "s1", workspaceId: "w1" }));
  });

  it("scopes an explicitly selected workspace independently of the active chat", async () => {
    render(<RevisionTimelinePanel filePath="book.xlsx" workspaceId="w2" />);
    await screen.findByRole("button", { name: "预览" });
    expect(fetchRevisions).toHaveBeenCalledWith("book.xlsx", expect.objectContaining({ workspaceId: "w2", sessionId: undefined }));
  });

  it("switches preview sheets and does not reopen a closed preview after a late reply", async () => {
    render(<RevisionTimelinePanel filePath="book.xlsx" />);
    fireEvent.click(await screen.findByRole("button", { name: "预览" }));
    await screen.findByRole("table", { name: "历史工作表预览" });
    const other = deferred<RevisionPreviewResponse>();
    vi.mocked(fetchRevisionPreview).mockReturnValue(other.promise);
    fireEvent.change(screen.getByLabelText("工作表"), { target: { value: "明细" } });
    expect(fetchRevisionPreview).toHaveBeenLastCalledWith(expect.objectContaining({ sheet: "明细" }));
    fireEvent.click(screen.getByRole("button", { name: "关闭" }));
    await act(async () => other.resolve(preview));
    expect(screen.queryByRole("table", { name: "历史工作表预览" })).toBeNull();
  });
});

describe("styled historical workbook preview", () => {
  it("renders merges, dimensions, styled empty cells and uncached formulas", () => {
    const onSheet = vi.fn();
    const { container } = render(<RevisionWorkbookPreview data={preview} loading={false} onSheet={onSheet} />);
    const title = screen.getByText("收款收据") as HTMLTableCellElement;
    expect(title.colSpan).toBe(3);
    expect(title.style.backgroundColor).toBe("rgb(25, 93, 133)");
    expect(title.style.color).toBe("rgb(255, 255, 255)");
    expect(title.parentElement?.style.height).toBe("48px");
    expect((container.querySelectorAll("col")[1] as HTMLElement).style.width).toBe("187.5px");
    expect(screen.getByText("128.00")).toBeTruthy();
    expect(screen.getByText("=A2*6").title).toBe("公式：=A2*6");
    const blank = container.querySelector("tbody tr:last-child td:last-child") as HTMLElement;
    expect(blank.style.backgroundColor).toBe("rgb(255, 0, 0)");
    expect(blank.style.borderBottom).toContain("double");
    fireEvent.change(screen.getByLabelText("工作表"), { target: { value: "明细" } });
    expect(onSheet).toHaveBeenCalledWith("明细");
    expect(container.querySelectorAll("tbody tr")).toHaveLength(3);
  });
});
