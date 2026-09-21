// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { cleanup, fireEvent, render } from "@testing-library/react";
import { ChatLiveSelectionChip } from "@/components/chat/ChatLiveSelectionChip";
import { useExcelStore } from "@/stores/excel-store";
import { useSessionStore } from "@/stores/session-store";
import { useWorkbookConversationStore } from "@/stores/workbook-conversation-store";
import type { Session } from "@/lib/types";

vi.mock("@/lib/excel-cell-edit", () => ({
  flushWorkbookEdits: vi.fn(),
  hasPendingWorkbookEdits: vi.fn(() => false),
  isWorkbookEditPaused: vi.fn(() => false),
  subscribeWorkbookEdits: () => () => {},
}));

const session: Session = { id: "s1", title: "Sales", workspaceId: "w1", messageCount: 0, inFlight: false };

function showChip(path = "sales.xlsx") {
  const rendered = render(<ChatLiveSelectionChip />);
  act(() => {
    useExcelStore.setState({
      liveSelection: { path, sheet: "明细", range: "C2:C9", contentVersion: "v1" },
    });
  });
  return rendered;
}

beforeEach(() => {
  cleanup();
  useSessionStore.setState({ activeSessionId: session.id, sessions: [session] });
  useWorkbookConversationStore.setState({ targets: {}, views: {}, pickerOpen: false });
  useExcelStore.setState({
    activeWorkspaceKey: "id:w1",
    fullViewPath: "sales.xlsx",
    fullViewSheet: "明细",
    panelOpen: false,
    activeFilePath: null,
    selectionMode: false,
    pendingSelection: null,
    pendingTemplateMessage: null,
    liveSelection: null,
  });
});

describe("ChatLiveSelectionChip", () => {
  it("renders the live selection and wires reference / analyze actions", () => {
    const { container } = showChip();
    const chip = container.querySelector("[data-em-live-selection]");
    expect(chip?.getAttribute("data-em-live-selection")).toBe("明细!C2:C9");

    fireEvent.click(container.querySelector('[data-em-live-selection-action="reference"]')!);
    expect(useExcelStore.getState().pendingSelection).toEqual({
      filePath: "sales.xlsx", sheet: "明细", range: "C2:C9", contentVersion: "v1",
    });

    fireEvent.click(container.querySelector('[data-em-live-selection-action="analyze"]')!);
    const message = useExcelStore.getState().pendingTemplateMessage;
    expect(message).toContain("C2:C9");
    expect(message?.startsWith("请分析")).toBe(true);
  });

  it("renders nothing in selection mode or for a different file", () => {
    act(() => { useExcelStore.setState({ selectionMode: true }); });
    expect(showChip().container.querySelector("[data-em-live-selection]")).toBeNull();

    act(() => { useExcelStore.setState({ selectionMode: false }); });
    expect(showChip("other.xlsx").container.querySelector("[data-em-live-selection]")).toBeNull();
  });
});
