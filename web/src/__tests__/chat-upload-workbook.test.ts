import { beforeEach, expect, it } from "vitest";
import { trackRecentExcelFile } from "@/components/chat/chat-input-insert";
import { resolveSheetFullViewTarget } from "@/lib/chat-workspace-tabs";
import { useExcelStore } from "@/stores/excel-store";

beforeEach(() => useExcelStore.setState({ recentFiles: [], dismissedPaths: new Set(), activeWorkspaceKey: null }));

it("makes an upload available to the Sheet menu before the file sidebar is loaded", () => {
  trackRecentExcelFile("uploads/报表.xlsx", "报表.xlsx", "id:upload-workspace");
  useExcelStore.getState().rebindSession(null, "id:upload-workspace");
  const input = {
    activeFilePath: null, activeSheet: null, fullViewPath: null, fullViewSheet: null,
    recentFiles: useExcelStore.getState().recentFiles, workspaceFiles: [], workspaceKey: useExcelStore.getState().activeWorkspaceKey,
  };
  expect(resolveSheetFullViewTarget(input)?.path).toBe("uploads/报表.xlsx");
  expect(resolveSheetFullViewTarget({ ...input, workspaceKey: "id:other-workspace" })).toBeNull();
});

it("does not offer an uploaded image as a workbook", () => {
  trackRecentExcelFile("uploads/图片.jpg", "图片.jpg", "id:workspace");
  expect(useExcelStore.getState().recentFiles).toEqual([]);
});
