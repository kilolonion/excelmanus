import { afterEach, beforeEach, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ get: vi.fn(), build: vi.fn(), refresh: vi.fn() }));
vi.mock("@/lib/api", () => ({ apiGet: mocks.get }));
vi.mock("@/lib/app-refresh", () => ({ refreshApp: mocks.refresh }));
vi.mock("@/lib/web-version", async (original) => ({ ...await original<object>(), fetchWebBuild: mocks.build }));

beforeEach(() => {
  vi.resetModules();
  vi.useFakeTimers();
  vi.stubEnv("NEXT_PUBLIC_WEB_BUILD_ID", "web-old");
  vi.stubGlobal("window", {});
  mocks.build.mockReset().mockResolvedValue("web-old");
  mocks.get.mockReset().mockResolvedValue({ status: "ok", version: "1", build_id: "backend-web", version_fingerprint: "api-old", api_schema_version: 1 });
});
afterEach(() => { vi.clearAllTimers(); vi.useRealTimers(); vi.unstubAllGlobals(); vi.unstubAllEnvs(); });

it("detects a frontend-only release even on the first poll", async () => {
  mocks.build.mockResolvedValue("web-new");
  const { ensureHealthHubPolling, useHealthHubStore } = await import("@/stores/health-hub-store");
  ensureHealthHubPolling();
  await vi.advanceTimersByTimeAsync(250);
  expect(useHealthHubStore.getState().newVersionAvailable).toBe(true);
});

it("dismisses one combined release without suppressing future releases", async () => {
  const { ensureHealthHubPolling, useHealthHubStore } = await import("@/stores/health-hub-store");
  ensureHealthHubPolling();
  await vi.advanceTimersByTimeAsync(250);
  expect(useHealthHubStore.getState().newVersionAvailable).toBe(false);
  mocks.build.mockResolvedValue("web-new");
  mocks.get.mockResolvedValue({ status: "ok", version: "2", build_id: "backend-web-new", version_fingerprint: "api-new", api_schema_version: 1 });
  await vi.advanceTimersByTimeAsync(15000);
  expect(useHealthHubStore.getState().newVersionAvailable).toBe(true);
  useHealthHubStore.getState().dismissVersion();
  await vi.advanceTimersByTimeAsync(30000);
  expect(useHealthHubStore.getState().newVersionAvailable).toBe(false);
  mocks.build.mockResolvedValue("web-newer");
  await vi.advanceTimersByTimeAsync(30000);
  expect(useHealthHubStore.getState().newVersionAvailable).toBe(true);
});

it("keeps refresh errors visible so unsaved work can be handled", async () => {
  const { useHealthHubStore } = await import("@/stores/health-hub-store");
  mocks.refresh.mockReturnValue("请先保存");
  useHealthHubStore.getState().refreshNow();
  expect(useHealthHubStore.getState().refreshError).toBe("请先保存");
});
