/**
 * SSE mutation 删除分流单测：
 * - MUTATION 事件中 deleted=true 的身份只走 handleWorkspaceFilesDeleted（统一清理），
 *   不得再进入 recentFiles / workbookChanges。
 * - deleted 与 live 混合批次按身份正确分流。
 * - legacy FILES_CHANGED 事件仍按原有“全部视为写入”的逻辑处理。
 *
 * chat-store 被 mock（只提供 addAffectedFiles 等被分发器调用的动作）；
 * excel / word / file-preview / session 使用真实 store，以便断言真实副作用。
 */

import { beforeEach, describe, expect, it, vi } from "vitest";

const { chatMock } = vi.hoisted(() => ({
  chatMock: {
    messages: [] as unknown[],
    messagesById: {} as Record<string, unknown>,
    addAffectedFiles: vi.fn(),
  },
}));

vi.mock("@/stores/chat-store", () => ({
  useChatStore: { getState: () => chatMock },
}));

vi.mock("@/lib/open-workspace-file", () => ({ openWorkspaceFile: vi.fn() }));

vi.mock("@/hooks/use-mobile", () => ({
  getIsMobile: () => false,
  getIsTablet: () => false,
  getIsDesktop: () => true,
  getIsMediumScreen: () => false,
}));

import { dispatchSSEEvent, type DeltaBatcher, type SSEHandlerContext, type SSEEvent } from "@/lib/sse-event-handler";
import { useExcelStore, type ExcelFileRef } from "@/stores/excel-store";
import { useFilePreviewStore } from "@/stores/file-preview-store";
import { useSessionStore } from "@/stores/session-store";
import { useWordStore } from "@/stores/word-store";
import { useWorkbookConversationStore } from "@/stores/workbook-conversation-store";
import { useWorkbookWorkspaceStore } from "@/stores/workbook-workspace-store";
import type { Session } from "@/lib/types";

const WS = "id:ws";
const WS_OTHER = "id:ws-other";
const session: Session = { id: "s1", title: "S", messageCount: 0, inFlight: false, workspaceId: "ws" };

function makeBatcher(): DeltaBatcher {
  return {
    pushText: vi.fn(),
    pushThinking: vi.fn(),
    flush: vi.fn(),
    dispose: vi.fn(),
    hasPendingContent: vi.fn().mockReturnValue(false),
  };
}

function makeCtx(overrides: Partial<SSEHandlerContext> = {}): SSEHandlerContext {
  return {
    assistantMsgId: "a1",
    batcher: makeBatcher(),
    effectiveSessionId: "s1",
    isFirstSend: true,
    thinkingInProgress: false,
    hadStreamError: false,
    ...overrides,
  };
}

function mutation(data: Record<string, unknown>): SSEEvent {
  return { event: "mutation", data };
}

function filesChanged(files: string[]): SSEEvent {
  return { event: "files_changed", data: { files } };
}

function excelRef(path: string, workspaceKey: string, lastUsedAt = 1): ExcelFileRef {
  return { path, filename: path.split("/").pop() ?? path, lastUsedAt, workspaceKey };
}

function recentPaths(): string[] {
  return useExcelStore.getState().recentFiles.map((file) => file.path);
}

function resetAll() {
  chatMock.addAffectedFiles.mockClear();
  useExcelStore.getState().clearSession();
  useExcelStore.setState({
    panelOpen: false,
    activeFilePath: null,
    activeSheet: null,
    activeWorkspaceKey: WS,
    fullViewPath: null,
    fullViewSheet: null,
    recentFiles: [],
    dismissedPaths: new Set<string>(),
    contentVersions: {},
    workbookChanges: {},
    liveSelection: null,
    draftRange: null,
    pendingSelection: null,
    workspaceFiles: [],
    workspaceFilesSessionId: "s1",
    workspaceFilesWorkspaceId: "ws",
    workspaceFilesVersion: 0,
  });
  useWordStore.setState({ recentFiles: [], panelOpen: false, activeDocPath: null, fullViewPath: null, docSnapshot: null });
  useFilePreviewStore.setState({ textOpen: false, imageOpen: false, textTarget: null, imageTarget: null, previewTabs: [] });
  useWorkbookWorkspaceStore.setState({ workspaces: {} });
  useWorkbookConversationStore.setState({ targets: {}, views: {}, pickerOpen: false });
  useSessionStore.setState({ sessions: [session], activeSessionId: "s1" });
}

