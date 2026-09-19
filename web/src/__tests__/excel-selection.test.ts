import { describe, expect, it } from "vitest";
import { readActiveRange } from "@/lib/excel-selection";

function api(names: { getName?: () => string; getSheetName?: () => string }, rows = 1, cols = 1) {
  return { getActiveWorkbook: () => ({ getActiveSheet: () => ({
    ...names,
    getSelection: () => ({ getActiveRange: () => ({
      getRow: () => 2, getColumn: () => 1, getNumRows: () => rows, getNumColumns: () => cols,
    }) }),
  }) }) };
}

describe("selection identity", () => {
  it("uses the real alternate worksheet name with a single A1 address", () => {
    expect(readActiveRange(api({ getSheetName: () => "销售" }))).toEqual({ sheet: "销售", range: "B3" });
  });
  it("never substitutes Sheet1 when the facade cannot report its name", () => {
    expect(readActiveRange(api({}))).toEqual({});
  });
  it("keeps zero-based UI coordinates aligned with Excel", () => {
    expect(readActiveRange(api({ getName: () => "销售" }, 2, 26))).toEqual({ sheet: "销售", range: "B3:AA4" });
  });
});
