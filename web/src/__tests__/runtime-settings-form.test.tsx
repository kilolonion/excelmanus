// @vitest-environment jsdom
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { RuntimeSettingsPanel, type RuntimeSettingGroup } from "@/components/settings/RuntimeSettingsPanel";
import { RuntimeTab } from "@/components/settings/RuntimeTab";
import { SettingsSearch, searchSettings } from "@/components/settings/SettingsSearch";
import { useSettingsNavigationStore } from "@/stores/settings-navigation-store";
import { settingError } from "@/lib/runtime-settings-form";
import { useUIStore } from "@/stores/ui-store";
import { RUNTIME_SETTING_GROUPS } from "@/components/settings/runtime-setting-groups";
import { useSettingsDraftStore } from "@/stores/settings-draft-store";
import { useConnectionStore } from "@/stores/connection-store";
import { apiGet, apiPut } from "@/lib/api";

vi.mock("@/lib/api", () => ({ apiGet: vi.fn(), apiPut: vi.fn() }));
const config = { memory_maintenance_interval_hours: 24, turn_cost_budget_usd: 0, max_iterations: 0, max_context_tokens_override: 0, max_context_tokens: 128000, chat_history_enabled: true, parallel_readonly_tools: false, parallel_tool_max: 4 };
const groups: RuntimeSettingGroup[] = [{ title: "参数", description: "", icon: null, items: [
  { key: "memory_maintenance_interval_hours", label: "维护间隔", desc: "", type: "float", min: 0.5 },
  { key: "turn_cost_budget_usd", label: "费用上限", desc: "", type: "float", min: 0 },
  { key: "max_iterations", label: "步数上限", desc: "", type: "int", min: 0 },
  { key: "chat_history_enabled", label: "保存聊天记录", desc: "", type: "bool", effect: "restart" },
]}];

beforeEach(() => {
  useSettingsDraftStore.setState({ drafts: {} });
  useSettingsNavigationStore.setState({ runtimeCategory: "conversation", targetKey: null, targetVersion: 0 });
  vi.stubGlobal("requestAnimationFrame", (callback: () => void) => setTimeout(callback, 0));
  vi.stubGlobal("cancelAnimationFrame", clearTimeout);
  vi.mocked(apiGet).mockResolvedValue(config);
  vi.mocked(apiPut).mockResolvedValue({});
});
afterEach(() => { cleanup(); vi.clearAllMocks(); });

async function show() {
  const view = render(<RuntimeSettingsPanel groups={groups} />);
  await screen.findByLabelText("维护间隔");
  return view;
}

