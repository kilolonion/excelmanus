import { afterEach, describe, expect, it, vi } from "vitest";

describe("persisted UI preference whitelist", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.resetModules();
  });

  it("drops unknown persisted fields while retaining permissions", async () => {
    let stored = JSON.stringify({ state: { unknownOption: "value", fullAccessEnabled: true }, version: 0 });
    const storage = {
      getItem: () => stored,
      setItem: (_key: string, value: string) => { stored = value; },
      removeItem: () => { stored = ""; },
    };
    vi.stubGlobal("window", { localStorage: storage, innerWidth: 1440 });
    vi.stubGlobal("BroadcastChannel", undefined);
    const { useUIStore } = await import("@/stores/ui-store");

    expect(useUIStore.getState().fullAccessEnabled).toBe(true);
    expect(useUIStore.getState()).not.toHaveProperty("unknownOption");
    useUIStore.getState().setFullAccessEnabled(false);
    expect(JSON.parse(stored).state).toEqual({ fullAccessEnabled: false, autoApproveEnabled: false });
  });
});
