import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { handleWorkbookMessageSent, recordWorkbookChatNavigation } from "@/lib/workbook-chat-navigation";
import { activateChatWorkspaceTab } from "@/lib/chat-workspace-tabs";
import { useWorkbookChatPreferencesStore as preferences } from "@/stores/workbook-chat-preferences-store";
import { useExcelStore } from "@/stores/excel-store";
import { useSessionStore } from "@/stores/session-store";
import { useWordStore } from "@/stores/word-store";
import { useWorkbookConversationStore } from "@/stores/workbook-conversation-store";

vi.mock("@/lib/excel-view-prefetch", () => ({ prefetchExcelView: vi.fn() }));

const path = "uploads/sales.xlsx";
function openSheet() {
  useExcelStore.getState().openFullView(path, "销售明细");
}
function sendAndChoose(tab: "chat" | "sheet") {
  openSheet();
  handleWorkbookMessageSent("s1");
  vi.advanceTimersByTime(500);
  activateChatWorkspaceTab(tab);
}

beforeEach(() => {
  vi.useFakeTimers();
  preferences.getState().setLearnFromNavigation(true);
  preferences.getState().setAutoReturnToChat(true);
  useSessionStore.setState({ activeSessionId: "s1", sessions: [
    { id: "s1", title: "Sales", workspaceId: "w1", messageCount: 0, inFlight: false },
    { id: "s2", title: "Other", workspaceId: "w2", messageCount: 0, inFlight: false },
  ] });
  useWordStore.setState({ fullViewPath: null, panelOpen: false });
  useExcelStore.setState({ fullViewPath: null, fullViewLayout: "embedded", compareMode: false, panelOpen: false });
  useWorkbookConversationStore.setState({ targets: {}, views: {} });
  openSheet();
});

afterEach(() => {
  preferences.getState().setLearnFromNavigation(false);
  vi.clearAllTimers();
  vi.useRealTimers();
});

describe("return to chat after sending from a workbook", () => {
  it("returns immediately and preserves the workbook association and active sheet", () => {
    handleWorkbookMessageSent("s1");
    expect(useExcelStore.getState().fullViewPath).toBeNull();
    expect(useExcelStore.getState().activeFilePath).toBe(path);
    expect(useWorkbookConversationStore.getState().targets.s1).toMatchObject({
      showSheet: false, sheet: "销售明细", file: { relative: path },
    });
    expect(preferences.getState().recentChoices).toEqual([]);
    vi.advanceTimersByTime(10_000);
    expect(preferences.getState().recentChoices).toEqual([true]);
  });

  it("keeps the sheet visible when the option is disabled", () => {
    preferences.getState().setAutoReturnToChat(false);
    handleWorkbookMessageSent("s1");
    expect(useExcelStore.getState().fullViewPath).toBe(path);
    vi.advanceTimersByTime(10_000);
    expect(preferences.getState().recentChoices).toEqual([false]);
  });

  it.each(["split", "chat", "word", "compare", "workspace", "session"])("ignores %s contexts", (context) => {
    if (context === "split") useExcelStore.setState({ fullViewLayout: "split" });
    if (context === "chat") useExcelStore.getState().closeFullView();
    if (context === "word") useWordStore.setState({ fullViewPath: "notes.docx" });
    if (context === "compare") useExcelStore.setState({ compareMode: true });
    if (context === "workspace") useExcelStore.setState({ activeWorkspaceKey: "id:other" });
    const before = useExcelStore.getState().fullViewPath;
    handleWorkbookMessageSent(context === "session" ? "s2" : "s1");
    expect(useExcelStore.getState().fullViewPath).toBe(before);
    vi.advanceTimersByTime(10_000);
    expect(preferences.getState().recentChoices).toEqual([]);
  });
});

describe("learning explicit navigation habits", () => {
  it("disables after four immediate returns to the sheet in five sends, then can learn the opposite", () => {
    sendAndChoose("sheet");
    sendAndChoose("sheet");
    openSheet(); handleWorkbookMessageSent("s1"); vi.advanceTimersByTime(10_000);
    sendAndChoose("sheet");
    expect(preferences.getState().autoReturnToChat).toBe(true);
    sendAndChoose("sheet");
    expect(preferences.getState().autoReturnToChat).toBe(false);
    expect(useExcelStore.getState().fullViewPath).toBe(path);
    expect(preferences.getState().recentChoices).toEqual([]);

    for (let i = 0; i < 5; i++) sendAndChoose("chat");
    expect(preferences.getState().autoReturnToChat).toBe(true);
    expect(useExcelStore.getState().fullViewPath).toBeNull();
  });

  it("does not adapt to occasional reversals or navigation after ten seconds", () => {
    for (let i = 0; i < 8; i++) {
      openSheet(); handleWorkbookMessageSent("s1");
      if (i % 2 === 0) activateChatWorkspaceTab("sheet");
      else { vi.advanceTimersByTime(10_001); activateChatWorkspaceTab("sheet"); }
    }
    expect(preferences.getState().autoReturnToChat).toBe(true);
    expect(preferences.getState().recentChoices).toHaveLength(5);
  });

  it("counts at most one choice per send", () => {
    handleWorkbookMessageSent("s1");
    activateChatWorkspaceTab("sheet");
    activateChatWorkspaceTab("chat");
    activateChatWorkspaceTab("sheet");
    vi.advanceTimersByTime(10_000);
    expect(preferences.getState().recentChoices).toEqual([false]);
  });

  it.each(["session", "file", "automatic", "word", "settings"])("discards an observation on %s changes", (change) => {
    handleWorkbookMessageSent("s1");
    if (change === "session") {
      useSessionStore.getState().setActiveSession("s2");
      useSessionStore.getState().setActiveSession("s1");
    }
    if (change === "file") recordWorkbookChatNavigation("sheet", "other.xlsx");
    if (change === "automatic") openSheet(); // Programmatic navigation is not a user preference.
    if (change === "word") useWordStore.setState({ fullViewPath: "notes.docx" });
    if (change === "settings") preferences.getState().setAutoReturnToChat(false);
    recordWorkbookChatNavigation("sheet", path);
    vi.advanceTimersByTime(10_000);
    expect(preferences.getState().recentChoices).toEqual([]);
  });

  it("lets the user fix the preference by turning learning off", () => {
    preferences.getState().setLearnFromNavigation(false);
    for (let i = 0; i < 6; i++) sendAndChoose("sheet");
    expect(preferences.getState().autoReturnToChat).toBe(true);
    expect(preferences.getState().recentChoices).toEqual([]);
  });

  it("clears previous evidence when the user changes the setting", () => {
    sendAndChoose("sheet");
    preferences.getState().setAutoReturnToChat(false);
    expect(preferences.getState().recentChoices).toEqual([]);
  });
});
