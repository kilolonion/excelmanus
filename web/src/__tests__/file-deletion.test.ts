/**
 * 文件删除一致性单测：
 * - excel-store.handleFilesDeleted：workspaceFiles / recentFiles / dismissedPaths、
 *   已打开表格标签（含跨会话组）、activeFilePath / fullViewPath / 对比 / 选区、
 *   版本与变更缓存、workspaceFilesVersion。
 * - word-store.handleFilesDeleted：最近文档列表与文档面板/全视图/快照。
 * - lib/file-deletion.handleWorkspaceFilesDeleted：统一入口串起 excel + word + 预览弹窗。
 */

import { beforeEach, describe, expect, it } from "vitest";

import { handleWorkspaceFilesDeleted } from "@/lib/file-deletion";
import { useExcelStore, type ExcelFileRef } from "@/stores/excel-store";
import { useFilePreviewStore } from "@/stores/file-preview-store";
import { useSessionStore } from "@/stores/session-store";
import { useWordStore, type WordSnapshot } from "@/stores/word-store";
import { useWorkbookConversationStore } from "@/stores/workbook-conversation-store";
import {
  useWorkbookWorkspaceStore,
  workbookWorkspaceKey,
} from "@/stores/workbook-workspace-store";
import type { Session } from "@/lib/types";

const WS = "id:ws";
const WS_OTHER = "id:ws-other";

const sessionA: Session = { id: "s1", title: "A", messageCount: 0, inFlight: false, workspaceId: "ws" };
const sessionB: Session = { id: "s2", title: "B", messageCount: 0, inFlight: false, workspaceId: "ws" };
const sessionOther: Session = { id: "s3", title: "C", messageCount: 0, inFlight: false, workspaceId: "ws-other" };

const GROUP_A = workbookWorkspaceKey("s1", WS);
const GROUP_B = workbookWorkspaceKey("s2", WS);
const GROUP_OTHER = workbookWorkspaceKey("s3", WS_OTHER);

function bindSessions(activeSessionId: string, sessions: Session[] = [sessionA, sessionB, sessionOther]) {
  useSessionStore.setState({ sessions, activeSessionId });
}

function workspaceFilePaths(key: string): string[] {
  return (useWorkbookWorkspaceStore.getState().workspaces[key]?.files ?? []).map((file) => file.path);
}

function recentPaths(): string[] {
  return useExcelStore.getState().recentFiles.map((file) => file.path).sort();
}

function excelRef(path: string, workspaceKey: string, lastUsedAt = 1): ExcelFileRef {
  return { path, filename: path.split("/").pop() ?? path, lastUsedAt, workspaceKey };
}

function resetAll() {
  useExcelStore.getState().clearSession();
  useExcelStore.setState({
    panelOpen: false,
    activeFilePath: null,
    activeSheet: null,
    activeWorkspaceKey: null,
    fullViewPath: null,
    fullViewSheet: null,
    fullViewLayout: "embedded",
    compareMode: false,
    compareFileA: null,
    compareFileB: null,
    compareSheetA: null,
    compareSheetB: null,
    compareRelationship: null,
    compareReturnPath: null,
    recentFiles: [],
    dismissedPaths: new Set<string>(),
    contentVersions: {},
    workbookChanges: {},
    liveSelection: null,
    draftRange: null,
    pendingSelection: null,
    workspaceFiles: [],
    wsFilesLoaded: false,
    workspaceFilesSessionId: undefined,
    workspaceFilesWorkspaceId: null,
    workspaceFilesVersion: 0,
  });
  useWordStore.setState({
    panelOpen: false,
    panelTab: "doc",
    activeDocPath: null,
    activeWorkspaceKey: null,
    fullViewPath: null,
    docSnapshot: null,
    refreshCounter: 0,
    recentFiles: [],
  });
  useFilePreviewStore.setState({
    textOpen: false,
    imageOpen: false,
    textTarget: null,
    imageTarget: null,
    previewTabs: [],
  });
  useWorkbookWorkspaceStore.setState({ workspaces: {} });
  useWorkbookConversationStore.setState({ targets: {}, views: {}, pickerOpen: false });
  useSessionStore.setState({ sessions: [], activeSessionId: null });
}

