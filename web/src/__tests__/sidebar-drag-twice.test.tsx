// @vitest-environment jsdom
import { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, createEvent, fireEvent, render, screen } from "@testing-library/react";
import { SessionList } from "@/components/sidebar/SessionList";
import { useSessionStore } from "@/stores/session-store";
import { fetchWorkspaces, reorderWorkspaces } from "@/lib/api";

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
  id, title: `Chat ${id}`, workspaceId: id[0], messageCount: 0, inFlight: false,
}));
const row = (key: string) => document.querySelector(`[data-sidebar-row="${key}"]`) as HTMLElement;
const card = (key: string) => row(key).querySelector('[draggable="true"]') as HTMLElement;
const keys = () => [...document.querySelectorAll("[data-sidebar-row]")].map((el) => el.getAttribute("data-sidebar-row"));
const transfer = () => ({ setData: vi.fn(), setDragImage: vi.fn(), effectAllowed: "", dropEffect: "" });
// jsdom 没有 DragEvent：fireEvent.drop/dragOver 会退化为普通 Event 并丢弃 clientY，
// 需要 createEvent 后手动注入，否则落点方向（before/after）无法验证。
const fireDrag = (el: HTMLElement, type: "drop" | "dragOver", dt: ReturnType<typeof transfer>, clientY: number) => {
  const ev = type === "drop" ? createEvent.drop(el) : createEvent.dragOver(el);
  Object.defineProperty(ev, "dataTransfer", { value: dt });
  Object.defineProperty(ev, "clientY", { value: clientY });
  fireEvent(el, ev);
};

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

describe("repeated sidebar drags", () => {
  it.each(["dragend", "outside-drop", "next-pointer"])(
    "releases an interrupted drag on %s and allows the next reorder",
    async (ending) => {
      await mount();
      const dt = transfer();
      fireEvent.dragStart(card("a:a1"), { dataTransfer: dt });
      fireDrag(row("a:a3"), "dragOver", dt, 40);
      const preview = dt.setDragImage.mock.calls[0][0] as HTMLElement;
      expect(preview.isConnected).toBe(true);

      // The native drag may finish outside the React list, or fail to emit
      // dragend after its source moves. The next gesture must still work.
      if (ending === "dragend") fireEvent.dragEnd(window);
      else if (ending === "outside-drop") fireEvent.drop(document.body);
      else fireEvent.pointerDown(card("a:a2"));

      expect(preview.isConnected).toBe(false);
      expect(document.querySelector("[data-drag-frozen]")).toBeNull();
      expect(document.querySelector("[data-drop-indicator]")).toBeNull();
      expect(useSessionStore.getState().sidebarSessionOrder).toEqual({});

      const next = transfer();
      fireEvent.pointerDown(card("a:a2"));
      fireEvent.dragStart(card("a:a2"), { dataTransfer: next });
      fireDrag(row("a:a3"), "drop", next, 40);
      expect(useSessionStore.getState().sidebarSessionOrder.a).toEqual(["a1", "a3", "a2"]);
      expect(document.querySelector("[data-drag-frozen]")).toBeNull();
    },
  );

  it("discards an old preview when a new native drag starts without dragend", async () => {
    await mount();
    const first = transfer();
    fireEvent.dragStart(card("a:a1"), { dataTransfer: first });
    const preview = first.setDragImage.mock.calls[0][0] as HTMLElement;
    const second = transfer();
    fireEvent.dragStart(card("a:a2"), { dataTransfer: second });
    expect(preview.isConnected).toBe(false);
    fireDrag(row("a:a3"), "drop", second, 40);
    expect(useSessionStore.getState().sidebarSessionOrder.a).toEqual(["a1", "a3", "a2"]);
    expect((second.setDragImage.mock.calls[0][0] as HTMLElement).isConnected).toBe(false);
  });

  it("still blocks menu-button drags and lets the next card gesture start", async () => {
    await mount();
    const source = card("a:a1");
    const blocked = transfer();
    fireEvent.pointerDown(source.querySelector("button")!);
    expect(fireEvent.dragStart(source, { dataTransfer: blocked })).toBe(false);
    expect(blocked.setDragImage).not.toHaveBeenCalled();
    fireEvent.pointerDown(source);
    const next = transfer();
    fireEvent.dragStart(source, { dataTransfer: next });
    fireDrag(row("a:a3"), "drop", next, 40);
    expect(useSessionStore.getState().sidebarSessionOrder.a).toEqual(["a2", "a3", "a1"]);
  });

  it("allows a second session drag after the first one completes", async () => {
    await mount();
    const dt1 = transfer();
    // First drag: a1 -> after a3  =>  [a2, a3, a1]
    fireEvent.dragStart(card("a:a1"), { dataTransfer: dt1 });
    fireDrag(row("a:a3"), "dragOver", dt1, 40);
    fireDrag(row("a:a3"), "drop", dt1, 40);
    fireEvent.dragEnd(card("a:a1"), { dataTransfer: dt1 });
    expect(useSessionStore.getState().sidebarSessionOrder.a).toEqual(["a2", "a3", "a1"]);
    expect(keys().slice(1, 4)).toEqual(["a:a2", "a:a3", "a:a1"]);

    // Second drag: a1 -> before a2  =>  [a1, a2, a3]
    const dt2 = transfer();
    fireEvent.dragStart(card("a:a1"), { dataTransfer: dt2 });
    fireDrag(row("a:a2"), "dragOver", dt2, 10);
    fireDrag(row("a:a2"), "drop", dt2, 10);
    fireEvent.dragEnd(card("a:a1"), { dataTransfer: dt2 });
    expect(useSessionStore.getState().sidebarSessionOrder.a).toEqual(["a1", "a2", "a3"]);
    expect(keys().slice(1, 4)).toEqual(["a:a1", "a:a2", "a:a3"]);
  });
});
