import { beforeEach, describe, expect, it } from "vitest";
import { activateChatWorkspaceTab } from "@/lib/chat-workspace-tabs";
import { toggleWorkbookPanelView } from "@/components/excel/WorkbookPanelButton";
import { workspaceLayout } from "@/lib/workspace-surface";
import { fileRefFromSession } from "@/lib/workspace-file-ref";
import { useExcelStore } from "@/stores/excel-store";
import { useSessionStore } from "@/stores/session-store";
import { useWordStore } from "@/stores/word-store";
import { useWorkbookConversationStore } from "@/stores/workbook-conversation-store";
import type { Session } from "@/lib/types";

const session: Session = { id: "s1", workspaceId: "w1", title: "Book", messageCount: 0, inFlight: false };
const path = "uploads/book.xlsx";

beforeEach(() => {
  useSessionStore.setState({ activeSessionId: session.id, sessions: [session] });
  useExcelStore.setState({ activeWorkspaceKey: "id:w1", activeFilePath: path, activeSheet: "订单",
    fullViewPath: null, fullViewSheet: null, fullViewLayout: "embedded", panelOpen: false, panelTab: "sheet",
    compareMode: false, recentFiles: [], workspaceFiles: [] });
  useWordStore.setState({ panelOpen: false, fullViewPath: null });
  useWorkbookConversationStore.setState({ targets: {}, views: {}, pickerOpen: false, pickerLayout: "embedded" });
});

describe("spreadsheet entry layouts", () => {
  it("opens the top sheet tab in the main area and keeps the composer below", () => {
    activateChatWorkspaceTab("sheet");
    const excel = useExcelStore.getState();
    expect(excel.fullViewPath).toBe(path);
    expect(excel.fullViewLayout).toBe("embedded");
    expect(workspaceLayout("excel", excel.fullViewLayout, false)).toEqual({
      split: false, chatVisible: false, composerVisible: true, fullHeightSheet: false,
    });
  });

  it("uses the old side-panel entry to open a single grid alongside the conversation", () => {
    toggleWorkbookPanelView(false);
    const excel = useExcelStore.getState();
    expect(excel.panelOpen).toBe(false);
    expect(excel.fullViewPath).toBe(path);
    expect(workspaceLayout("excel", excel.fullViewLayout, false)).toEqual({
      split: true, chatVisible: true, composerVisible: true, fullHeightSheet: true,
    });
    expect(useWorkbookConversationStore.getState().targets.s1.layout).toBe("split");
  });

  it("switches layouts without changing the viewed workbook, sheet, or version", () => {
    activateChatWorkspaceTab("sheet");
    const file = fileRefFromSession(path, session);
    useWorkbookConversationStore.getState().observe("s1", file, { status: "ready", sheet: "产品", version: "sha256:aaaa" });
    toggleWorkbookPanelView(false);
    expect(useExcelStore.getState()).toMatchObject({ fullViewPath: path, fullViewSheet: "产品", fullViewLayout: "split" });
    activateChatWorkspaceTab("sheet");
    expect(useExcelStore.getState()).toMatchObject({ fullViewPath: path, fullViewSheet: "产品", fullViewLayout: "embedded" });
    const target = useWorkbookConversationStore.getState().targets.s1;
    expect(target).toMatchObject({ sheet: "产品", layout: "embedded" });
    expect(Object.values(useWorkbookConversationStore.getState().views)[0]).toMatchObject({ status: "ready", version: "sha256:aaaa" });
  });

  it("closes the split view on a second side-panel click while retaining its discussion target", () => {
    toggleWorkbookPanelView(false);
    toggleWorkbookPanelView(false);
    expect(useExcelStore.getState().fullViewPath).toBeNull();
    expect(useWorkbookConversationStore.getState().targets.s1).toMatchObject({ showSheet: false, layout: "split" });
  });

  it("returns from history to the sheet before closing the split view", () => {
    toggleWorkbookPanelView(false);
    useExcelStore.getState().setPanelTab("history");
    toggleWorkbookPanelView(false);
    expect(useExcelStore.getState()).toMatchObject({ fullViewPath: path, fullViewLayout: "split", panelTab: "sheet" });
  });

  it("remembers which entry opened the file picker", () => {
    useExcelStore.setState({ activeFilePath: null });
    toggleWorkbookPanelView(false);
    expect(useWorkbookConversationStore.getState()).toMatchObject({ pickerOpen: true, pickerLayout: "split" });
    useWorkbookConversationStore.getState().closePicker();
    activateChatWorkspaceTab("sheet");
    expect(useWorkbookConversationStore.getState()).toMatchObject({ pickerOpen: true, pickerLayout: "embedded" });
  });

  it("keeps mobile in a single view with a working return-to-chat toggle", () => {
    toggleWorkbookPanelView(true);
    expect(useExcelStore.getState().fullViewLayout).toBe("embedded");
    expect(workspaceLayout("excel", "split", true)).toEqual({ split: false, chatVisible: false, composerVisible: false, fullHeightSheet: true });
    toggleWorkbookPanelView(true);
    expect(useExcelStore.getState().fullViewPath).toBeNull();
  });
});
