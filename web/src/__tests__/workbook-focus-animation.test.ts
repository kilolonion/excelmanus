// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ISelectionStyle } from "@univerjs/sheets";
import { highlightWorkbookRanges } from "@/lib/workbook-focus";

const frames = new Map<number, FrameRequestCallback>();
const overlays: { dispose: () => void }[] = [];
let sequence = 0;
let reduced: EventTarget & { matches: boolean };

function frame(now: number) {
  const pending = [...frames.values()];
  frames.clear();
  pending.forEach((callback) => callback(now));
}

function fixture() {
  const control = () => ({ updateStyle: vi.fn(), setEvent: vi.fn() });
  const marks = new Map<string, { selection: { style: Partial<ISelectionStyle> }; control: ReturnType<typeof control> }>();
  const sheet = {
    getRange: (address: string) => address,
    highlightRanges: vi.fn((ranges: string[], style: Partial<ISelectionStyle>) => {
      const ids = ranges.map(() => String(++sequence));
      ids.forEach((id) => marks.set(id, { selection: { style }, control: control() }));
      return { dispose: vi.fn(() => ids.forEach((id) => marks.delete(id))) };
    }),
  };
  const isActive = vi.fn(() => true);
  const registry = { getShapeMap: () => marks };
  const start = () => {
    const overlay = highlightWorkbookRanges(sheet, ["A1:B3", "D1:D3"], "planned", { registry, isActive });
    overlays.push(overlay);
    return overlay;
  };
  return { marks, sheet, isActive, start, control };
}

beforeEach(() => {
  reduced = Object.assign(new EventTarget(), { matches: false });
  vi.stubGlobal("matchMedia", vi.fn(() => reduced));
  vi.stubGlobal("requestAnimationFrame", vi.fn((callback: FrameRequestCallback) => {
    const id = ++sequence; frames.set(id, callback); return id;
  }));
  vi.stubGlobal("cancelAnimationFrame", vi.fn((id: number) => frames.delete(id)));
  vi.spyOn(document, "hidden", "get").mockReturnValue(false);
});

afterEach(() => {
  overlays.splice(0).forEach((overlay) => overlay.dispose());
  frames.clear();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("workbook breathing focus", () => {
  it("animates only its own native marks, reuses shapes and keeps cell contents legible", () => {
    const f = fixture();
    const unrelated = { selection: { style: { fill: "red" } }, control: f.control() };
    f.marks.set("copy-selection", unrelated);
    const overlay = f.start();
    frame(0);
    const outline = [...f.marks.values()].at(-1)!;
    const peak = { ...outline.selection.style };
    frame(1400);
    expect(outline.selection.style.stroke).not.toBe(peak.stroke);
    expect(Number(peak.fill!.split(",").at(-1)!.replace(")", ""))).toBeLessThan(0.1);
    expect(f.sheet.highlightRanges).toHaveBeenCalledTimes(2);
    expect(unrelated.control.updateStyle).not.toHaveBeenCalled();
    expect(outline.control.setEvent).toHaveBeenLastCalledWith(false);
    overlay.dispose(); overlay.dispose();
    expect(frames.size).toBe(0);
    expect([...f.marks.keys()]).toEqual(["copy-selection"]);
    f.sheet.highlightRanges.mock.results.forEach(({ value }) => expect(value.dispose).toHaveBeenCalledOnce());
  });

  it("uses a static focus for reduced motion and responds to preference changes", () => {
    reduced.matches = true;
    const f = fixture(); f.start();
    expect(frames.size).toBe(0);
    const outline = [...f.marks.values()].at(-1)!;
    const steady = { ...outline.selection.style };
    reduced.matches = false; reduced.dispatchEvent(new Event("change"));
    expect(frames.size).toBe(1);
    frame(0);
    expect(outline.selection.style).not.toEqual(steady);
    reduced.matches = true; reduced.dispatchEvent(new Event("change"));
    expect(frames.size).toBe(0);
    expect(outline.selection.style).toEqual(steady);
  });

  it("pauses while the page is hidden and cleans up listeners on disposal", () => {
    const f = fixture(); const overlay = f.start();
    vi.spyOn(document, "hidden", "get").mockReturnValue(true);
    document.dispatchEvent(new Event("visibilitychange"));
    expect(frames.size).toBe(0);
    vi.spyOn(document, "hidden", "get").mockReturnValue(false);
    document.dispatchEvent(new Event("visibilitychange"));
    expect(frames.size).toBe(1);
    overlay.dispose();
    reduced.dispatchEvent(new Event("change"));
    document.dispatchEvent(new Event("visibilitychange"));
    expect(frames.size).toBe(0);
  });

  it("updates replacement controls after native layout refresh", () => {
    const f = fixture(); f.start(); frame(0);
    const mark = [...f.marks.values()][0];
    const oldControl = mark.control;
    oldControl.updateStyle.mockClear();
    mark.control = f.control();
    frame(100);
    expect(oldControl.updateStyle).not.toHaveBeenCalled();
    expect(mark.control.updateStyle).toHaveBeenCalledOnce();
  });

  it.each(["sheet switch", "native removal"])("stops painting after %s", (reason) => {
    const f = fixture(); f.start(); frame(0);
    if (reason === "sheet switch") f.isActive.mockReturnValue(false);
    else f.marks.clear();
    frame(100);
    expect(frames.size).toBe(0);
    expect(f.marks.size).toBe(0);
  });

  it("removes a partial halo if creating the outline fails", () => {
    const f = fixture();
    f.sheet.highlightRanges.mockImplementationOnce((ranges, style) => {
      f.marks.set("halo", { selection: { style }, control: f.control() });
      return { dispose: vi.fn(() => { f.marks.clear(); }) };
    }).mockImplementationOnce(() => { throw new Error("outline failed"); });
    expect(() => f.start()).toThrow("outline failed");
    expect(f.marks.size).toBe(0);
    expect(frames.size).toBe(0);
  });
});
