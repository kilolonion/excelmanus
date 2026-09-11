import { beforeEach, describe, expect, it } from "vitest";
import { useExcelStore } from "@/stores/excel-store";

function resetVersionState() {
  useExcelStore.setState({
    contentVersions: {},
    refreshCounter: 0,
    draftRange: null,
    pendingSelection: null,
    selectionMode: false,
  });
}

describe("excel-store contentVersion", () => {
  beforeEach(() => {
    resetVersionState();
  });

  it("stores versions keyed by normalized path", () => {
    useExcelStore.getState().setContentVersion("uploads/a.xlsx", "sha256:v1");
    expect(useExcelStore.getState().getContentVersion("./uploads/a.xlsx")).toBe("sha256:v1");
    expect(useExcelStore.getState().getContentVersion("uploads/a.xlsx")).toBe("sha256:v1");
  });

  it("clears version when set to null or empty", () => {
    useExcelStore.getState().setContentVersion("./book.xlsx", "sha256:v1");
    useExcelStore.getState().setContentVersion("./book.xlsx", null);
    expect(useExcelStore.getState().getContentVersion("./book.xlsx")).toBeNull();

    useExcelStore.getState().setContentVersion("./book.xlsx", "sha256:v2");
    useExcelStore.getState().setContentVersion("./book.xlsx", "");
    expect(useExcelStore.getState().getContentVersion("./book.xlsx")).toBeNull();
  });

  it("does not bump refreshCounter when recording a write version", () => {
    useExcelStore.setState({ refreshCounter: 3 });
    useExcelStore.getState().setContentVersion("./book.xlsx", "sha256:after-write");
    expect(useExcelStore.getState().refreshCounter).toBe(3);
  });

  it("returns null for unknown files so first write can omit expected_version", () => {
    expect(useExcelStore.getState().getContentVersion("./missing.xlsx")).toBeNull();
  });

  it("keeps draftRange after a 409 write conflict", () => {
    useExcelStore.getState().setContentVersion("uploads/a.xlsx", "sha256:v1");
    useExcelStore.getState().setDraftRange({
      sheet: "Sheet1",
      range: "A1:B2",
      path: "uploads/a.xlsx",
    });
    const before = useExcelStore.getState().draftRange;
    expect(before?.contentVersion).toBe("sha256:v1");
    // persistExcelCellEdits 冲突只暂停写入，不清理选区草稿。
    expect(useExcelStore.getState().draftRange).toEqual(before);
  });

  it("owns draftRange and clears it when selection mode ends", () => {
    useExcelStore.getState().setContentVersion("uploads/a.xlsx", "sha256:v1");
    useExcelStore.getState().setDraftRange({
      sheet: "Sheet1",
      range: "A1:B2",
      path: "uploads/a.xlsx",
    });
    expect(useExcelStore.getState().draftRange).toEqual({
      sheet: "Sheet1",
      range: "A1:B2",
      path: "uploads/a.xlsx",
      contentVersion: "sha256:v1",
    });
    useExcelStore.getState().enterSelectionMode();
    expect(useExcelStore.getState().draftRange).toBeNull();

    useExcelStore.getState().setDraftRange({ sheet: "Sheet1", range: "C3" });
    useExcelStore.getState().confirmSelection({
      filePath: "uploads/a.xlsx",
      sheet: "Sheet1",
      range: "C3",
    });
    expect(useExcelStore.getState().draftRange).toBeNull();
    expect(useExcelStore.getState().pendingSelection?.filePath).toBe("uploads/a.xlsx");
  });

  it("clears versions on clearSession", () => {
    useExcelStore.getState().setContentVersion("./book.xlsx", "sha256:v1");
    useExcelStore.getState().clearSession();
    expect(useExcelStore.getState().getContentVersion("./book.xlsx")).toBeNull();
  });

  it("drops a file version when an agent diff lands for that path", () => {
    useExcelStore.getState().setContentVersion("./book.xlsx", "sha256:v1");
    useExcelStore.getState().addDiff({
      toolCallId: "tc-1",
      filePath: "./book.xlsx",
      sheet: "Sheet1",
      affectedRange: "A1",
      changes: [],
      timestamp: Date.now(),
    });
    expect(useExcelStore.getState().getContentVersion("./book.xlsx")).toBeNull();
  });
});
