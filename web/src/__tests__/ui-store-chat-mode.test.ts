import { beforeEach, describe, expect, it } from "vitest";

import { useUIStore } from "@/stores/ui-store";

describe("chatMode hydrate vs click", () => {
  beforeEach(() => {
    useUIStore.setState({
      chatMode: "write",
      chatModeOwned: false,
    });
  });

  it("hydrates session detail once when not owned", () => {
    useUIStore.getState().hydrateChatMode("plan");
    expect(useUIStore.getState().chatMode).toBe("plan");
    expect(useUIStore.getState().chatModeOwned).toBe(false);
  });

  it("setChatMode owns the tab so later hydrate is ignored", () => {
    useUIStore.getState().setChatMode("read");
    useUIStore.getState().hydrateChatMode("plan");
    expect(useUIStore.getState().chatMode).toBe("read");
    expect(useUIStore.getState().chatModeOwned).toBe(true);
  });

  it("session switch releases ownership so the next session can hydrate", () => {
    useUIStore.getState().setChatMode("plan");
    useUIStore.getState().releaseChatModeOwnership();
    useUIStore.getState().hydrateChatMode("write");
    expect(useUIStore.getState().chatModeOwned).toBe(false);
    expect(useUIStore.getState().chatMode).toBe("write");
  });
});
