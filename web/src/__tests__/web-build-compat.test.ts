import { afterEach, expect, it, vi } from "vitest";
import { fetchWebBuild } from "@/lib/web-version";

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers(); });

it("checks builds on older WebViews without AbortSignal.timeout", async () => {
  vi.spyOn(AbortSignal, "timeout").mockImplementation(() => { throw new Error("not supported"); });
  const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ buildId: "new" })));
  vi.stubGlobal("fetch", fetcher);
  expect(await fetchWebBuild()).toBe("new");
  expect(fetcher.mock.calls[0][1].cache).toBe("no-store");
});

it("aborts a stalled version probe and releases its timer", async () => {
  vi.useFakeTimers();
  vi.stubGlobal("fetch", vi.fn((_url, { signal }) => new Promise((_resolve, reject) => {
    signal.addEventListener("abort", () => reject(new Error("aborted")));
  })));
  const assertion = expect(fetchWebBuild()).rejects.toThrow("aborted");
  await vi.advanceTimersByTimeAsync(5000);
  await assertion;
  expect(vi.getTimerCount()).toBe(0);
});