describe("runtime settings editing", () => {
  it("allows clearing a numeric input, rejects empty and fractional integers, and sends precise costs", async () => {
    await show();
    fireEvent.change(screen.getByLabelText("步数上限"), { target: { value: "" } });
    expect(screen.getByText("请输入有效数字")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "保存配置" }));
    expect(apiPut).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText("步数上限"), { target: { value: "1.5" } });
    expect(screen.getByText("请输入整数")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("步数上限"), { target: { value: "0" } });
    fireEvent.change(screen.getByLabelText("费用上限"), { target: { value: "0.000125" } });
    fireEvent.change(screen.getByLabelText("维护间隔"), { target: { value: "48" } });
    fireEvent.click(screen.getByRole("button", { name: "保存配置" }));
    await waitFor(() => expect(apiPut).toHaveBeenCalledWith("/config/runtime", { turn_cost_budget_usd: 0.000125, memory_maintenance_interval_hours: 48 }));
  });

  it("removes reverted edits and preserves unsaved edits across page navigation", async () => {
    const first = await show();
    fireEvent.change(screen.getByLabelText("步数上限"), { target: { value: "15" } });
    first.unmount();
    await show();
    expect((screen.getByLabelText("步数上限") as HTMLInputElement).value).toBe("15");
    fireEvent.change(screen.getByLabelText("步数上限"), { target: { value: "0" } });
    expect((screen.getByRole("button", { name: "保存配置" }) as HTMLButtonElement).disabled).toBe(true);
    expect(useSettingsDraftStore.getState().drafts).toEqual({});
  });

  it("keeps the draft on save failure and retries successfully", async () => {
    await show();
    vi.mocked(apiPut).mockRejectedValueOnce(new Error("服务暂时不可用"));
    fireEvent.change(screen.getByLabelText("步数上限"), { target: { value: "20" } });
    fireEvent.click(screen.getByRole("button", { name: "保存配置" }));
    expect(await screen.findByRole("alert")).toHaveProperty("textContent", "服务暂时不可用");
    expect(useSettingsDraftStore.getState().drafts.max_iterations).toBe("20");
    fireEvent.click(screen.getByRole("button", { name: "保存配置" }));
    await waitFor(() => expect(useSettingsDraftStore.getState().drafts).toEqual({}));
  });

  it("announces restart before saving and clears acknowledged edits before restart", async () => {
    const restart = vi.spyOn(useConnectionStore.getState(), "triggerRestart").mockImplementation(async () => {});
    vi.mocked(apiPut).mockResolvedValue({ restarting: true, restart_reason: "历史记录已更新" });
    await show();
    fireEvent.click(screen.getByRole("switch", { name: "保存聊天记录" }));
    expect(screen.getByText(/保存将重启服务并中断/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "保存并重启" }));
    await waitFor(() => expect(restart).toHaveBeenCalledWith("历史记录已更新"));
    expect(useSettingsDraftStore.getState().drafts).toEqual({});
    restart.mockRestore();
  });

  it("offers retry after load failure", async () => {
    vi.mocked(apiGet).mockRejectedValueOnce(new Error("连接失败"));
    render(<RuntimeSettingsPanel groups={groups} />);
    fireEvent.click(await screen.findByRole("button", { name: "重新加载" }));
    expect(await screen.findByLabelText("维护间隔")).toBeTruthy();
  });

  it("does not lose edits made while a save is in progress", async () => {
    let complete!: (value: object) => void;
    vi.mocked(apiPut).mockReturnValue(new Promise((resolve) => { complete = resolve; }));
    await show();
    fireEvent.change(screen.getByLabelText("步数上限"), { target: { value: "20" } });
    fireEvent.click(screen.getByRole("button", { name: "保存配置" }));
    useSettingsDraftStore.getState().update("max_iterations", "30", 0);
    complete({});
    await waitFor(() => expect((screen.getByRole("button", { name: "保存配置" }) as HTMLButtonElement).disabled).toBe(false));
    expect(useSettingsDraftStore.getState().drafts.max_iterations).toBe("30");
  });

  it("finds settings across categories and exposes automatic context and dependencies", async () => {
    render(<><SettingsSearch /><RuntimeTab /></>);
    await screen.findByText("消息与助手");
    fireEvent.change(screen.getByRole("searchbox", { name: "搜索全部设置" }), { target: { value: "费用" } });
    fireEvent.click(screen.getByRole("button", { name: /^费用上限/ }));
    expect(screen.getByLabelText("费用上限")).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", { name: /对话容量/ }));
    const auto = screen.getByRole("checkbox", { name: "跟随模型自动匹配" });
    expect(auto.getAttribute("aria-checked")).toBe("true");
    fireEvent.click(auto);
    expect((screen.getByLabelText("默认对话容量") as HTMLInputElement).value).toBe("128000");
    fireEvent.click(auto);
    expect(useSettingsDraftStore.getState().drafts).toEqual({});
    fireEvent.change(screen.getByRole("searchbox", { name: "搜索全部设置" }), { target: { value: "同时读取的工具数" } });
    fireEvent.click(screen.getByRole("button", { name: /^同时读取的工具数/ }));
    expect((screen.getByLabelText("同时读取的工具数") as HTMLInputElement).disabled).toBe(true);
  });

  it("searches every category, supports empty results and keyboard navigation", () => {
    expect(searchSettings("API Key").some((entry) => entry.key === "connection")).toBe(true);
    expect(searchSettings("memory_auto_load_lines")[0].tab).toBe("memory");
    expect(searchSettings("命令前缀")[0].tab).toBe("skills");
    expect(searchSettings("密码").some((entry) => entry.tab === "access")).toBe(true);
    expect(searchSettings("版本").some((entry) => entry.tab === "version")).toBe(true);
    render(<SettingsSearch />);
    const input = screen.getByRole("searchbox", { name: "搜索全部设置" });
    fireEvent.change(input, { target: { value: "不存在的项目xyz" } });
    expect(screen.getByText(/没有匹配的设置/)).toBeTruthy();
    fireEvent.keyDown(input, { key: "Escape" });
    expect((input as HTMLInputElement).value).toBe("");
    fireEvent.change(input, { target: { value: "记忆过期" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(useUIStore.getState().settingsTab).toBe("memory");
    expect(useSettingsNavigationStore.getState().targetKey).toBe("memory_expire_days");
  });

  it("validates strict ratio bounds without excluding valid small values", () => {
    const item = RUNTIME_SETTING_GROUPS.flatMap((group) => group.items).find((item) => item.key === "compaction_threshold_ratio")!;
    expect(settingError(item, 0, {})).toBeTruthy();
    expect(settingError(item, 1, {})).toBeTruthy();
    expect(settingError(item, 0.005, {})).toBeUndefined();
  });

  it("contains each runtime setting only once", () => {
    const keys = RUNTIME_SETTING_GROUPS.flatMap((group) => group.items.map((item) => item.key));
    expect(new Set(keys).size).toBe(keys.length);
  });
});
