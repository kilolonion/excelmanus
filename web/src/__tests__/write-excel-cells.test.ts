import { beforeEach, describe, expect, it, vi } from "vitest";

import { writeExcelCells } from "@/lib/api";

function jsonResponse(status: number, body: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as Response;
}

describe("writeExcelCells", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    global.fetch = vi.fn();
  });

  it("sends expected_version and returns content_version on success", async () => {
    const fetchMock = vi.mocked(global.fetch);
    fetchMock.mockResolvedValue(
      jsonResponse(200, {
        status: "success",
        cells_written: 1,
        content_version: "sha256:abc",
      }),
    );

    const result = await writeExcelCells({
      path: "uploads/a.xlsx",
      sheet: "Sheet1",
      changes: [{ cell: "B2", value: 42 }],
      sessionId: "sess-1",
      expectedVersion: "sha256:old",
    });

    expect(result).toEqual({
      status: "success",
      cells_written: 1,
      content_version: "sha256:abc",
      code: undefined,
    });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("/files/excel/write");
    expect(init?.method).toBe("POST");
    const body = JSON.parse(String(init?.body));
    expect(body).toMatchObject({
      path: "./uploads/a.xlsx",
      sheet: "Sheet1",
      changes: [{ cell: "B2", value: 42 }],
      session_id: "sess-1",
      expected_version: "sha256:old",
    });
    expect(body.operations).toBeNull();
  });

  it("sends expected_version null when omitted (API will reject)", async () => {
    const fetchMock = vi.mocked(global.fetch);
    fetchMock.mockResolvedValue(
      jsonResponse(200, { status: "success", cells_written: 1, content_version: "sha256:new" }),
    );

    await writeExcelCells({
      path: "./book.xlsx",
      changes: [{ cell: "A1", value: "x" }],
    });

    const body = JSON.parse(String(fetchMock.mock.calls[0][1]?.body));
    expect(body.expected_version).toBeNull();
    expect(body.session_id).toBeNull();
  });

  it("parses HTTP 409 into status=conflict and code VERSION_CONFLICT", async () => {
    vi.mocked(global.fetch).mockResolvedValue(
      jsonResponse(409, {
        error: "版本冲突",
        code: "VERSION_CONFLICT",
      }),
    );

    const result = await writeExcelCells({
      path: "./book.xlsx",
      changes: [{ cell: "A1", value: 1 }],
      expectedVersion: "sha256:stale",
    });

    expect(result.status).toBe("conflict");
    expect(result.cells_written).toBe(0);
    expect(result.code).toBe("VERSION_CONFLICT");
  });

  it("defaults 409 code when body has no code", async () => {
    vi.mocked(global.fetch).mockResolvedValue(jsonResponse(409, { error: "conflict" }));
    const result = await writeExcelCells({
      path: "./book.xlsx",
      changes: [{ cell: "A1", value: 1 }],
    });
    expect(result.code).toBe("VERSION_CONFLICT");
  });

  it("throws on non-409 errors", async () => {
    vi.mocked(global.fetch).mockResolvedValue(jsonResponse(500, { error: "写入失败" }));
    await expect(
      writeExcelCells({ path: "./book.xlsx", changes: [{ cell: "A1", value: 1 }] }),
    ).rejects.toThrow("写入失败");
  });
});
