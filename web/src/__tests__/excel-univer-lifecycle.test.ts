import { describe, expect, it, vi } from "vitest";
import { activateWorkbookSheet } from "@/lib/excel-univer-lifecycle";

describe("activateWorkbookSheet", () => {
  it("activates through the installed facade instead of assuming getName exists", () => {
    const target = { getSheetName: () => "原始数" };
    const setActiveSheet = vi.fn();
    expect(activateWorkbookSheet({ getActiveWorkbook: () => ({
      getSheetByName: (name) => name === "原始数" ? target : null,
      setActiveSheet,
    }) }, "原始数")).toBe(true);
    expect(setActiveSheet).toHaveBeenCalledWith(target);
  });

  it("activates the named sheet", () => {
    const activate = vi.fn();
    const ok = activateWorkbookSheet(
      {
        getActiveWorkbook: () => ({
          getSheets: () => [
            { getName: () => "Sheet1", activate: vi.fn() },
            { getName: () => "明细", activate },
          ],
        }),
      },
      "明细",
    );
    expect(ok).toBe(true);
    expect(activate).toHaveBeenCalledOnce();
  });

  it("returns false when the sheet or workbook is missing", () => {
    expect(activateWorkbookSheet(null, "明细")).toBe(false);
    expect(activateWorkbookSheet({ getActiveWorkbook: () => null }, "明细")).toBe(false);
    expect(
      activateWorkbookSheet(
        { getActiveWorkbook: () => ({ getSheets: () => [{ getName: () => "Sheet1" }] }) },
        "明细",
      ),
    ).toBe(false);
    expect(
      activateWorkbookSheet(
        { getActiveWorkbook: () => ({ getSheets: () => [{ getName: () => "Sheet1" }] }) },
        undefined,
      ),
    ).toBe(false);
  });
});
