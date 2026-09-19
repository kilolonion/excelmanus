export type ChatMode = "write" | "read" | "plan";

export function parseChatMode(value: unknown): ChatMode | null {
  if (value === "write" || value === "read" || value === "plan") {
    return value;
  }
  return null;
}

/** 每个 session 只 hydrate 一次。用户点选 / SSE 之后不再用详情覆盖。 */
export function shouldHydrateChatMode(input: {
  sessionId: string | null | undefined;
  hydratedSessionId: string | null;
  owned: boolean;
}): boolean {
  if (!input.sessionId) return false;
  if (input.owned) return false;
  return input.hydratedSessionId !== input.sessionId;
}