beforeEach(() => {
  resetAll();
});

describe("excel-store.handleFilesDeleted", () => {
  it("剔除被删文件，写入 dismissedPaths 并 bump workspaceFilesVersion", () => {
    bindSessions("s1");
    useExcelStore.setState({
      activeWorkspaceKey: WS,
      workspaceFilesWorkspaceId: "ws",
      workspaceFiles: [
        { path: "report.xlsx", filename: "report.xlsx" },
        { path: "keep.xlsx", filename: "keep.xlsx" },
      ],
      recentFiles: [
        excelRef("./report.xlsx", WS, 3),
        excelRef("./keep.xlsx", WS, 2),
        // 其它工作区桶里的同名路径不受本次删除影响。
        excelRef("./report.xlsx", WS_OTHER, 1),
      ],
      contentVersions: { [`${WS}|./report.xlsx`]: "v1", [`${WS}|./keep.xlsx`]: "v2" },
      workbookChanges: { [`${WS}|./report.xlsx`]: { sequence: 1, version: "v1", source: "remote" } },
    });
    const versionBefore = useExcelStore.getState().workspaceFilesVersion;

    handleWorkspaceFilesDeleted(["./report.xlsx"], WS);

    expect(useExcelStore.getState().workspaceFiles.map((file) => file.path)).toEqual(["keep.xlsx"]);
    expect(recentPaths()).toEqual(["./keep.xlsx", "./report.xlsx"]);
    expect(useExcelStore.getState().recentFiles.find((file) => file.workspaceKey === WS_OTHER)?.path)
      .toBe("./report.xlsx");
    expect(useExcelStore.getState().dismissedPaths.has(`${WS}|./report.xlsx`)).toBe(true);
    expect(Object.keys(useExcelStore.getState().contentVersions)).toEqual([`${WS}|./keep.xlsx`]);
    expect(Object.keys(useExcelStore.getState().workbookChanges)).toEqual([]);
    expect(useExcelStore.getState().workspaceFilesVersion).toBe(versionBefore + 1);
  });

  it("删除文件夹时前缀下的后代一并剔除（workspaceFiles / recentFiles / contentVersions）", () => {
    bindSessions("s1");
    useExcelStore.setState({
      activeWorkspaceKey: WS,
      workspaceFilesWorkspaceId: "ws",
      workspaceFiles: [
        { path: "a/one.xlsx", filename: "one.xlsx" },
        { path: "a/b/two.xlsx", filename: "two.xlsx" },
        { path: "ab/three.xlsx", filename: "three.xlsx" },
        { path: "keep.xlsx", filename: "keep.xlsx" },
      ],
      recentFiles: [
        excelRef("./a/one.xlsx", WS, 4),
        excelRef("./a/b/two.xlsx", WS, 3),
        // "ab" 只是同前缀字符串，不是目录后代，必须保留。
        excelRef("./ab/three.xlsx", WS, 2),
      ],
      contentVersions: {
        [`${WS}|./a/one.xlsx`]: "v1",
        [`${WS}|./a/b/two.xlsx`]: "v2",
        [`${WS}|./ab/three.xlsx`]: "v3",
      },
    });

    handleWorkspaceFilesDeleted(["a"], WS);

    expect(useExcelStore.getState().workspaceFiles.map((file) => file.path)).toEqual(["ab/three.xlsx", "keep.xlsx"]);
    expect(recentPaths()).toEqual(["./ab/three.xlsx"]);
    expect(Object.keys(useExcelStore.getState().contentVersions)).toEqual([`${WS}|./ab/three.xlsx`]);
  });

  it("文件夹删除为每个已知后代补 dismissal 键（不止文件夹本身）", () => {
    bindSessions("s1");
    useExcelStore.setState({
      activeWorkspaceKey: WS,
      workspaceFilesWorkspaceId: "ws",
      workspaceFiles: [
        { path: "a/one.xlsx", filename: "one.xlsx" },
        { path: "a/b/two.xlsx", filename: "two.xlsx" },
      ],
      recentFiles: [excelRef("./a/one.xlsx", WS, 2)],
    });

    handleWorkspaceFilesDeleted(["a"], WS);

    const dismissed = useExcelStore.getState().dismissedPaths;
    // 文件夹目标本身。
    expect(dismissed.has(`${WS}|./a`)).toBe(true);
    // workspaceFiles 中的后代。
    expect(dismissed.has(`${WS}|./a/one.xlsx`)).toBe(true);
    expect(dismissed.has(`${WS}|./a/b/two.xlsx`)).toBe(true);
    // 不是裸键：dismissal 必须带来源工作区前缀。
    expect(dismissed.has("./a/one.xlsx")).toBe(false);
  });

  it("仅存在于 recentFiles（workspaceFiles 未加载）的后代也补 dismissal 键", () => {
    bindSessions("s1");
    useExcelStore.setState({
      activeWorkspaceKey: WS,
      workspaceFilesWorkspaceId: "ws",
      workspaceFiles: [],
      recentFiles: [excelRef("./a/only-recent.xlsx", WS, 1), excelRef("./keep.xlsx", WS, 1)],
    });

    handleWorkspaceFilesDeleted(["a"], WS);

    const dismissed = useExcelStore.getState().dismissedPaths;
    expect(dismissed.has(`${WS}|./a/only-recent.xlsx`)).toBe(true);
    expect(dismissed.has(`${WS}|./keep.xlsx`)).toBe(false);
    expect(recentPaths()).toEqual(["./keep.xlsx"]);
  });

  it("后代 dismissal 阻止 SSE 回声按子路径重加，且不误伤同前缀兄弟目录", () => {
    bindSessions("s1");
    useExcelStore.setState({
      activeWorkspaceKey: WS,
      workspaceFilesWorkspaceId: "ws",
      workspaceFiles: [
        { path: "a/one.xlsx", filename: "one.xlsx" },
        { path: "a/b/two.xlsx", filename: "two.xlsx" },
        { path: "ab/three.xlsx", filename: "three.xlsx" },
      ],
      recentFiles: [excelRef("./a/one.xlsx", WS, 3), excelRef("./ab/three.xlsx", WS, 2)],
    });

    handleWorkspaceFilesDeleted(["a"], WS);

    // SSE 回声以子路径形式到达时不得重新进入最近列表。
    useExcelStore.getState().addRecentFileIfNotDismissed({ path: "./a/one.xlsx", filename: "one.xlsx" }, WS);
    useExcelStore.getState().addRecentFileIfNotDismissed({ path: "./a/b/two.xlsx", filename: "two.xlsx" }, WS);
    expect(recentPaths()).toEqual(["./ab/three.xlsx"]);

    // "ab" 只是同前缀字符串，不是 "a" 的后代：不得被误写 dismissal，系统侧仍可加入。
    const dismissed = useExcelStore.getState().dismissedPaths;
    expect(dismissed.has(`${WS}|./ab/three.xlsx`)).toBe(false);
    expect(dismissed.has("./ab/three.xlsx")).toBe(false);
    useExcelStore.getState().addRecentFileIfNotDismissed({ path: "./ab/three.xlsx", filename: "three.xlsx" }, WS);
    expect(recentPaths()).toEqual(["./ab/three.xlsx"]);
  });

  it("dismissedPaths 阻止系统侧 SSE 回声重新加入最近列表（显式打开仍可恢复）", () => {
    bindSessions("s1");
    useExcelStore.setState({ activeWorkspaceKey: WS, recentFiles: [excelRef("./gone.xlsx", WS, 1)] });

    handleWorkspaceFilesDeleted(["./gone.xlsx"], WS);

    expect(useExcelStore.getState().dismissedPaths.has(`${WS}|./gone.xlsx`)).toBe(true);
    expect(useExcelStore.getState().dismissedPaths.has("./gone.xlsx")).toBe(false);

    // 系统侧（SSE mutation 回声）不得把已删文件重新放回。
    useExcelStore.getState().addRecentFileIfNotDismissed({ path: "./gone.xlsx", filename: "gone.xlsx" }, WS);
    expect(useExcelStore.getState().recentFiles).toEqual([]);

    // 用户显式打开是更强的意图，恢复并清除 dismissal。
    useExcelStore.getState().addRecentFile({ path: "./gone.xlsx", filename: "gone.xlsx" }, WS);
    expect(useExcelStore.getState().recentFiles.map((file) => file.path)).toEqual(["./gone.xlsx"]);
    expect(useExcelStore.getState().dismissedPaths.has(`${WS}|./gone.xlsx`)).toBe(false);
  });

  it("关闭同一工作区下跨会话组的已打开表格标签，其它工作区不受影响", () => {
    bindSessions("s1");
    useExcelStore.getState().openFullView("report.xlsx");
    useExcelStore.getState().openFullView("other.xlsx");
    bindSessions("s2");
    useExcelStore.getState().openFullView("report.xlsx");
    bindSessions("s3");
    useExcelStore.getState().openFullView("report.xlsx");

    expect(workspaceFilePaths(GROUP_A)).toEqual(["report.xlsx", "other.xlsx"]);
    expect(workspaceFilePaths(GROUP_B)).toEqual(["report.xlsx"]);
    expect(workspaceFilePaths(GROUP_OTHER)).toEqual(["report.xlsx"]);

    bindSessions("s1");
    useExcelStore.setState({ activeWorkspaceKey: WS });
    handleWorkspaceFilesDeleted(["report.xlsx"], WS);

    expect(workspaceFilePaths(GROUP_A)).toEqual(["other.xlsx"]);
    expect(workspaceFilePaths(GROUP_B)).toEqual([]);
    // 另一工作区的会话组保持原样。
    expect(workspaceFilePaths(GROUP_OTHER)).toEqual(["report.xlsx"]);
  });

  it("activeFilePath / fullViewPath 命中时收敛到剩余主表，并清理对比、选区与缓存", () => {
    bindSessions("s1");
    useExcelStore.getState().openFullView("report.xlsx", "Sheet1");
    useExcelStore.getState().openFullView("other.xlsx", "Sheet2");
    useExcelStore.setState({
      panelOpen: true,
      activeFilePath: "./report.xlsx",
      activeSheet: "Sheet1",
      fullViewPath: "./report.xlsx",
      fullViewSheet: "Sheet1",
      contentVersions: { [`${WS}|./report.xlsx`]: "v1", [`${WS}|./other.xlsx`]: "v2" },
      workbookChanges: {
        [`${WS}|./report.xlsx`]: { sequence: 1, version: "v1", source: "remote" },
        [`${WS}|./other.xlsx`]: { sequence: 2, version: "v2", source: "remote" },
      },
      liveSelection: { path: "./report.xlsx", sheet: "Sheet1", range: "A1:B2" },
      draftRange: { path: "./report.xlsx", sheet: "Sheet1", range: "A1:B2" },
      pendingSelection: { filePath: "./report.xlsx", sheet: "Sheet1", range: "A1:B2" },
    });
    // 对话绑定指向当前主表 report.xlsx。
    expect(useWorkbookConversationStore.getState().targets.s1?.file.relative).toBe("report.xlsx");

    handleWorkspaceFilesDeleted(["./report.xlsx"], WS);

    const state = useExcelStore.getState();
    expect(state.activeFilePath).toBe("other.xlsx");
    expect(state.fullViewPath).toBe("other.xlsx");
    // 仍有剩余表格可显示，面板维持打开。
    expect(state.panelOpen).toBe(true);
    expect(Object.keys(state.contentVersions)).toEqual([`${WS}|./other.xlsx`]);
    expect(Object.keys(state.workbookChanges)).toEqual([`${WS}|./other.xlsx`]);
    expect(state.liveSelection).toBeNull();
    expect(state.draftRange).toBeNull();
    expect(state.pendingSelection).toBeNull();
    // 对话绑定改绑到同组剩余主表。
    expect(useWorkbookConversationStore.getState().targets.s1?.file.relative).toBe("other.xlsx");
  });

  it("被删文件是唯一表格时关闭全屏/面板并解绑对话", () => {
    bindSessions("s1");
    useExcelStore.getState().openFullView("solo.xlsx", "Sheet1");
    useExcelStore.setState({ panelOpen: true });
    expect(useWorkbookConversationStore.getState().targets.s1?.file.relative).toBe("solo.xlsx");

    handleWorkspaceFilesDeleted(["./solo.xlsx"], WS);

    const state = useExcelStore.getState();
    expect(workspaceFilePaths(GROUP_A)).toEqual([]);
    expect(state.activeFilePath).toBeNull();
    expect(state.fullViewPath).toBeNull();
    expect(state.panelOpen).toBe(false);
    expect(useWorkbookConversationStore.getState().targets.s1).toBeUndefined();
  });

  it("对比模式引用被删文件时退出对比并清空对比字段", () => {
    bindSessions("s1");
    useExcelStore.setState({ activeWorkspaceKey: WS });
    useExcelStore.getState().openCompare("a.xlsx", "b.xlsx");
    expect(useExcelStore.getState().compareMode).toBe(true);

    handleWorkspaceFilesDeleted(["a.xlsx"], WS);

    expect(useExcelStore.getState()).toMatchObject({
      compareMode: false,
      compareFileA: null,
      compareFileB: null,
      compareSheetA: null,
      compareSheetB: null,
      compareRelationship: null,
      compareReturnPath: null,
    });
  });

  it("不触碰其它工作区作用域的 workspaceFiles", () => {
    bindSessions("s1");
    useExcelStore.setState({
      activeWorkspaceKey: WS,
      // 文件列表属于另一个工作区：本次删除不应清空它。
      workspaceFilesWorkspaceId: "ws-x",
      workspaceFiles: [{ path: "report.xlsx", filename: "report.xlsx" }],
    });

    handleWorkspaceFilesDeleted(["report.xlsx"], WS);

    expect(useExcelStore.getState().workspaceFiles.map((file) => file.path)).toEqual(["report.xlsx"]);
  });

  it("空路径 / 空白路径是 no-op（不 bump 版本、不写 dismissal）", () => {
    bindSessions("s1");
    useExcelStore.setState({ activeWorkspaceKey: WS });
    const versionBefore = useExcelStore.getState().workspaceFilesVersion;

    handleWorkspaceFilesDeleted([], WS);
    handleWorkspaceFilesDeleted(["   "], WS);

    expect(useExcelStore.getState().workspaceFilesVersion).toBe(versionBefore);
    expect(useExcelStore.getState().dismissedPaths.size).toBe(0);
  });
});

