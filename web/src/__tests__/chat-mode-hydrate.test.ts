import { describe, expect, it } from "vitest";

import {
  parseChatMode,
  shouldHydrateChatMode,
} from "@/lib/chat-mode-hydrate";

describe("parseChatMode", () => {
  it("accepts write/read/plan", () => {
    expect(parseChatMode("plan")).toBe("plan");
    expect(parseChatMode("read")).toBe("read");
    expect(parseChatMode("write")).toBe("write");
  });

  it("rejects unknown values", () => {
    expect(parseChatMode("full_access")).toBeNull();
    expect(parseChatMode("")).toBeNull();
    expect(parseChatMode(undefined)).toBeNull();
  });
});

describe("shouldHydrateChatMode", () => {
  it("hydrates once per session when not owned", () => {
    expect(
      shouldHydrateChatMode({
        sessionId: "s1",
        hydratedSessionId: null,
        owned: false,
      }),
    ).toBe(true);
    expect(
      shouldHydrateChatMode({
        sessionId: "s1",
        hydratedSessionId: "s1",
        owned: false,
      }),
    ).toBe(false);
  });

  it("does not overwrite a user click or SSE setChatMode", () => {
    expect(
      shouldHydrateChatMode({
        sessionId: "s1",
        hydratedSessionId: null,
        owned: true,
      }),
    ).toBe(false);
  });

  it("hydrates again after switching sessions", () => {
    expect(
      shouldHydrateChatMode({
        sessionId: "s2",
        hydratedSessionId: "s1",
        owned: false,
      }),
    ).toBe(true);
  });

  it("skips empty session id", () => {
    expect(
      shouldHydrateChatMode({
        sessionId: null,
        hydratedSessionId: null,
        owned: false,
      }),
    ).toBe(false);
  });
});
