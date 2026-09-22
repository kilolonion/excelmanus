// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
const mocks = vi.hoisted(() => ({ blocker: vi.fn((): string | null => null) }));
vi.mock("@/lib/app-refresh", () => ({ appRefreshBlocker: mocks.blocker }));
import { DesktopUpdateProgress } from "@/components/DesktopUpdateProgress";
import { DesktopUpdateCard } from "@/components/settings/DesktopUpdateCard";

const initial: ExcelManusDesktopUpdateStatus = { revision: 1, phase: "downloading", received: 1024 * 1024,
  total: 4 * 1024 * 1024, percent: 25, bytesPerSecond: 1024 * 1024, error: "", latest: "1.9.0" };

function bridge(snapshot: Promise<ExcelManusDesktopUpdateStatus> = Promise.resolve(initial)) {
  let receive: (value: ExcelManusDesktopUpdateStatus) => void = () => {};
  const unsubscribe = vi.fn();
  window.excelManusDesktop = {
    selectFolder: vi.fn(), pickChatFiles: vi.fn(), getUpdateStatus: vi.fn(() => snapshot),
    onUpdateStatus: vi.fn(callback => { receive = callback; return unsubscribe; }),
    downloadUpdate: vi.fn().mockResolvedValue(undefined), cancelUpdate: vi.fn().mockResolvedValue(undefined),
    installUpdate: vi.fn().mockResolvedValue(initial),
  };
  return { send: (value: Partial<ExcelManusDesktopUpdateStatus>) => act(() => receive({ ...initial, ...value })), unsubscribe };
}

afterEach(() => { cleanup(); delete window.excelManusDesktop; mocks.blocker.mockReturnValue(null); });

describe("desktop progress", () => {
  it("shows bytes, percentage, speed and cancel; restores progress on remount", async () => {
    const { unsubscribe } = bridge();
    const first = render(<DesktopUpdateProgress />);
    expect((await screen.findByRole("progressbar")).getAttribute("value")).toBe("25");
    expect(screen.getByText(/25.0% · 1.0 MB \/ 4.0 MB · 1.0 MB\/s/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "取消下载" }));
    expect(window.excelManusDesktop!.cancelUpdate).toHaveBeenCalledOnce();
    first.unmount();
    expect(unsubscribe).toHaveBeenCalledOnce();
    render(<DesktopUpdateProgress />);
    expect((await screen.findByRole("progressbar")).getAttribute("value")).toBe("25");
  });

  it("does not overwrite events with a stale snapshot and handles unknown size", async () => {
    let resolve!: (status: ExcelManusDesktopUpdateStatus) => void;
    const { send } = bridge(new Promise(done => { resolve = done; }));
    render(<DesktopUpdateProgress />);
    send({ revision: 5, received: 2048, total: null, percent: null });
    await act(async () => resolve(initial));
    expect(screen.getByText(/大小未知 · 2.0 KB/)).toBeTruthy();
    expect(screen.getByRole("progressbar").hasAttribute("value")).toBe(false);
  });

  it("retains errors, supports download retry and guards installation and native quit", async () => {
    const { send } = bridge();
    render(<DesktopUpdateProgress />);
    await screen.findByRole("progressbar");
    send({ revision: 2, phase: "error", error: "安装包校验失败" });
    expect(screen.getByRole("alert").textContent).toBe("安装包校验失败");
    fireEvent.click(screen.getByRole("button", { name: "重新下载" }));
    expect(window.excelManusDesktop!.downloadUpdate).toHaveBeenCalledOnce();
    send({ revision: 3, phase: "ready" });
    mocks.blocker.mockReturnValue("还有任务正在运行");
    fireEvent.click(screen.getByRole("button", { name: "退出并更新" }));
    expect(screen.getByRole("alert").textContent).toBe("还有任务正在运行");
    expect(window.excelManusDesktop!.installUpdate).not.toHaveBeenCalled();
    const blocked = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(blocked);
    expect(blocked.defaultPrevented).toBe(true);
    mocks.blocker.mockReturnValue(null);
    fireEvent.click(screen.getByRole("button", { name: "退出并更新" }));
    expect(window.excelManusDesktop!.installUpdate).toHaveBeenCalledOnce();
  });

  it("does not claim a browser download for the new native bridge", async () => {
    bridge(Promise.resolve({ ...initial, phase: "idle" }));
    window.excelManusDesktop!.checkUpdate = vi.fn().mockResolvedValue({ current: "1.8.0", latest: "1.9.0",
      hasUpdate: true, downloadUrl: "https://github.com/kilolonion/excelmanus/releases/download/v1.9.0/setup.exe" });
    render(<DesktopUpdateCard current="1.8.0" />);
    fireEvent.click(screen.getByRole("button", { name: "检查更新" }));
    fireEvent.click(await screen.findByRole("button", { name: "下载 v1.9.0 安装包" }));
    await act(async () => {});
    expect(window.excelManusDesktop!.downloadUpdate).toHaveBeenCalledOnce();
    expect(screen.queryByText(/已打开浏览器下载/)).toBeNull();
  });
});