describe("word-store.handleFilesDeleted", () => {
  const snapshot: WordSnapshot = {
    file: "./docs/report.docx",
    total_paragraphs: 1,
    returned_paragraphs: 1,
    truncated: false,
    paragraphs: [{ text: "hi", style: "Normal" }],
    tables: [],
    total_tables: 0,
    sections: 1,
    properties: { title: "报告" },
  };

  it("从最近列表剔除并关闭命中的文档面板/全视图/快照", () => {
    useWordStore.setState({
      recentFiles: ["./docs/report.docx", "./docs/keep.docx"],
      panelOpen: true,
      activeDocPath: "./docs/report.docx",
      fullViewPath: "./docs/report.docx",
      docSnapshot: snapshot,
    });

    handleWorkspaceFilesDeleted(["./docs/report.docx"], WS);

    expect(useWordStore.getState().recentFiles).toEqual(["./docs/keep.docx"]);
    expect(useWordStore.getState().activeDocPath).toBeNull();
    expect(useWordStore.getState().fullViewPath).toBeNull();
    expect(useWordStore.getState().docSnapshot).toBeNull();
    expect(useWordStore.getState().panelOpen).toBe(false);
  });

  it("删除文件夹时文档后代一并剔除，无关文档保留", () => {
    useWordStore.setState({
      recentFiles: ["./docs/a.docx", "./docs/sub/b.docx", "./docs-archive/c.docx", "./keep.docx"],
      panelOpen: true,
      activeDocPath: "./docs/sub/b.docx",
      fullViewPath: "./docs/sub/b.docx",
    });

    handleWorkspaceFilesDeleted(["./docs"], WS);

    expect(useWordStore.getState().recentFiles).toEqual(["./docs-archive/c.docx", "./keep.docx"]);
    expect(useWordStore.getState().activeDocPath).toBeNull();
    expect(useWordStore.getState().fullViewPath).toBeNull();
    expect(useWordStore.getState().panelOpen).toBe(false);
  });

  it("无关删除不影响当前文档状态", () => {
    useWordStore.setState({
      recentFiles: ["./docs/keep.docx"],
      panelOpen: true,
      activeDocPath: "./docs/keep.docx",
    });

    handleWorkspaceFilesDeleted(["./docs/gone.docx"], WS);

    expect(useWordStore.getState().recentFiles).toEqual(["./docs/keep.docx"]);
    expect(useWordStore.getState().activeDocPath).toBe("./docs/keep.docx");
    expect(useWordStore.getState().panelOpen).toBe(true);
  });
});

