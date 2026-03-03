"use client";

import { useMemo } from "react";
import {
  Ellipsis,
  CheckSquare,
  Square,
  Trash2,
  Download,
  AtSign,
  Combine,
  ArrowLeftRight,
  Layers,
} from "lucide-react";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { FileTypeIcon, isExcelFile } from "@/components/ui/file-type-icon";
import { useExcelStore } from "@/stores/excel-store";
import { downloadFile, normalizeExcelPath, fetchFileRegistry, updateFileGroupMembers } from "@/lib/api";
import { isPreviewableWorkspaceFile } from "@/lib/file-preview";
import { normalizePath } from "./file-tree-helpers";

export interface FlatFileListViewProps {
  files: { path: string; filename: string; is_dir?: boolean }[];
  recentTimestamps: Map<string, number>;
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
}

export function FlatFileListView(props: FlatFileListViewProps) {
  const { files, recentTimestamps, sessionId, panelOpen, activeFilePath, draggingPath, selectMode, selectedPaths, pendingBackups, onDragStart, onDragEnd, onClick, onDoubleClick, onRemove } = props;

  // 最近使用的文件排前面，其余按文件名字母序
  const flatFiles = useMemo(() => {
    const all = files.filter((f) => !f.is_dir);
    return all.sort((a, b) => {
      const tA = recentTimestamps.get(normalizeExcelPath(a.path)) ?? 0;
      const tB = recentTimestamps.get(normalizeExcelPath(b.path)) ?? 0;
      if (tA !== tB) return tB - tA;
      return a.filename.localeCompare(b.filename);
    });
  }, [files, recentTimestamps]);

  if (flatFiles.length === 0) {
    return (
      <div className="px-2 py-3 text-[11px] text-muted-foreground/60 text-center">
        暂无文件，点击上方 + 上传
      </div>
    );
  }

  return (
    <div className="space-y-0.5">
      {flatFiles.map((file) => {
        const excel = isExcelFile(file.filename);
        const previewable = isPreviewableWorkspaceFile(file.filename);
        const isFileActive = excel && panelOpen && activeFilePath === file.path;
        const isDragging = draggingPath === file.path;
        const isSelected = selectedPaths.has(file.path);
        const normalized = normalizePath(file.path);
        const dirPart = normalized.includes("/") ? normalized.slice(0, normalized.lastIndexOf("/")) : "";

        return (
          <div
            key={file.path}
            draggable={!selectMode || isSelected}
            onDragStart={(e) => onDragStart(e, file)}
            onDragEnd={onDragEnd}
            onClick={() => onClick(file.path)}
            onDoubleClick={() => { if (selectMode) return; onDoubleClick(file.path); }}
            className={`group relative flex items-center gap-2.5 pl-5 pr-2 py-2 rounded-lg transition-colors duration-100 text-[13px] cursor-pointer ${
              isSelected ? "bg-accent/80" : isFileActive ? "bg-accent/60" : "hover:bg-accent/40"
            } ${isDragging ? "opacity-70 scale-[0.98]" : ""}`}
            title={
              selectMode
                ? "点击选择"
                : excel
                  ? `单击: 侧边面板 | 双击: 全屏\n${file.path}`
                  : previewable
                    ? `单击: 预览 | 菜单: 下载\n${file.path}`
                    : `单击: 下载\n${file.path}`
            }
          >
            {selectMode ? (
              isSelected ? (
                <CheckSquare className="h-4.5 w-4.5 flex-shrink-0" style={{ color: "var(--em-primary)" }} />
              ) : (
                <Square className="h-4.5 w-4.5 flex-shrink-0 text-muted-foreground/50" />
              )
            ) : (
              <FileTypeIcon filename={file.filename} className={`h-4.5 w-4.5 flex-shrink-0${isFileActive ? " opacity-100" : ""}`} />
            )}

            <div className="flex-1 min-w-0">
              <span className={`block truncate leading-snug ${isFileActive ? "font-medium text-foreground" : "text-foreground/80"}`}>
                {file.filename}
              </span>
              {dirPart && (
                <span className="block truncate text-[11px] text-muted-foreground/60 leading-tight mt-0.5">
                  {dirPart}
                </span>
              )}
            </div>

            {pendingBackups.some((b) => normalizeExcelPath(b.original_path) === normalizeExcelPath(file.path)) && (
              <span
                className="flex-shrink-0 h-2 w-2 rounded-full"
                style={{ backgroundColor: "var(--em-primary)" }}
                title="沙箱修改待应用"
              />
            )}

            {!selectMode && (
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
                    <DropdownMenuItem onClick={(e) => {
                      e.stopPropagation();
                      useExcelStore.getState().setPendingTemplateMessage(
                        `请将 @file:${file.filename} 与 进行合并`
                      );
                    }}>
                      <Combine className="h-4 w-4" />
                      与其他文件合并
                    </DropdownMenuItem>
                    {(() => {
                      const otherExcels = flatFiles.filter(
                        (f) => f.path !== file.path && isExcelFile(f.filename),
                      );
                      if (otherExcels.length > 0) {
                        return (
                          <DropdownMenuSub>
                            <DropdownMenuSubTrigger>
                              <ArrowLeftRight className="h-4 w-4" />
                              与其他文件对比
                            </DropdownMenuSubTrigger>
                            <DropdownMenuSubContent className="w-44">
                              {otherExcels.slice(0, 10).map((other) => (
                                <DropdownMenuItem
                                  key={other.path}
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    useExcelStore.getState().openCompare(file.path, other.path);
                                  }}
                                >
                                  {other.filename}
                                </DropdownMenuItem>
                              ))}
                            </DropdownMenuSubContent>
                          </DropdownMenuSub>
                        );
                      }
                      return (
                        <DropdownMenuItem onClick={(e) => {
                          e.stopPropagation();
                          useExcelStore.getState().setPendingTemplateMessage(
                            `请对比 @file:${file.filename} 和 的差异`
                          );
                        }}>
                          <ArrowLeftRight className="h-4 w-4" />
                          与其他文件对比
                        </DropdownMenuItem>
                      );
                    })()}
                    {(() => {
                      const groups = useExcelStore.getState().fileGroups;
                      if (groups.length > 0) {
                        return (
                          <DropdownMenuSub>
                            <DropdownMenuSubTrigger>
                              <Layers className="h-4 w-4" />
                              加入文件组
                            </DropdownMenuSubTrigger>
                            <DropdownMenuSubContent className="w-36">
                              {groups.map((g) => (
                                <DropdownMenuItem
                                  key={g.id}
                                  onClick={async (e) => {
                                    e.stopPropagation();
                                    try {
                                      const regData = await fetchFileRegistry();
                                      if ("files" in regData) {
                                        const entry = regData.files.find(
                                          (f) => f.canonical_path === file.path || f.canonical_path === `./${file.path}`,
                                        );
                                        if (entry) {
                                          await updateFileGroupMembers(g.id, { add: [{ file_id: entry.id }] });
                                          useExcelStore.getState().loadFileGroups();
                                        }
                                      }
                                    } catch { /* silent */ }
                                  }}
                                >
                                  {g.name}
                                </DropdownMenuItem>
                              ))}
                            </DropdownMenuSubContent>
                          </DropdownMenuSub>
                        );
                      }
                      return null;
                    })()}
                    <DropdownMenuSeparator />
                    <DropdownMenuItem variant="destructive" onClick={(e) => { e.stopPropagation(); onRemove(file.path); }}>
                      <Trash2 className="h-4 w-4" />
                      删除
                    </DropdownMenuItem>
                  </DropdownMenuContent>
                </DropdownMenu>
              </>
            )}
          </div>
        );
      })}
    </div>
  );
}
