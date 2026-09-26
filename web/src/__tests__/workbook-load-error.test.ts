/**
 * 侧边栏/全屏表格打开失败的原因分类：
 * - 只有后端明确回答“这里没有这个文件”（404 / PATH_INVALID）才算删除；
 * - 工作区不一致（400/409 FILE_SCOPE_*）不能再冒充“文件已删除”；
 * - 服务重启、网络中断等可恢复故障要进入自动重试预算。
 */

import { describe, expect, it } from "vitest";

import {
  classifyWorkbookLoadError,
  shouldRetryWorkbookLoad,
  workbookLoadFailure,
  workbookLoadRetryDelay,
} from "@/lib/workbook-load-error";

function httpError(status: number, message: string, code?: string) {
  const err = new Error(message) as Error & { status?: number; code?: string };
  err.status = status;
  if (code) err.code = code;
  return err;
}

describe("classifyWorkbookLoadError", () => {
  it("treats the observe 404 as a confirmed missing file", () => {
    const failure = workbookLoadFailure(httpError(404, "文件不存在或路径非法"));
    expect(failure.kind).toBe("missing");
    expect(failure.confirmedMissing).toBe(true);
    expect(failure.message).toContain("文件已删除或不存在");
  });

  it("treats a PATH_INVALID snapshot error as confirmed missing", () => {
    const failure = workbookLoadFailure(httpError(400, "文件不存在: outputs/gone.xlsx", "PATH_INVALID"));
    expect(failure.kind).toBe("missing");
    expect(failure.confirmedMissing).toBe(true);
  });

  it.each([
    ["FILE_SCOPE_REQUIRED", 400, "无法确定工作区，请从会话重新打开文件"],
    ["FILE_SCOPE_MISMATCH", 409, "会话与文件工作区不一致，请重新打开文件"],
  ])("keeps %s a scope problem instead of a deletion", (code, status, message) => {
    const failure = workbookLoadFailure(httpError(status, message, code));
    expect(failure.kind).toBe("scope");
    expect(failure.confirmedMissing).toBe(false);
    expect(failure.message).toContain("工作区");
    expect(failure.message).not.toContain("文件已删除");
  });

  it("never closes a workbook on a message-only match", () => {
    // 没有状态码/错误码时只认文案：可以提示，但不能据此关闭用户的标签。
    const failure = workbookLoadFailure(new Error("文件不存在"));
    expect(failure.kind).toBe("missing");
    expect(failure.confirmedMissing).toBe(false);
  });

  it("recognises stale view tokens and service faults", () => {
    expect(classifyWorkbookLoadError(httpError(409, "观察版本已变化", "STALE_VIEW"))).toBe("stale");
    expect(classifyWorkbookLoadError(new Error("STALE_VIEW: 响应文件或版本不匹配"))).toBe("stale");
    expect(classifyWorkbookLoadError(httpError(503, "service unavailable"))).toBe("transient");
    expect(classifyWorkbookLoadError(new Error("read ECONNRESET"))).toBe("transient");
    expect(classifyWorkbookLoadError(new Error("Failed to proxy http://127.0.0.1:8000"))).toBe("transient");
  });

  it("keeps unknown failures verbatim so the user sees the real message", () => {
    const failure = workbookLoadFailure(new Error("载入工作表失败"));
    expect(failure.kind).toBe("unknown");
    expect(failure.message).toBe("载入工作表失败");
    expect(failure.confirmedMissing).toBe(false);
  });
});

describe("shouldRetryWorkbookLoad", () => {
  it("retries a transient fault within budget", () => {
    expect(shouldRetryWorkbookLoad("transient", 0)).toBe(true);
    expect(shouldRetryWorkbookLoad("transient", 1)).toBe(true);
    expect(shouldRetryWorkbookLoad("transient", 2)).toBe(false);
  });

  it("gives a missing file exactly one retry so a write in flight cannot close the pane", () => {
    expect(shouldRetryWorkbookLoad("missing", 0)).toBe(true);
    expect(shouldRetryWorkbookLoad("missing", 1)).toBe(false);
  });

  it("does not retry scope or unknown failures", () => {
    expect(shouldRetryWorkbookLoad("scope", 0)).toBe(false);
    expect(shouldRetryWorkbookLoad("unknown", 0)).toBe(false);
  });

  it("backs off between attempts", () => {
    expect(workbookLoadRetryDelay(0)).toBeLessThan(workbookLoadRetryDelay(1));
    expect(workbookLoadRetryDelay(9)).toBe(workbookLoadRetryDelay(1));
  });
});