describe("file-deletion 统一入口（预览弹窗）", () => {
  it("命中文本预览目标时关闭文本预览", () => {
    useFilePreviewStore.getState().openText("./notes/a.txt", "a.txt");

    handleWorkspaceFilesDeleted(["./notes/a.txt"], WS);

    expect(useFilePreviewStore.getState().textOpen).toBe(false);
  });

  it("命中文件夹时关闭后代图片预览", () => {
    useFilePreviewStore.getState().openImage("./assets/pic.png", "pic.png");

    handleWorkspaceFilesDeleted(["./assets"], WS);

    expect(useFilePreviewStore.getState().imageOpen).toBe(false);
  });

  it("无关目标保持预览打开", () => {
    useFilePreviewStore.getState().openText("./notes/keep.txt", "keep.txt");

    handleWorkspaceFilesDeleted(["./notes/gone.txt"], WS);

    expect(useFilePreviewStore.getState().textOpen).toBe(true);
    expect(useFilePreviewStore.getState().textTarget?.path).toBe("./notes/keep.txt");
  });

  it("一次入口同时收敛 excel / word / 预览三处口径", () => {
    bindSessions("s1");
    useExcelStore.getState().openFullView("report.xlsx");
    useExcelStore.setState({ activeWorkspaceKey: WS });
    useWordStore.setState({ panelOpen: true, activeDocPath: "./docs/report.docx", recentFiles: ["./docs/report.docx"] });
    useFilePreviewStore.getState().openText("./docs/report.docx", "report.docx");

    handleWorkspaceFilesDeleted(["./report.xlsx", "./docs/report.docx"], WS);

    expect(useExcelStore.getState().fullViewPath).toBeNull();
    expect(useWordStore.getState().panelOpen).toBe(false);
    expect(useWordStore.getState().recentFiles).toEqual([]);
    expect(useFilePreviewStore.getState().textOpen).toBe(false);
  });
});
