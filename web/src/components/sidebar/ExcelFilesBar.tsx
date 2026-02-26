"use client";

import { useRef, useCallback, useState, useEffect } from "react";
import {
  FileSpreadsheet,
  FilePlus,
  FolderPlus,
  Plus,
  X,
  GripVertical,
  Ellipsis,
  CheckSquare,
  Square,
  Trash2,
  FolderTree,
  List,
  Folder,
  FolderOpen,
  ChevronRight,
  ChevronDown,
  Upload,
  Download,
  Pencil,
  AtSign,
} from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { FileTypeIcon, isExcelFile } from "@/components/ui/file-type-icon";
import { useExcelStore } from "@/stores/excel-store";
import { useSessionStore } from "@/stores/session-store";
import { useAuthStore } from "@/stores/auth-store";
import {
  uploadFile,
  uploadFileToFolder,
  fetchExcelFiles,
  fetchWorkspaceFiles,
  downloadFile,
  normalizeExcelPath,
  workspaceMkdir,
  workspaceCreateFile,
  workspaceDeleteItem,
  workspaceRenameItem,
} from "@/lib/api";
import { fileItemVariants } from "@/lib/sidebar-motion";

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
  const clearAllRecentFiles = useExcelStore((s) => s.clearAllRecentFiles);
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

  // 视图模式：扁平列表 vs 文件夹树
  const [treeView, setTreeView] = useState(true);

  // 工作区全部文件供树形展示
  const [workspaceFiles, setWorkspaceFiles] = useState<{ path: string; filename: string; is_dir?: boolean }[]>([]);
  const [wsFilesLoaded, setWsFilesLoaded] = useState(false);

  // 多选模式
  const [selectMode, setSelectMode] = useState(false);
  const [selectedPaths, setSelectedPaths] = useState<Set<string>>(new Set());

  // 删除确认弹窗
  const [confirmRemoveOpen, setConfirmRemoveOpen] = useState(false);
  const [pendingRemovePaths, setPendingRemovePaths] = useState<string[]>([]);

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

  const toggleSelectAll = useCallback(() => {
    setSelectedPaths((prev) => {
      if (prev.size === recentFiles.length) return new Set();
      return new Set(recentFiles.map((f) => f.path));
    });
  }, [recentFiles]);

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

  const confirmRemove = useCallback(() => {
    if (pendingRemovePaths.length === 1) {
      removeRecentFile(pendingRemovePaths[0]);
    } else if (pendingRemovePaths.length > 0) {
      removeRecentFiles(pendingRemovePaths);
    }
    setConfirmRemoveOpen(false);
    setPendingRemovePaths([]);
    exitSelectMode();
  }, [pendingRemovePaths, removeRecentFile, removeRecentFiles, exitSelectMode]);

  const requestClearAll = useCallback(() => {
    setPendingRemovePaths(recentFiles.map((f) => f.path));
    setConfirmRemoveOpen(true);
  }, [recentFiles]);

  const confirmClearAll = useCallback(() => {
    clearAllRecentFiles();
    setConfirmRemoveOpen(false);
    setPendingRemovePaths([]);
    exitSelectMode();
  }, [clearAllRecentFiles, exitSelectMode]);

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

  const isClearAll = pendingRemovePaths.length === recentFiles.length && recentFiles.length > 0;

  // 空状态：仅非嵌入时显示（父组件控制可见性）
  if (recentFiles.length === 0 && !embedded) {
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

  if (recentFiles.length === 0) return null;

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
              title="清空工作区列表"
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
        <div className="flex items-center justify-between mb-1">
          <div className="flex items-center gap-0.5">
            <button
              onClick={() => setTreeView((v) => !v)}
              className={`h-5 w-5 flex items-center justify-center rounded transition-colors ${
                treeView
                  ? "text-foreground bg-accent"
                  : "text-muted-foreground hover:text-foreground hover:bg-accent/60"
              }`}
              title={treeView ? "列表视图" : "文件夹视图"}
            >
              {treeView ? <List className="h-3 w-3" /> : <FolderTree className="h-3 w-3" />}
            </button>
          </div>
          <div className="flex items-center gap-0.5">
            <button
              onClick={() => (selectMode ? exitSelectMode() : setSelectMode(true))}
              className={`h-5 w-5 flex items-center justify-center rounded transition-colors ${
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
              className="h-5 w-5 flex items-center justify-center rounded text-muted-foreground hover:text-destructive hover:bg-destructive/10 transition-colors"
              title="清空工作区列表"
            >
              <Trash2 className="h-3 w-3" />
            </button>
            <button
              onClick={() => fileInputRef.current?.click()}
              className="h-5 w-5 flex items-center justify-center rounded text-muted-foreground hover:text-foreground hover:bg-accent/60 transition-colors"
              title="上传文件"
            >
              <Plus className="h-3 w-3" />
            </button>
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
            {selectedPaths.size === recentFiles.length ? "取消全选" : "全选"}
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
        <FileTreeView
          files={workspaceFiles}
          sessionId={activeSessionId ?? undefined}
          panelOpen={panelOpen}
          activeFilePath={activeFilePath}
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
        />
      ) : (
        <div className="space-y-0.5">
          <AnimatePresence>
            {recentFiles.map((file) => {
              const isFileActive = panelOpen && activeFilePath === file.path;
              const isDragging = draggingPath === file.path;
              const isSelected = selectedPaths.has(file.path);

              return (
                <motion.div
                  key={file.path}
                  variants={fileItemVariants}
                  initial="initial"
                  animate="animate"
                  layout
                  draggable={!selectMode}
                  onDragStart={(e) =>
                    handleDragStart(e as unknown as React.DragEvent, file)
                  }
                  onDragEnd={handleDragEnd}
                  onClick={() => handleClick(file.path)}
                  onDoubleClick={() => handleDoubleClick(file.path)}
                  className={`group relative flex items-center gap-1.5 px-2 py-1.5 min-h-8 rounded-lg cursor-pointer transition-colors duration-150 ease-out text-xs ${
                    isSelected
                      ? "bg-accent/80"
                      : isFileActive
                        ? "bg-accent/60"
                        : "hover:bg-accent/40"
                  } ${isDragging ? "opacity-70 scale-[0.98] shadow-lg" : ""}`}
                  title={selectMode ? "点击选择" : `单击: 侧边面板 | 双击: 全屏 | 拖拽: @引用\n${file.path}`}
                  tabIndex={0}
                >
                  {selectMode ? (
                    isSelected ? (
                      <CheckSquare className="h-3.5 w-3.5 flex-shrink-0" style={{ color: "var(--em-primary)" }} />
                    ) : (
                      <Square className="h-3.5 w-3.5 flex-shrink-0 text-muted-foreground/50" />
                    )
                  ) : (
                    <GripVertical className="h-3 w-3 text-muted-foreground/30 flex-shrink-0 opacity-0 group-hover:opacity-100 cursor-grab transition-opacity duration-150" />
                  )}
                  <FileSpreadsheet
                    className="h-3.5 w-3.5 flex-shrink-0"
                    style={{
                      color: isFileActive
                        ? "var(--em-primary)"
                        : "var(--em-primary-light)",
                    }}
                  />
                  <span
                    className={`flex-1 min-w-0 truncate ${
                      isFileActive
                        ? "font-medium text-foreground"
                        : "text-foreground/80"
                    }`}
                  >
                    {file.filename}
                  </span>

                  {/* Pending backup dot indicator */}
                  {pendingBackups.some((b) => normalizeExcelPath(b.original_path) === normalizeExcelPath(file.path)) && (
                    <span
                      className="flex-shrink-0 h-2 w-2 rounded-full"
                      style={{ backgroundColor: "var(--em-primary)" }}
                      title="沙盒修改待应用"
                    />
                  )}

                  {/* Quick-add @ button + Three-dot menu — hidden in select mode */}
                  {!selectMode && (
                    <>
                      <button
                        className="flex-shrink-0 h-5 w-5 flex items-center justify-center rounded-md text-muted-foreground transition-opacity duration-150 hover:text-[var(--em-primary)] hover:bg-[var(--em-primary-alpha-10)] opacity-0 group-hover:opacity-100 touch-show"
                        onClick={(e) => { e.stopPropagation(); useExcelStore.getState().mentionFileToInput(file); }}
                        title="添加到输入框"
                      >
                        <AtSign className="h-3 w-3" />
                      </button>
                      <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                          <button
                            className={`flex-shrink-0 h-5 w-5 flex items-center justify-center rounded-md text-muted-foreground transition-opacity duration-150 hover:bg-accent hover:text-foreground ${
                              isFileActive
                                ? "opacity-100"
                                : "opacity-0 group-hover:opacity-100 touch-show"
                            }`}
                            onClick={(e) => e.stopPropagation()}
                          >
                            <Ellipsis className="h-3.5 w-3.5" />
                          </button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent
                          side="right"
                          align="start"
                          className="w-36"
                        >
                          <DropdownMenuItem
                            onClick={(e) => {
                              e.stopPropagation();
                              useExcelStore.getState().mentionFileToInput(file);
                            }}
                          >
                            <AtSign className="h-4 w-4" />
                            添加到输入框
                          </DropdownMenuItem>
                          {pendingBackups.some((b) => normalizeExcelPath(b.original_path) === normalizeExcelPath(file.path)) && (
                            <DropdownMenuItem
                              onClick={async (e) => {
                                e.stopPropagation();
                                if (activeSessionId) {
                                  await applyFile(activeSessionId, file.path);
                                }
                              }}
                            >
                              <Upload className="h-4 w-4" />
                              Apply 到原文件
                            </DropdownMenuItem>
                          )}
                          <DropdownMenuItem
                            onClick={(e) => {
                              e.stopPropagation();
                              downloadFile(
                                file.path,
                                file.filename,
                                activeSessionId ?? undefined
                              ).catch(() => {});
                            }}
                          >
                            <Download className="h-4 w-4" />
                            下载
                          </DropdownMenuItem>
                          <DropdownMenuSeparator />
                          <DropdownMenuItem
                            variant="destructive"
                            onClick={(e) => {
                              e.stopPropagation();
                              requestRemove([file.path]);
                            }}
                          >
                            <X className="h-4 w-4" />
                            移除
                          </DropdownMenuItem>
                        </DropdownMenuContent>
                      </DropdownMenu>
                    </>
                  )}
                </motion.div>
              );
            })}
          </AnimatePresence>
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
        isClearAll={isClearAll}
        onConfirm={isClearAll ? confirmClearAll : confirmRemove}
        onCancel={() => {
          setConfirmRemoveOpen(false);
          setPendingRemovePaths([]);
        }}
      />
    </div>
  );
}

/* ── Tree data helpers ── */

interface TreeNode {
  name: string;
  fullPath: string;
  children: TreeNode[];
  file?: { path: string; filename: string };
}

function normalizePath(p: string): string {
  const n = normalizeExcelPath(p);
  return n.startsWith("./") ? n.slice(2) : n;
}

function buildTree(files: { path: string; filename: string; is_dir?: boolean }[]): TreeNode {
  const root: TreeNode = { name: "", fullPath: "", children: [] };

  const ensureFolder = (parent: TreeNode, name: string, fullPath: string): TreeNode => {
    let existing = parent.children.find((c) => !c.file && c.name === name);
    if (!existing) {
      existing = { name, fullPath, children: [] };
      parent.children.push(existing);
    }
    return existing;
  };

  for (const file of files) {
    const normalized = normalizePath(file.path);
    const parts = normalized.split("/").filter(Boolean);
    if (parts.length === 0) continue;

    if (file.is_dir) {
      // 创建文件夹节点（及中间目录）
      let current = root;
      for (let i = 0; i < parts.length; i++) {
        current = ensureFolder(current, parts[i], parts.slice(0, i + 1).join("/"));
      }
    } else {
      // 创建文件节点及中间目录
      let current = root;
      for (let i = 0; i < parts.length - 1; i++) {
        current = ensureFolder(current, parts[i], parts.slice(0, i + 1).join("/"));
      }
      const leafName = parts[parts.length - 1];
      current.children.push({
        name: leafName,
        fullPath: normalized,
        children: [],
        file,
      });
    }
  }

  return collapseTree(root);
}

function collapseTree(node: TreeNode): TreeNode {
  node.children = node.children.map(collapseTree);

  // 排序：先文件夹后文件，均按字母序
  node.children.sort((a, b) => {
    const aIsDir = !a.file;
    const bIsDir = !b.file;
    if (aIsDir !== bIsDir) return aIsDir ? -1 : 1;
    return a.name.localeCompare(b.name);
  });

  // 折叠单子文件夹（类似 VSCode）
  if (!node.file && node.children.length === 1 && !node.children[0].file && node.name !== "") {
    const child = node.children[0];
    return {
      ...child,
      name: `${node.name}/${child.name}`,
      fullPath: child.fullPath,
    };
  }

  return node;
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
    <div className="space-y-0">
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

/* ── InlineRenameInput ── */

function InlineRenameInput({
  defaultValue,
  onConfirm,
  onCancel,
}: {
  defaultValue: string;
  onConfirm: (value: string) => void;
  onCancel: () => void;
}) {
  const [value, setValue] = useState(defaultValue);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    inputRef.current?.focus();
    // 选择不含扩展名的名称部分
    const dotIdx = defaultValue.lastIndexOf(".");
    inputRef.current?.setSelectionRange(0, dotIdx > 0 ? dotIdx : defaultValue.length);
  }, [defaultValue]);

  return (
    <input
      ref={inputRef}
      value={value}
      onChange={(e) => setValue(e.target.value)}
      onKeyDown={(e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          const trimmed = value.trim();
          if (trimmed && trimmed !== defaultValue) onConfirm(trimmed);
          else onCancel();
        }
        if (e.key === "Escape") onCancel();
      }}
      onBlur={() => {
        const trimmed = value.trim();
        if (trimmed && trimmed !== defaultValue) onConfirm(trimmed);
        else onCancel();
      }}
      className="flex-1 min-w-0 bg-accent/60 text-xs text-foreground rounded px-1 py-0.5 outline-none ring-1 ring-[var(--em-primary)]"
      onClick={(e) => e.stopPropagation()}
    />
  );
}

/* ── InlineCreateInput (for new file/folder creation) ── */

function InlineCreateInput({
  placeholder,
  onConfirm,
  onCancel,
}: {
  placeholder: string;
  onConfirm: (value: string) => void;
  onCancel: () => void;
}) {
  const [value, setValue] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  useEffect(() => { inputRef.current?.focus(); }, []);

  return (
    <input
      ref={inputRef}
      value={value}
      onChange={(e) => setValue(e.target.value)}
      onKeyDown={(e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          const trimmed = value.trim();
          if (trimmed) onConfirm(trimmed);
          else onCancel();
        }
        if (e.key === "Escape") onCancel();
      }}
      onBlur={() => {
        const trimmed = value.trim();
        if (trimmed) onConfirm(trimmed);
        else onCancel();
      }}
      placeholder={placeholder}
      className="w-full bg-accent/60 text-xs text-foreground rounded px-1 py-0.5 outline-none ring-1 ring-[var(--em-primary)] placeholder:text-muted-foreground/50"
      onClick={(e) => e.stopPropagation()}
    />
  );
}

/* ── TreeNodeItem (recursive) ── */

interface TreeNodeProps {
  node: TreeNode;
  sessionId?: string;
  depth: number;
  panelOpen: boolean;
  activeFilePath: string | null;
  draggingPath: string | null;
  selectMode: boolean;
  selectedPaths: Set<string>;
  onDragStart: (e: React.DragEvent, file: { path: string; filename: string }) => void;
  onDragEnd: () => void;
  onClick: (path: string) => void;
  onDoubleClick: (path: string) => void;
  onRemove: (path: string) => void;
  onRefresh: () => void;
  onUploadToFolder: (folder: string) => void;
}

function TreeNodeItem(props: TreeNodeProps) {
  const { node, sessionId, depth, panelOpen, activeFilePath, draggingPath, selectMode, selectedPaths, onDragStart, onDragEnd, onClick, onDoubleClick, onRemove, onRefresh, onUploadToFolder } = props;
  const [expanded, setExpanded] = useState(depth < 2);
  const [renaming, setRenaming] = useState(false);
  const [creating, setCreating] = useState<"file" | "folder" | null>(null);
  const isFolder = !node.file;
  const indent = depth * 12;

  // ── Folder node ──
  if (isFolder) {
    const handleRename = async (newName: string) => {
      const parentPath = node.fullPath.includes("/") ? node.fullPath.slice(0, node.fullPath.lastIndexOf("/")) : "";
      const newPath = parentPath ? `${parentPath}/${newName}` : newName;
      try {
        await workspaceRenameItem(node.fullPath, newPath);
        useExcelStore.getState().bumpWorkspaceFilesVersion();
        onRefresh();
      } catch { /* silent */ }
      setRenaming(false);
    };

    const handleDelete = async () => {
      if (!window.confirm(`确定删除文件夹 "${node.name}" 及其所有内容？`)) return;
      try {
        await workspaceDeleteItem(node.fullPath);
        // W8: 同步清理 recentFiles 中属于该文件夹的条目
        const excelStore = useExcelStore.getState();
        const prefix = node.fullPath + "/";
        const toRemove = excelStore.recentFiles
          .filter((f) => f.path.includes(prefix) || f.path.endsWith("/" + node.fullPath))
          .map((f) => f.path);
        if (toRemove.length > 0) excelStore.removeRecentFiles(toRemove);
        excelStore.bumpWorkspaceFilesVersion();
        onRefresh();
      } catch { /* silent */ }
    };

    const handleCreate = async (name: string) => {
      const fullPath = node.fullPath ? `${node.fullPath}/${name}` : name;
      try {
        if (creating === "folder") {
          await workspaceMkdir(fullPath);
        } else {
          await workspaceCreateFile(fullPath);
        }
        useExcelStore.getState().bumpWorkspaceFilesVersion();
        onRefresh();
      } catch { /* silent */ }
      setCreating(null);
    };

    return (
      <div>
        <div className="group flex items-center gap-0 py-0.5 text-xs text-muted-foreground hover:text-foreground hover:bg-accent/30 rounded transition-colors duration-100" style={{ paddingLeft: `${indent + 4}px` }}>
          <button onClick={() => setExpanded((v) => !v)} className="flex items-center gap-1 flex-1 min-w-0 py-0.5">
            {expanded ? <ChevronDown className="h-3 w-3 flex-shrink-0" /> : <ChevronRight className="h-3 w-3 flex-shrink-0" />}
            {expanded ? <FolderOpen className="h-3.5 w-3.5 flex-shrink-0 text-[var(--em-primary-light)]" /> : <Folder className="h-3.5 w-3.5 flex-shrink-0 text-[var(--em-primary-light)]" />}
            {renaming ? (
              <InlineRenameInput defaultValue={node.name} onConfirm={handleRename} onCancel={() => setRenaming(false)} />
            ) : (
              <span className="truncate">{node.name}</span>
            )}
          </button>
          {!selectMode && !renaming && (
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <button className="flex-shrink-0 h-5 w-5 flex items-center justify-center rounded-md text-muted-foreground transition-opacity duration-150 hover:bg-accent hover:text-foreground opacity-0 group-hover:opacity-100" onClick={(e) => e.stopPropagation()}>
                  <Ellipsis className="h-3.5 w-3.5" />
                </button>
              </DropdownMenuTrigger>
              <DropdownMenuContent side="right" align="start" className="w-36">
                <DropdownMenuItem onClick={(e) => { e.stopPropagation(); setCreating("file"); setExpanded(true); }}>
                  <FilePlus className="h-4 w-4" />
                  新建文件
                </DropdownMenuItem>
                <DropdownMenuItem onClick={(e) => { e.stopPropagation(); setCreating("folder"); setExpanded(true); }}>
                  <FolderPlus className="h-4 w-4" />
                  新建文件夹
                </DropdownMenuItem>
                <DropdownMenuItem onClick={(e) => { e.stopPropagation(); onUploadToFolder(node.fullPath); }}>
                  <Upload className="h-4 w-4" />
                  上传到此处
                </DropdownMenuItem>
                <DropdownMenuSeparator />
                <DropdownMenuItem onClick={(e) => { e.stopPropagation(); setRenaming(true); }}>
                  <Pencil className="h-4 w-4" />
                  重命名
                </DropdownMenuItem>
                <DropdownMenuItem variant="destructive" onClick={(e) => { e.stopPropagation(); handleDelete(); }}>
                  <Trash2 className="h-4 w-4" />
                  删除
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          )}
        </div>
        {expanded && (
          <div>
            {creating && (
              <div className="flex items-center gap-1 py-0.5" style={{ paddingLeft: `${(depth + 1) * 12 + 4 + 16}px` }}>
                {creating === "folder" ? <Folder className="h-3.5 w-3.5 flex-shrink-0 text-[var(--em-primary-light)]" /> : <FileTypeIcon filename="new.txt" className="h-3.5 w-3.5 flex-shrink-0" />}
                <InlineCreateInput placeholder={creating === "folder" ? "文件夹名称" : "文件名称"} onConfirm={handleCreate} onCancel={() => setCreating(null)} />
              </div>
            )}
            {node.children.map((child) => (
              <TreeNodeItem key={child.fullPath} {...props} node={child} depth={depth + 1} />
            ))}
          </div>
        )}
      </div>
    );
  }

  // ── File node ──
  const file = node.file!;
  const excel = isExcelFile(file.filename);
  const isFileActive = excel && panelOpen && activeFilePath === file.path;
  const isDragging = draggingPath === file.path;
  const isSelected = selectedPaths.has(file.path);

  const handleFileClick = () => {
    if (selectMode) { /* toggle handled by parent */ return; }
    if (excel) onClick(file.path);
  };
  const handleFileDblClick = () => {
    if (selectMode) return;
    if (excel) onDoubleClick(file.path);
  };

  const handleRenameFile = async (newName: string) => {
    const parentPath = node.fullPath.includes("/") ? node.fullPath.slice(0, node.fullPath.lastIndexOf("/")) : "";
    const newPath = parentPath ? `${parentPath}/${newName}` : newName;
    try {
      await workspaceRenameItem(node.fullPath, newPath);
      // W8: 从 recentFiles 移除旧路径（新路径会在下次扫描时加入）
      if (file) useExcelStore.getState().removeRecentFile(file.path);
      useExcelStore.getState().bumpWorkspaceFilesVersion();
      onRefresh();
    } catch { /* silent */ }
    setRenaming(false);
  };

  const handleDeleteFile = async () => {
    if (!window.confirm(`确定删除文件 "${node.name}"？`)) return;
    try {
      await workspaceDeleteItem(node.fullPath);
      // W8: 同步从 recentFiles 移除
      if (file) useExcelStore.getState().removeRecentFile(file.path);
      useExcelStore.getState().bumpWorkspaceFilesVersion();
      onRefresh();
    } catch { /* silent */ }
  };

  return (
    <div
      draggable={!selectMode && !renaming}
      onDragStart={(e) => onDragStart(e, file)}
      onDragEnd={onDragEnd}
      onClick={handleFileClick}
      onDoubleClick={handleFileDblClick}
      className={`group relative flex items-center gap-1 py-0.5 pr-1 rounded transition-colors duration-100 text-xs ${
        excel ? "cursor-pointer" : "cursor-default"
      } ${
        isSelected ? "bg-accent/80" : isFileActive ? "bg-accent/60" : "hover:bg-accent/40"
      } ${isDragging ? "opacity-70 scale-[0.98]" : ""}`}
      style={{ paddingLeft: `${indent + 4 + 16}px` }}
      title={selectMode ? "点击选择" : excel ? `单击: 侧边面板 | 双击: 全屏\n${file.path}` : file.path}
    >
      {selectMode ? (
        isSelected ? (
          <CheckSquare className="h-3.5 w-3.5 flex-shrink-0" style={{ color: "var(--em-primary)" }} />
        ) : (
          <Square className="h-3.5 w-3.5 flex-shrink-0 text-muted-foreground/50" />
        )
      ) : excel ? (
        <FileSpreadsheet className="h-3.5 w-3.5 flex-shrink-0" style={{ color: isFileActive ? "var(--em-primary)" : "var(--em-primary-light)" }} />
      ) : (
        <FileTypeIcon filename={file.filename} className="h-3.5 w-3.5 flex-shrink-0" />
      )}

      {renaming ? (
        <InlineRenameInput defaultValue={node.name} onConfirm={handleRenameFile} onCancel={() => setRenaming(false)} />
      ) : (
        <span className={`flex-1 min-w-0 truncate ${isFileActive ? "font-medium text-foreground" : "text-foreground/80"}`}>
          {node.name}
        </span>
      )}

      {!selectMode && !renaming && (
        <>
          <button
            className="flex-shrink-0 h-5 w-5 flex items-center justify-center rounded-md text-muted-foreground transition-opacity duration-150 hover:text-[var(--em-primary)] hover:bg-[var(--em-primary-alpha-10)] opacity-0 group-hover:opacity-100 touch-show"
            onClick={(e) => { e.stopPropagation(); useExcelStore.getState().mentionFileToInput(file); }}
            title="添加到输入框"
          >
            <AtSign className="h-3 w-3" />
          </button>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <button
                className={`flex-shrink-0 h-5 w-5 flex items-center justify-center rounded-md text-muted-foreground transition-opacity duration-150 hover:bg-accent hover:text-foreground ${
                  isFileActive ? "opacity-100" : "opacity-0 group-hover:opacity-100 touch-show"
                }`}
                onClick={(e) => e.stopPropagation()}
              >
                <Ellipsis className="h-3.5 w-3.5" />
              </button>
            </DropdownMenuTrigger>
            <DropdownMenuContent side="right" align="start" className="w-36">
              <DropdownMenuItem onClick={(e) => { e.stopPropagation(); useExcelStore.getState().mentionFileToInput(file); }}>
                <AtSign className="h-4 w-4" />
                添加到输入框
              </DropdownMenuItem>
              {excel && (
                <DropdownMenuItem onClick={(e) => { e.stopPropagation(); downloadFile(file.path, file.filename, sessionId).catch(() => {}); }}>
                  <Download className="h-4 w-4" />
                  下载
                </DropdownMenuItem>
              )}
              <DropdownMenuSeparator />
              <DropdownMenuItem onClick={(e) => { e.stopPropagation(); setRenaming(true); }}>
                <Pencil className="h-4 w-4" />
                重命名
              </DropdownMenuItem>
              <DropdownMenuItem variant="destructive" onClick={(e) => { e.stopPropagation(); handleDeleteFile(); }}>
                <Trash2 className="h-4 w-4" />
                删除
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </>
      )}
    </div>
  );
}

/* ── ExcelFilesDialog ── */

function ExcelFilesDialog({
  files,
  sessionId,
  onClose,
  onClickFile,
  onDoubleClickFile,
  onRemoveFile,
}: {
  files: { path: string; filename: string; lastUsedAt: number }[];
  sessionId?: string;
  onClose: () => void;
  onClickFile: (path: string) => void;
  onDoubleClickFile: (path: string) => void;
  onRemoveFile: (path: string) => void;
}) {
  const [search, setSearch] = useState("");

  const filtered = search.trim()
    ? files.filter(
        (f) =>
          f.filename.toLowerCase().includes(search.toLowerCase()) ||
          f.path.toLowerCase().includes(search.toLowerCase())
      )
    : files;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
      onClick={onClose}
    >
      <div
        className="bg-background border border-border rounded-xl shadow-xl w-[440px] max-h-[520px] flex flex-col overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-4 py-3 border-b border-border">
          <span className="text-sm font-semibold">
            工作区文件 ({files.length})
          </span>
          <button
            onClick={onClose}
            className="p-1 rounded hover:bg-muted text-muted-foreground hover:text-foreground transition-colors"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="px-4 py-2 border-b border-border">
          <input
            type="text"
            placeholder="搜索文件..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-full px-3 py-1.5 text-sm rounded-md border border-border bg-muted/30 placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-[var(--em-primary)]"
            autoFocus
          />
        </div>

        <div className="flex-1 overflow-y-auto px-2 py-1">
          {filtered.length === 0 ? (
            <div className="flex items-center justify-center py-8 text-sm text-muted-foreground">
              未找到匹配文件
            </div>
          ) : (
            filtered.map((file) => {
              const time = new Date(file.lastUsedAt).toLocaleDateString(
                "zh-CN",
                {
                  month: "short",
                  day: "numeric",
                  hour: "2-digit",
                  minute: "2-digit",
                }
              );
              return (
                <div
                  key={file.path}
                  className="group flex items-center gap-2 px-3 py-2 rounded-md hover:bg-muted/50 cursor-pointer text-sm transition-colors"
                  onClick={() => {
                    onClickFile(file.path);
                    onClose();
                  }}
                  onDoubleClick={() => {
                    onDoubleClickFile(file.path);
                    onClose();
                  }}
                  title={file.path}
                >
                  <FileSpreadsheet
                    className="h-4 w-4 flex-shrink-0"
                    style={{ color: "var(--em-primary)" }}
                  />
                  <div className="flex-1 min-w-0">
                    <div className="truncate text-foreground/90">
                      {file.filename}
                    </div>
                    <div className="truncate text-[10px] text-muted-foreground">
                      {file.path}
                    </div>
                  </div>
                  <span className="text-[10px] text-muted-foreground whitespace-nowrap">
                    {time}
                  </span>
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      downloadFile(file.path, file.filename, sessionId).catch(() => {});
                    }}
                    className="p-1 rounded opacity-0 group-hover:opacity-100 hover:bg-muted text-muted-foreground hover:text-foreground transition-all"
                    title="下载"
                  >
                    <Download className="h-3 w-3" />
                  </button>
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      onRemoveFile(file.path);
                    }}
                    className="p-1 rounded opacity-0 group-hover:opacity-100 hover:bg-muted text-muted-foreground hover:text-foreground transition-all"
                    title="移除"
                  >
                    <X className="h-3 w-3" />
                  </button>
                </div>
              );
            })
          )}
        </div>
      </div>
    </div>
  );
}

/* ── RemoveConfirmDialog ── */

function RemoveConfirmDialog({
  open,
  count,
  isClearAll,
  onConfirm,
  onCancel,
}: {
  open: boolean;
  count: number;
  isClearAll: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Enter") {
        e.preventDefault();
        e.stopPropagation();
        onConfirm();
      }
    };
    window.addEventListener("keydown", handler, true);
    return () => window.removeEventListener("keydown", handler, true);
  }, [open, onConfirm]);

  const title = isClearAll
    ? "清空工作区文件列表？"
    : count === 1
      ? "移除此文件？"
      : `移除 ${count} 个文件？`;

  const description = isClearAll
    ? "将从工作区列表中清除所有文件引用。磁盘上的原文件不会被删除，之后仍可重新上传或引用。"
    : "将从工作区列表中移除所选文件引用。磁盘上的原文件不会被删除，之后仍可重新上传或引用。";

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onCancel()}>
      <DialogContent className="sm:max-w-[420px]" onOpenAutoFocus={(e) => e.preventDefault()}>
        <DialogHeader>
          <DialogTitle className="text-base font-semibold">{title}</DialogTitle>
          <DialogDescription className="text-sm text-muted-foreground mt-1">
            {description}
          </DialogDescription>
        </DialogHeader>

        <div className="flex items-center justify-end gap-2 pt-2">
          <Button
            variant="ghost"
            size="sm"
            onClick={onCancel}
            className="text-muted-foreground"
          >
            取消
            <kbd className="ml-1.5 text-[10px] text-muted-foreground/60 font-normal">esc</kbd>
          </Button>
          <Button
            variant="destructive"
            size="sm"
            onClick={onConfirm}
          >
            {isClearAll ? "清空列表" : "确认移除"}
            <kbd className="ml-1.5 text-[10px] opacity-60 font-normal">↵</kbd>
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
