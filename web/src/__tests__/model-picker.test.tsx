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
