"use client";

import { useState, useEffect, useCallback } from "react";
import {
  Clock,
  FileText,
  GitBranch,
  HardDrive,
  Info,
  Loader2,
  Upload,
  Scan,
  Bot,
  Copy,
  ShieldCheck,
} from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import { Badge } from "@/components/ui/badge";
import {
  fetchFileRegistry,
  type FileRegistryEntry,
  type FileRegistryEvent,
} from "@/lib/api";

// ── Helpers ──────────────────────────────────────────────

function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatTime(iso: string): string {
  if (!iso) return "-";
  try {
    const d = new Date(iso);
    return d.toLocaleString("zh-CN", {
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso.slice(0, 16);
  }
}

function formatTimeFull(iso: string): string {
  if (!iso) return "-";
  try {
    return new Date(iso).toLocaleString("zh-CN");
  } catch {
    return iso;
  }
}

const ORIGIN_LABELS: Record<string, { label: string; icon: typeof Upload }> = {
  uploaded: { label: "用户上传", icon: Upload },
  scan: { label: "扫描发现", icon: Scan },
  agent_created: { label: "Agent 产出", icon: Bot },
  backup: { label: "备份副本", icon: Copy },
  cow_copy: { label: "CoW 保护", icon: ShieldCheck },
};

const EVENT_LABELS: Record<string, string> = {
  uploaded: "上传",
  created: "创建",
  modified: "修改",
  backed_up: "备份",
  cow_created: "CoW 创建",
  deleted: "删除",
  renamed: "重命名",
  moved: "移动",
  restored: "恢复",
  applied: "应用",
  staged: "暂存",
  committed: "提交",
};

// ── Component ────────────────────────────────────────────

interface FileInfoDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  filePath: string;
}

export function FileInfoDialog({ open, onOpenChange, filePath }: FileInfoDialogProps) {
  const [loading, setLoading] = useState(false);
  const [entry, setEntry] = useState<FileRegistryEntry | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!filePath) return;
    setLoading(true);
    setError(null);
    try {
      const res = await fetchFileRegistry({ fileId: filePath, includeEvents: true });
      if ("file" in res) {
        setEntry(res.file);
      } else if ("files" in res && res.files.length > 0) {
        // fallback: find exact match by canonical_path
        const match = res.files.find((f) => f.canonical_path === filePath);
        setEntry(match ?? res.files[0]);
      } else {
        setEntry(null);
        setError("文件注册表中未找到此文件的元数据");
      }
    } catch {
      setError("无法加载文件信息");
    } finally {
      setLoading(false);
    }
  }, [filePath]);

  useEffect(() => {
    if (open) load();
  }, [open, load]);

  const originInfo = entry ? ORIGIN_LABELS[entry.origin] : null;
  const OriginIcon = originInfo?.icon ?? FileText;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md max-h-[90dvh] sm:max-h-[80vh] overflow-y-auto p-4 sm:p-6">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 text-base">
            <Info className="h-4 w-4 text-muted-foreground" />
            文件信息
          </DialogTitle>
          <DialogDescription className="text-xs font-mono break-all line-clamp-2 sm:truncate sm:line-clamp-none" title={filePath}>
            {filePath}
          </DialogDescription>
        </DialogHeader>

        {loading && (
          <div className="flex items-center justify-center py-8">
            <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
          </div>
        )}

        {error && !loading && (
          <div className="text-center py-6 text-sm text-muted-foreground">{error}</div>
        )}

        {entry && !loading && (
          <div className="space-y-3 sm:space-y-4">
            {/* ── Basic info ── */}
            <section className="space-y-2">
              <h4 className="text-xs font-medium text-muted-foreground uppercase tracking-wider">基本信息</h4>
              <div className="grid grid-cols-[auto_1fr] gap-x-2 sm:gap-x-3 gap-y-1.5 text-[13px] sm:text-sm">
                <span className="text-muted-foreground">文件名</span>
                <span className="font-medium truncate" title={entry.original_name}>{entry.original_name}</span>

                <span className="text-muted-foreground">类型</span>
                <span>
                  <Badge variant="outline" className="text-[10px] h-5 px-1.5">
                    {entry.file_type}
                  </Badge>
                </span>

                <span className="text-muted-foreground">大小</span>
                <span>{formatBytes(entry.size_bytes)}</span>

                <span className="text-muted-foreground">来源</span>
                <span className="flex items-center gap-1.5">
                  <OriginIcon className="h-3.5 w-3.5 text-muted-foreground" />
                  {originInfo?.label ?? entry.origin}
                  {entry.origin_tool && (
                    <Badge variant="secondary" className="text-[10px] h-4 px-1">
                      {entry.origin_tool}
                    </Badge>
                  )}
                </span>

                {entry.origin_session_id && (
                  <>
                    <span className="text-muted-foreground">来源会话</span>
                    <span className="text-xs font-mono text-muted-foreground truncate" title={entry.origin_session_id}>
                      {entry.origin_session_id.slice(0, 12)}...
                    </span>
                  </>
                )}

                {entry.origin_turn != null && (
                  <>
                    <span className="text-muted-foreground">来源轮次</span>
                    <span>第 {entry.origin_turn} 轮</span>
                  </>
                )}

                <span className="text-muted-foreground">创建时间</span>
                <span title={formatTimeFull(entry.created_at)}>{formatTime(entry.created_at)}</span>

                <span className="text-muted-foreground">更新时间</span>
                <span title={formatTimeFull(entry.updated_at)}>{formatTime(entry.updated_at)}</span>

                {entry.deleted_at && (
                  <>
                    <span className="text-muted-foreground">删除时间</span>
                    <span className="text-destructive" title={formatTimeFull(entry.deleted_at)}>
                      {formatTime(entry.deleted_at)}
                    </span>
                  </>
                )}
              </div>
            </section>

            {/* ── Sheet meta ── */}
            {entry.sheet_meta && entry.sheet_meta.length > 0 && (
              <section className="space-y-2">
                <h4 className="text-xs font-medium text-muted-foreground uppercase tracking-wider flex items-center gap-1.5">
                  <HardDrive className="h-3 w-3" />
                  工作表 ({entry.sheet_meta.length})
                </h4>
                <div className="space-y-1">
                  {entry.sheet_meta.map((sheet, i) => {
                    const name = (sheet.name as string) || `Sheet${i + 1}`;
                    const rows = (sheet.rows as number) || 0;
                    const cols = (sheet.columns as number) || 0;
                    return (
                      <div
                        key={i}
                        className="flex items-center gap-2 text-sm px-2 py-1 rounded-md bg-muted/30"
                      >
                        <span className="font-medium truncate flex-1">{name}</span>
                        {rows > 0 && (
                          <span className="text-xs text-muted-foreground flex-shrink-0">
                            {rows} x {cols}
                          </span>
                        )}
                      </div>
                    );
                  })}
                </div>
              </section>
            )}

            {/* ── Lineage ── */}
            {entry.lineage && entry.lineage.length > 1 && (
              <section className="space-y-2">
                <h4 className="text-xs font-medium text-muted-foreground uppercase tracking-wider flex items-center gap-1.5">
                  <GitBranch className="h-3 w-3" />
                  版本链 ({entry.lineage.length})
                </h4>
                <div className="space-y-0.5">
                  {entry.lineage.map((ancestor, i) => (
                    <div
                      key={ancestor.id}
                      className="flex items-center gap-2 text-sm pl-2"
                    >
                      <div className="flex flex-col items-center flex-shrink-0" style={{ width: 16 }}>
                        <div
                          className="w-2 h-2 rounded-full flex-shrink-0"
                          style={{
                            backgroundColor: i === 0 ? "var(--em-primary)" : "var(--em-primary-alpha-30)",
                          }}
                        />
                        {i < entry.lineage!.length - 1 && (
                          <div className="w-px flex-1 min-h-3 bg-border" />
                        )}
                      </div>
                      <span className={`truncate ${i === 0 ? "font-medium" : "text-muted-foreground"}`}>
                        {ancestor.original_name}
                      </span>
                      <Badge variant="outline" className="text-[10px] h-4 px-1 flex-shrink-0">
                        {ORIGIN_LABELS[ancestor.origin]?.label ?? ancestor.origin}
                      </Badge>
                    </div>
                  ))}
                </div>
              </section>
            )}

            {/* ── Events timeline ── */}
            {entry.events && entry.events.length > 0 && (
              <section className="space-y-2">
                <h4 className="text-xs font-medium text-muted-foreground uppercase tracking-wider flex items-center gap-1.5">
                  <Clock className="h-3 w-3" />
                  事件历史 ({entry.events.length})
                </h4>
                <div className="space-y-0">
                  {entry.events.map((evt: FileRegistryEvent, i: number) => (
                    <div
                      key={evt.id}
                      className="flex items-start gap-2 text-sm"
                    >
                      <div className="flex flex-col items-center flex-shrink-0 pt-1" style={{ width: 16 }}>
                        <div className="w-1.5 h-1.5 rounded-full bg-muted-foreground/40 flex-shrink-0" />
                        {i < entry.events!.length - 1 && (
                          <div className="w-px flex-1 min-h-4 bg-border" />
                        )}
                      </div>
                      <div className="flex-1 min-w-0 pb-2">
                        <div className="flex items-center gap-1 sm:gap-1.5 flex-wrap">
                          <span className="font-medium text-xs">
                            {EVENT_LABELS[evt.event_type] ?? evt.event_type}
                          </span>
                          {evt.tool_name && (
                            <Badge variant="secondary" className="text-[10px] h-4 px-1">
                              {evt.tool_name}
                            </Badge>
                          )}
                          <span className="text-[10px] text-muted-foreground ml-auto flex-shrink-0">
                            {formatTime(evt.created_at)}
                          </span>
                        </div>
                        {evt.turn != null && (
                          <div className="text-[11px] text-muted-foreground">
                            第 {evt.turn} 轮
                          </div>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              </section>
            )}

            {/* ── Children (backups/copies) ── */}
            {entry.children && entry.children.length > 0 && (
              <section className="space-y-2">
                <h4 className="text-xs font-medium text-muted-foreground uppercase tracking-wider flex items-center gap-1.5">
                  <Copy className="h-3 w-3" />
                  关联副本 ({entry.children.length})
                </h4>
                <div className="space-y-1">
                  {entry.children.map((child) => (
                    <div
                      key={child.id}
                      className="flex items-center gap-2 text-sm px-2 py-1 rounded-md bg-muted/30"
                    >
                      <span className="truncate flex-1 text-muted-foreground font-mono text-[11px] sm:text-xs">
                        {child.canonical_path}
                      </span>
                      <Badge variant="outline" className="text-[10px] h-4 px-1 flex-shrink-0">
                        {ORIGIN_LABELS[child.origin]?.label ?? child.origin}
                      </Badge>
                    </div>
                  ))}
                </div>
              </section>
            )}
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
