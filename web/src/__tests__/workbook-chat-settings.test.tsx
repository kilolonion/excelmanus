// @vitest-environment jsdom
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { RuntimeTab } from "@/components/settings/RuntimeTab";
import { useWorkbookChatPreferencesStore as preferences } from "@/stores/workbook-chat-preferences-store";

vi.mock("@/lib/api", () => ({ apiGet: vi.fn().mockRejectedValue(new Error("offline")), apiPut: vi.fn() }));
vi.mock("@/lib/settings-cache", () => ({ settingsCache: { get: vi.fn(), set: vi.fn() } }));

afterEach(cleanup);

describe("workbook controls in system settings", () => {
  it("remains usable when backend settings cannot load and applies changes immediately", async () => {
    preferences.getState().setAutoReturnToChat(true);
    preferences.getState().setLearnFromNavigation(true);
    render(<RuntimeTab />);
    await screen.findByText("无法获取系统配置");
    const autoReturn = screen.getByRole("switch", { name: "发送后返回对话" });
    expect(autoReturn.getAttribute("aria-checked")).toBe("true");
    fireEvent.click(autoReturn);
    expect(preferences.getState().autoReturnToChat).toBe(false);
    expect(autoReturn.getAttribute("aria-checked")).toBe("false");
    fireEvent.click(screen.getByRole("switch", { name: "根据使用习惯自动调整" }));
    expect(preferences.getState().learnFromNavigation).toBe(false);
  });
});
