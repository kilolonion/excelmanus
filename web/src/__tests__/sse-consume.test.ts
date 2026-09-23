import { afterEach, describe, expect, it, vi } from "vitest";
import { consumeSSE } from "@/lib/sse";

describe("consumeSSE", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("flushes every event when the connection closes without a trailing blank line", async () => {
    const payload = [
      "event: text_delta\n",
      'data: {"content":"第一段"}\n',
      "\n",
      "event: text_delta\n",
      'data: {"content":"第二段"}',
    ].join("");
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(new TextEncoder().encode(payload), {
        status: 200,
        headers: { "content-type": "text/event-stream" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const events: { event: string; data: Record<string, unknown> }[] = [];
    await consumeSSE("/api/v1/chat/stream", {}, (event) => events.push(event));

    expect(events).toEqual([
      { event: "text_delta", data: { content: "第一段" } },
      { event: "text_delta", data: { content: "第二段" } },
    ]);
  });
});
