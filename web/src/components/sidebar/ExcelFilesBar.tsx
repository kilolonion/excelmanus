"use client";

import { useRef, useCallback, useState, useEffect, useMemo, useId } from "react";
import {
  FileSpreadsheet,
  FolderPlus,
  Plus,
  CheckSquare,
  Trash2,
  FolderTree,
  List,
  Folder,
  Eye,
  EyeOff,
  GripVertical,
  AtSign,
  Combine,
  ArrowLeftRight,
  Layers,
  Search,
  X,
  RefreshCw,
  MoreHorizontal,
  LayoutGrid,
  ArrowRight,
} from "lucide-react";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { useExcelStore } from "@/stores/excel-store";
import {
  EMPTY_WORKBOOK_WORKSPACE,
  useWorkbookWorkspaceStore,
  workbookWorkspaceKey,
} from "@/stores/workbook-workspace-store";
import { useSessionStore } from "@/stores/session-store";
import {
  uploadFile,
  uploadFileToFolder,
  normalizeExcelPath,
  workspaceMkdir,
  workspaceDeleteItem,
} from "@/lib/api";
import { mapWithConcurrency } from "@/lib/concurrency";
import { openWorkspaceFile } from "@/lib/open-workspace-file";
import {
  recentFilesForWorkspace,
  workspaceKeyFromSession,
} from "@/lib/workspace-file-ref";
import { isSpreadsheetFile, WORKSPACE_FILE_INPUT_ACCEPT } from "@/lib/file-kind";
import { displayFilePath } from "@/lib/file-identity";
import { formatFileMention } from "@/components/chat/chat-input-insert";
import {
  buildTree,
  filterWorkspaceFiles,
  removeWorkspaceEntries,
  upsertWorkspaceEntry,
} from "./file-tree-helpers";
import { InlineCreateInput } from "./InlineInputs";
import { TreeNodeItem } from "./TreeNodeItem";
import { FlatFileListView } from "./FlatFileListView";
import { FileGroupListView } from "./FileGroupListView";
import { RemoveConfirmDialog } from "./ExcelFilesDialogs";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

function isNotFoundError(err: unknown): boolean {
  const msg = err instanceof Error ? err.message : String(err ?? "");
  return /404|not found|不存在/i.test(msg);
}

interface ExcelFilesBarProps {
  /** When true, renders as a flat list without section header (header is handled by parent). */
  embedded?: boolean;
}

