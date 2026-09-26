"use client";

import { useMemo, useState, type RefObject } from "react";
import { defaultRangeExtractor, useVirtualizer } from "@tanstack/react-virtual";
import {
  Ellipsis,
  Check,
  Folder,
  FolderOpen,
  Trash2,
  Download,
  AtSign,
  Combine,
  ArrowLeftRight,
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
import { FileTypeIcon } from "@/components/ui/file-type-icon";
import { isSpreadsheetFile, workspaceFileOpenHint } from "@/lib/file-kind";
import { displayFilePath } from "@/lib/file-identity";
import { useOpenWorkspacePathSet } from "@/lib/open-workspace-file";
import { useExcelStore } from "@/stores/excel-store";
import { downloadFile, normalizeExcelPath } from "@/lib/api";
import { formatFileMention } from "@/components/chat/chat-input-insert";
import {
  glassMenuDangerItemClass,
  glassMenuItemClass,
  glassMenuPanelClass,
} from "@/components/ui/menu-panel";
import { cn } from "@/lib/utils";
import { normalizePath } from "./file-tree-helpers";
import styles from "./FilePanel.module.css";

export interface FlatFileListViewProps {
  scrollRef?: RefObject<HTMLDivElement | null>;
  files: { path: string; filename: string; is_dir?: boolean }[];
  recentTimestamps: Map<string, number>;
  sessionId?: string;
  draggingPath: string | null;
  selectMode: boolean;
  selectedPaths: Set<string>;
  onDragStart: (e: React.DragEvent, file: { path: string; filename: string }) => void;
  onDragEnd: () => void;
  onClick: (path: string) => void;
  onDoubleClick: (path: string) => void;
  onRemove: (path: string) => void;
  emptyMessage?: string;
}

export function FlatFileListView(props: FlatFileListViewProps) {
  const { files, recentTimestamps, sessionId, draggingPath, selectMode, selectedPaths, onDragStart, onDragEnd, onClick, onDoubleClick, onRemove, emptyMessage = "暂无文件，点击上方上传" } = props;
  const openPaths = useOpenWorkspacePathSet();
  const [menuPath, setMenuPath] = useState<string | null>(null);

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

  // Comparison menus need at most ten alternatives, not a full scan for every row.
  const comparisonFiles = useMemo(
    () => flatFiles.filter((file) => isSpreadsheetFile(file.filename)).slice(0, 11),
    [flatFiles],
  );
  const pinnedIndex = flatFiles.findIndex((file) => file.path === (menuPath ?? draggingPath));
  const estimatedRowSize = 64;
  const virtualizer = useVirtualizer({
    count: flatFiles.length,
    getScrollElement: () => props.scrollRef?.current ?? null,
    getItemKey: (index) => flatFiles[index].path,
    estimateSize: () => estimatedRowSize,
    overscan: 5,
    enabled: !!props.scrollRef,
    rangeExtractor: (range) => {
      const indices = defaultRangeExtractor(range);
      return pinnedIndex < 0 ? indices : [...new Set([...indices, pinnedIndex])].sort((a, b) => a - b);
    },
  });
  // The sidebar's scroll container can still have a zero-sized initial
  // measurement while its open animation is settling. In that state the
  // virtualizer has no range yet and returns no rows. Render the complete
  // list until the first usable range is available; the virtualizer will
  // trigger a rerender after measuring the container and take over then.
  const virtualRows = props.scrollRef ? virtualizer.getVirtualItems() : [];
  const rows = virtualRows.length > 0
    ? virtualRows
    : flatFiles.map((file, index) => ({ key: file.path, index, start: index * estimatedRowSize }));

  if (flatFiles.length === 0) {
    return (
      <div className={styles.empty}>
        <FolderOpen aria-hidden="true" />
        {emptyMessage}
      </div>
    );
  }

  return (
    <div style={props.scrollRef ? { height: virtualizer.getTotalSize(), position: "relative" } : undefined}>
      {rows.map((row) => {
        const file = flatFiles[row.index];
        const isFileActive = openPaths.has(file.path);
        const isDragging = draggingPath === file.path;
        const isSelected = selectedPaths.has(file.path);
        const isWorkbook = isSpreadsheetFile(file.filename || file.path);
        const normalized = normalizePath(file.path);
        const dirPart = normalized.includes("/") ? normalized.slice(0, normalized.lastIndexOf("/")) : "";

        return (
          <div key={row.key} data-index={row.index} className={styles.rowSlot}
            ref={props.scrollRef ? virtualizer.measureElement : undefined}
            style={props.scrollRef ? { position: "absolute", top: 0, left: 0, width: "100%", transform: `translateY(${row.start}px)` } : undefined}>
          <div
            draggable={!selectMode || isSelected}
            onDragStart={(e) => onDragStart(e, file)}
            onDragEnd={onDragEnd}
            onClick={() => onClick(file.path)}
            onDoubleClick={() => { if (selectMode) return; onDoubleClick(file.path); }}
            onKeyDown={(event) => {
              if (event.target !== event.currentTarget) return;
              if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                onClick(file.path);
              }
            }}
            role={selectMode ? "checkbox" : "button"}
            aria-checked={selectMode ? isSelected : undefined}
            aria-label={selectMode ? `选择 ${file.filename}` : `打开 ${file.filename}`}
            tabIndex={0}
            data-active={isFileActive}
            data-selected={isSelected}
            data-dragging={isDragging}
            className={cn("em-file-row group relative", styles.fileRow)}
            title={
              selectMode
                ? "点击选择"
                : `${workspaceFileOpenHint(file.filename)}\n${displayFilePath(file.path)}`
            }
          >
            {selectMode && (
              <span className={styles.checkbox} data-checked={isSelected} aria-hidden="true">
                {isSelected && <Check />}
              </span>
            )}
            <span className={styles.fileIcon} aria-hidden="true">
              <FileTypeIcon filename={file.filename} />
            </span>

            <div className={styles.fileInfo}>
              <span className={styles.fileName}>
                <span>{file.filename}</span>
                {isFileActive && <span className={styles.openDot} title="已在工作区打开" />}
              </span>
              {dirPart && (
                <span className={styles.filePath}>
                  <Folder aria-hidden="true" />
                  <span>{dirPart}</span>
                </span>
              )}
            </div>

            {!selectMode && (
              <>
                <button
                  className="flex-shrink-0 h-6 w-6 flex items-center justify-center rounded-md text-muted-foreground transition-opacity duration-150 hover:text-[var(--em-primary)] hover:bg-[var(--em-primary-alpha-10)] opacity-0 group-hover:opacity-100 touch-show"
                  onClick={(e) => { e.stopPropagation(); useExcelStore.getState().mentionFileToInput(file); }}
                  title="添加到输入框"
                >
                  <AtSign className="h-3.5 w-3.5" />
                </button>
                <DropdownMenu onOpenChange={(open) => setMenuPath(open ? file.path : null)}>
                  <DropdownMenuTrigger asChild>
                    <button
                      aria-label={`${file.filename} 的文件选项`}
                      className={`flex-shrink-0 h-6 w-6 flex items-center justify-center rounded-md text-muted-foreground transition-opacity duration-150 hover:bg-accent hover:text-foreground ${
                        isFileActive ? "opacity-100" : "opacity-0 group-hover:opacity-100 touch-show"
                      }`}
                      onClick={(e) => e.stopPropagation()}
                    >
                      <Ellipsis className="h-3.5 w-3.5" />
                    </button>
                  </DropdownMenuTrigger>
                  {menuPath === file.path && <DropdownMenuContent side="right" align="start" className={glassMenuPanelClass}>
                    <DropdownMenuItem className={glassMenuItemClass} onClick={(e) => { e.stopPropagation(); useExcelStore.getState().mentionFileToInput(file); }}>
                      <AtSign className="h-4 w-4" />
                      添加到输入框
                    </DropdownMenuItem>
                    <DropdownMenuItem className={glassMenuItemClass} onClick={(e) => { e.stopPropagation(); downloadFile(file.path, file.filename, sessionId).catch(() => {}); }}>
                      <Download className="h-4 w-4" />
                      下载
                    </DropdownMenuItem>
                    {isWorkbook && (
                      <>
                        <DropdownMenuSeparator />
                        <DropdownMenuItem className={glassMenuItemClass} onClick={(e) => {
                          e.stopPropagation();
                          useExcelStore.getState().setPendingTemplateMessage(
                            `请将 ${formatFileMention({ path: file.path })} 与 进行合并`
                          );
                        }}>
                          <Combine className="h-4 w-4" />
                          与其他文件合并
                        </DropdownMenuItem>
                        {(() => {
                          const otherExcels = comparisonFiles.filter((f) => f.path !== file.path);
                          if (otherExcels.length > 0) {
                            return (
                              <DropdownMenuSub>
                                <DropdownMenuSubTrigger className={glassMenuItemClass}>
                                  <ArrowLeftRight className="h-4 w-4" />
                                  与其他文件对比
                                </DropdownMenuSubTrigger>
                                <DropdownMenuSubContent className={glassMenuPanelClass}>
                                  {otherExcels.slice(0, 10).map((other) => (
                                    <DropdownMenuItem
                                      key={other.path}
                                      className={cn(glassMenuItemClass, "max-w-[min(24rem,calc(100vw-2rem))]")}
                                      onClick={(e) => {
                                        e.stopPropagation();
                                        useExcelStore.getState().openCompare(file.path, other.path);
                                      }}
                                    >
                                      <span className="min-w-0 truncate" title={other.filename}>
                                        {other.filename}
                                      </span>
                                    </DropdownMenuItem>
                                  ))}
                                </DropdownMenuSubContent>
                              </DropdownMenuSub>
                            );
                          }
                          return (
                            <DropdownMenuItem className={glassMenuItemClass} onClick={(e) => {
                              e.stopPropagation();
                              useExcelStore.getState().setPendingTemplateMessage(
                                `请对比 ${formatFileMention({ path: file.path })} 和 的差异`
                              );
                            }}>
                              <ArrowLeftRight className="h-4 w-4" />
                              与其他文件对比
                            </DropdownMenuItem>
                          );
                        })()}
                      </>
                    )}
                    <DropdownMenuSeparator />
                    <DropdownMenuItem variant="destructive" className={glassMenuDangerItemClass} onClick={(e) => { e.stopPropagation(); onRemove(file.path); }}>
                      <Trash2 className="h-4 w-4" />
                      删除
                    </DropdownMenuItem>
                  </DropdownMenuContent>}
                </DropdownMenu>
              </>
            )}
          </div>
          </div>
        );
      })}
    </div>
  );
}
