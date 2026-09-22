import { describe, expect, it, vi } from "vitest";
import { highlightWorkbookRanges, splitWorkbookRangeSpec, workbookFocusRanges } from "@/lib/workbook-focus";
import { useWorkbookFocusStore } from "@/stores/workbook-focus-store";

describe("workbook result navigation", () => {
  it("preserves quoted sheet names and separate selection areas", () => {
    expect(splitWorkbookRangeSpec("'Sales ! O''Brien'!$A$2:$A$5,C2:C5")).toEqual({ sheet: "Sales ! O'Brien", range: "$A$2:$A$5,C2:C5" });
    expect(splitWorkbookRangeSpec("D4")).toEqual({ range: "D4" });
    expect(workbookFocusRanges("$a$2:$a$5,C2:C5,C2:C5")).toEqual(["A2:A5", "C2:C5"]);
    expect(workbookFocusRanges("A:C,2:5")).toEqual(["A:C", "2:5"]);
  });

  it.each(["=SUM(A1)", "A0", "XFE1", "A1048577", "A1,,B1", "'Other'!A1", Array(65).fill("A1").join(",")])("rejects invalid or oversized focus %s", (range) => {
    expect(workbookFocusRanges(range)).toEqual([]);
  });

  it("uses disposable visual overlays without touching saved cell formatting", () => {
    const dispose = vi.fn();
    const sheet = { getRange: vi.fn((address: string) => ({ address })), highlightRanges: vi.fn(() => ({ dispose })), setBackground: vi.fn() };
    const overlay = highlightWorkbookRanges(sheet, ["A1:A3", "C1:C3"]);
    expect(sheet.highlightRanges).toHaveBeenCalledWith([{ address: "A1:A3" }, { address: "C1:C3" }], expect.any(Object));
    expect(sheet.setBackground).not.toHaveBeenCalled();
    overlay.dispose(); expect(dispose).toHaveBeenCalledOnce();
  });

  it("scopes navigation to a workspace and gives repeated clicks distinct requests", () => {
    const store = useWorkbookFocusStore.getState();
    const file = { relative: "book.xlsx", workspaceId: "w1", workspaceKey: "id:w1" };
    store.focus(file, "Sheet1", "A1");
    const first = useWorkbookFocusStore.getState().request!;
    store.complete(first.id);
    store.focus(file, "Sheet1", "=invalid");
    expect(useWorkbookFocusStore.getState().request).toBeNull();
    store.focus(file, "Sheet1", "A1");
    expect(useWorkbookFocusStore.getState().request).toMatchObject({ file, sheet: "Sheet1", ranges: ["A1"] });
    expect(useWorkbookFocusStore.getState().request!.id).toBeGreaterThan(first.id);
  });
});