beforeEach(() => {
  resetAll();
});

describe("mutation deleted 身份分流", () => {
  it("deleted 身份只走清理：不进 recentFiles，写 dismissedPaths 并清缓存", () => {
    useExcelStore.setState({
      workspaceFiles: [
        { path: "report.xlsx", filename: "report.xlsx" },
        { path: "keep.xlsx", filename: "keep.xlsx" },
      ],
      recentFiles: [excelRef("./report.xlsx", WS, 2), excelRef("./keep.xlsx", WS, 1)],
      contentVersions: { [`${WS}|./report.xlsx`]: "v1" },
      workbookChanges: { [`${WS}|./report.xlsx`]: { sequence: 1, version: "v1", source: "remote" } },
    });
    const versionBefore = useExcelStore.getState().workspaceFilesVersion;

    dispatchSSEEvent(mutation({
      files: ["report.xlsx"],
      mutations: [{ identity: "report.xlsx", deleted: true }],
    }), makeCtx());

    expect(recentPaths()).toEqual(["./keep.xlsx"]);
    expect(useExcelStore.getState().workspaceFiles.map((file) => file.path)).toEqual(["keep.xlsx"]);
    expect(useExcelStore.getState().dismissedPaths.has(`${WS}|./report.xlsx`)).toBe(true);
    expect(useExcelStore.getState().contentVersions).toEqual({});
    expect(useExcelStore.getState().workbookChanges).toEqual({});
    // handleFilesDeleted(+1) 与 bumpWorkspaceFilesVersion(+1)
    expect(useExcelStore.getState().workspaceFilesVersion).toBe(versionBefore + 2);
    expect(chatMock.addAffectedFiles).toHaveBeenCalledWith("a1", ["report.xlsx"]);
  });

  it("deleted 与 live 混合批次分流正确：live 记录 content_version，deleted 不记录", () => {
    useExcelStore.setState({
      workspaceFiles: [
        { path: "report.xlsx", filename: "report.xlsx" },
        { path: "new.xlsx", filename: "new.xlsx" },
      ],
      recentFiles: [excelRef("./report.xlsx", WS, 3), excelRef("./keep.xlsx", WS, 2)],
    });

    dispatchSSEEvent(mutation({
      files: ["report.xlsx", "new.xlsx"],
      mutations: [
        { identity: "./report.xlsx", deleted: true },
        { identity: "./new.xlsx", content_version: "v2" },
      ],
    }), makeCtx());

    expect(recentPaths()).toEqual(["./new.xlsx", "./keep.xlsx"]);
    expect(useExcelStore.getState().contentVersions).toEqual({ [`${WS}|./new.xlsx`]: "v2" });
    expect(Object.keys(useExcelStore.getState().workbookChanges)).toEqual([`${WS}|./new.xlsx`]);
    // addAffectedFiles 收到的是 files 与 mutation identity 的原样并集（仅按字符串去重），
    // 因此 ./ 前缀写法不同的同一身份会各出现一次；副作用本身已按归一化身份去重。
    expect(chatMock.addAffectedFiles).toHaveBeenCalledWith("a1", [
      "report.xlsx",
      "new.xlsx",
      "./report.xlsx",
      "./new.xlsx",
    ]);
  });

  it("identity 与 files 的 ./ 前缀写法不一致时仍能识别 deleted", () => {
    useExcelStore.setState({ recentFiles: [excelRef("./gone.xlsx", WS, 1)] });

    dispatchSSEEvent(mutation({
      files: ["gone.xlsx"],
      mutations: [{ identity: "./gone.xlsx", deleted: true }],
    }), makeCtx());

    expect(recentPaths()).toEqual([]);
    expect(useExcelStore.getState().dismissedPaths.has(`${WS}|./gone.xlsx`)).toBe(true);
  });

  it("mutation 只带 identity、不带 files 时仍然走清理", () => {
    useExcelStore.setState({ recentFiles: [excelRef("./gone.xlsx", WS, 1)] });

    dispatchSSEEvent(mutation({
      mutations: [{ identity: "./gone.xlsx", deleted: true }],
    }), makeCtx());

    expect(recentPaths()).toEqual([]);
    expect(useExcelStore.getState().dismissedPaths.has(`${WS}|./gone.xlsx`)).toBe(true);
  });

  it("deleted=true 但缺少 identity 时不进入删除分流（回落为写入）", () => {
    dispatchSSEEvent(mutation({
      files: ["plain.xlsx"],
      mutations: [{ deleted: true }],
    }), makeCtx());

    expect(recentPaths()).toEqual(["plain.xlsx"]);
    expect(useExcelStore.getState().dismissedPaths.size).toBe(0);
  });

  it("删除只影响来源工作区桶，其它工作区同名文件保留", () => {
    useExcelStore.setState({
      recentFiles: [excelRef("./shared.xlsx", WS, 2), excelRef("./shared.xlsx", WS_OTHER, 1)],
    });

    dispatchSSEEvent(mutation({
      files: ["shared.xlsx"],
      mutations: [{ identity: "shared.xlsx", deleted: true }],
    }), makeCtx());

    expect(recentPaths()).toEqual(["./shared.xlsx"]);
    expect(useExcelStore.getState().recentFiles[0].workspaceKey).toBe(WS_OTHER);
    expect(useExcelStore.getState().dismissedPaths.has(`${WS}|./shared.xlsx`)).toBe(true);
    expect(useExcelStore.getState().dismissedPaths.has(`${WS_OTHER}|./shared.xlsx`)).toBe(false);
  });

  it("deleted 文档从 word 最近列表剔除，且不会被 handleFilesChanged 重新加入", () => {
    useWordStore.setState({ recentFiles: ["./docs/report.docx", "./docs/keep.docx"] });

    dispatchSSEEvent(mutation({
      files: ["./docs/report.docx", "./docs/keep.docx"],
      mutations: [{ identity: "./docs/report.docx", deleted: true }],
    }), makeCtx());

    expect(useWordStore.getState().recentFiles).toEqual(["./docs/keep.docx"]);
    // live 的非表格文档不污染 workbook 最近列表。
    expect(useExcelStore.getState().recentFiles).toEqual([]);
  });

  it("deleted 目标不在任何列表中时只写 dismissal，不新增最近文件", () => {
    dispatchSSEEvent(mutation({
      files: ["never-seen.xlsx"],
      mutations: [{ identity: "never-seen.xlsx", deleted: true }],
    }), makeCtx());

    expect(useExcelStore.getState().recentFiles).toEqual([]);
    expect(useExcelStore.getState().dismissedPaths.has(`${WS}|./never-seen.xlsx`)).toBe(true);
  });
});

