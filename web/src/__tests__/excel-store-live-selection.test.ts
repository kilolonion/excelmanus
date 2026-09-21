import { beforeEach, describe, expect, it } from "vitest";
import { useExcelStore } from "@/stores/excel-store";

describe("excel-store liveSelection", () => {
  beforeEach(() => {
    useExcelStore.setState({ liveSelection: null });
  });

  it("keeps the same reference when the selection is unchanged", () => {
    const sel = { path: "sales.xlsx", sheet: "明细", range: "C2:C9", contentVersion: "v1" };
    useExcelStore.getState().setLiveSelection(sel);
    const before = useExcelStore.getState().liveSelection;
    expect(before).toEqual(sel);
    useExcelStore.getState().setLiveSelection({ ...sel });
    expect(useExcelStore.getState().liveSelection).toBe(before);
  });

  it("updates when a field changes and clears with null", () => {
    useExcelStore.getState().setLiveSelection({ path: "sales.xlsx", sheet: "明细", range: "C2:C9" });
    const before = useExcelStore.getState().liveSelection;
    useExcelStore.getState().setLiveSelection({ path: "sales.xlsx", sheet: "明细", range: "D1" });
    expect(useExcelStore.getState().liveSelection).not.toBe(before);
    expect(useExcelStore.getState().liveSelection?.range).toBe("D1");
    useExcelStore.getState().setLiveSelection(null);
    expect(useExcelStore.getState().liveSelection).toBeNull();
    useExcelStore.getState().setLiveSelection(null);
    expect(useExcelStore.getState().liveSelection).toBeNull();
  });
});
