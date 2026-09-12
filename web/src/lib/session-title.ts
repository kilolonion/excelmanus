import type { Message } from "@/lib/types";
import { stripInjectedUserPromptBlocks } from "@/lib/injected-user-prompt";

export function buildDefaultSessionTitle(sessionId: string): string {
  return `会话 ${sessionId.slice(0, 8)}`;
}

export function isFallbackSessionTitle(
  title: string | null | undefined,
  sessionId: string
): boolean {
  const normalized = (title ?? "").trim();
  if (!normalized) return true;
  if (normalized === sessionId) return true;
  if (normalized === sessionId.slice(0, 8)) return true;
  return normalized === buildDefaultSessionTitle(sessionId);
}

/** 即时标题：去上传前缀、取首行。超出侧栏宽度由 CSS truncate 显示省略号。 */
export function instantSessionTitle(userText: string): string {
  const trimmed = userText.trim();
  if (!trimmed) return "";
  const cleaned = trimmed.replace(/\[已上传(?:文件|图片): [^\]]*\]\s*/g, "").trim();
  const source = cleaned || trimmed;
  for (const line of source.split(/\r?\n/)) {
    const collapsed = line.replace(/\s+/g, " ").trim();
    if (collapsed) return collapsed;
  }
  return "";
}

export function deriveSessionTitleFromMessages(messages: Message[]): string {
  for (const message of messages) {
    if (message.role !== "user") continue;
    const content = stripInjectedUserPromptBlocks(message.content);
    const title = instantSessionTitle(content);
    if (title) return title;
  }
  return "";
}
