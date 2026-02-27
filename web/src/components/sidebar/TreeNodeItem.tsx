"use client";

import React, { useState } from "react";
import {
  FileSpreadsheet,
  FilePlus,
  FolderPlus,
  Ellipsis,
  CheckSquare,
  Square,
  Trash2,
  Folder,
  FolderOpen,
  ChevronRight,
  ChevronDown,
  Upload,
  Download,
  Pencil,
  AtSign,
} from "lucide-react";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { FileTypeIcon, isExcelFile } from "@/components/ui/file-type-icon";
import { useExcelStore } from "@/stores/excel-store";
import {
  downloadFile,
  normalizeExcelPath,
  workspaceMkdir,
  workspaceCreateFile,
  workspaceDeleteItem,
  workspaceRenameItem,
} from "@/lib/api";
import type { TreeNode } from "./file-tree-helpers";
import { countFiles, isSysFolderName } from "./file-tree-helpers";
import { InlineRenameInput, InlineCreateInput } from "./InlineInputs";

export interface TreeNodeProps {
  node: TreeNode;
  sessionId?: string;
  depth: number;
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
  onUploadToFolder: (folder: string) => void;
}

export function TreeNodeItem(props: TreeNodeProps) {
  const { node, sessionId, depth, panelOpen, activeFilePath, draggingPath, selectMode, selectedPaths, pendingBackups, onDragStart, onDragEnd, onClick, onDoubleClick, onRemove, onRefresh, onUploadToFolder } = props;
  const [expanded, setExpanded] = useState(depth < 2);
  const [renaming, setRenaming] = useState(false);
  const [creating, setCreating] = useState<"file" | "folder" | null>(null);
  const [dragOver, setDragOver] = useState(false);
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

    const handleFolderDragOver = (e: React.DragEvent) => {
      // Only accept if we're dragging a file (not this folder itself)
      if (!draggingPath || draggingPath === node.fullPath) return;
      // Don't allow dropping into own subfolder
      if (draggingPath.startsWith(node.fullPath + "/")) return;
      e.preventDefault();
      e.dataTransfer.dropEffect = "move";
      setDragOver(true);
    };

    const handleFolderDragLeave = () => {
      setDragOver(false);
    };

    const handleFolderDrop = async (e: React.DragEvent) => {
      e.preventDefault();
      setDragOver(false);
      if (!draggingPath || draggingPath === node.fullPath) return;
      if (draggingPath.startsWith(node.fullPath + "/")) return;
      // Extract filename from dragging path
      const filename = draggingPath.includes("/")
        ? draggingPath.slice(draggingPath.lastIndexOf("/") + 1)
        : draggingPath;
      const newPath = node.fullPath ? `${node.fullPath}/${filename}` : filename;
      if (newPath === draggingPath) return;
      try {
        await workspaceRenameItem(draggingPath, newPath);
        // Update recentFiles: remove old path
        useExcelStore.getState().removeRecentFile(draggingPath);
        useExcelStore.getState().bumpWorkspaceFilesVersion();
        onRefresh();
      } catch { /* silent */ }
      setExpanded(true);
    };

    return (
      <div>
        <div
          className={`group flex items-center gap-1.5 py-1.5 text-[13px] text-muted-foreground hover:text-foreground rounded-lg transition-colors duration-100 ${
            dragOver ? "bg-[var(--em-primary-alpha-15)] ring-1 ring-[var(--em-primary)]" : "hover:bg-accent/30"
          }`}
          style={{ paddingLeft: `${indent + 4}px` }}
          onDragOver={handleFolderDragOver}
          onDragLeave={handleFolderDragLeave}
          onDrop={handleFolderDrop}
        >
          <button onClick={() => setExpanded((v) => !v)} className="flex items-center gap-1.5 flex-1 min-w-0">
            {expanded ? <ChevronDown className="h-3.5 w-3.5 flex-shrink-0" /> : <ChevronRight className="h-3.5 w-3.5 flex-shrink-0" />}
            {expanded ? <FolderOpen className="h-4.5 w-4.5 flex-shrink-0 text-[var(--em-primary-light)]" /> : <Folder className="h-4.5 w-4.5 flex-shrink-0 text-[var(--em-primary-light)]" />}
            {renaming ? (
              <InlineRenameInput defaultValue={node.name} onConfirm={handleRename} onCancel={() => setRenaming(false)} />
            ) : (
              <span className="truncate">{node.name}</span>
            )}
            {(() => {
              const sysLabel = isSysFolderName(node.name);
              const fileCount = countFiles(node);
              return (
                <span className="flex items-center gap-1 flex-shrink-0">
                  {sysLabel && (
                    <span className="text-[10px] px-1 py-0.5 rounded bg-[var(--em-primary-alpha-10)] text-[var(--em-primary)]">
                      {sysLabel}
                    </span>
                  )}
                  {fileCount > 0 && (
                    <span className="text-[10px] text-muted-foreground/50">{fileCount}</span>
                  )}
                </span>
              );
            })()}
          </button>
          {!selectMode && !renaming && (
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <button className="flex-shrink-0 h-6 w-6 flex items-center justify-center rounded-md text-muted-foreground transition-opacity duration-150 hover:bg-accent hover:text-foreground opacity-0 group-hover:opacity-100" onClick={(e) => e.stopPropagation()}>
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
              <div className="flex items-center gap-1.5 py-1.5" style={{ paddingLeft: `${(depth + 1) * 12 + 4 + 16}px` }}>
                {creating === "folder" ? <Folder className="h-4.5 w-4.5 flex-shrink-0 text-[var(--em-primary-light)]" /> : <FileTypeIcon filename="new.txt" className="h-4.5 w-4.5 flex-shrink-0" />}
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
    onClick(file.path);
  };
  const handleFileDblClick = () => {
    if (selectMode) return;
    onDoubleClick(file.path);
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
      className={`group relative flex items-center gap-2.5 py-2 pr-2 rounded-lg transition-colors duration-100 text-[13px] cursor-pointer ${
        isSelected ? "bg-accent/80" : isFileActive ? "bg-accent/60" : "hover:bg-accent/40"
      } ${isDragging ? "opacity-70 scale-[0.98]" : ""}`}
      style={{ paddingLeft: `${indent + 4 + 16}px` }}
      title={selectMode ? "点击选择" : excel ? `单击: 侧边面板 | 双击: 全屏\n${file.path}` : `单击: 下载\n${file.path}`}
    >
      {selectMode ? (
        isSelected ? (
          <CheckSquare className="h-4.5 w-4.5 flex-shrink-0" style={{ color: "var(--em-primary)" }} />
        ) : (
          <Square className="h-4.5 w-4.5 flex-shrink-0 text-muted-foreground/50" />
        )
      ) : excel ? (
        <FileSpreadsheet className="h-4.5 w-4.5 flex-shrink-0" style={{ color: isFileActive ? "var(--em-primary)" : "var(--em-primary-light)" }} />
      ) : (
        <FileTypeIcon filename={file.filename} className="h-4.5 w-4.5 flex-shrink-0" />
      )}

      {renaming ? (
        <InlineRenameInput defaultValue={node.name} onConfirm={handleRenameFile} onCancel={() => setRenaming(false)} />
      ) : (
        <span className={`flex-1 min-w-0 truncate leading-snug ${isFileActive ? "font-medium text-foreground" : "text-foreground/80"}`}>
          {node.name}
        </span>
      )}

      {pendingBackups.some((b) => normalizeExcelPath(b.original_path) === normalizeExcelPath(file.path)) && (
        <span
          className="flex-shrink-0 h-2 w-2 rounded-full"
          style={{ backgroundColor: "var(--em-primary)" }}
          title="沙箱修改待应用"
        />
      )}

      {!selectMode && !renaming && (
        <>
          <button
            className="flex-shrink-0 h-6 w-6 flex items-center justify-center rounded-md text-muted-foreground transition-opacity duration-150 hover:text-[var(--em-primary)] hover:bg-[var(--em-primary-alpha-10)] opacity-0 group-hover:opacity-100 touch-show"
            onClick={(e) => { e.stopPropagation(); useExcelStore.getState().mentionFileToInput(file); }}
            title="添加到输入框"
          >
            <AtSign className="h-3.5 w-3.5" />
          </button>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <button
                className={`flex-shrink-0 h-6 w-6 flex items-center justify-center rounded-md text-muted-foreground transition-opacity duration-150 hover:bg-accent hover:text-foreground ${
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
              <DropdownMenuItem onClick={(e) => { e.stopPropagation(); downloadFile(file.path, file.filename, sessionId).catch(() => {}); }}>
                <Download className="h-4 w-4" />
                下载
              </DropdownMenuItem>
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
