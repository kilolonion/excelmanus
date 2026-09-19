import { describe, expect, it } from "vitest";
import {
  fileCachePrefix,
  matchesFileCacheKey,
  normalizeExcelPath,
  snapshotCacheKey,
  viewCacheKey,
} from "@/lib/api";

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
