// @vitest-environment jsdom
import React from "react";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { apiGet, apiPut } from "@/lib/api";
import { MemoryTab } from "@/components/settings/MemoryTab";
import { RuntimeTab } from "@/components/settings/RuntimeTab";
import { SkillsTab } from "@/components/settings/SkillsTab";
import { useSettingsNavigationStore } from "@/stores/settings-navigation-store";
import { useSettingsDraftStore } from "@/stores/settings-draft-store";
import { AccessTab } from "@/components/settings/AccessTab";

vi.mock("@/lib/api", () => ({
  apiGet: vi.fn(),
  apiPut: vi.fn().mockResolvedValue({}),
  apiDelete: vi.fn(),
  setManageToken: vi.fn(),
}));

vi.mock("@/lib/access-api", () => ({
  fetchAccessSettings: vi.fn().mockResolvedValue({
    enabled: false,
    username: "admin",
    password_configured: false,
    manage_token_configured: false,
    session_hours: 24,
  }),
  logoutFromInstance: vi.fn(),
  saveAccessSettings: vi.fn(),
}));

vi.mock("@/lib/settings-cache", () => ({
  settingsCache: {
    get: vi.fn(),
    set: vi.fn(),
    delete: vi.fn(),
    invalidatePrefix: vi.fn(),
  },
}));

const runtimeConfig = {
  memory_enabled: true,
  memory_expire_days: 90,
  memory_maintenance_enabled: true,
  memory_maintenance_min_entries: 20,
  memory_maintenance_new_threshold: 5,
  memory_maintenance_interval_hours: 24,
  memory_maintenance_model: "",
  session_ttl_seconds: 3600,
  max_sessions: 20,
  max_consecutive_failures: 3,
  turn_timeout_seconds: 0,
  subagent_enabled: true,
  agent_self_management_enabled: false,
  friendly_error_messages: true,
  max_context_tokens: 128000,
  chat_history_enabled: true,
  compaction_enabled: true,
  compaction_threshold_ratio: 0.8,
  compaction_keep_recent_turns: 4,
  compaction_max_summary_tokens: 2000,
  subagent_timeout_seconds: 600,
  subagent_max_consecutive_failures: 3,
  parallel_subagent_max: 3,
  tool_result_hard_cap_chars: 50000,
  parallel_readonly_tools: true,
  parallel_tool_max: 8,
  log_level: "INFO",
};

beforeAll(() => {
  vi.stubGlobal("ResizeObserver", class {
    observe() {}
    unobserve() {}
    disconnect() {}
  });
});

afterAll(() => {
  vi.unstubAllGlobals();
});

beforeEach(() => {
  useSettingsDraftStore.setState({ drafts: {} });
  useSettingsNavigationStore.setState({ runtimeCategory: "conversation", targetKey: null });
  vi.mocked(apiGet).mockImplementation(async (url: string) => {
    if (url === "/config/runtime") return runtimeConfig;
    if (url === "/memory") return [];
    if (url === "/skills") return [];
    throw new Error(`unexpected URL: ${url}`);
  });
  vi.mocked(apiPut).mockResolvedValue({});
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("settings organization", () => {
  it("keeps memory behavior and maintenance controls beside the memory library", async () => {
    render(<MemoryTab />);

    expect(await screen.findByRole("heading", { name: "记忆配置" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "自动维护" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "记忆库" })).toBeTruthy();

    fireEvent.click(screen.getByRole("switch", { name: "跨会话记忆" }));
    fireEvent.click(screen.getByRole("button", { name: "保存配置" }));

    await waitFor(() => {
      expect(apiPut).toHaveBeenCalledWith("/config/runtime", { memory_enabled: false });
    });
  });

  it("does not repeat memory maintenance controls in system settings", async () => {
    render(<RuntimeTab />);

    expect(await screen.findByText("消息与助手")).toBeTruthy();
    expect(screen.queryByText("记忆自动维护")).toBeNull();
    expect(screen.queryByText("跨会话记忆")).toBeNull();
    expect(screen.queryByText("技能发现")).toBeNull();
    expect(screen.queryByText("代码风险分级")).toBeNull();
    expect(screen.queryByText("图片识别")).toBeNull();
  });

  it("places skill discovery and Hook controls with installed skills", async () => {
    render(<SkillsTab />);

    expect(await screen.findByRole("heading", { name: "技能发现" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "技能 Hook" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "已安装技能" })).toBeTruthy();
  });

  it("places execution safeguards in the security section", async () => {
    render(<AccessTab />);

    expect(await screen.findByRole("heading", { name: "登录保护" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "代码执行与工具校验" })).toBeTruthy();
    expect(screen.getByRole("switch", { name: "代码风险分级" })).toBeTruthy();
  });
});