describe("legacy files_changed 事件", () => {
  it("仍按原逻辑把文件加入最近列表并记录变更，不触发删除清理", () => {
    const versionBefore = useExcelStore.getState().workspaceFilesVersion;

    dispatchSSEEvent(filesChanged(["./legacy.xlsx"]), makeCtx());

    expect(recentPaths()).toEqual(["./legacy.xlsx"]);
    expect(useExcelStore.getState().recentFiles[0]).toMatchObject({ workspaceKey: WS });
    expect(useExcelStore.getState().dismissedPaths.size).toBe(0);
    expect(Object.keys(useExcelStore.getState().workbookChanges)).toEqual([`${WS}|./legacy.xlsx`]);
    // legacy 路径没有删除清理：addRecentFileIfNotDismissed 与显式 bump 各 +1。
    expect(useExcelStore.getState().workspaceFilesVersion).toBe(versionBefore + 2);
    expect(chatMock.addAffectedFiles).toHaveBeenCalledWith("a1", ["./legacy.xlsx"]);
  });

  it("已有的 dismissedPaths 仍然挡住 legacy 回声", () => {
    useExcelStore.setState({ dismissedPaths: new Set([`${WS}|./hidden.xlsx`]) });

    dispatchSSEEvent(filesChanged(["./hidden.xlsx"]), makeCtx());

    expect(useExcelStore.getState().recentFiles).toEqual([]);
  });

  it("legacy 事件不会像 mutation deleted 那样清空已打开表格", () => {
    useExcelStore.getState().openFullView("kept.xlsx");
    expect(useExcelStore.getState().fullViewPath).toBe("kept.xlsx");

    dispatchSSEEvent(filesChanged(["kept.xlsx"]), makeCtx());

    expect(useExcelStore.getState().fullViewPath).toBe("kept.xlsx");
  });
});
