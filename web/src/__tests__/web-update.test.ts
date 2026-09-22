import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ get: vi.fn(), refresh: vi.fn(), build: vi.fn() }));
vi.mock("@/lib/api", () => ({ apiGet: mocks.get }));
vi.mock("@/lib/app-refresh", () => ({ refreshApp: mocks.refresh }));
vi.mock("@/lib/web-version", async (original) => ({ ...await original<object>(), fetchWebBuild: mocks.build }));
import { useConnectionStore, upgradeResultForRequest } from "@/stores/connection-store";
import { versionDifference } from "@/lib/web-version";

beforeEach(() => {
  vi.useFakeTimers();
  mocks.get.mockReset();
  mocks.refresh.mockReset().mockReturnValue(null);
  mocks.build.mockReset().mockResolvedValue("web-new");
  useConnectionStore.getState().reset();
});
afterEach(() => { useConnectionStore.getState().reset(); vi.useRealTimers(); });

describe("web update completion", () => {
  it("does not accept a previous update's result", () => {
    expect(upgradeResultForRequest({ request_id: "old", ok: true }, "new")).toBe("pending");
  });
  it("shows matching failure without refreshing even when service is up", async () => {
    mocks.get.mockResolvedValue({ request_id: "new", ok: false, error: "build failed" });
    await useConnectionStore.getState().triggerRestart("update", { upgradeRequestId: "new" });
    expect(useConnectionStore.getState().restartError).toBe("build failed");
    expect(mocks.refresh).not.toHaveBeenCalled();
  });
  it("waits for matching success and both services, then refreshes", async () => {
    let ready = false;
    mocks.get.mockImplementation(async (url: string) => url === "/health" ? { status: "ok" } : { request_id: ready ? "new" : "old", ok: true });
    const running = useConnectionStore.getState().triggerRestart("update", { upgradeRequestId: "new" });
    await vi.advanceTimersByTimeAsync(1000);
    expect(mocks.refresh).not.toHaveBeenCalled();
    ready = true;
    await vi.advanceTimersByTimeAsync(1500);
    await running;
    expect(mocks.refresh).toHaveBeenCalledOnce();
  });
  it("upgrades an already-running generic restart to request-specific monitoring", async () => {
    mocks.get.mockImplementation(async (url: string) => url === "/health" ? { status: "draining" } : { request_id: "new", ok: false, error: "update failed" });
    const old = useConnectionStore.getState().triggerRestart("draining");
    const current = useConnectionStore.getState().triggerRestart("update", { upgradeRequestId: "new" });
    await vi.advanceTimersByTimeAsync(10000);
    await Promise.all([old, current]);
    expect(useConnectionStore.getState().restartError).toBe("update failed");
    expect(mocks.refresh).not.toHaveBeenCalled();
  });
  it("does not refresh after the user returns to the page", async () => {
    mocks.get.mockImplementation(async (url: string) => url === "/health" ? { status: "ok" } : { request_id: "new", ok: true });
    const running = useConnectionStore.getState().triggerRestart("update", { upgradeRequestId: "new" });
    await vi.advanceTimersByTimeAsync(100);
    useConnectionStore.getState().reset();
    await vi.advanceTimersByTimeAsync(1000);
    await running;
    expect(mocks.refresh).not.toHaveBeenCalled();
  });
  it("shows a save reminder when refreshing would discard edits", async () => {
    mocks.get.mockImplementation(async (url: string) => url === "/health" ? { status: "ok" } : { request_id: "new", ok: true });
    mocks.refresh.mockReturnValue("请先保存表格");
    const running = useConnectionStore.getState().triggerRestart("update", { upgradeRequestId: "new" });
    await vi.advanceTimersByTimeAsync(1000);
    await running;
    expect(useConnectionStore.getState().restartError).toBe("请先保存表格");
  });
});

it("detects backend changes with an unchanged web build, and schema rollback", () => {
  const baseline = { buildId: "same", fingerprint: "old", apiSchemaVersion: 2 };
  expect(versionDifference(baseline, { ...baseline, fingerprint: "new" }).changed).toBe(true);
  expect(versionDifference(baseline, { ...baseline, buildId: "new" }).changed).toBe(true);
  expect(versionDifference(baseline, { ...baseline, apiSchemaVersion: 1 }).incompatible).toBe(true);
});
