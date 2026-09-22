import { afterEach, describe, expect, it, vi } from "vitest";

afterEach(() => { vi.unstubAllGlobals(); vi.resetModules(); });

describe("workbook chat preference persistence", () => {
  it("restores local choices without restoring transient state or unknown values", async () => {
    let stored = JSON.stringify({ state: {
      autoReturnToChat: false, learnFromNavigation: true,
      recentChoices: [false, "bad", true, true, true], preferenceVersion: 999, unknown: "ignored",
    }, version: 0 });
    vi.stubGlobal("window", { localStorage: {
      getItem: () => stored,
      setItem: (_key: string, value: string) => { stored = value; },
      removeItem: () => { stored = ""; },
    } });
    const { useWorkbookChatPreferencesStore: store } = await import("@/stores/workbook-chat-preferences-store");
    expect(store.getState()).toMatchObject({
      autoReturnToChat: false, learnFromNavigation: true, recentChoices: [false, true, true, true], preferenceVersion: 0,
    });
    expect(store.getState()).not.toHaveProperty("unknown");
    store.getState().recordChoice(true);
    expect(store.getState().autoReturnToChat).toBe(true);
    expect(JSON.parse(stored).state).toEqual({ autoReturnToChat: true, learnFromNavigation: true, recentChoices: [] });
  });

  it("uses safe defaults for invalid saved values", async () => {
    vi.stubGlobal("window", { localStorage: {
      getItem: () => JSON.stringify({ state: { autoReturnToChat: "false", learnFromNavigation: 0, recentChoices: {} }, version: 0 }),
      setItem: vi.fn(), removeItem: vi.fn(),
    } });
    const { useWorkbookChatPreferencesStore: store } = await import("@/stores/workbook-chat-preferences-store");
    expect(store.getState()).toMatchObject({ autoReturnToChat: true, learnFromNavigation: true, recentChoices: [] });
  });
});
