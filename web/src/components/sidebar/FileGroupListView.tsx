"use client";

import { useState, useCallback, useEffect } from "react";
import {
  ChevronDown,
  ChevronRight,
  Trash2,
  AtSign,
  Pencil,
  Layers,
  ArrowLeftRight,
} from "lucide-react";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { FileTypeIcon } from "@/components/ui/file-type-icon";
import { useExcelStore } from "@/stores/excel-store";
import { updateFileGroup, type FileGroup } from "@/lib/api";
import { InlineRenameInput } from "./InlineInputs";
import { FileRelationshipGraph } from "./FileRelationshipGraph";

interface FileGroupListViewProps {
  onClickFile: (path: string) => void;
}

export function FileGroupListView({ onClickFile }: FileGroupListViewProps) {
  const fileGroups = useExcelStore((s) => s.fileGroups);
  const fileGroupsLoaded = useExcelStore((s) => s.fileGroupsLoaded);
  const loadFileGroups = useExcelStore((s) => s.loadFileGroups);
  const deleteGroup = useExcelStore((s) => s.deleteGroup);

  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set());
  const [renamingId, setRenamingId] = useState<string | null>(null);

  useEffect(() => {
    if (!fileGroupsLoaded) loadFileGroups();
  }, [fileGroupsLoaded, loadFileGroups]);

  const toggleExpand = useCallback((id: string) => {
    setExpandedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const handleRename = useCallback(
    async (groupId: string, newName: string) => {
      const name = newName.trim();
      setRenamingId(null);
      if (!name) return;
      try {
        await updateFileGroup(groupId, { name });
        loadFileGroups();
      } catch {
        // silent
      }
    },
    [loadFileGroups],
  );

  const handleReferenceToChat = useCallback(
    (group: FileGroup) => {
      const files = group.members.map((m) => ({
        path: m.canonical_path,
        filename: m.original_name,
      }));
      if (files.length > 0) {
        useExcelStore.getState().mentionFilesToInput(files);
      }
    },
    [],
  );

  if (!fileGroupsLoaded) {
    return (
      <div className="flex flex-col items-center justify-center py-6 gap-2 text-muted-foreground/60">
        <div className="h-4 w-4 border-2 border-current border-t-transparent rounded-full animate-spin" />
        <span className="text-[11px]">加载文件组…</span>
      </div>
    );
  }

  if (fileGroups.length === 0) {
    return (
      <div className="flex flex-col items-center gap-2 py-6 text-center">
        <Layers className="h-6 w-6 text-muted-foreground/40" />
        <span className="text-[11px] text-muted-foreground/60">
          暂无文件组，多选文件后可创建
        </span>
      </div>
    );
  }

  return (
    <div className="space-y-1">
      {/* 文件关系可视化 — 在文件组列表顶部展示 */}
      <div className="border-b border-border/40 mb-1">
        <FileRelationshipGraph onClickFile={onClickFile} />
      </div>

      {fileGroups.map((group) => {
        const isExpanded = expandedIds.has(group.id);
        const memberCount = group.members?.length ?? 0;

        return (
          <div key={group.id} className="rounded-lg overflow-hidden">
            {/* Group header */}
            <div
              draggable
              onDragStart={(e) => {
                if (group.members && group.members.length > 0) {
                  const files = group.members.map((m) => ({
                    path: m.canonical_path,
                    filename: m.original_name,
                  }));
                  e.dataTransfer.setData(
                    "text/plain",
                    files.map((f) => `@file:${f.filename}`).join(" "),
                  );
                  e.dataTransfer.setData(
                    "application/x-excel-file",
                    JSON.stringify(files),
                  );
                  e.dataTransfer.effectAllowed = "copy";
                }
              }}
              className="flex items-center gap-2 px-3 py-2 cursor-pointer hover:bg-accent/40 transition-colors duration-100 group"
              onClick={() => toggleExpand(group.id)}
            >
              {isExpanded ? (
                <ChevronDown className="h-3.5 w-3.5 flex-shrink-0 text-muted-foreground" />
              ) : (
                <ChevronRight className="h-3.5 w-3.5 flex-shrink-0 text-muted-foreground" />
              )}
              <Layers
                className="h-4 w-4 flex-shrink-0"
                style={{ color: "var(--em-primary)" }}
              />
              <div className="flex-1 min-w-0">
                {renamingId === group.id ? (
                  <InlineRenameInput
                    defaultValue={group.name}
                    onConfirm={(name: string) => handleRename(group.id, name)}
                    onCancel={() => setRenamingId(null)}
                  />
                ) : (
                  <span className="block truncate text-[13px] font-medium text-foreground/90">
                    {group.name}
                  </span>
                )}
              </div>
              <span className="flex-shrink-0 text-[10px] text-muted-foreground/60">
                {memberCount} 个文件
              </span>
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <button
                    className="flex-shrink-0 h-6 w-6 flex items-center justify-center rounded-md text-muted-foreground opacity-0 group-hover:opacity-100 transition-opacity duration-150 hover:bg-accent hover:text-foreground"
                    onClick={(e) => e.stopPropagation()}
                  >
                    <Pencil className="h-3 w-3" />
                  </button>
                </DropdownMenuTrigger>
                <DropdownMenuContent side="right" align="start" className="w-36">
                  <DropdownMenuItem
                    onClick={(e) => {
                      e.stopPropagation();
                      handleReferenceToChat(group);
                    }}
                  >
                    <AtSign className="h-4 w-4" />
                    引用到聊天
                  </DropdownMenuItem>
                  {memberCount >= 2 && (
                    <DropdownMenuItem
                      onClick={(e) => {
                        e.stopPropagation();
                        const a = group.members[0]?.canonical_path;
                        const b = group.members[1]?.canonical_path;
                        if (a && b) {
                          useExcelStore.getState().openCompare(a, b);
                        }
                      }}
                    >
                      <ArrowLeftRight className="h-4 w-4" />
                      对比前两个文件
                    </DropdownMenuItem>
                  )}
                  <DropdownMenuItem
                    onClick={(e) => {
                      e.stopPropagation();
                      setRenamingId(group.id);
                    }}
                  >
                    <Pencil className="h-4 w-4" />
                    重命名
                  </DropdownMenuItem>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem
                    variant="destructive"
                    onClick={(e) => {
                      e.stopPropagation();
                      deleteGroup(group.id);
                    }}
                  >
                    <Trash2 className="h-4 w-4" />
                    删除文件组
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            </div>

            {/* Group members (expanded) */}
            {isExpanded && group.members && group.members.length > 0 && (
              <div className="pl-6 pb-1">
                {group.members.map((member) => (
                  <div
                    key={member.file_id}
                    draggable
                    onDragStart={(e) => {
                      e.dataTransfer.setData("text/plain", `@file:${member.original_name}`);
                      e.dataTransfer.setData(
                        "application/x-excel-file",
                        JSON.stringify({ path: member.canonical_path, filename: member.original_name }),
                      );
                      e.dataTransfer.effectAllowed = "copy";
                    }}
                    className="flex items-center gap-2 px-3 py-1.5 rounded-md cursor-pointer hover:bg-accent/30 transition-colors duration-100 text-[12px]"
                    onClick={() => onClickFile(member.canonical_path)}
                  >
                    <FileTypeIcon
                      filename={member.original_name}
                      className="h-3.5 w-3.5 flex-shrink-0"
                    />
                    <span className="flex-1 min-w-0 truncate text-foreground/80">
                      {member.original_name}
                    </span>
                    {member.role !== "member" && (
                      <span
                        className="flex-shrink-0 text-[9px] px-1.5 py-0.5 rounded-full"
                        style={{
                          backgroundColor: "var(--em-primary-alpha-10)",
                          color: "var(--em-primary)",
                        }}
                      >
                        {member.role}
                      </span>
                    )}
                  </div>
                ))}
              </div>
            )}

            {isExpanded && (!group.members || group.members.length === 0) && (
              <div className="pl-10 pb-2 text-[11px] text-muted-foreground/50">
                组内暂无文件
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
