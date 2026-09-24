// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AddWorkspaceDialog } from "@/components/sidebar/AddWorkspaceDialog";
import { createWorkspaceFolder, selectWorkspaceFolder } from "@/lib/api";

vi.mock("@/lib/api", () => ({
  createWorkspaceFolder: vi.fn().mockResolvedValue({}),
  updateWorkspaceFolder: vi.fn(),
  selectWorkspaceFolder: vi.fn(),
}));

beforeEach(() => { vi.clearAllMocks(); });
afterEach(() => { cleanup(); delete window.excelManusDesktop; });

function openDialog() {
  const onCreated = vi.fn();
  const onOpenChange = vi.fn();
  render(<AddWorkspaceDialog open onCreated={onCreated} onOpenChange={onOpenChange} />);
  fireEvent.click(screen.getByRole("button", { name: "选择 ExcelManus 可读取和编辑的文件夹" }));
  return { onCreated, onOpenChange };
}

describe("workspace folder selection", () => {
  it("uses the local backend in a browser and submits the selected absolute path", async () => {
    vi.mocked(selectWorkspaceFolder).mockResolvedValue("/Users/test/季度 报表");
    const { onCreated, onOpenChange } = openDialog();
    await waitFor(() => expect((screen.getByLabelText("源文件夹路径") as HTMLInputElement).value).toBe("/Users/test/季度 报表"));
    fireEvent.click(screen.getByRole("button", { name: "添加工作区" }));
    await waitFor(() => expect(onCreated).toHaveBeenCalledOnce());
    expect(createWorkspaceFolder).toHaveBeenCalledWith("/Users/test/季度 报表", "季度 报表");
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it("keeps the desktop native picker", async () => {
    const selectFolder = vi.fn().mockResolvedValue("/Users/test/Desktop");
    window.excelManusDesktop = { selectFolder, pickChatFiles: vi.fn() };
    openDialog();
    await screen.findByDisplayValue("/Users/test/Desktop");
    expect(selectFolder).toHaveBeenCalledOnce();
    expect(selectWorkspaceFolder).not.toHaveBeenCalled();
  });

  it("cancels without an error or creating a workspace, and prevents duplicate clicks", async () => {
    let resolve!: (path: string | null) => void;
    vi.mocked(selectWorkspaceFolder).mockReturnValue(new Promise((done) => { resolve = done; }));
    openDialog();
    const pending = screen.getByRole("button", { name: "正在打开系统文件夹选择器…" });
    expect((pending as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(pending);
    expect(selectWorkspaceFolder).toHaveBeenCalledOnce();
    await act(async () => { resolve(null); });
    expect(screen.queryByLabelText("源文件夹路径")).toBeNull();
    expect(createWorkspaceFolder).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "选择 ExcelManus 可读取和编辑的文件夹" })).toBeTruthy();
  });

  it("keeps manual entry usable when native selection fails", async () => {
    vi.mocked(selectWorkspaceFolder).mockRejectedValue(new Error("文件夹选择已超时，请重新选择"));
    openDialog();
    await screen.findByText("文件夹选择已超时，请重新选择");
    fireEvent.change(screen.getByLabelText("源文件夹路径"), { target: { value: "/Users/test/Documents" } });
    fireEvent.click(screen.getByRole("button", { name: "添加工作区" }));
    await waitFor(() => expect(createWorkspaceFolder).toHaveBeenCalledWith("/Users/test/Documents", "Documents"));
  });
});
