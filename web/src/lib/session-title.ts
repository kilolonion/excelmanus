import type { Message } from "@/lib/types";

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

export function deriveSessionTitleFromMessages(messages: Message[]): string {
  for (const message of messages) {
    if (message.role !== "user") continue;
    const content = message.content.trim();
    if (content) return content.slice(0, 20);
  }
  return "";
}