export function ExcelFilesBar({ embedded }: ExcelFilesBarProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const recentFiles = useExcelStore((s) => s.recentFiles);
  const activeWorkspaceKey = useExcelStore((s) => s.activeWorkspaceKey);
  const addRecentFile = useExcelStore((s) => s.addRecentFile);
  const removeRecentFile = useExcelStore((s) => s.removeRecentFile);
  const removeRecentFiles = useExcelStore((s) => s.removeRecentFiles);
  const workspaceFilesVersion = useExcelStore((s) => s.workspaceFilesVersion);
  const workspaceFiles = useExcelStore((s) => s.workspaceFiles);
  const wsFilesLoaded = useExcelStore((s) => s.wsFilesLoaded);
  const workspaceFilesLoading = useExcelStore((s) => s.workspaceFilesLoading);
  const workspaceFilesError = useExcelStore((s) => s.workspaceFilesError);
  const refreshWorkspaceFiles = useExcelStore((s) => s.refreshWorkspaceFiles);
  const showSystemFiles = useExcelStore((s) => s.showSystemFiles);
  const toggleShowSystemFiles = useExcelStore((s) => s.toggleShowSystemFiles);
  const demoFile = useExcelStore((s) => s.demoFile);
  const groupViewMode = useExcelStore((s) => s.groupViewMode);
  const toggleGroupViewMode = useExcelStore((s) => s.toggleGroupViewMode);
  const createGroupFromSelected = useExcelStore((s) => s.createGroupFromSelected);
  const loadFileGroups = useExcelStore((s) => s.loadFileGroups);

  // 先按系统文件开关过滤，再按搜索词过滤展示列表。
  const workspaceVisibleFiles = useMemo(
    () => filterWorkspaceFiles(workspaceFiles, showSystemFiles),
    [workspaceFiles, showSystemFiles],
  );
  const activeSessionId = useSessionStore((s) => s.activeSessionId);
  const activeWorkspaceId = useSessionStore((s) =>
    s.sessions?.find((session) => session.id === s.activeSessionId)?.workspaceId ?? null,
  );
  const workbookWorkspace = useWorkbookWorkspaceStore((state) =>
    state.workspaces[workbookWorkspaceKey(activeSessionId, activeWorkspaceKey ?? "")] ?? EMPTY_WORKBOOK_WORKSPACE,
  );
  const fileInputRef = useRef<HTMLInputElement>(null);
  const fileInputId = useId();
  const [draggingPath, setDraggingPath] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadFailureCount, setUploadFailureCount] = useState(0);

  const scopedRecentFiles = useMemo(
    () => recentFilesForWorkspace(recentFiles, activeWorkspaceKey),
    [recentFiles, activeWorkspaceKey],
  );

  // recentFiles 仅用于排序权重（最近使用文件排前面）
  const recentTimestamps = useMemo(() => {
    const m = new Map<string, number>();
    for (const f of scopedRecentFiles) m.set(normalizeExcelPath(f.path), f.lastUsedAt);
    return m;
  }, [scopedRecentFiles]);

  // 视图模式：扁平列表 vs 文件夹树（默认列表视图）
  const [treeView, setTreeView] = useState(false);
  const [fileQuery, setFileQuery] = useState("");
  useEffect(() => { scrollRef.current?.scrollTo({ top: 0 }); }, [fileQuery]);

  const visibleFiles = useMemo(() => {
    const query = fileQuery.trim().toLocaleLowerCase();
    if (!query) return workspaceVisibleFiles;
    return workspaceVisibleFiles.filter((file) =>
      `${file.filename} ${displayFilePath(file.path)}`.toLocaleLowerCase().includes(query),
    );
  }, [fileQuery, workspaceVisibleFiles]);

  // 多选模式
  const [selectMode, setSelectMode] = useState(false);
  const [selectedPaths, setSelectedPaths] = useState<Set<string>>(new Set());

  // 删除确认弹窗
  const [confirmRemoveOpen, setConfirmRemoveOpen] = useState(false);
  const [pendingRemovePaths, setPendingRemovePaths] = useState<string[]>([]);
  const [creatingRootFolder, setCreatingRootFolder] = useState(false);
  const [creatingGroup, setCreatingGroup] = useState(false);

  const exitSelectMode = useCallback(() => {
    setSelectMode(false);
    setSelectedPaths(new Set());
  }, []);

  const toggleSelect = useCallback((path: string) => {
    setSelectedPaths((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  }, []);

  const wsFilePaths = visibleFiles.filter((f) => !f.is_dir).map((f) => f.path);
  const allVisibleFilePaths = workspaceVisibleFiles.filter((f) => !f.is_dir).map((f) => f.path);
  const selectedWorkbookFiles = visibleFiles.filter(
    (file) => !file.is_dir && selectedPaths.has(file.path) && isSpreadsheetFile(file.filename || file.path),
  );
  const totalFileCount = workspaceFiles.filter((f) => !f.is_dir).length;
  const hiddenCount = totalFileCount - allVisibleFilePaths.length;
  const hasQuery = fileQuery.trim().length > 0;

  const toggleSelectAll = useCallback(() => {
    setSelectedPaths((prev) => {
      if (prev.size === wsFilePaths.length) return new Set();
      return new Set(wsFilePaths);
    });
  }, [wsFilePaths]);

  const setViewMode = useCallback((mode: "list" | "tree" | "groups") => {
    if (mode === "groups") {
      if (!groupViewMode) toggleGroupViewMode();
      void loadFileGroups({ force: true });
      return;
    }
    if (groupViewMode) toggleGroupViewMode();
    setTreeView(mode === "tree");
  }, [groupViewMode, toggleGroupViewMode, loadFileGroups]);

  const handleCreateRootFolder = useCallback(async (name: string) => {
    const folderName = name.trim();
    if (!folderName) {
      setCreatingRootFolder(false);
      return;
    }

    const prevWorkspaceFiles = useExcelStore.getState().workspaceFiles;
    useExcelStore.setState({
      workspaceFiles: upsertWorkspaceEntry(prevWorkspaceFiles, {
        path: folderName,
        filename: folderName,
        is_dir: true,
      }),
      wsFilesLoaded: true,
    });
    setCreatingRootFolder(false);

    try {
      await workspaceMkdir(folderName, activeSessionId, activeWorkspaceId);
      useExcelStore.getState().bumpWorkspaceFilesVersion();
      refreshWorkspaceFiles(activeSessionId);
    } catch (err) {
      if (!isNotFoundError(err)) {
        useExcelStore.setState({ workspaceFiles: prevWorkspaceFiles });
      }
    }
  }, [refreshWorkspaceFiles, activeSessionId, activeWorkspaceId]);

  useEffect(() => {
    void refreshWorkspaceFiles(activeSessionId, { cached: true });
  }, [activeSessionId, activeWorkspaceKey, refreshWorkspaceFiles]);

  const openFilePicker = useCallback(() => {
    if (uploading) return;
    const input = fileInputRef.current;
    if (!input) return;

    try {
      if (typeof input.showPicker === "function") {
        input.showPicker();
        return;
      }
    } catch {
      // Fall back to click() for browsers/webviews without showPicker support.
    }

    input.click();
  }, [uploading]);

  // Agent 创建/修改文件时自动刷新树（mutation SSE 事件）
  const prevVersionRef = useRef(workspaceFilesVersion);
  const prevGroupVersionRef = useRef(workspaceFilesVersion);
  useEffect(() => {
    if (workspaceFilesVersion === prevVersionRef.current) return;
    prevVersionRef.current = workspaceFilesVersion;
    // 短延迟以合并快速连续事件
    const timer = setTimeout(() => {
      refreshWorkspaceFiles();
    }, 500);
    return () => clearTimeout(timer);
  }, [workspaceFilesVersion, refreshWorkspaceFiles]);

  // File mutations also invalidate group membership. Keep this debounce
  // separate from the file-list timer so toggling views cannot cancel it.
  useEffect(() => {
    if (workspaceFilesVersion === prevGroupVersionRef.current) return;
    prevGroupVersionRef.current = workspaceFilesVersion;
    if (!groupViewMode) return;
    const timer = setTimeout(() => { void loadFileGroups({ force: true }); }, 500);
    return () => clearTimeout(timer);
  }, [workspaceFilesVersion, groupViewMode, loadFileGroups]);

  const handleUpload = useCallback(
    async (e: React.ChangeEvent<HTMLInputElement>) => {
      const files = e.target.files;
      if (!files) return;
      const fileList = Array.from(files);
      if (fileList.length === 0 || uploading) return;
      // Capture the scope before awaiting uploads. The user may switch
      // conversations while the server is receiving a large file.
      const uploadSessionId = activeSessionId;
      const uploadWorkspaceId = activeWorkspaceId;
      const uploadWorkspaceKey = activeWorkspaceKey
        ?? workspaceKeyFromSession(useSessionStore.getState().sessions?.find((s) => s.id === uploadSessionId));
      setUploadFailureCount(0);
      setUploading(true);
      let failed = 0;
      try {
        const uploaded = await mapWithConcurrency(
          fileList,
          async (file) => {
            try {
              return await uploadFile(file, uploadSessionId, uploadWorkspaceId);
            } catch {
              failed += 1;
              return null;
            }
          },
          4,
        );
        for (const result of uploaded) {
          if (!result) continue;
          addRecentFile({ path: result.path, filename: result.filename }, uploadWorkspaceKey ?? undefined);
        }
        if (uploaded.some(Boolean)) {
          // Invalidate an in-flight pre-upload scan so it cannot win with a
          // snapshot that predates the newly uploaded files.
          useExcelStore.getState().bumpWorkspaceFilesVersion();
          const currentSession = useSessionStore.getState().sessions?.find((s) => s.id === uploadSessionId);
          if (useSessionStore.getState().activeSessionId === uploadSessionId
            && workspaceKeyFromSession(currentSession) === uploadWorkspaceKey) {
            await refreshWorkspaceFiles(uploadSessionId);
          }
        }
        if (failed > 0) setUploadFailureCount(failed);
      } finally {
        e.target.value = "";
        setUploading(false);
      }
    },
    [addRecentFile, refreshWorkspaceFiles, activeSessionId, activeWorkspaceId, activeWorkspaceKey, uploading]
  );

  const handleClick = useCallback(
    (path: string) => {
      if (selectMode) {
        toggleSelect(path);
        return;
      }
      openWorkspaceFile(path);
    },
    [selectMode, toggleSelect]
  );

  const handleDoubleClick = useCallback(
    (path: string) => {
      if (selectMode) return;
      openWorkspaceFile(path, { intent: "full" });
    },
    [selectMode]
  );

  // 移除前显示确认对话框
  const requestRemove = useCallback((paths: string[]) => {
    if (paths.length === 0) return;
    setPendingRemovePaths(paths);
    setConfirmRemoveOpen(true);
  }, []);

  const confirmRemove = useCallback(async () => {
    setDeleting(true);
    const prevWorkspaceFiles = useExcelStore.getState().workspaceFiles;
    useExcelStore.setState({
      workspaceFiles: removeWorkspaceEntries(prevWorkspaceFiles, pendingRemovePaths),
      wsFilesLoaded: true,
    });

    try {
      const deleteResults = await mapWithConcurrency(
        pendingRemovePaths,
        async (path) => {
          try {
            await workspaceDeleteItem(path, activeSessionId, activeWorkspaceId);
            return true;
          } catch (err) {
            return isNotFoundError(err);
          }
        },
        4,
      );
      const hasFailure = deleteResults.some((ok) => !ok);
      if (hasFailure) {
        useExcelStore.setState({ workspaceFiles: prevWorkspaceFiles });
      } else {
        // 同步清理 recentFiles 中对应条目
        if (pendingRemovePaths.length === 1) {
          removeRecentFile(pendingRemovePaths[0], activeWorkspaceKey);
        } else if (pendingRemovePaths.length > 0) {
          removeRecentFiles(pendingRemovePaths, activeWorkspaceKey);
        }
        useExcelStore.getState().bumpWorkspaceFilesVersion();
      }
      refreshWorkspaceFiles(activeSessionId);
    } finally {
      setDeleting(false);
    }
    setConfirmRemoveOpen(false);
    setPendingRemovePaths([]);
    exitSelectMode();
  }, [pendingRemovePaths, removeRecentFile, removeRecentFiles, exitSelectMode, refreshWorkspaceFiles, activeSessionId, activeWorkspaceId, activeWorkspaceKey]);

  const requestClearAll = useCallback(() => {
    if (allVisibleFilePaths.length === 0) return;
    setPendingRemovePaths(allVisibleFilePaths);
    setConfirmRemoveOpen(true);
  }, [allVisibleFilePaths]);

  const handleDragStart = useCallback(
    (e: React.DragEvent, file: { path: string; filename: string }) => {
      // 多选模式下：如果被拖拽的文件在已选集合中，携带全部已选文件
      if (selectMode && selectedPaths.has(file.path) && selectedPaths.size > 0) {
        const selectedFiles = visibleFiles
          .filter((f) => !f.is_dir && selectedPaths.has(f.path))
          .map((f) => ({ path: f.path, filename: f.filename }));
        e.dataTransfer.setData(
          "text/plain",
          selectedFiles.map((f) => formatFileMention({ path: f.path })).join(" ")
        );
        e.dataTransfer.setData(
          "application/x-excel-file",
          JSON.stringify(selectedFiles)
        );
        e.dataTransfer.effectAllowed = "copy";
        setDraggingPath(file.path);
        useExcelStore.getState().draggingFileCount = selectedFiles.length;
        return;
      }
      if (selectMode) return;
      e.dataTransfer.setData("text/plain", formatFileMention({ path: file.path }));
      e.dataTransfer.setData(
        "application/x-excel-file",
        JSON.stringify(file)
      );
      e.dataTransfer.effectAllowed = "copy";
      setDraggingPath(file.path);
      useExcelStore.getState().draggingFileCount = 1;
    },
    [selectMode, selectedPaths, visibleFiles]
  );

  const handleDragEnd = useCallback(() => {
    setDraggingPath(null);
    useExcelStore.getState().draggingFileCount = 0;
  }, []);

  const isDeleteAll = pendingRemovePaths.length === allVisibleFilePaths.length && allVisibleFilePaths.length > 0;

  // 空状态：仅非嵌入时显示（父组件控制可见性）
  if (totalFileCount === 0 && !embedded) {
    return (
      <div className="px-3 pb-2">
        <div className="flex items-center justify-between mb-1.5">
          <span className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
            工作区文件
          </span>
          <button
            type="button"
            onClick={openFilePicker}
            disabled={uploading}
            className="min-h-8 min-w-8 flex items-center justify-center rounded text-muted-foreground hover:text-white transition-all duration-150 ease-out"
            onPointerEnter={(e) => {
              e.currentTarget.style.backgroundColor = "var(--em-primary)";
            }}
            onPointerLeave={(e) => {
              e.currentTarget.style.backgroundColor = "";
            }}
            title="上传文件"
          >
            <Plus className="h-3 w-3" />
          </button>
        </div>
        <button
          type="button"
          onClick={openFilePicker}
          disabled={uploading}
          className="w-full flex items-center gap-2 px-2 py-1.5 min-h-8 rounded-md border border-dashed text-xs text-muted-foreground hover:text-foreground hover:border-solid transition-all duration-150 ease-out"
          style={{ borderColor: "var(--em-primary)" }}
          onPointerEnter={(e) => {
            e.currentTarget.style.backgroundColor =
              "var(--em-primary-alpha-10)";
          }}
          onPointerLeave={(e) => {
            e.currentTarget.style.backgroundColor = "";
          }}
        >
          <FileSpreadsheet
            className="h-3.5 w-3.5 flex-shrink-0"
            style={{ color: "var(--em-primary)" }}
          />
          {uploading ? "正在上传文件…" : "上传或 @引用 Excel 文件"}
        </button>
        {uploadFailureCount > 0 && (
          <div role="alert" className="px-1 pt-1 text-xs text-destructive">
            {uploadFailureCount} 个文件上传失败，请重试。
          </div>
        )}
        <input
          id={fileInputId}
          data-file-picker="sidebar-upload"
          ref={fileInputRef}
          type="file"
          className="sr-only"
          accept={WORKSPACE_FILE_INPUT_ACCEPT}
          multiple
          disabled={uploading}
          onChange={handleUpload}
        />
      </div>
    );
  }

  if (totalFileCount === 0 && !wsFilesLoaded && !embedded) return null;

  return (
    <div className={embedded ? "flex h-full min-h-0 flex-col px-2 py-1" : "px-3 pb-2"}>
      {/* Section header — only in standalone mode */}
      {!embedded && (
        <div className="flex items-center justify-between mb-1.5">
          <span className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
            工作区文件
          </span>
          <div className="flex items-center gap-0.5">
            <button
              onClick={toggleShowSystemFiles}
              className={`min-h-8 min-w-8 flex items-center justify-center rounded transition-all duration-150 ease-out ${
                showSystemFiles
                  ? "text-foreground bg-accent"
                  : "text-muted-foreground hover:text-foreground hover:bg-accent/60"
              }`}
              title={showSystemFiles ? "隐藏系统文件" : `显示系统文件${hiddenCount > 0 ? ` (已隐藏 ${hiddenCount})` : ""}`}
            >
              {showSystemFiles ? <Eye className="h-3 w-3" /> : <EyeOff className="h-3 w-3" />}
            </button>
            <button
              onClick={() => (selectMode ? exitSelectMode() : setSelectMode(true))}
              className={`min-h-8 min-w-8 flex items-center justify-center rounded transition-all duration-150 ease-out ${
                selectMode
                  ? "text-foreground bg-accent"
                  : "text-muted-foreground hover:text-foreground hover:bg-accent/60"
              }`}
              title={selectMode ? "退出多选" : "多选"}
            >
              <CheckSquare className="h-3 w-3" />
            </button>
            <button
              onClick={requestClearAll}
              className="min-h-8 min-w-8 flex items-center justify-center rounded text-muted-foreground hover:text-destructive hover:bg-destructive/10 transition-all duration-150 ease-out"
              title="删除所有文件"
            >
              <Trash2 className="h-3 w-3" />
            </button>
            <button
              type="button"
              onClick={openFilePicker}
              disabled={uploading}
              className="min-h-8 min-w-8 flex items-center justify-center rounded text-muted-foreground hover:text-white transition-all duration-150 ease-out"
              onPointerEnter={(e) => {
                e.currentTarget.style.backgroundColor = "var(--em-primary)";
              }}
              onPointerLeave={(e) => {
                e.currentTarget.style.backgroundColor = "";
              }}
              title="上传文件"
            >
              <Plus className="h-3 w-3" />
            </button>
          </div>
        </div>
      )}

      {/* File controls */}
      {embedded && (
        <TooltipProvider delayDuration={300}>
          <div
            className="mb-2 shrink-0 space-y-2 border-b border-border/40 pb-2"
            data-coach-id="coach-sidebar-file-tools"
          >
            <div className="flex items-center justify-between gap-2">
              <div className="min-w-0">
                <div className="flex items-center gap-1.5">
                  <span className="text-[13px] font-semibold text-foreground">工作区文件</span>
                  <span className="rounded-full bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
                    {totalFileCount}
                  </span>
                </div>
                <span className="text-[10px] text-muted-foreground/70">
                  {hasQuery
                    ? `找到 ${wsFilePaths.length} 个匹配项`
                    : hiddenCount > 0
                      ? `${hiddenCount} 个系统文件已隐藏`
                      : workspaceFilesLoading
                        ? "正在同步文件列表…"
                        : "拖拽文件到聊天框即可引用"}
                </span>
              </div>
              <button
                type="button"
                onClick={openFilePicker}
                className="inline-flex h-8 shrink-0 items-center gap-1.5 rounded-md px-2.5 text-[11px] font-medium text-white shadow-sm transition-opacity hover:opacity-90"
                style={{ backgroundColor: "var(--em-primary)" }}
              >
                <Plus className="h-3.5 w-3.5" />
                {uploading ? "上传中…" : "上传文件"}
              </button>
            </div>

            <div className="relative">
              <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground/70" />
              <input
                type="search"
                value={fileQuery}
                onChange={(event) => setFileQuery(event.target.value)}
                placeholder="搜索文件名或路径"
                aria-label="搜索工作区文件"
                className="h-8 w-full rounded-md border border-border/70 bg-background/70 pl-8 pr-8 text-[11px] outline-none transition-colors placeholder:text-muted-foreground/60 focus:border-[var(--em-primary)] focus:ring-1 focus:ring-[var(--em-primary-alpha-10)]"
              />
              {fileQuery && (
                <button
                  type="button"
                  onClick={() => setFileQuery("")}
                  aria-label="清除文件搜索"
                  className="absolute right-1.5 top-1/2 flex h-5 w-5 -translate-y-1/2 items-center justify-center rounded text-muted-foreground hover:bg-muted hover:text-foreground"
                >
                  <X className="h-3 w-3" />
                </button>
              )}
            </div>

            <div className="flex items-center justify-between gap-2">
              <div className="flex min-w-0 items-center gap-0.5 rounded-md bg-muted/60 p-0.5">
                {([
                  ["list", List, "列表"],
                  ["tree", FolderTree, "文件夹"],
                  ["groups", Layers, "文件组"],
                ] as const).map(([mode, Icon, label]) => {
                  const active = mode === "groups"
                    ? groupViewMode
                    : !groupViewMode && (mode === "tree" ? treeView : !treeView);
                  return (
                    <button
                      key={mode}
                      type="button"
                      onClick={() => setViewMode(mode)}
                      aria-label={`${label}视图`}
                      aria-pressed={active}
                      className={`inline-flex h-7 items-center gap-1 rounded px-2 text-[10px] transition-colors ${
                        active
                          ? "bg-background text-[var(--em-primary)] shadow-sm"
                          : "text-muted-foreground hover:text-foreground"
                      }`}
                    >
                      <Icon className="h-3.5 w-3.5" />
                      <span className="hidden min-[360px]:inline">{label}</span>
                    </button>
                  );
                })}
              </div>

              <div className="flex shrink-0 items-center gap-0.5">
                <Tooltip>
                  <TooltipTrigger asChild>
                    <button
                      type="button"
                      onClick={() => (selectMode ? exitSelectMode() : setSelectMode(true))}
                      className={`flex h-7 w-7 items-center justify-center rounded-md transition-all duration-150 ${
                        selectMode
                          ? "bg-[var(--em-primary-alpha-10)] text-[var(--em-primary)]"
                          : "text-muted-foreground hover:bg-muted hover:text-foreground"
                      }`}
                      aria-label={selectMode ? "退出多选" : "批量选择"}
                    >
                      <CheckSquare className="h-3.5 w-3.5" />
                    </button>
                  </TooltipTrigger>
                  <TooltipContent side="bottom">{selectMode ? "退出多选" : "批量选择"}</TooltipContent>
                </Tooltip>
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <button
                      type="button"
                      className="flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                      aria-label="更多文件操作"
                    >
                      <MoreHorizontal className="h-3.5 w-3.5" />
                    </button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent side="bottom" align="end" className="w-48">
                    <DropdownMenuItem disabled={uploading} onClick={openFilePicker}>
                      <Plus className="h-4 w-4" />
                      {uploading ? "上传中…" : "上传文件"}
                    </DropdownMenuItem>
                    <DropdownMenuItem onClick={() => setCreatingRootFolder(true)}>
                      <FolderPlus className="h-4 w-4" />
                      新建文件夹
                    </DropdownMenuItem>
                    <DropdownMenuSeparator />
                    <DropdownMenuItem onClick={() => { void refreshWorkspaceFiles(); }}>
                      <RefreshCw className={`h-4 w-4 ${workspaceFilesLoading ? "animate-spin" : ""}`} />
                      刷新文件列表
                    </DropdownMenuItem>
                    <DropdownMenuItem onClick={toggleShowSystemFiles}>
                      {showSystemFiles ? <Eye className="h-4 w-4" /> : <EyeOff className="h-4 w-4" />}
                      {showSystemFiles ? "隐藏系统文件" : `显示系统文件${hiddenCount > 0 ? `（${hiddenCount} 个）` : ""}`}
                    </DropdownMenuItem>
                    <DropdownMenuSeparator />
                    <DropdownMenuItem variant="destructive" onClick={requestClearAll}>
                      <Trash2 className="h-4 w-4" />
                      清空所有文件
                    </DropdownMenuItem>
                  </DropdownMenuContent>
                </DropdownMenu>
              </div>
            </div>
          </div>
        </TooltipProvider>
      )}

      {embedded && workbookWorkspace.files.length > 1 && (
        <div
          className="mb-2 flex shrink-0 items-center gap-2.5 rounded-lg border px-2.5 py-2"
          style={{
            borderColor: "var(--em-primary-alpha-20)",
            background: "linear-gradient(135deg, var(--em-primary-alpha-10), var(--em-panel))",
          }}
        >
          <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-[var(--em-primary-alpha-12)] text-[var(--em-primary)]">
            <LayoutGrid className="h-3.5 w-3.5" />
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-1.5 text-[11px] font-semibold">
              <span>多表工作区</span>
              <span className="rounded-full bg-[var(--em-primary-alpha-12)] px-1.5 py-0.5 text-[9px] font-medium text-[var(--em-primary)]">
                {workbookWorkspace.files.length} 张已打开
              </span>
            </div>
            <p className="mt-0.5 truncate text-[10px] text-muted-foreground">并排查看、对比和引用当前工作簿</p>
          </div>
          <button
            type="button"
            className="inline-flex h-7 shrink-0 items-center gap-1 rounded-md px-2 text-[10px] font-semibold text-[var(--em-primary)] transition-colors hover:bg-[var(--em-primary-alpha-10)]"
            onClick={() => {
              const target = workbookWorkspace.files.find((file) => file.path === workbookWorkspace.focused)
                ?? workbookWorkspace.files[0];
              if (target) useExcelStore.getState().openFullView(target.path, target.sheet);
            }}
          >
            进入工作区
            <ArrowRight className="h-3 w-3" />
          </button>
        </div>
      )}

      {/* Onboarding demo file (injected during coach marks, auto-removed after) */}
      {embedded && demoFile && (
        <div
          data-coach-id="coach-demo-file"
          className="mb-1 rounded-lg overflow-hidden"
          style={{ border: "1px dashed var(--em-primary)", backgroundColor: "var(--em-primary-alpha-06)" }}
        >
          <div
            draggable
            onDragStart={(e) => {
              e.dataTransfer.setData("text/plain", formatFileMention({ path: demoFile.path }));
              e.dataTransfer.setData(
                "application/x-excel-file",
                JSON.stringify(demoFile),
              );
              e.dataTransfer.effectAllowed = "copy";
              useExcelStore.getState().draggingFileCount = 1;
            }}
            onDragEnd={() => {
              useExcelStore.getState().draggingFileCount = 0;
            }}
            onClick={() => openWorkspaceFile(demoFile.path)}
            className="flex items-center gap-2.5 pl-5 pr-2 py-2 cursor-pointer transition-colors duration-100 hover:bg-accent/40"
          >
            <FileSpreadsheet className="h-4.5 w-4.5 flex-shrink-0" style={{ color: "var(--em-primary)" }} />
            <span className="flex-1 min-w-0 truncate text-[13px] font-medium" style={{ color: "var(--em-primary)" }}>
              {demoFile.filename}
            </span>
            <GripVertical className="h-3.5 w-3.5 flex-shrink-0 text-muted-foreground/50" />
          </div>
        </div>
      )}

      {/* Multi-select action bar */}
      {selectMode && (
        <div className="order-last flex shrink-0 flex-wrap items-center gap-x-2 gap-y-1 border-t border-border/40 bg-background/80 px-1 pt-2">
          <button
            onClick={toggleSelectAll}
            className="text-[10px] text-muted-foreground hover:text-foreground transition-colors"
          >
            {selectedPaths.size === wsFilePaths.length && wsFilePaths.length > 0 ? "取消全选" : "全选"}
          </button>
          {selectedPaths.size > 0 && (
            <>
              <span className="text-[10px] text-muted-foreground">
                · 已选 {selectedPaths.size} 项
              </span>
              <button
                onClick={() => {
                  const selectedFiles = visibleFiles
                    .filter((f) => !f.is_dir && selectedPaths.has(f.path))
                    .map((f) => ({ path: f.path, filename: f.filename }));
                  if (selectedFiles.length > 0) {
                    useExcelStore.getState().mentionFilesToInput(selectedFiles);
                    exitSelectMode();
                  }
                }}
                className="ml-auto text-[10px] transition-colors"
                style={{ color: "var(--em-primary)" }}
                title="将已选文件引用到聊天输入框"
              >
                <span className="inline-flex items-center gap-0.5">
                  <AtSign className="h-3 w-3" />
                  引用到聊天
                </span>
              </button>
              {selectedPaths.size === 2 && selectedWorkbookFiles.length === 2 && (() => {
                const pair = selectedWorkbookFiles.map((f) => f.path);
                return pair.length === 2 ? (
                  <>
                    <button
                      onClick={() => {
                        useExcelStore.getState().setPendingTemplateMessage(
                          `请将 ${formatFileMention({ path: pair[0] })} 与 ${formatFileMention({ path: pair[1] })} 进行合并`
                        );
                        exitSelectMode();
                      }}
                      className="text-[10px] transition-colors"
                      style={{ color: "var(--em-primary)" }}
                      title="将两个文件合并"
                    >
                      <span className="inline-flex items-center gap-0.5">
                        <Combine className="h-3 w-3" />
                        合并
                      </span>
                    </button>
                    <button
                      onClick={() => {
                        useExcelStore.getState().openCompare(pair[0], pair[1]);
                        exitSelectMode();
                      }}
                      className="text-[10px] transition-colors"
                      style={{ color: "var(--em-primary)" }}
                      title="可视化对比两个文件"
                    >
                      <span className="inline-flex items-center gap-0.5">
                        <ArrowLeftRight className="h-3 w-3" />
                        对比
                      </span>
                    </button>
                  </>
                ) : null;
              })()}
              {selectedWorkbookFiles.length >= 2 && (
                <button
                  onClick={() => {
                    const paths = selectedWorkbookFiles.map((file) => file.path);
                    for (const path of paths) openWorkspaceFile(path, { intent: "full" });
                    if (paths[0]) useExcelStore.getState().focusWorkbook(paths[0]);
                    exitSelectMode();
                  }}
                  className="text-[10px] transition-colors"
                  style={{ color: "var(--em-primary)" }}
                  title="在多表工作区中同时打开已选表格"
                >
                  <span className="inline-flex items-center gap-0.5">
                    <LayoutGrid className="h-3 w-3" />
                    打开到多表工作区
                  </span>
                </button>
              )}
              <button
                onClick={() => setCreatingGroup(true)}
                className="text-[10px] transition-colors"
                style={{ color: "var(--em-primary)" }}
                title="将已选文件创建为文件组"
              >
                <span className="inline-flex items-center gap-0.5">
                  <Layers className="h-3 w-3" />
                  创建文件组
                </span>
              </button>
              <button
                onClick={() => requestRemove(Array.from(selectedPaths))}
                className="text-[10px] text-destructive hover:text-destructive/80 transition-colors"
              >
                移除所选
              </button>
            </>
          )}
          {creatingGroup && (
            <div className="flex items-center gap-1 mt-1 w-full">
              <Layers className="h-3.5 w-3.5 flex-shrink-0" style={{ color: "var(--em-primary)" }} />
              <InlineCreateInput
                placeholder="输入文件组名称"
                onConfirm={async (name) => {
                  setCreatingGroup(false);
                  const fileIds = Array.from(selectedPaths);
                  // 需要通过 file registry 获取 file_id，这里用 path 作为 id
                  // 后端 create_group 接收的是 file_registry 的 id
                  // 前端需要先获取 registry entry ids
                  const { fetchFileRegistry } = await import("@/lib/api");
                  try {
                    const regData = await fetchFileRegistry({ sessionId: activeSessionId });
                    if ("files" in regData) {
                      const pathToId = new Map<string, string>();
                      for (const f of regData.files) {
                        pathToId.set(f.canonical_path, f.id);
                      }
                      const ids = fileIds
                        .map((p) => pathToId.get(p) ?? pathToId.get(`./${p}`))
                        .filter((id): id is string => !!id);
                      if (ids.length > 0) {
                        await createGroupFromSelected(name, ids);
                      }
                    }
                  } catch {
                    // silent
                  }
                  exitSelectMode();
                }}
                onCancel={() => setCreatingGroup(false)}
              />
            </div>
          )}
        </div>
      )}

      {workspaceFilesError && (
        <div role="alert" className="px-2 py-2 text-xs text-destructive">
          文件列表加载失败。
          <button type="button" className="ml-1 underline" onClick={() => void refreshWorkspaceFiles(activeSessionId)}>重试</button>
        </div>
      )}
      {uploadFailureCount > 0 && (
        <div role="alert" className="px-2 py-1 text-xs text-destructive">
          {uploadFailureCount} 个文件上传失败，请重试。
        </div>
      )}
      <div ref={scrollRef} data-file-scroll-viewport className={embedded ? "min-h-0 flex-1 overflow-y-auto pr-1" : undefined}>
        {groupViewMode ? (
          <FileGroupListView key={fileQuery} onClickFile={handleClick} query={fileQuery} />
        ) : !wsFilesLoaded ? (workspaceFilesError ? null : (
          <div className="flex flex-col items-center justify-center py-6 gap-2 text-muted-foreground/60">
            <div className="h-4 w-4 border-2 border-current border-t-transparent rounded-full animate-spin" />
            <span className="text-[11px]">加载文件列表…</span>
          </div>
        )) : treeView ? (
          <>
            {creatingRootFolder && (
              <div className="flex items-center gap-1.5 py-1.5 px-2 mb-0.5">
                <Folder className="h-4.5 w-4.5 flex-shrink-0 text-[var(--em-primary-light)]" />
                <InlineCreateInput
                  placeholder="文件夹名称"
                  onConfirm={handleCreateRootFolder}
                  onCancel={() => setCreatingRootFolder(false)}
                />
              </div>
            )}
            <FileTreeView
              key={fileQuery}
              files={visibleFiles}
              sessionId={activeSessionId ?? undefined}
              draggingPath={draggingPath}
              selectMode={selectMode}
              selectedPaths={selectedPaths}
              onDragStart={handleDragStart}
              onDragEnd={handleDragEnd}
              onClick={handleClick}
              onDoubleClick={handleDoubleClick}
              onRemove={(path) => requestRemove([path])}
              onRefresh={refreshWorkspaceFiles}
              onAddRecentFile={addRecentFile}
              emptyMessage={hasQuery ? "未找到匹配文件" : hiddenCount > 0 ? `${hiddenCount} 个系统文件已隐藏` : "暂无文件，点击上方上传"}
            />
          </>
        ) : (
          <FlatFileListView
            scrollRef={embedded ? scrollRef : undefined}
            files={visibleFiles}
            recentTimestamps={recentTimestamps}
            sessionId={activeSessionId ?? undefined}
            draggingPath={draggingPath}
            selectMode={selectMode}
            selectedPaths={selectedPaths}
            onDragStart={handleDragStart}
            onDragEnd={handleDragEnd}
            onClick={handleClick}
            onDoubleClick={handleDoubleClick}
            onRemove={(path) => requestRemove([path])}
            emptyMessage={hasQuery ? "未找到匹配文件" : hiddenCount > 0 ? `${hiddenCount} 个系统文件已隐藏` : "暂无文件，点击上方上传"}
          />
        )}
      </div>

      <input
        id={fileInputId}
        data-file-picker="sidebar-upload"
        ref={fileInputRef}
        type="file"
        className="sr-only"
        accept={WORKSPACE_FILE_INPUT_ACCEPT}
        multiple
        disabled={uploading}
        onChange={handleUpload}
      />

      {/* Remove confirmation dialog */}
      <RemoveConfirmDialog
        open={confirmRemoveOpen}
        count={pendingRemovePaths.length}
        isDeleteAll={isDeleteAll}
        deleting={deleting}
        onConfirm={confirmRemove}
        onCancel={() => {
          setConfirmRemoveOpen(false);
          setPendingRemovePaths([]);
        }}
      />
    </div>
  );
}

/* ── FileTreeView ── */

interface TreeViewProps {
  files: { path: string; filename: string; is_dir?: boolean }[];
  sessionId?: string;
  draggingPath: string | null;
  selectMode: boolean;
  selectedPaths: Set<string>;
  onDragStart: (e: React.DragEvent, file: { path: string; filename: string }) => void;
  onDragEnd: () => void;
  onClick: (path: string) => void;
  onDoubleClick: (path: string) => void;
  onRemove: (path: string) => void;
  onRefresh: () => void;
  onAddRecentFile: (file: { path: string; filename: string }) => void;
  emptyMessage?: string;
}

function FileTreeView(props: TreeViewProps) {
  const tree = useMemo(() => buildTree(props.files), [props.files]);
  const [visibleCount, setVisibleCount] = useState(100);
  const folderUploadRef = useRef<HTMLInputElement>(null);
  const [uploadTargetFolder, setUploadTargetFolder] = useState("");

  const handleFolderUpload = useCallback(
    async (e: React.ChangeEvent<HTMLInputElement>) => {
      const files = e.target.files;
      if (!files) return;
      const fileList = Array.from(files);
      const uploaded = await mapWithConcurrency(
        fileList,
        async (file) => {
          try {
            return await uploadFileToFolder(file, uploadTargetFolder, props.sessionId);
          } catch {
            return null;
          }
        },
        4,
      );
      for (const result of uploaded) {
        if (!result) continue;
        props.onAddRecentFile({ path: result.path, filename: result.filename });
      }
      e.target.value = "";
      if (uploaded.some(Boolean)) useExcelStore.getState().bumpWorkspaceFilesVersion();
      props.onRefresh();
    },
    [uploadTargetFolder, props]
  );

  return (
    <div className="space-y-0.5">
      {tree.children.length === 0 && (
        <div className="px-2 py-3 text-[11px] text-muted-foreground/60 text-center">
          {props.emptyMessage ?? "暂无文件，点击上方上传"}
        </div>
      )}
      {tree.children.slice(0, visibleCount).map((node) => (
        <TreeNodeItem
          key={node.fullPath}
          node={node}
          sessionId={props.sessionId}
          depth={0}
          draggingPath={props.draggingPath}
          selectMode={props.selectMode}
          selectedPaths={props.selectedPaths}
          onDragStart={props.onDragStart}
          onDragEnd={props.onDragEnd}
          onClick={props.onClick}
          onDoubleClick={props.onDoubleClick}
          onRemove={props.onRemove}
          onRefresh={props.onRefresh}
          onUploadToFolder={(folder) => {
            setUploadTargetFolder(folder);
            setTimeout(() => folderUploadRef.current?.click(), 0);
          }}
        />
      ))}
      {tree.children.length > visibleCount && (
        <button type="button" className="w-full p-2 text-xs text-muted-foreground hover:text-foreground"
          onClick={() => setVisibleCount((count) => count + 100)}>
          显示更多（剩余 {tree.children.length - visibleCount} 项）
        </button>
      )}
      <input
        ref={folderUploadRef}
        type="file"
        className="sr-only"
        multiple
        onChange={handleFolderUpload}
      />
    </div>
  );
}
