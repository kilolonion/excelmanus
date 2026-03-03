import { directFetch } from "./api";

export interface SSEEvent {
  event: string;
  data: Record<string, unknown>;
}

export type SSEHandler = (event: SSEEvent) => void;

/** 携带 HTTP 状态码的 SSE 错误，供调用方按状态码分类。 */
export class SSEError extends Error {
  statusCode: number;
  responseBody: string;

  constructor(statusCode: number, message: string, responseBody?: string) {
    super(message);
    this.name = "SSEError";
    this.statusCode = statusCode;
    this.responseBody = responseBody || "";
  }
}

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
    const text = await response.text().catch(() => "");
    let errorMsg = `SSE error: ${response.status}`;
    try {
      const data = JSON.parse(text);
      if (data.error) errorMsg = data.error;
      else if (data.detail) errorMsg = data.detail;
    } catch { /* not JSON, use status text */ }
    throw new SSEError(response.status, errorMsg, text);
  }

  const reader = response.body?.getReader();
  if (!reader) throw new SSEError(0, "服务端未返回响应体，请检查网络连接或刷新页面重试。");

  const decoder = new TextDecoder();
  let buffer = "";
  let currentEvent = "";
  let currentDataLines: string[] = [];
  let _eventCount = 0;

  const emitEvent = () => {
    if (!currentEvent || currentDataLines.length === 0) return;
    const rawJson = currentDataLines.join("\n");
    try {
      const data = JSON.parse(rawJson);
      try {
        handler({ event: currentEvent, data });
      } catch (handlerErr) {
        // 处理器异常不能中断 SSE 读取循环 — 记录日志并继续，
        // 确保后续事件（工具调用、文本增量、done）仍能送达。
        console.error("[SSE] handler error for event", currentEvent, handlerErr);
      }
      _eventCount++;
    } catch {
      // 记录格式错误的 JSON 以便调试（截断避免日志爆炸）
      console.warn(
        "[SSE] malformed JSON for event '%s' (len=%d): %s",
        currentEvent,
        rawJson.length,
        rawJson.length > 200 ? rawJson.slice(0, 200) + "…" : rawJson,
      );
    }
    currentEvent = "";
    currentDataLines = [];
  };

  try {
    while (true) {
      let readResult: ReadableStreamReadResult<Uint8Array>;
      try {
        readResult = await reader.read();
      } catch (readErr) {
        // reader.read() 抛出通常意味着底层连接中断
        if ((readErr as Error).name === "AbortError") throw readErr;
        throw new SSEError(
          0,
          _eventCount > 0
            ? "流式传输中断，请重试。"
            : "连接在接收数据前断开，请检查网络。",
        );
      }
      const { done, value } = readResult;
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
