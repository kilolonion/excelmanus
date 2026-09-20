import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  fileCachePrefix,
  matchesFileCacheKey,
  normalizeExcelPath,
  snapshotCacheKey,
  viewCacheKey,
  fetchWorkbookView,
  invalidateWorkbookViewCache,
} from "@/lib/api";
import { demoWorkbookView } from "@/lib/workbook-view";

describe("workbook cache identity", () => {
  it("uses the same workspaceKey|path prefix for snapshot and view keys", () => {
    const prefix = fileCachePrefix("id:ws-a", "./report.xlsx");
    expect(prefix).toBe("id:ws-a|./report.xlsx");
    expect(snapshotCacheKey("./report.xlsx", { workspaceKey: "id:ws-a", maxRows: 500 }))
      .toBe(`${prefix}|500|1`);
    expect(viewCacheKey({
      workspaceKey: "id:ws-a",
      relative: "report.xlsx",
      version: "sha256:v1",
    })).toBe(`${prefix}|sha256:v1|*|A1:AX200|1`);
  });

  it("invalidates snapshot keys by write identity, not path-only prefix", () => {
    const key = snapshotCacheKey("report.xlsx", { workspaceKey: "id:ws-a", maxRows: 500 });
    expect(key.startsWith(`${normalizeExcelPath("report.xlsx")}|`)).toBe(false);
    expect(matchesFileCacheKey(key, { workspaceKey: "id:ws-a", relative: "./report.xlsx" })).toBe(true);
    expect(matchesFileCacheKey(key, { workspaceKey: "id:ws-b", relative: "./report.xlsx" })).toBe(false);
  });

  it("invalidates view keys with the same identity as snapshot", () => {
    const identity = { workspaceKey: "id:ws-a", relative: "./report.xlsx" };
    const snapshot = snapshotCacheKey(identity.relative, { workspaceKey: identity.workspaceKey });
    const view = viewCacheKey({
      workspaceKey: identity.workspaceKey,
      relative: identity.relative,
      version: "unknown",
    });
    expect(matchesFileCacheKey(snapshot, identity)).toBe(true);
    expect(matchesFileCacheKey(view, identity)).toBe(true);
  });
});

describe("workbook request lifecycle", () => {
  const opts = { path: "book.xlsx", workspaceKey: "id:ws", sessionId: "session", withStyles: false };
  const data = () => ({ ...demoWorkbookView("book.xlsx"), file: { workspaceKey: "id:ws", relative: "book.xlsx" } });
  beforeEach(() => invalidateWorkbookViewCache());
  afterEach(() => { invalidateWorkbookViewCache(); vi.unstubAllGlobals(); });

  it("reuses a completed open hint and promotes it to the exact version and active sheet", async () => {
    const fetcher = vi.fn(async () => new Response(JSON.stringify(data())));
    vi.stubGlobal("fetch", fetcher);
    await fetchWorkbookView(opts);
    await fetchWorkbookView({ ...opts, viewGeneration: 2 });
    await fetchWorkbookView({ ...opts, expectedVersion: "sha256:demo", sheet: "Sheet1" });
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("does not cancel a shared request while another surface still consumes it", async () => {
    let resolve!: (r: Response) => void;
    let signal!: AbortSignal;
    vi.stubGlobal("fetch", vi.fn((_url, init) => {
      signal = init.signal;
      return new Promise<Response>((r) => { resolve = r; });
    }));
    const aborter = new AbortController();
    const first = fetchWorkbookView({ ...opts, signal: aborter.signal });
    const second = fetchWorkbookView({ ...opts, viewGeneration: 9 });
    const rejected = expect(first).rejects.toMatchObject({ name: "AbortError" });
    aborter.abort();
    expect(signal.aborted).toBe(false);
    resolve(new Response(JSON.stringify(data())));
    await rejected;
    expect((await second).content_version).toBe("sha256:demo");
  });

  it("aborts the last reader and never caches invalidated late replies", async () => {
    let resolve!: (r: Response) => void;
    let signal!: AbortSignal;
    const fetcher = vi.fn((_url, init) => {
      signal = init.signal;
      return new Promise<Response>((r) => { resolve = r; });
    });
    vi.stubGlobal("fetch", fetcher);
    const first = fetchWorkbookView(opts);
    const rejected = expect(first).rejects.toMatchObject({ name: "AbortError" });
    invalidateWorkbookViewCache({ workspaceKey: "id:ws", relative: "book.xlsx" });
    expect(signal.aborted).toBe(true);
    resolve(new Response(JSON.stringify(data())));
    await rejected;
    fetcher.mockImplementationOnce(async () => new Response(JSON.stringify(data())));
    await fetchWorkbookView(opts);
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it("rejects a mismatched workspace or version before publishing the cache", async () => {
    const fetcher = vi.fn(async () => new Response(JSON.stringify({ ...data(), file: { workspaceKey: "id:other", relative: "book.xlsx" } })));
    vi.stubGlobal("fetch", fetcher);
    await expect(fetchWorkbookView(opts)).rejects.toThrow("不匹配");
    fetcher.mockImplementation(async () => new Response(JSON.stringify(data())));
    await expect(fetchWorkbookView({ ...opts, expectedVersion: "v2" })).rejects.toThrow("不匹配");
  });
});
