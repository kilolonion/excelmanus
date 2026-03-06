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
} from "lucide-react";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { useExcelStore } from "@/stores/excel-store";
import { useSessionStore } from "@/stores/session-store";
import { useAuthStore } from "@/stores/auth-store";
import {
  uploadFile,
  uploadFileToFolder,
  fetchExcelFiles,
  normalizeExcelPath,
  downloadFile,
  workspaceMkdir,
  workspaceDeleteItem,
} from "@/lib/api";
import { mapWithConcurrency } from "@/lib/concurrency";
import { isExcelFile, isImageFile, isTextPreviewableFile, isWordFile } from "@/lib/file-preview";
import { CodePreviewModal } from "@/components/chat/CodePreviewModal";
import { ImagePreviewModal } from "@/components/chat/ImagePreviewModal";
import { useWordStore } from "@/stores/word-store";
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
import { ExcelFilesDialog, RemoveConfirmDialog } from "./ExcelFilesDialogs";
import { StorageBar } from "./StorageBar";
import { FileRelationshipGraph } from "./FileRelationshipGraph";

const ALL_EXTENSIONS = ".xlsx,.xls,.xlsm,.xlsb,.csv,.py,.txt,.json,.md,.pdf,.png,.jpg,.jpeg,.gif,.svg,.html,.css,.js,.ts,.xml,.yaml,.yml,.toml,.sh,.sql,.docx";

function isNotFoundError(err: unknown): boolean {
  const msg = err instanceof Error ? err.message : String(err ?? "");
  return /404|not found|不存在/i.test(msg);
}

