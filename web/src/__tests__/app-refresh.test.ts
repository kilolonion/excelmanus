import { afterEach, expect, it, vi } from "vitest";
const mocks = vi.hoisted(() => ({ dirty: vi.fn(), sessions: vi.fn() }));
vi.mock("@/lib/excel-cell-edit", () => ({ hasUnsavedWorkbookEdits: mocks.dirty }));
vi.mock("@/stores/session-store", () => ({ useSessionStore: { getState: mocks.sessions } }));
import { refreshApp } from "@/lib/app-refresh";
afterEach(() => vi.unstubAllGlobals());
it.each(["unsaved", "running", "ready"])("refresh is safe with %s work", (state) => {
  const reload = vi.fn();
  vi.stubGlobal("window", { location: { reload } });
  mocks.dirty.mockReturnValue(state === "unsaved");
  mocks.sessions.mockReturnValue({ sessions: [{ inFlight: state === "running" }] });
  expect(refreshApp() === null).toBe(state === "ready");
  expect(reload).toHaveBeenCalledTimes(state === "ready" ? 1 : 0);
});
