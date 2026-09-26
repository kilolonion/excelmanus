/**
 * 后端确认路径不可读后退役表格目标（excel-store.dropMissingWorkbook）：
 * - 陈旧标签从工作簿组里关闭，面板不再反复挂载同一个 404；
 * - 最近打开、版本/变更缓存、面板与全屏视图目标一并清理；
 * - 其它工作区不受影响，也不写 dismissedPaths（文件重建后仍可打开）。
 */

import { beforeEach, describe, expect, it } from "vitest";

import { useExcelStore } from "@/stores/excel-store";
import { useSessionStore } from "@/stores/session-store";
import { useWorkbookConversationStore } from "@/stores/workbook-conversation-store";
import { useWorkbookWorkspaceStore, workbookWorkspaceKey } from "@/stores/workbook-workspace-store";
import { fileRefFromSession, versionStoreKey } from "@/lib/workspace-file-ref";
import type { Session } from "@/lib/types";

const session: Session = { id: "s1", title: "A", messageCount: 0, inFlight: false, workspaceId: "ws" };
const KEY = workbookWorkspaceKey(session.id, "id:ws");
const GONE = "outputs/月度销售经营看板.xlsx";
const KEPT = "outputs/广告与销售_回归分析报告.xlsx";

function reset() {
  useSessionStore.setState({ sessions: [session], activeSessionId: session.id });
  useWorkbookWorkspaceStore.setState({ workspaces: {} });
  useWorkbookConversationStore.setState({ targets: {}, views: {} });
  useExcelStore.setState({
    panelOpen: true,
    activeFilePath: GONE,
    activeSheet: "看板",
    activeWorkspaceKey: "id:ws",
    fullViewPath: GONE,
    fullViewSheet: "看板",
    selectionMode: true,
    draftRange: { path: GONE, sheet: "看板", range: "A1:B2" },
    recentFiles: [
      { path: GONE, filename: "月度销售经营看板.xlsx", lastUsedAt: 2, workspaceKey: "id:ws" },
      { path: KEPT, filename: "广告与销售_回归分析报告.xlsx", lastUsedAt: 1, workspaceKey: "id:ws" },
      { path: GONE, filename: "月度销售经营看板.xlsx", lastUsedAt: 1, workspaceKey: "id:other" },
    ],
    dismissedPaths: new Set<string>(),
    contentVersions: { [versionStoreKey(GONE, "id:ws")]: "sha256:old" },
    workbookChanges: { [versionStoreKey(GONE, "id:ws")]: { sequence: 1, source: "remote" } },
  });
  useWorkbookWorkspaceStore.getState().open(KEY, GONE, "看板");
  useWorkbookWorkspaceStore.getState().open(KEY, KEPT);
  useWorkbookConversationStore.getState().bind(session.id, fileRefFromSession(GONE, session), "看板");
}

describe("dropMissingWorkbook", () => {
  beforeEach(reset);

  it("closes the dead tab and keeps the remaining workbook", () => {
    useExcelStore.getState().dropMissingWorkbook(GONE, "id:ws");
    const files = useWorkbookWorkspaceStore.getState().workspaces[KEY].files.map((f) => f.path);
    expect(files).toEqual([KEPT]);
    expect(useWorkbookWorkspaceStore.getState().workspaces[KEY].focused).toBe(KEPT);
  });

  it("clears the panel and full-view target that pointed at the missing file", () => {
    useExcelStore.getState().dropMissingWorkbook(GONE, "id:ws");
    const state = useExcelStore.getState();
    expect(state.activeFilePath).toBeNull();
    expect(state.activeSheet).toBeNull();
    expect(state.fullViewPath).toBeNull();
    expect(state.selectionMode).toBe(false);
    expect(state.draftRange).toBeNull();
  });

  it("evicts only the failing workspace bucket and never dismisses the path", () => {
    useExcelStore.getState().dropMissingWorkbook(GONE, "id:ws");
    const state = useExcelStore.getState();
    expect(state.recentFiles.map((f) => `${f.workspaceKey}|${f.path}`)).toEqual([
      `id:ws|${KEPT}`,
      `id:other|${GONE}`,
    ]);
    // 文件重建后必须还能重新出现，因此不写 dismissal。
    expect(state.dismissedPaths.size).toBe(0);
  });

  it("drops the stale version and change records for that identity", () => {
    useExcelStore.getState().dropMissingWorkbook(GONE, "id:ws");
    const state = useExcelStore.getState();
    expect(state.contentVersions[versionStoreKey(GONE, "id:ws")]).toBeUndefined();
    expect(state.workbookChanges[versionStoreKey(GONE, "id:ws")]).toBeUndefined();
  });

  it("rebinds the conversation to the remaining primary workbook", () => {
    useExcelStore.getState().dropMissingWorkbook(GONE, "id:ws");
    expect(useWorkbookConversationStore.getState().targets[session.id]?.file.relative).toBe(KEPT);
  });

  it("leaves another workspace's tab and target untouched", () => {
    const otherGroup = workbookWorkspaceKey("s2", "id:other");
    useWorkbookWorkspaceStore.getState().open(otherGroup, GONE, "看板");
    useExcelStore.getState().dropMissingWorkbook(GONE, "id:ws");
    expect(useWorkbookWorkspaceStore.getState().workspaces[otherGroup].files.map((f) => f.path)).toEqual([GONE]);
  });
});
