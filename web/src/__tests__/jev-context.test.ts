import { beforeEach, describe, expect, it } from "vitest";
import { buildJevSheetContext } from "@/lib/jev-context";
import { useExcelStore } from "@/stores/excel-store";
import { useSessionStore } from "@/stores/session-store";
import { useWorkbookConversationStore, workbookViewKey } from "@/stores/workbook-conversation-store";
import type { Session } from "@/lib/types";

describe("Jev sheet context", () => {
  beforeEach(() => {
    useSessionStore.setState({ sessions: [{ id: "s1", workspaceId: "w1" }] as Session[] });
    useWorkbookConversationStore.setState({ targets: {}, views: {} });
    useExcelStore.setState({ activeWorkspaceKey: "id:w1", panelOpen: true,
      activeFilePath: "./sales.xlsx", activeSheet: "明细", fullViewPath: null, draftRange: null,
      liveSelection: null });
  });

  it("sends only the visible workbook identity", () => {
    expect(buildJevSheetContext("s1")).toEqual({ workspace_id: "w1", path: "./sales.xlsx", sheet: "明细", range: "" });
    expect(buildJevSheetContext()).toBeUndefined();
  });

  it("drops another workspace's view and closed panels", () => {
    useExcelStore.setState({ activeWorkspaceKey: "id:w2" });
    expect(buildJevSheetContext("s1")).toBeUndefined();
    useExcelStore.setState({ activeWorkspaceKey: "id:w1", panelOpen: false });
    expect(buildJevSheetContext("s1")).toBeUndefined();
  });

  it("does not reuse a draft from a different workbook or sheet", () => {
    useExcelStore.setState({ draftRange: { path: "other.xlsx", sheet: "明细", range: "B2" } });
    expect(buildJevSheetContext("s1")?.range).toBe("");
    useExcelStore.setState({ draftRange: { path: "sales.xlsx", sheet: "明细", range: "B2:B5" } });
    expect(buildJevSheetContext("s1")?.range).toBe("B2:B5");
  });

  it("falls back to the live selection reported by the grid", () => {
    useExcelStore.setState({ liveSelection: { path: "sales.xlsx", sheet: "明细", range: "C2:C9" } });
    expect(buildJevSheetContext("s1")?.range).toBe("C2:C9");
    useExcelStore.setState({ liveSelection: { path: "sales.xlsx", sheet: "汇总", range: "C2:C9" } });
    expect(buildJevSheetContext("s1")?.range).toBe("");
    useExcelStore.setState({
      liveSelection: { path: "sales.xlsx", sheet: "明细", range: "C2:C9" },
      draftRange: { path: "sales.xlsx", sheet: "明细", range: "B2" },
    });
    expect(buildJevSheetContext("s1")?.range).toBe("B2");
  });

  it("uses the session's ready workbook and normal selection", () => {
    const file = { workspaceId: "w1", workspaceKey: "id:w1", relative: "bound.xlsx" };
    useWorkbookConversationStore.getState().bind("s1", file, "汇总");
    useWorkbookConversationStore.getState().observe("s1", file, { status: "ready", sheet: "汇总", range: "C4:D8" });
    expect(buildJevSheetContext("s1")).toEqual({ workspace_id: "w1", path: "bound.xlsx", sheet: "汇总", range: "C4:D8" });
    useWorkbookConversationStore.getState().observe("s1", file, { status: "ready", sheet: "汇总", range: "A2" });
    expect(useWorkbookConversationStore.getState().views[workbookViewKey("s1", file)].range).toBe("A2");
    expect(buildJevSheetContext("s1")?.range).toBe("A2");
    useWorkbookConversationStore.getState().observe("s1", file, { status: "ready", sheet: "汇总" });
    expect(buildJevSheetContext("s1")?.range).toBe("A2");
    useWorkbookConversationStore.getState().observe("s1", file, { status: "ready", sheet: "汇总", range: undefined });
    expect(buildJevSheetContext("s1")?.range).toBe("");
    useWorkbookConversationStore.getState().observe("s1", file, { status: "loading" });
    expect(buildJevSheetContext("s1")).toBeUndefined();
  });
});
