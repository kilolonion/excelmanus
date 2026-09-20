import { describe, expect, it, vi } from "vitest";

const directFetch = vi.hoisted(() => vi.fn());
const getAuthHeaders = vi.hoisted(() => vi.fn(() => ({ Authorization: "Bearer manage-secret" })));

vi.mock("@/lib/api", () => ({ directFetch, getAuthHeaders }));

import { consumeSSE } from "@/lib/sse";

describe("SSE authentication", () => {
  it("carries the management token on the runtime streaming connection", async () => {
    directFetch.mockResolvedValueOnce({
      ok: true,
      body: new ReadableStream({ start(controller) { controller.close(); } }),
    } as Response);

    await consumeSSE("http://127.0.0.1:54321/api/v1/chat/stream", {}, vi.fn());

    expect(directFetch).toHaveBeenCalledWith(
      "http://127.0.0.1:54321/api/v1/chat/stream",
      expect.objectContaining({
        headers: expect.objectContaining({
          Authorization: "Bearer manage-secret",
          Accept: "text/event-stream",
        }),
      }),
    );
  });
});
