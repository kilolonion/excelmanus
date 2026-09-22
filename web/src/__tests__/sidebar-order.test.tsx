// @vitest-environment jsdom
import { act, type ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { SessionList } from "@/components/sidebar/SessionList";
import { useSessionStore } from "@/stores/session-store";
import { fetchWorkspaces, reorderWorkspaces } from "@/lib/api";
import { applySidebarOrder, moveSidebarItem } from "@/lib/sidebar-order";

vi.mock("@/lib/api", () => ({ fetchWorkspaces: vi.fn(), reorderWorkspaces: vi.fn() }));
vi.mock("@/lib/session-actions", () => ({ createOrReuseSession: vi.fn() }));
vi.mock("@/lib/chat-actions", () => ({ stopGeneration: vi.fn() }));
vi.mock("@/stores/chat-store", () => ({ useChatStore: (selector: (state: object) => unknown) => selector({}) }));
vi.mock("@/components/sidebar/AddWorkspaceDialog", () => ({ AddWorkspaceDialog: () => null }));
vi.mock("@/components/ui/scroll-area", () => ({ ScrollArea: ({ children }: { children: ReactNode }) => <div>{children}</div> }));
vi.mock("@tanstack/react-virtual", () => ({
  defaultRangeExtractor: () => [],
  useVirtualizer: ({ count, getItemKey }: { count: number; getItemKey: (index: number) => string }) => ({
    getTotalSize: () => count * 44, measureElement: () => {},
    getVirtualItems: () => Array.from({ length: count }, (_, index) => ({ index, key: getItemKey(index), start: index * 44 })),
  }),
}));

const workspaces = ["a", "b", "c"].map((id) => ({ id, path: `/${id}`, title: `Workspace ${id}` }));
const sessions = ["a1", "a2", "a3", "b1"].map((id) => ({
  id, title: id === "a2" ? "Hidden" : `Chat ${id}`, workspaceId: id[0], messageCount: 0, inFlight: false,
}));
const row = (key: string) => document.querySelector(`[data-sidebar-row="${key}"]`) as HTMLElement;
const card = (key: string) => row(key).querySelector('[draggable="true"]') as HTMLElement;
const keys = () => [...document.querySelectorAll("[data-sidebar-row]")].map((element) => element.getAttribute("data-sidebar-row"));
const transfer = () => ({ setData: vi.fn(), setDragImage: vi.fn(), effectAllowed: "", dropEffect: "" });

async function mount() {
  render(<SessionList />);
  await screen.findByText("Workspace a");
}

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  useSessionStore.setState({ sessions, sidebarSessionOrder: {}, activeSessionId: null });
  vi.mocked(fetchWorkspaces).mockResolvedValue(workspaces);
  vi.mocked(reorderWorkspaces).mockResolvedValue([]);
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({ top: 0, left: 0, width: 300, height: 44, bottom: 44, right: 300, x: 0, y: 0, toJSON: () => ({}) });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe("sidebar ordering", () => {
  it("handles adjacent downward moves and both ends without mutating the input", () => {
    expect(moveSidebarItem(workspaces, "a", "b", "after").map((w) => w.id)).toEqual(["b", "a", "c"]);
    expect(moveSidebarItem(workspaces, "a", "c", "after").map((w) => w.id)).toEqual(["b", "c", "a"]);
    expect(moveSidebarItem(workspaces, "c", "a", "before").map((w) => w.id)).toEqual(["c", "a", "b"]);
    expect(moveSidebarItem(workspaces, "a", "b", "before")).toBe(workspaces);
    expect(workspaces.map((w) => w.id)).toEqual(["a", "b", "c"]);
  });

  it("drags only the workspace header, freezes its children, and moves them together on drop", async () => {
    await mount();
    expect(document.querySelector("[data-drag-frozen]")).toBeNull();
    const dataTransfer = transfer();
    const original = keys();
    fireEvent.dragStart(card("group:a"), { dataTransfer, clientX: 20, clientY: 10 });
    const preview = dataTransfer.setDragImage.mock.calls[0][0] as HTMLElement;
    expect(preview.textContent).toContain("Workspace a");
    expect(preview.textContent).not.toContain("Chat a1");
    expect(row("group:a").dataset.dragFrozen).toBe("true");
    expect(row("a:a1").dataset.dragFrozen).toBe("true");
    expect(row("b:b1").dataset.dragFrozen).toBeUndefined();
    expect(fireEvent.dragEnter(row("b:b1"), { dataTransfer })).toBe(false);
    fireEvent.dragOver(row("b:b1"), { dataTransfer, clientY: 40 });
    expect(keys()).toEqual(original);
    expect(row("b:b1").querySelector('[data-drop-indicator="after"]')).not.toBeNull();
    fireEvent.drop(row("b:b1"), { dataTransfer, clientY: 40 });
    await waitFor(() => expect(reorderWorkspaces).toHaveBeenCalledWith(["b", "a", "c"]));
    expect(keys().slice(0, 6)).toEqual(["group:b", "b:b1", "group:a", "a:a1", "a:a2", "a:a3"]);
    expect(preview.isConnected).toBe(false);
  });

  it("cancels without moving anything or saving", async () => {
    await mount();
    const original = keys();
    const dataTransfer = transfer();
    fireEvent.dragStart(card("group:a"), { dataTransfer });
    fireEvent.dragOver(row("group:c"), { dataTransfer, clientY: 40 });
    fireEvent.dragEnd(card("group:a"), { dataTransfer });
    expect(keys()).toEqual(original);
    expect(reorderWorkspaces).not.toHaveBeenCalled();
    expect(document.querySelector("[data-drag-frozen]")).toBeNull();
    expect(document.querySelector("[data-drop-indicator]")).toBeNull();
  });

  it("restores the previous workspace order when saving fails", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    vi.mocked(reorderWorkspaces).mockRejectedValue(new Error("offline"));
    await mount();
    const original = keys();
    const dataTransfer = transfer();
    fireEvent.dragStart(card("group:a"), { dataTransfer });
    fireEvent.drop(row("group:c"), { dataTransfer, clientY: 40 });
    await screen.findByRole("alert");
    expect(keys()).toEqual(original);
  });

  it("preserves hidden conversations and persists manual order through polling and hydration", async () => {
    await mount();
    fireEvent.change(screen.getByPlaceholderText("搜索对话…"), { target: { value: "Chat" } });
    const dataTransfer = transfer();
    fireEvent.dragStart(card("a:a1"), { dataTransfer });
    fireEvent.drop(row("a:a3"), { dataTransfer, clientY: 40 });
    expect(useSessionStore.getState().sidebarSessionOrder.a).toEqual(["a2", "a3", "a1"]);
    fireEvent.change(screen.getByPlaceholderText("搜索对话…"), { target: { value: "" } });
    act(() => useSessionStore.getState().mergeSessions(sessions.map((s) => ({ ...s, updatedAt: s.id === "a1" ? "2099" : "2026" }))));
    expect(keys().slice(1, 4)).toEqual(["a:a2", "a:a3", "a:a1"]);
    const saved = localStorage.getItem("excelmanus-sessions")!;
    act(() => useSessionStore.setState({ sidebarSessionOrder: {} }));
    localStorage.setItem("excelmanus-sessions", saved);
    await act(async () => { await useSessionStore.persist.rehydrate(); });
    expect(keys().slice(1, 4)).toEqual(["a:a2", "a:a3", "a:a1"]);
    expect(applySidebarOrder([{ id: "new" }, ...sessions], useSessionStore.getState().sidebarSessionOrder.a)[0].id).toBe("new");
  });

  it("rejects a conversation drop into another workspace", async () => {
    await mount();
    const dataTransfer = transfer();
    fireEvent.dragStart(card("a:a1"), { dataTransfer });
    fireEvent.drop(row("b:b1"), { dataTransfer, clientY: 40 });
    expect(useSessionStore.getState().sidebarSessionOrder).toEqual({});
    expect(reorderWorkspaces).not.toHaveBeenCalled();
  });

  it("keeps rows stationary if a session refresh arrives during a drag", async () => {
    await mount();
    const original = keys();
    const dataTransfer = transfer();
    fireEvent.dragStart(card("group:a"), { dataTransfer });
    act(() => useSessionStore.getState().setSessions([...sessions].reverse()));
    expect(keys()).toEqual(original);
    fireEvent.dragEnd(card("group:a"), { dataTransfer });
    expect(keys().slice(1, 4)).toEqual(["a:a3", "a:a2", "a:a1"]);
  });

  it("also saves the order of ungrouped conversations", async () => {
    useSessionStore.setState({ sessions: sessions.map((s) => ({ ...s, workspaceId: null })) });
    await mount();
    const dataTransfer = transfer();
    fireEvent.dragStart(card("__ungrouped__:a1"), { dataTransfer });
    fireEvent.drop(row("__ungrouped__:a3"), { dataTransfer, clientY: 40 });
    expect(useSessionStore.getState().sidebarSessionOrder.__ungrouped__).toEqual(["a2", "a3", "a1", "b1"]);
  });
});
