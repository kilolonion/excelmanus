import { describe, expect, it } from "vitest";
import { formatSelectionConfirmLabel, isSingleCellSelection, readActiveRange } from "@/lib/excel-selection";

function api(names: { getName?: () => string; getSheetName?: () => string }, rows = 1, cols = 1) {
  return { getActiveWorkbook: () => ({ getActiveSheet: () => ({
    ...names,
    getSelection: () => ({ getActiveRange: () => ({
      getRow: () => 2, getColumn: () => 1, getNumRows: () => rows, getNumColumns: () => cols,
    }) }),
  }) }) };
}

describe("selection identity", () => {
  const range = (row: number, col: number, rows = 1, cols = 1, rangeType = 0) => ({
    getRow: () => row, getColumn: () => col, getHeight: () => rows, getWidth: () => cols,
    getRange: () => ({ rangeType }),
  });
  const multiApi = (ranges: ReturnType<typeof range>[]) => ({ getActiveWorkbook: () => ({ getActiveSheet: () => ({
    getSheetName: () => "销售",
    getSelection: () => ({ getActiveRange: () => ranges[0], getActiveRangeList: () => ranges }),
  }) }) });

  it("keeps disjoint cells and rectangles without including the gaps", () => {
    expect(readActiveRange(multiApi([range(0, 0), range(3, 3, 2, 2), range(0, 0)])))
      .toEqual({ sheet: "销售", range: "A1,D4:E5" });
    expect(isSingleCellSelection("A1,D4")).toBe(false);
    expect(formatSelectionConfirmLabel("sales.xlsx", "销售", "A1,D4", "wrong active value"))
      .toBe("引用 sales.xlsx · 销售!A1,D4（2 个区域）");
  });

  it("preserves whole rows and columns including disjoint axes", () => {
    expect(readActiveRange(multiApi([range(1, 0, 3, 30, 1), range(0, 2, 100, 2, 2)])))
      .toEqual({ sheet: "销售", range: "2:4,C:D" });
  });

  it("does not silently reference only part of an invalid selection", () => {
    expect(readActiveRange(multiApi([range(0, 0), range(-1, 1)]))).toEqual({ sheet: "销售" });
    expect(readActiveRange(multiApi([]))).toEqual({ sheet: "销售" });
  });

  it("uses the real alternate worksheet name with a single A1 address", () => {
    expect(readActiveRange(api({ getSheetName: () => "销售" }))).toEqual({ sheet: "销售", range: "B3" });
  });
  it("never substitutes Sheet1 when the facade cannot report its name", () => {
    expect(readActiveRange(api({}))).toEqual({});
  });
  it("keeps zero-based UI coordinates aligned with Excel", () => {
    expect(readActiveRange(api({ getName: () => "销售" }, 2, 26))).toEqual({ sheet: "销售", range: "B3:AA4" });
  });
  it("reads multi-cell dimensions from the installed Univer facade", () => {
    const currentApi = { getActiveWorkbook: () => ({ getActiveSheet: () => ({
      getSheetName: () => "销售",
      getSelection: () => ({ getActiveRange: () => ({
        getRow: () => 2, getColumn: () => 1, getHeight: () => 2, getWidth: () => 26,
      }) }),
    }) }) };
    expect(readActiveRange(currentApi)).toEqual({ sheet: "销售", range: "B3:AA4" });
    expect(readActiveRange({ getActiveWorkbook: () => null })).toEqual({});
  });
});