/** Hook: long-press detection for touch devices (opens context menu) */
function useLongPress(onLongPress: () => void, delay = 500) {
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const triggeredRef = useRef(false);

  const start = useCallback((e: React.TouchEvent) => {
    triggeredRef.current = false;
    timerRef.current = setTimeout(() => {
      triggeredRef.current = true;
      onLongPress();
    }, delay);
  }, [onLongPress, delay]);

  const cancel = useCallback(() => {
    if (timerRef.current) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  const wasTriggered = useCallback(() => triggeredRef.current, []);

  return { onTouchStart: start, onTouchEnd: cancel, onTouchMove: cancel, wasTriggered };
}

interface ExcelFilesBarProps {
  /** When true, renders as a flat list without section header (header is handled by parent). */
  embedded?: boolean;
}

export function ExcelFilesBar({ embedded }: ExcelFilesBarProps) {
  const recentFiles = useExcelStore((s) => s.recentFiles);
  const addRecentFile = useExcelStore((s) => s.addRecentFile);
  const removeRecentFile = useExcelStore((s) => s.removeRecentFile);
  const removeRecentFiles = useExcelStore((s) => s.removeRecentFiles);
  const mergeRecentFiles = useExcelStore((s) => s.mergeRecentFiles);
  const openPanel = useExcelStore((s) => s.openPanel);
  const openFullView = useExcelStore((s) => s.openFullView);
  const closeExcelPanel = useExcelStore((s) => s.closePanel);
  const closeExcelFullView = useExcelStore((s) => s.closeFullView);
  const closeCompare = useExcelStore((s) => s.closeCompare);
  const panelOpen = useExcelStore((s) => s.panelOpen);
  const activeFilePath = useExcelStore((s) => s.activeFilePath);
  const pendingBackups = useExcelStore((s) => s.pendingBackups);
  const applyFile = useExcelStore((s) => s.applyFile);
  const workspaceFilesVersion = useExcelStore((s) => s.workspaceFilesVersion);
  const workspaceFiles = useExcelStore((s) => s.workspaceFiles);
  const wsFilesLoaded = useExcelStore((s) => s.wsFilesLoaded);
  const refreshWorkspaceFiles = useExcelStore((s) => s.refreshWorkspaceFiles);
  const showSystemFiles = useExcelStore((s) => s.showSystemFiles);
  const toggleShowSystemFiles = useExcelStore((s) => s.toggleShowSystemFiles);
  const demoFile = useExcelStore((s) => s.demoFile);
  const groupViewMode = useExcelStore((s) => s.groupViewMode);
  const toggleGroupViewMode = useExcelStore((s) => s.toggleGroupViewMode);
  const createGroupFromSelected = useExcelStore((s) => s.createGroupFromSelected);
  const openWordPanel = useWordStore((s) => s.openPanel);
  const openWordFullView = useWordStore((s) => s.openFullView);
  const closeWordPanel = useWordStore((s) => s.closePanel);
  const closeWordFullView = useWordStore((s) => s.closeFullView);

  // 过滤后的文件列表（根据 showSystemFiles 开关决定是否展示系统文件）
  const visibleFiles = useMemo(
    () => filterWorkspaceFiles(workspaceFiles, showSystemFiles),
    [workspaceFiles, showSystemFiles],
  );
  const activeSessionId = useSessionStore((s) => s.activeSessionId);
  const currentUserId = useAuthStore((s) => s.user?.id ?? "__anonymous__");
  const fileInputRef = useRef<HTMLInputElement>(null);
  const fileInputId = useId();
  const scannedUserIdRef = useRef<string | null>(null);
  const [draggingPath, setDraggingPath] = useState<string | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [textPreviewTarget, setTextPreviewTarget] = useState<{ path: string; filename: string } | null>(null);
  const [textPreviewOpen, setTextPreviewOpen] = useState(false);
  const [imagePreviewTarget, setImagePreviewTarget] = useState<{ path: string; filename: string } | null>(null);
  const [imagePreviewOpen, setImagePreviewOpen] = useState(false);

  // recentFiles 仅用于排序权重（最近使用文件排前面）
  const recentTimestamps = useMemo(() => {
    const m = new Map<string, number>();
    for (const f of recentFiles) m.set(normalizeExcelPath(f.path), f.lastUsedAt);
    return m;
  }, [recentFiles]);

  // 视图模式：扁平列表 vs 文件夹树（默认列表视图）
  const [treeView, setTreeView] = useState(false);

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
  const totalFileCount = workspaceFiles.filter((f) => !f.is_dir).length;
  const hiddenCount = totalFileCount - wsFilePaths.length;

  const toggleSelectAll = useCallback(() => {
    setSelectedPaths((prev) => {
      if (prev.size === wsFilePaths.length) return new Set();
      return new Set(wsFilePaths);
    });
  }, [wsFilePaths]);

  useEffect(() => {
    if (scannedUserIdRef.current === currentUserId) return;
    scannedUserIdRef.current = currentUserId;
    fetchExcelFiles()
      .then((files) => {
        if (files.length > 0) {
          mergeRecentFiles(
            files.map((f) => ({
              path: f.path,
              filename: f.filename,
              modifiedAt: f.modified_at ? f.modified_at * 1000 : 0,
            }))
          );
        }
      })
      .catch(() => {});
  }, [mergeRecentFiles, currentUserId]);

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
      await workspaceMkdir(folderName);
      useExcelStore.getState().bumpWorkspaceFilesVersion();
      refreshWorkspaceFiles();
    } catch (err) {
      if (!isNotFoundError(err)) {
        useExcelStore.setState({ workspaceFiles: prevWorkspaceFiles });
      }
    }
  }, [refreshWorkspaceFiles]);

  useEffect(() => {
    if (wsFilesLoaded) return;
    refreshWorkspaceFiles();
  }, [wsFilesLoaded, refreshWorkspaceFiles]);

  const openFilePicker = useCallback(() => {
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
  }, []);

  // Agent 创建/修改文件时自动刷新树（files_changed SSE 事件）
  const loadFileGroups = useExcelStore((s) => s.loadFileGroups);
  const prevVersionRef = useRef(workspaceFilesVersion);
  useEffect(() => {
    if (workspaceFilesVersion === prevVersionRef.current) return;
    prevVersionRef.current = workspaceFilesVersion;
    // 短延迟以合并快速连续事件
    const timer = setTimeout(() => {
      refreshWorkspaceFiles();
      if (groupViewMode) loadFileGroups();
    }, 500);
    return () => clearTimeout(timer);
  }, [workspaceFilesVersion, refreshWorkspaceFiles, groupViewMode, loadFileGroups]);

  const handleUpload = useCallback(
    async (e: React.ChangeEvent<HTMLInputElement>) => {
      const files = e.target.files;
      if (!files) return;
      const fileList = Array.from(files);
      const uploaded = await mapWithConcurrency(
        fileList,
        async (file) => {
          try {
            return await uploadFile(file);
          } catch {
            return null;
          }
        },
        4,
      );
      for (const result of uploaded) {
        if (!result) continue;
        addRecentFile({ path: result.path, filename: result.filename });
      }
      e.target.value = "";
      refreshWorkspaceFiles();
    },
    [addRecentFile, refreshWorkspaceFiles]
  );

  const handleClick = useCallback(
    (path: string) => {
      if (selectMode) {
        toggleSelect(path);
        return;
      }
      // Excel 文件打开侧边面板；文本/图片单击预览；其他文件下载。
      const filename = path.includes("/") ? path.slice(path.lastIndexOf("/") + 1) : path;
      if (isExcelFile(filename)) {
        setTextPreviewOpen(false);
        setImagePreviewOpen(false);
        closeWordPanel();
        closeWordFullView();
        openPanel(path);
        return;
      }
      if (isWordFile(filename)) {
        setTextPreviewOpen(false);
        setImagePreviewOpen(false);
        closeExcelPanel();
        openWordPanel(path);
        return;
      }
      if (isImageFile(filename)) {
        setTextPreviewOpen(false);
        setImagePreviewTarget({ path, filename });
        setImagePreviewOpen(true);
        return;
      }
      if (isTextPreviewableFile(filename)) {
        setImagePreviewOpen(false);
        setTextPreviewTarget({ path, filename });
        setTextPreviewOpen(true);
        return;
      }
      downloadFile(path, filename, activeSessionId ?? undefined).catch(() => {});
    },
    [
      activeSessionId,
      closeExcelPanel,
      closeWordFullView,
      closeWordPanel,
      openPanel,
      openWordPanel,
      selectMode,
      toggleSelect,
    ]
  );

  const handleDoubleClick = useCallback(
    (path: string) => {
      if (selectMode) return;
      const filename = path.includes("/") ? path.slice(path.lastIndexOf("/") + 1) : path;
      if (isExcelFile(filename)) {
        closeWordPanel();
        closeWordFullView();
        openFullView(path);
        return;
      }
      if (isWordFile(filename)) {
        closeCompare();
        closeExcelFullView();
        closeExcelPanel();
        closeWordPanel();
        openWordFullView(path);
        return;
      }
      // 文本/图片维持单击预览，双击不触发下载。
      if (isImageFile(filename) || isTextPreviewableFile(filename)) return;
      downloadFile(path, filename, activeSessionId ?? undefined).catch(() => {});
    },
    [
      activeSessionId,
      closeCompare,
      closeExcelFullView,
      closeExcelPanel,
      closeWordFullView,
      closeWordPanel,
      openFullView,
      openWordFullView,
      selectMode,
    ]
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
            await workspaceDeleteItem(path);
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
          removeRecentFile(pendingRemovePaths[0]);
        } else if (pendingRemovePaths.length > 0) {
          removeRecentFiles(pendingRemovePaths);
        }
        useExcelStore.getState().bumpWorkspaceFilesVersion();
      }
      refreshWorkspaceFiles();
    } finally {
      setDeleting(false);
    }
    setConfirmRemoveOpen(false);
    setPendingRemovePaths([]);
    exitSelectMode();
  }, [pendingRemovePaths, removeRecentFile, removeRecentFiles, exitSelectMode, refreshWorkspaceFiles]);

  const requestClearAll = useCallback(() => {
    setPendingRemovePaths(wsFilePaths);
    setConfirmRemoveOpen(true);
  }, [wsFilePaths]);

  const handleDragStart = useCallback(
    (e: React.DragEvent, file: { path: string; filename: string }) => {
      // 多选模式下：如果被拖拽的文件在已选集合中，携带全部已选文件
      if (selectMode && selectedPaths.has(file.path) && selectedPaths.size > 0) {
        const selectedFiles = visibleFiles
          .filter((f) => !f.is_dir && selectedPaths.has(f.path))
          .map((f) => ({ path: f.path, filename: f.filename }));
        e.dataTransfer.setData(
          "text/plain",
          selectedFiles.map((f) => `@file:${f.filename}`).join(" ")
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
      e.dataTransfer.setData("text/plain", `@file:${file.filename}`);
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

  const isDeleteAll = pendingRemovePaths.length === wsFilePaths.length && wsFilePaths.length > 0;

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
          上传或 @引用 Excel 文件
        </button>
        <input
          id={fileInputId}
          data-file-picker="sidebar-upload"
          ref={fileInputRef}
          type="file"
          className="sr-only"
          accept={ALL_EXTENSIONS}
          multiple
          onChange={handleUpload}
        />
      </div>
    );
  }

  if (totalFileCount === 0 && !wsFilesLoaded && !embedded) return null;

  return (
    <div className={embedded ? "px-2 py-1" : "px-3 pb-2"}>
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

      {/* Upload button row in embedded mode */}
      {embedded && (
        <TooltipProvider delayDuration={300}>
          <div className="flex items-center justify-between mb-2 border-b border-border/40 pb-1.5">
            {/* Left group: view toggle */}
            <div className="flex items-center gap-1 rounded-lg bg-muted/50 p-0.5">
              <Tooltip>
                <TooltipTrigger asChild>
                  <button
                    onClick={() => { if (groupViewMode) toggleGroupViewMode(); else setTreeView((v) => !v); }}
                    className={`h-9 w-9 sm:h-7 sm:w-7 flex items-center justify-center rounded-md transition-all duration-150 ${
                      !groupViewMode && treeView
                        ? "text-[var(--em-primary)] bg-[var(--em-primary-alpha-10)] shadow-sm"
                        : !groupViewMode
                          ? "text-muted-foreground hover:text-[var(--em-primary)] hover:bg-[var(--em-primary-alpha-10)]"
                          : "text-muted-foreground hover:text-[var(--em-primary)] hover:bg-[var(--em-primary-alpha-10)]"
                    }`}
                  >
                    {treeView && !groupViewMode ? <List className="h-3.5 w-3.5" /> : <FolderTree className="h-3.5 w-3.5" />}
                  </button>
                </TooltipTrigger>
                <TooltipContent side="bottom">{groupViewMode ? "切换文件视图" : treeView ? "切换列表视图" : "切换文件夹视图"}</TooltipContent>
              </Tooltip>
              <Tooltip>
                <TooltipTrigger asChild>
                  <button
                    onClick={toggleGroupViewMode}
                    className={`h-9 w-9 sm:h-7 sm:w-7 flex items-center justify-center rounded-md transition-all duration-150 ${
                      groupViewMode
                        ? "text-[var(--em-primary)] bg-[var(--em-primary-alpha-10)] shadow-sm"
                        : "text-muted-foreground hover:text-[var(--em-primary)] hover:bg-[var(--em-primary-alpha-10)]"
                    }`}
                  >
                    <Layers className="h-3.5 w-3.5" />
                  </button>
                </TooltipTrigger>
                <TooltipContent side="bottom">{groupViewMode ? "退出文件组视图" : "文件组视图"}</TooltipContent>
              </Tooltip>
            </div>
            {/* Right group: actions */}
            <div className="flex items-center gap-1 rounded-lg bg-muted/50 p-0.5">
              <Tooltip>
                <TooltipTrigger asChild>
                  <button
                    onClick={toggleShowSystemFiles}
                    className={`h-9 w-9 sm:h-7 sm:w-7 flex items-center justify-center rounded-md transition-all duration-150 ${
                      showSystemFiles
                        ? "text-[var(--em-primary)] bg-[var(--em-primary-alpha-10)] shadow-sm"
                        : "text-muted-foreground hover:text-[var(--em-primary)] hover:bg-[var(--em-primary-alpha-10)]"
                    }`}
                  >
                    {showSystemFiles ? <Eye className="h-3.5 w-3.5" /> : <EyeOff className="h-3.5 w-3.5" />}
                  </button>
                </TooltipTrigger>
                <TooltipContent side="bottom">{showSystemFiles ? "隐藏系统文件" : `显示系统文件${hiddenCount > 0 ? ` (已隐藏 ${hiddenCount})` : ""}`}</TooltipContent>
              </Tooltip>
              <Tooltip>
                <TooltipTrigger asChild>
                  <button
                    onClick={() => (selectMode ? exitSelectMode() : setSelectMode(true))}
                    className={`h-9 w-9 sm:h-7 sm:w-7 flex items-center justify-center rounded-md transition-all duration-150 ${
                      selectMode
                        ? "text-[var(--em-primary)] bg-[var(--em-primary-alpha-10)] shadow-sm"
                        : "text-muted-foreground hover:text-[var(--em-primary)] hover:bg-[var(--em-primary-alpha-10)]"
                    }`}
                  >
                    <CheckSquare className="h-3.5 w-3.5" />
                  </button>
                </TooltipTrigger>
                <TooltipContent side="bottom">{selectMode ? "退出多选" : "批量选择"}</TooltipContent>
              </Tooltip>
              <Tooltip>
                <TooltipTrigger asChild>
                  <button
                    onClick={requestClearAll}
                    className="h-9 w-9 sm:h-7 sm:w-7 flex items-center justify-center rounded-md text-muted-foreground hover:text-destructive hover:bg-destructive/10 transition-all duration-150"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </TooltipTrigger>
                <TooltipContent side="bottom">清空所有文件</TooltipContent>
              </Tooltip>
              {treeView && (
                <Tooltip>
                  <TooltipTrigger asChild>
                    <button
                      onClick={() => setCreatingRootFolder(true)}
                      className="h-9 w-9 sm:h-7 sm:w-7 flex items-center justify-center rounded-md text-muted-foreground hover:text-[var(--em-primary)] hover:bg-[var(--em-primary-alpha-10)] transition-all duration-150"
                    >
                      <FolderPlus className="h-3.5 w-3.5" />
                    </button>
                  </TooltipTrigger>
                  <TooltipContent side="bottom">新建文件夹</TooltipContent>
                </Tooltip>
              )}
              <Tooltip>
                <TooltipTrigger asChild>
                  <button
                    type="button"
                    onClick={openFilePicker}
                    className="h-9 w-9 sm:h-7 sm:w-7 flex items-center justify-center rounded-md text-white shadow-sm transition-all duration-150 hover:opacity-90"
                    style={{ backgroundColor: "var(--em-primary)" }}
                  >
                    <Plus className="h-3.5 w-3.5" />
                  </button>
                </TooltipTrigger>
                <TooltipContent side="bottom">上传文件</TooltipContent>
              </Tooltip>
            </div>
          </div>
        </TooltipProvider>
      )}

      {/* Storage progress bar */}
      {embedded && <StorageBar />}

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
              e.dataTransfer.setData("text/plain", `@file:${demoFile.filename}`);
              e.dataTransfer.setData(
                "application/x-excel-file",
                JSON.stringify(demoFile),
              );
              e.dataTransfer.effectAllowed = "copy";
            }}
            onClick={() => openPanel(demoFile.path)}
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
        <div className="flex items-center gap-1 mb-1 px-1">
          <button
            onClick={toggleSelectAll}
            className="text-[10px] text-muted-foreground hover:text-foreground transition-colors"
          >
            {selectedPaths.size === wsFilePaths.length ? "取消全选" : "全选"}
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
              {selectedPaths.size === 2 && (() => {
                const pair = visibleFiles
                  .filter((f) => !f.is_dir && selectedPaths.has(f.path))
                  .map((f) => f.filename);
                return pair.length === 2 ? (
                  <>
                    <button
                      onClick={() => {
                        useExcelStore.getState().setPendingTemplateMessage(
                          `请将 @file:${pair[0]} 与 @file:${pair[1]} 进行合并`
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
                        const paths = visibleFiles
                          .filter((f) => !f.is_dir && selectedPaths.has(f.path))
                          .map((f) => f.path);
                        if (paths.length === 2) {
                          useExcelStore.getState().openCompare(paths[0], paths[1]);
                        }
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
                    const regData = await fetchFileRegistry();
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

      {/* 文件关系图（非文件组视图 + 工作区有 ≥2 个 Excel 文件时） */}
      {!groupViewMode && wsFilesLoaded && visibleFiles.filter((f) => !f.is_dir && isExcelFile(f.filename)).length >= 2 && (
        <details className="border-b border-border/40 text-[11px]">
          <summary className="flex items-center gap-1.5 px-3 py-1.5 cursor-pointer text-muted-foreground hover:text-foreground select-none">
            <ArrowLeftRight className="h-3 w-3" />
            <span className="font-medium">文件关系</span>
          </summary>
          <FileRelationshipGraph onClickFile={handleClick} />
        </details>
      )}

      {groupViewMode ? (
        <FileGroupListView onClickFile={handleClick} />
      ) : !wsFilesLoaded ? (
        <div className="flex flex-col items-center justify-center py-6 gap-2 text-muted-foreground/60">
          <div className="h-4 w-4 border-2 border-current border-t-transparent rounded-full animate-spin" />
          <span className="text-[11px]">加载文件列表…</span>
        </div>
      ) : treeView ? (
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
            files={visibleFiles}
            sessionId={activeSessionId ?? undefined}
            panelOpen={panelOpen}
            activeFilePath={activeFilePath}
            draggingPath={draggingPath}
            selectMode={selectMode}
            selectedPaths={selectedPaths}
            pendingBackups={pendingBackups}
            onDragStart={handleDragStart}
            onDragEnd={handleDragEnd}
            onClick={handleClick}
            onDoubleClick={handleDoubleClick}
            onRemove={(path) => requestRemove([path])}
            onRefresh={refreshWorkspaceFiles}
            onAddRecentFile={addRecentFile}
          />
        </>
      ) : (
        <FlatFileListView
          files={visibleFiles}
          recentTimestamps={recentTimestamps}
          sessionId={activeSessionId ?? undefined}
          panelOpen={panelOpen}
          activeFilePath={activeFilePath}
          draggingPath={draggingPath}
          selectMode={selectMode}
          selectedPaths={selectedPaths}
          pendingBackups={pendingBackups}
          onDragStart={handleDragStart}
          onDragEnd={handleDragEnd}
          onClick={handleClick}
          onDoubleClick={handleDoubleClick}
          onRemove={(path) => requestRemove([path])}
        />
      )}

      {false && wsFilesLoaded && wsFilePaths.length === 0 && embedded && (
        <div className="flex flex-col items-center gap-2 py-4 text-center">
          <FileSpreadsheet className="h-6 w-6 text-muted-foreground/40" />
          <span className="text-[11px] text-muted-foreground/60">
            {hiddenCount > 0
              ? `${hiddenCount} 个系统文件已隐藏，点击眼睛图标显示`
              : "暂无文件，点击上方 + 上传"}
          </span>
        </div>
      )}

      {dialogOpen && (
        <ExcelFilesDialog
          files={recentFiles}
          sessionId={activeSessionId ?? undefined}
          onClose={() => setDialogOpen(false)}
          onClickFile={handleClick}
          onDoubleClickFile={handleDoubleClick}
          onRemoveFile={(path) => requestRemove([path])}
        />
      )}

      {textPreviewTarget && (
        <CodePreviewModal
          filePath={textPreviewTarget.path}
          filename={textPreviewTarget.filename}
          open={textPreviewOpen}
          onOpenChange={setTextPreviewOpen}
        />
      )}
      {imagePreviewTarget && (
        <ImagePreviewModal
          imagePath={imagePreviewTarget.path}
          filename={imagePreviewTarget.filename}
          open={imagePreviewOpen}
          onOpenChange={setImagePreviewOpen}
        />
      )}

      <input
        id={fileInputId}
        data-file-picker="sidebar-upload"
        ref={fileInputRef}
        type="file"
        className="sr-only"
        accept={ALL_EXTENSIONS}
        multiple
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
  panelOpen: boolean;
  activeFilePath: string | null;
  draggingPath: string | null;
  selectMode: boolean;
  selectedPaths: Set<string>;
  pendingBackups: { original_path: string }[];
  onDragStart: (e: React.DragEvent, file: { path: string; filename: string }) => void;
  onDragEnd: () => void;
  onClick: (path: string) => void;
  onDoubleClick: (path: string) => void;
  onRemove: (path: string) => void;
  onRefresh: () => void;
  onAddRecentFile: (file: { path: string; filename: string }) => void;
}

function FileTreeView(props: TreeViewProps) {
  const tree = buildTree(props.files);
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
            return await uploadFileToFolder(file, uploadTargetFolder);
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
      props.onRefresh();
    },
    [uploadTargetFolder, props]
  );

  return (
    <div className="space-y-0.5">
      {tree.children.length === 0 && (
        <div className="px-2 py-3 text-[11px] text-muted-foreground/60 text-center">
          暂无文件，点击上方 + 上传
        </div>
      )}
      {tree.children.map((node) => (
        <TreeNodeItem
          key={node.fullPath}
          node={node}
          sessionId={props.sessionId}
          depth={0}
          panelOpen={props.panelOpen}
          activeFilePath={props.activeFilePath}
          draggingPath={props.draggingPath}
          selectMode={props.selectMode}
          selectedPaths={props.selectedPaths}
          pendingBackups={props.pendingBackups}
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
