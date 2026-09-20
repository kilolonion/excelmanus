import { afterEach, expect, it, vi } from "vitest";
import { saveBlob } from "@/lib/save-blob";

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

it("hands Android files to the system save bridge without creating a browser download", () => {
  const nativeSave = vi.fn();
  const objectUrl = vi.spyOn(URL, "createObjectURL");
  vi.stubGlobal("window", { excelManusAndroid: { version: 1, saveBlob: nativeSave } });
  const blob = new Blob(["result"]);
  saveBlob(blob, "C:\\报表\\本月结果.xlsx");
  expect(nativeSave).toHaveBeenCalledWith(blob, "本月结果.xlsx");
  expect(objectUrl).not.toHaveBeenCalled();
});

it("keeps Chinese Windows filenames and waits for the desktop download to consume the URL", () => {
  vi.useFakeTimers();
  const revoke = vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
  vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:download");
  const anchor = { href: "", download: "", click: vi.fn(), remove: vi.fn() };
  vi.stubGlobal("document", { createElement: () => anchor, body: { appendChild: vi.fn() } });
  saveBlob(new Blob(["result"]), "C:\\报表\\本月结果.xlsx");
  expect(anchor.download).toBe("本月结果.xlsx");
  expect(anchor.click).toHaveBeenCalledOnce();
  expect(anchor.remove).toHaveBeenCalledOnce();
  expect(revoke).not.toHaveBeenCalled();
  vi.runAllTimers();
  expect(revoke).toHaveBeenCalledWith("blob:download");
});
