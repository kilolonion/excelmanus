// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TopModelSelector } from "@/components/chat/TopModelSelector";
import { ModelPickerContent } from "@/components/chat/ModelPickerContent";
import { useUIStore } from "@/stores/ui-store";
import type { ModelInfo } from "@/lib/types";

const { apiGet, apiPut } = vi.hoisted(() => ({ apiGet: vi.fn(), apiPut: vi.fn() }));
vi.mock("@/lib/api", () => ({ apiGet, apiPut, apiPost: vi.fn(), apiDelete: vi.fn() }));

const models: ModelInfo[] = [
  { name: "fast", display_name: "DeepSeek Flash", model: "proxy/fast", resolved_model: "deepseek-v4.1-flash", provider: "deepseek", active: true },
  { name: "reasoning", model: "qwen-3.8-27b", provider: "qwen", description: "推理模型", active: false },
];

beforeEach(() => {
  vi.clearAllMocks();
  useUIStore.setState({ currentModel: "fast", modelProfileVersion: 0, settingsOpen: false });
  Object.defineProperty(window, "matchMedia", { configurable: true, value: vi.fn().mockImplementation(() => ({
    matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn(),
  })) });
  HTMLElement.prototype.scrollIntoView = vi.fn();
  apiGet.mockImplementation(async (path: string) => path === "/models" ? { models } : { items: [] });
});
afterEach(cleanup);

