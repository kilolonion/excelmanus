// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { DesktopUpdateCard } from "@/components/settings/DesktopUpdateCard";

const available: ExcelManusDesktopUpdate = {
  current: "1.8.0", latest: "1.9.0", hasUpdate: true, platform: "win32",
  releaseNotes: "New features", releaseUrl: "https://github.com/kilolonion/excelmanus/releases/tag/v1.9.0",
  downloadUrl: "https://github.com/kilolonion/excelmanus/releases/download/v1.9.0/installer.exe", installerName: "installer.exe",
};

afterEach(() => { cleanup(); delete window.excelManusDesktop; });

describe("desktop updates", () => {
  it("checks releases, downloads through the native bridge and explains preservation", async () => {
    const download = vi.fn().mockResolvedValue(undefined);
    window.excelManusDesktop = {
      selectFolder: vi.fn(), pickChatFiles: vi.fn(),
      checkUpdate: vi.fn().mockResolvedValue(available), downloadUpdate: download,
    };
    render(<DesktopUpdateCard current="1.8.0" />);
    expect(screen.getByText(/不会删除、移动或修改工作区/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "检查更新" }));
    fireEvent.click(await screen.findByRole("button", { name: "下载 v1.9.0 安装包" }));
    expect(await screen.findByText(/下载完成后，保存工作并退出/)).toBeTruthy();
    expect(download).toHaveBeenCalledOnce();
  });

  it("shows unavailable packages and failed checks without a stale download button", async () => {
    const check = vi.fn().mockResolvedValueOnce(available)
      .mockResolvedValueOnce({ ...available, downloadUrl: null })
      .mockRejectedValueOnce(new Error("网络连接失败"));
    window.excelManusDesktop = { selectFolder: vi.fn(), pickChatFiles: vi.fn(), checkUpdate: check, downloadUpdate: vi.fn() };
    render(<DesktopUpdateCard current="1.8.0" />);
    fireEvent.click(screen.getByRole("button", { name: "检查更新" }));
    await screen.findByRole("button", { name: "下载 v1.9.0 安装包" });
    fireEvent.click(screen.getByRole("button", { name: "检查更新" }));
    expect(await screen.findByText(/尚未发布适用于本机的安装包/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "下载 v1.9.0 安装包" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "检查更新" }));
    expect((await screen.findByRole("alert")).textContent).toBe("网络连接失败");
    expect(screen.queryByRole("button", { name: "下载 v1.9.0 安装包" })).toBeNull();
  });

  it("keeps a release-page fallback for older desktop bridges", () => {
    render(<DesktopUpdateCard current="1.8.0" />);
    expect(screen.queryByRole("button", { name: "检查更新" })).toBeNull();
    expect(screen.getByRole("link", { name: "查看发布页面" }).getAttribute("href")).toBe("https://github.com/kilolonion/excelmanus/releases");
  });
});
