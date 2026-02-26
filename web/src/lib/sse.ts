import { directFetch } from "./api";

export interface SSEEvent {
  event: string;
  data: Record<string, unknown>;
}

export type SSEHandler = (event: SSEEvent) => void;

export async function consumeSSE(
  url: string,
  body: unknown,
  handler: SSEHandler,
  signal?: AbortSignal
): Promise<void> {
  const response = await directFetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });

  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(data.error || `SSE error: ${response.status}`);
  }

  const reader = response.body?.getReader();
  if (!reader) throw new Error("No response body");

  const decoder = new TextDecoder();
  let buffer = "";
  let currentEvent = "";
  let currentDataLines: string[] = [];

  const emitEvent = () => {
    if (!currentEvent || currentDataLines.length === 0) return;
    try {
      const data = JSON.parse(currentDataLines.join("\n"));
      try {
        handler({ event: currentEvent, data });
      } catch (handlerErr) {
        // 处理器异常不能中断 SSE 读取循环 — 记录日志并继续，
        // 确保后续事件（工具调用、文本增量、done）仍能送达。
        console.error("[SSE] handler error for event", currentEvent, handlerErr);
      }
    } catch {
      // 跳过格式错误的 JSON
    }
    currentEvent = "";
    currentDataLines = [];
  };

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";

      for (const rawLine of lines) {
        const line = rawLine.endsWith("\r") ? rawLine.slice(0, -1) : rawLine;
        if (line.startsWith("event:")) {
          currentEvent = line.slice(6).trim();
        } else if (line.startsWith("data:")) {
          currentDataLines.push(line.slice(5).replace(/^ /, ""));
        } else if (line === "") {
          emitEvent();
        }
      }
    }

    // 部分服务端可能未以空行结尾，EOF 时补一次 flush。
    emitEvent();
  } finally {
    reader.releaseLock();
  }
}