describe("model picker interactions", () => {
  it("finds models by display name, resolved model ID and provider, trimming the query", () => {
    render(<ModelPickerContent models={models} currentModel="fast" onSelect={vi.fn()} onClose={vi.fn()} />);
    const input = screen.getByRole("textbox");
    for (const query of ["  deepseek-v4.1  ", "DeepSeek Flash"]) {
      fireEvent.change(input, { target: { value: query } });
      expect(screen.getAllByRole("button", { pressed: true })).toHaveLength(1);
      expect(screen.queryByText("reasoning")).toBeNull();
    }
    fireEvent.change(input, { target: { value: "百炼" } });
    expect(screen.getByText("reasoning")).toBeTruthy();
    expect(screen.queryByText("DeepSeek Flash")).toBeNull();
    fireEvent.change(input, { target: { value: "no-match" } });
    expect(screen.getByRole("status").textContent).toContain("未找到匹配");
    fireEvent.click(screen.getByLabelText("清空搜索"));
    expect(screen.getByText("DeepSeek Flash")).toBeTruthy();
  });

  it("moves from search into the model list with arrow keys and preserves search editing keys", () => {
    const select = vi.fn();
    render(<ModelPickerContent models={models} currentModel="fast" onSelect={select} onClose={vi.fn()} />);
    const search = screen.getByRole("textbox");
    search.focus();
    fireEvent.keyDown(search, { key: "Home" });
    expect(document.activeElement).toBe(search);
    fireEvent.keyDown(search, { key: "ArrowDown" });
    expect(document.activeElement?.textContent).toContain("DeepSeek Flash");
    fireEvent.keyDown(document.activeElement!, { key: "ArrowDown" });
    expect(document.activeElement?.textContent).toContain("reasoning");
    fireEvent.click(document.activeElement!);
    expect(select).toHaveBeenCalledWith("reasoning");
  });

  it("keeps the active model in its provider group and highlights it without reordering", () => {
    const providerModels: ModelInfo[] = [
      { name: "codex proxy", model: "gpt-6-astra", base_url: "https://gateway.example.com/v1", protocol: "openai", active: true },
      { name: "codex direct", model: "gpt-6-sol", base_url: "https://gateway.example.com/v1", protocol: "openai", active: false },
    ];
    const view = render(<ModelPickerContent models={providerModels} currentModel="codex proxy" onSelect={vi.fn()} onClose={vi.fn()} />);
    const group = screen.getByRole("region", { name: "codex proxy" });
    const rows = () => Array.from(group.querySelectorAll<HTMLButtonElement>("[data-model-row]"));
    expect(group.textContent).toContain("codex proxy2");
    expect(rows().map((row) => row.textContent)).toEqual(expect.arrayContaining([expect.stringContaining("codex proxy"), expect.stringContaining("codex direct")]));
    expect(rows()).toHaveLength(2);
    expect(rows().map((row) => row.getAttribute("aria-pressed"))).toEqual(["true", "false"]);
    expect(rows().every((row) => row.querySelector('[style*="openai.svg"]'))).toBe(true);

    view.rerender(<ModelPickerContent models={providerModels} currentModel="codex direct" onSelect={vi.fn()} onClose={vi.fn()} />);
    expect(rows().map((row) => row.textContent?.includes("codex proxy"))).toEqual([true, false]);
    expect(rows().map((row) => row.getAttribute("aria-pressed"))).toEqual(["false", "true"]);
    expect(screen.queryByText("当前使用")).toBeNull();
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "codex proxy" } });
    expect(screen.getByRole("region", { name: "codex proxy" }).querySelectorAll("[data-model-row]")).toHaveLength(2);
  });

  it("uses each model brand for logos while retaining provider groups", () => {
    const brandedModels: ModelInfo[] = [
      { name: "claude-sonnet-4-6", model: "antigravity/claude-sonnet-4-6", active: false },
      { name: "codex proxy", model: "gpt-6-astra", base_url: "https://gateway.example.com/v1", active: true },
      { name: "小米 MiMo", model: "mimo-v2.6-pro-ultraspeed", active: false },
    ];
    render(<ModelPickerContent models={brandedModels} currentModel="codex proxy" onSelect={vi.fn()} onClose={vi.fn()} />);
    const rows = Array.from(document.querySelectorAll<HTMLButtonElement>("[data-model-row]"));
    expect(rows.map((row) => row.querySelector("[role=img]")?.getAttribute("aria-label")))
      .toEqual(["Antigravity", "OpenAI", "小米 MiMo"]);
    expect(rows.map((row) => row.querySelector("[role=img] > span")?.getAttribute("style")))
      .toEqual([expect.stringContaining("gemini.svg"), expect.stringContaining("openai.svg"), expect.stringContaining("xiaomi.svg")]);
    expect(screen.getByRole("region", { name: "codex proxy" })).toBeTruthy();
  });

  it("opens at the active model after loading and does not scroll again on selection", () => {
    const rect = (top: number, height: number) => ({ top, height }) as DOMRect;
    const geometry = vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
      return this.getAttribute("data-selected") === "true" ? rect(350, 50) : rect(100, 200);
    });
    const height = vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockImplementation(function (this: HTMLElement) {
      return this.hasAttribute("aria-busy") ? 200 : 0;
    });
    try {
      const view = render(<ModelPickerContent models={[]} currentModel="reasoning" onSelect={vi.fn()} onClose={vi.fn()} />);
      const list = document.getElementById(screen.getByRole("textbox").getAttribute("aria-controls")!)!;
      expect(list.scrollTop).toBe(0);
      view.rerender(<ModelPickerContent models={models} currentModel="reasoning" onSelect={vi.fn()} onClose={vi.fn()} />);
      expect(list.scrollTop).toBe(175);

      list.scrollTop = 40;
      view.rerender(<ModelPickerContent models={models} currentModel="fast" onSelect={vi.fn()} onClose={vi.fn()} />);
      expect(list.scrollTop).toBe(40);

      view.unmount();
      const reopened = render(<ModelPickerContent models={models} currentModel="fast" onSelect={vi.fn()} onClose={vi.fn()} />);
      const reopenedList = document.getElementById(screen.getByRole("textbox").getAttribute("aria-controls")!)!;
      expect(reopenedList.scrollTop).toBe(175);
      reopened.unmount();
    } finally {
      geometry.mockRestore();
      height.mockRestore();
    }
  });

  it("uses the configured custom provider name in the top selector", async () => {
    const providerModels: ModelInfo[] = [
      { name: "codex proxy", model: "gpt-6-astra", base_url: "https://gateway.example.com/v1", active: true },
      { name: "codex direct", model: "gpt-6-sol", base_url: "https://gateway.example.com/v1", active: false },
    ];
    apiGet.mockImplementation(async (path: string) => path === "/models" ? { models: providerModels } : { items: [] });
    useUIStore.setState({ currentModel: "codex proxy" });
    render(<TopModelSelector />);
    const trigger = await screen.findByRole("button", { name: "选择模型：codex proxy" });
    expect(trigger.querySelector('[aria-label="OpenAI 品牌"] [role="img"]')?.getAttribute("style"))
      .toContain("openai.svg");
    fireEvent.click(trigger);
    const group = await screen.findByRole("region", { name: "codex proxy" });
    expect(group.querySelectorAll("[data-model-row]")).toHaveLength(2);
  });

  it("distinguishes empty, loading and failed lists and offers a real reload action", () => {
    const reload = vi.fn();
    const props = { models: [], currentModel: "", onSelect: vi.fn(), onClose: vi.fn(), onReload: reload };
    const view = render(<ModelPickerContent {...props} loading />);
    expect(screen.getByRole("status").textContent).toContain("正在加载");
    view.rerender(<ModelPickerContent {...props} loadError="Failed to fetch" />);
    expect(screen.getByRole("status").textContent).toContain("加载失败");
    expect(screen.getByRole("alert").textContent).toContain("暂时无法连接服务");
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(reload).toHaveBeenCalledOnce();
    view.rerender(<ModelPickerContent {...props} />);
    expect(screen.getByRole("status").textContent).toContain("还没有配置模型");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("focuses search on desktop and closes with Escape without changing the model", async () => {
    render(<TopModelSelector />);
    const trigger = await screen.findByRole("button", { name: "选择模型：DeepSeek Flash" });
    fireEvent.click(trigger);
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("textbox")));
    fireEvent.keyDown(screen.getByRole("textbox"), { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(apiPut).not.toHaveBeenCalled();
  });

  it("keeps the picker open on a failed switch and closes only after a successful retry", async () => {
    apiPut.mockRejectedValueOnce(new TypeError("Failed to fetch")).mockResolvedValueOnce({});
    render(<TopModelSelector />);
    fireEvent.click(await screen.findByRole("button", { name: "选择模型：DeepSeek Flash" }));
    fireEvent.click(await screen.findByRole("button", { name: /reasoning/ }));
    expect((await screen.findByRole("alert")).textContent).toContain("模型未切换，请重新选择");
    expect(useUIStore.getState().currentModel).toBe("fast");
    fireEvent.click(screen.getByRole("button", { name: /reasoning/ }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(apiPut).toHaveBeenCalledTimes(2);
    expect(apiPut).toHaveBeenLastCalledWith("/models/active", { name: "reasoning" }, { direct: true });
  });

  it("returns focus to the mobile trigger when the sheet is dismissed", async () => {
    vi.mocked(window.matchMedia).mockReturnValue({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() } as unknown as MediaQueryList);
    render(<TopModelSelector />);
    const trigger = await screen.findByRole("button", { name: "选择模型：DeepSeek Flash" });
    fireEvent.click(trigger);
    const close = await screen.findByRole("button", { name: "关闭模型选择" });
    expect(screen.getByRole("dialog")).toBeTruthy();
    fireEvent.click(close);
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    await waitFor(() => expect(document.activeElement).toBe(trigger));
  });
});
