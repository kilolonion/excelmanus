import { afterEach, describe, expect, it, vi } from "vitest";

describe("model profile notifications", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.resetModules();
  });

  it("invalidates cached settings locally and on a remote tab notification without echoing", async () => {
    const channel = { onmessage: null as (() => void) | null, postMessage: vi.fn() };
    vi.stubGlobal("BroadcastChannel", class {
      constructor() { return channel; }
    });
    vi.stubGlobal("localStorage", { getItem: () => null, setItem: vi.fn(), removeItem: vi.fn() });
    const { settingsCache } = await import("@/lib/settings-cache");
    const { useUIStore } = await import("@/stores/ui-store");
    const observed: number[] = [];
    const unsubscribe = useUIStore.subscribe((s) => {
      observed.push(s.modelProfileVersion);
      // 消费者收到通知时，旧缓存必须已经失效。
      expect(settingsCache.get("/config/models")).toBeUndefined();
    });
    settingsCache.set("/thinking", { effort: "medium" });
    settingsCache.set("/config/models", { profiles: [{ name: "old" }] });
    useUIStore.getState().bumpModelProfiles();
    expect(channel.postMessage).toHaveBeenCalledTimes(1);

    settingsCache.set("/config/models", { profiles: [{ name: "still-old" }] });
    channel.onmessage?.();
    expect(observed).toEqual([1, 2]);
    expect(channel.postMessage).toHaveBeenCalledTimes(1);
    expect(settingsCache.get("/thinking")).toEqual({ effort: "medium" });
    unsubscribe();
  });
});
