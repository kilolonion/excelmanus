"use client";

import { useRef, useCallback, useState, useEffect, useMemo } from "react";
import {
  FileSpreadsheet,
  FolderPlus,
  Plus,
  CheckSquare,
  Trash2,
  FolderTree,
  List,
  Folder,
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
  fetchWorkspaceFiles,
  normalizeExcelPath,
  workspaceMkdir,
  workspaceDeleteItem,
} from "@/lib/api";
import { buildTree } from "./file-tree-helpers";
import { InlineCreateInput } from "./InlineInputs";
import { TreeNodeItem } from "./TreeNodeItem";
import { FlatFileListView } from "./FlatFileListView";
import { ExcelFilesDialog, RemoveConfirmDialog } from "./ExcelFilesDialogs";

const EXCEL_EXTENSIONS = ".xlsx,.xls,.csv";

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
  const panelOpen = useExcelStore((s) => s.panelOpen);
  const activeFilePath = useExcelStore((s) => s.activeFilePath);
  const pendingBackups = useExcelStore((s) => s.pendingBackups);
  const applyFile = useExcelStore((s) => s.applyFile);
  const workspaceFilesVersion = useExcelStore((s) => s.workspaceFilesVersion);
  const activeSessionId = useSessionStore((s) => s.activeSessionId);
  const currentUserId = useAuthStore((s) => s.user?.id ?? "__anonymous__");
  const fileInputRef = useRef<HTMLInputElement>(null);
  const scannedUserIdRef = useRef<string | null>(null);
  const [draggingPath, setDraggingPath] = useState<string | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);

  // recentFiles 仅用于排序权重（最近使用文件排前面）
  const recentTimestamps = useMemo(() => {
    const m = new Map<string, number>();
    for (const f of recentFiles) m.set(normalizeExcelPath(f.path), f.lastUsedAt);
    return m;
  }, [recentFiles]);

  // 视图模式：扁平列表 vs 文件夹树
  const [treeView, setTreeView] = useState(true);

  // 工作区全部文件（树状与列表视图共用数据源）
  const [workspaceFiles, setWorkspaceFiles] = useState<{ path: string; filename: string; is_dir?: boolean }[]>([]);
  const [wsFilesLoaded, setWsFilesLoaded] = useState(false);

  // 多选模式
  const [selectMode, setSelectMode] = useState(false);
  const [selectedPaths, setSelectedPaths] = useState<Set<string>>(new Set());

  // 删除确认弹窗
  const [confirmRemoveOpen, setConfirmRemoveOpen] = useState(false);
  const [pendingRemovePaths, setPendingRemovePaths] = useState<string[]>([]);
  const [creatingRootFolder, setCreatingRootFolder] = useState(false);

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

  const wsFilePaths = workspaceFiles.filter((f) => !f.is_dir).map((f) => f.path);

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

  const refreshWorkspaceFiles = useCallback(() => {
    fetchWorkspaceFiles()
      .then((files) => {
        setWorkspaceFiles(files.map((f) => ({ path: f.path, filename: f.filename, is_dir: f.is_dir })));
        setWsFilesLoaded(true);
      })
      .catch(() => {});
  }, []);

  const handleCreateRootFolder = useCallback(async (name: string) => {
    try {
      await workspaceMkdir(name);
      useExcelStore.getState().bumpWorkspaceFilesVersion();
      refreshWorkspaceFiles();
    } catch { /* silent */ }
    setCreatingRootFolder(false);
  }, [refreshWorkspaceFiles]);

  useEffect(() => {
    if (wsFilesLoaded) return;
    refreshWorkspaceFiles();
  }, [wsFilesLoaded, refreshWorkspaceFiles]);

  // Agent 创建/修改文件时自动刷新树（files_changed SSE 事件）
  const prevVersionRef = useRef(workspaceFilesVersion);
  useEffect(() => {
    if (workspaceFilesVersion === prevVersionRef.current) return;
    prevVersionRef.current = workspaceFilesVersion;
    // 短延迟以合并快速连续事件
    const timer = setTimeout(() => refreshWorkspaceFiles(), 500);
    return () => clearTimeout(timer);
  }, [workspaceFilesVersion, refreshWorkspaceFiles]);

  const handleUpload = useCallback(
    async (e: React.ChangeEvent<HTMLInputElement>) => {
      const files = e.target.files;
      if (!files) return;
      for (const file of Array.from(files)) {
        try {
          const result = await uploadFile(file);
          addRecentFile({ path: result.path, filename: result.filename });
        } catch {
          // 上传静默失败
        }
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
      openPanel(path);
    },
    [openPanel, selectMode, toggleSelect]
  );

  const handleDoubleClick = useCallback(
    (path: string) => {
      if (selectMode) return;
      openFullView(path);
    },
    [openFullView, selectMode]
  );

  // 移除前显示确认对话框
  const requestRemove = useCallback((paths: string[]) => {
    if (paths.length === 0) return;
    setPendingRemovePaths(paths);
    setConfirmRemoveOpen(true);
  }, []);

  const confirmRemove = useCallback(async () => {
    setDeleting(true);
    try {
      for (const p of pendingRemovePaths) {
        try { await workspaceDeleteItem(p); } catch { /* silent */ }
      }
      // 同步清理 recentFiles 中对应条目
      if (pendingRemovePaths.length === 1) {
        removeRecentFile(pendingRemovePaths[0]);
      } else if (pendingRemovePaths.length > 0) {
        removeRecentFiles(pendingRemovePaths);
      }
      useExcelStore.getState().bumpWorkspaceFilesVersion();
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
      if (selectMode) return;
      e.dataTransfer.setData("text/plain", `@file:${file.filename}`);
      e.dataTransfer.setData(
        "application/x-excel-file",
        JSON.stringify(file)
      );
      e.dataTransfer.effectAllowed = "copy";
      setDraggingPath(file.path);
    },
    [selectMode]
  );

  const handleDragEnd = useCallback(() => {
    setDraggingPath(null);
  }, []);

  const isDeleteAll = pendingRemovePaths.length === wsFilePaths.length && wsFilePaths.length > 0;

  // 空状态：仅非嵌入时显示（父组件控制可见性）
  if (wsFilePaths.length === 0 && !embedded) {
    return (
      <div className="px-3 pb-2">
        <div className="flex items-center justify-between mb-1.5">
          <span className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
            工作区文件
          </span>
          <button
            onClick={() => fileInputRef.current?.click()}
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
          onClick={() => fileInputRef.current?.click()}
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
          ref={fileInputRef}
          type="file"
          className="hidden"
          accept={EXCEL_EXTENSIONS}
          multiple
          onChange={handleUpload}
        />
      </div>
    );
  }

  if (wsFilePaths.length === 0 && !wsFilesLoaded) return null;

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
              onClick={() => fileInputRef.current?.click()}
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
                    onClick={() => setTreeView((v) => !v)}
                    className={`h-7 w-7 flex items-center justify-center rounded-md transition-all duration-150 ${
                      treeView
                        ? "text-[var(--em-primary)] bg-[var(--em-primary-alpha-10)] shadow-sm"
                        : "text-muted-foreground hover:text-[var(--em-primary)] hover:bg-[var(--em-primary-alpha-10)]"
                    }`}
                  >
                    {treeView ? <List className="h-3.5 w-3.5" /> : <FolderTree className="h-3.5 w-3.5" />}
                  </button>
                </TooltipTrigger>
                <TooltipContent side="bottom">{treeView ? "切换列表视图" : "切换文件夹视图"}</TooltipContent>
              </Tooltip>
            </div>
            {/* Right group: actions */}
            <div className="flex items-center gap-1 rounded-lg bg-muted/50 p-0.5">
              <Tooltip>
                <TooltipTrigger asChild>
                  <button
                    onClick={() => (selectMode ? exitSelectMode() : setSelectMode(true))}
                    className={`h-7 w-7 flex items-center justify-center rounded-md transition-all duration-150 ${
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
                    className="h-7 w-7 flex items-center justify-center rounded-md text-muted-foreground hover:text-destructive hover:bg-destructive/10 transition-all duration-150"
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
                      className="h-7 w-7 flex items-center justify-center rounded-md text-muted-foreground hover:text-[var(--em-primary)] hover:bg-[var(--em-primary-alpha-10)] transition-all duration-150"
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
                    onClick={() => fileInputRef.current?.click()}
                    className="h-7 w-7 flex items-center justify-center rounded-md text-white shadow-sm transition-all duration-150 hover:opacity-90"
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
                onClick={() => requestRemove(Array.from(selectedPaths))}
                className="ml-auto text-[10px] text-destructive hover:text-destructive/80 transition-colors"
              >
                移除所选
              </button>
            </>
          )}
        </div>
      )}

      {treeView ? (
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
            files={workspaceFiles}
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
          files={workspaceFiles}
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

      <input
        ref={fileInputRef}
        type="file"
        className="hidden"
        accept={EXCEL_EXTENSIONS}
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
      for (const file of Array.from(files)) {
        try {
          const result = await uploadFileToFolder(file, uploadTargetFolder);
          props.onAddRecentFile({ path: result.path, filename: result.filename });
        } catch {
          // 静默
        }
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
        className="hidden"
        multiple
        onChange={handleFolderUpload}
      />
    </div>
  );
}
