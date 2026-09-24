// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { JevInlineRail, JevTimelineButton, JevTimelineDrawer } from "@/components/chat/JevTimeline";
import { RuntimeSettingsPanel, type RuntimeSettings } from "@/components/settings/RuntimeSettingsPanel";
import { jevChatEnabledFromRuntime } from "@/lib/jev-settings";
import { settingsCache } from "@/lib/settings-cache";
import { useJevStore } from "@/stores/jev-store";

const apiGet = vi.fn();
const apiPut = vi.fn();

vi.mock("@/hooks/use-mobile", () => ({ useIsDesktop: () => true }));
vi.mock("@/lib/api", () => ({ apiGet: (...args: unknown[]) => apiGet(...args), apiPut: (...args: unknown[]) => apiPut(...args) }));

const runtime = {
  jev_experimental_enabled: true,
  jev_enabled: "enforce",
  ai_gateway: { configured: true },
};

beforeEach(() => {
  settingsCache.clear();
  apiGet.mockReset().mockResolvedValue(runtime);
  apiPut.mockReset().mockResolvedValue({});
  useJevStore.getState().setChatEnabled(false);
});

afterEach(() => cleanup());

describe("Jev UI visibility", () => {
  it("closes the pinned sidebar, header button, and inline rail when Jev is disabled", () => {
    act(() => {
      useJevStore.getState().setChatEnabled(true);
      useJevStore.setState({ pinned: true, drawerOpen: true, pending: true });
    });
    render(<><JevTimelineButton /><JevTimelineDrawer /><JevInlineRail /></>);
    expect(screen.getByRole("button", { name: "Jev 时间线" })).toBeTruthy();
    expect(screen.getByTestId("jev-desktop-sidebar")).toBeTruthy();
    expect(screen.getByRole("region", { name: "Jev 任务辅助" })).toBeTruthy();

    act(() => useJevStore.getState().setChatEnabled(false));
    expect(screen.queryByRole("button", { name: "Jev 时间线" })).toBeNull();
    expect(screen.queryByTestId("jev-desktop-sidebar")).toBeNull();
    expect(screen.queryByRole("region", { name: "Jev 任务辅助" })).toBeNull();
    expect(useJevStore.getState()).toMatchObject({ pinned: false, drawerOpen: false, pending: false });

    act(() => useJevStore.getState().setChatEnabled(true));
    expect(screen.getByRole("button", { name: "Jev 时间线" })).toBeTruthy();
    expect(screen.queryByTestId("jev-desktop-sidebar")).toBeNull();
  });

  it("syncs the saved experimental switch only after the runtime update succeeds", async () => {
    const onSaved = vi.fn((config: RuntimeSettings) => {
      useJevStore.getState().setChatEnabled(jevChatEnabledFromRuntime(config as Parameters<typeof jevChatEnabledFromRuntime>[0]));
    });
    useJevStore.getState().setChatEnabled(true);
    render(<RuntimeSettingsPanel groups={[{
      title: "实验性功能",
      icon: null,
      items: [{ key: "jev_experimental_enabled", label: "启用实验性 Jev", desc: "", icon: null, type: "bool" }],
    }]} onSaved={onSaved} />);

    fireEvent.click(await screen.findByRole("switch", { name: "启用实验性 Jev" }));
    expect(useJevStore.getState().chatEnabled).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "保存配置" }));
    await waitFor(() => expect(onSaved).toHaveBeenCalledWith({ ...runtime, jev_experimental_enabled: false }));
    expect(apiPut).toHaveBeenCalledWith("/config/runtime", { jev_experimental_enabled: false });
    expect(useJevStore.getState().chatEnabled).toBe(false);
  });
});
