import { afterEach, describe, expect, it, vi } from "vitest";
import { uploadFile, uploadFileFromUrl, uploadFileToFolder } from "@/lib/api";

function successfulUploadResponse(): Response {
  return {
    ok: true,
    json: async () => ({
      filename: "订单与产品.xlsx",
      path: "./uploads/example_订单与产品.xlsx",
      size: 6961,
    }),
  } as Response;
}

describe("upload request routing", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("uploads suggestion files through the same-origin API proxy", async () => {
    const fetchMock = vi.fn().mockResolvedValue(successfulUploadResponse());
    vi.stubGlobal("fetch", fetchMock);

    const file = new File(["workbook"], "订单与产品.xlsx", {
      type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    });
    await uploadFile(file);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0]?.[0]).toBe("/api/v1/upload");
    expect(fetchMock.mock.calls[0]?.[1]).toMatchObject({ method: "POST" });
  });

  it("keeps folder and URL uploads on the same-origin API proxy", async () => {
    const fetchMock = vi.fn().mockResolvedValue(successfulUploadResponse());
    vi.stubGlobal("fetch", fetchMock);

    await uploadFileToFolder(new File(["csv"], "数据.csv"), "reports", "session-1");
    await uploadFileFromUrl("https://example.com/数据.csv");

    expect(fetchMock.mock.calls.map((call) => call[0])).toEqual([
      "/api/v1/upload",
      "/api/v1/upload-from-url",
    ]);
  });

  it("binds local and URL uploads to the selected workspace", async () => {
    const fetchMock = vi.fn().mockResolvedValue(successfulUploadResponse());
    vi.stubGlobal("fetch", fetchMock);

    await uploadFile(new File(["data"], "data.csv"), "session-1", "workspace-1");
    await uploadFileFromUrl(
      "https://example.com/data.csv",
      "session-1",
      "workspace-1",
    );

    const form = fetchMock.mock.calls[0]?.[1]?.body as FormData;
    expect(form.get("session_id")).toBe("session-1");
    expect(form.get("workspace_id")).toBe("workspace-1");
    expect(JSON.parse(String(fetchMock.mock.calls[1]?.[1]?.body))).toMatchObject({
      session_id: "session-1",
      workspace_id: "workspace-1",
    });
  });
});
