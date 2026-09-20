"use client";

import { Fragment, useEffect, useState, useCallback, useRef } from "react";
import { Loader2, RotateCcw, AlertTriangle, CheckCircle2, History, Eye, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  OverlayCard,
  OverlayCardAction,
  OverlayCardFooter,
  OverlayCardHeader,
} from "@/components/ui/overlay-card";
import { useSessionStore } from "@/stores/session-store";
import { useExcelStore } from "@/stores/excel-store";
import { isWordDocumentPath, useWordStore } from "@/stores/word-store";
import { useIsMobile } from "@/hooks/use-mobile";
import {
  fetchRevisions,
  fetchRevisionPreview,
  deleteRevision,
  restoreRevision,
  type WorkbookRevisionItem,
} from "@/lib/api";
import { workspaceKeyFromSession } from "@/lib/workspace-file-ref";
import {
  fileBaseName,
  formatRelativeTime,
  groupRevisions,
  isCurrentRevision,
  revisionReasonLabel,
  shortContentVersion,
} from "@/lib/revision-display";

function refreshOpenDocument(path: string, contentVersion: string | null) {
  const excel = useExcelStore.getState();
  const session = useSessionStore.getState();
  const active = session.sessions.find((item) => item.id === session.activeSessionId);
  const workspaceKey = excel.activeWorkspaceKey ?? workspaceKeyFromSession(active);
  excel.notifyWorkbookChanged(path, workspaceKey, contentVersion || undefined, "refresh");
  excel.bumpWorkspaceFilesVersion();
  if (isWordDocumentPath(path)) {
    useWordStore.getState().triggerRefresh();
  }
}

function reasonTone(reason: string): string {
  if (reason === "afterEdit") return "text-emerald-600 dark:text-emerald-400";
  if (reason === "beforeRestore") return "text-amber-600 dark:text-amber-400";
  if (reason === "checkpoint") return "text-sky-600 dark:text-sky-400";
  return "text-muted-foreground";
}

export function RevisionTimelinePanel({
  filePath,
  workspaceId,
  active = true,
}: {
  filePath: string | null;
  workspaceId?: string | null;
  active?: boolean;
}) {
  const activeSessionId = useSessionStore((s) => s.activeSessionId);
  const workspaceFilesVersion = useExcelStore((s) => s.workspaceFilesVersion);
  const excelRefresh = useExcelStore((s) => s.refreshCounter);
  const wordRefresh = useWordStore((s) => s.refreshCounter);
  const isMobile = useIsMobile();
  const [revisions, setRevisions] = useState<WorkbookRevisionItem[]>([]);
  const [currentVersion, setCurrentVersion] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [restoring, setRestoring] = useState(false);
  const [target, setTarget] = useState<WorkbookRevisionItem | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [lastResult, setLastResult] = useState<{ ok: boolean; message: string } | null>(null);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [preview, setPreview] = useState<{ revision: WorkbookRevisionItem; cells: { address: string; value: string }[] } | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const loadGeneration = useRef(0);
  const loadAbort = useRef<AbortController | null>(null);

  useEffect(() => {
    setRevisions([]);
    setCurrentVersion(null);
    setLastResult(null);
    setExpandedId(null);
    setLoadError(null);
    setTarget(null);
    setConfirmOpen(false);
  }, [filePath, workspaceId, activeSessionId]);

  const load = useCallback(async () => {
    const generation = ++loadGeneration.current;
    loadAbort.current?.abort();
    const controller = new AbortController();
    loadAbort.current = controller;
    if (!filePath) {
      setRevisions([]);
      setCurrentVersion(null);
      setLoadError(null);
      return;
    }
    setLoading(true);
    try {
      const data = await fetchRevisions(filePath, {
        sessionId: activeSessionId ?? undefined,
        workspaceId,
        limit: 100,
        signal: controller.signal,
      });
      if (generation !== loadGeneration.current) return;
      setRevisions(data.revisions);
      setCurrentVersion(data.content_version);
      setLoadError(null);
    } catch (err) {
      if (controller.signal.aborted || generation !== loadGeneration.current) return;
      setRevisions([]);
      setCurrentVersion(null);
      setLoadError(err instanceof Error ? err.message : "无法读取文件版本");
    } finally {
      if (generation === loadGeneration.current) setLoading(false);
    }
  }, [filePath, activeSessionId, workspaceId]);

  useEffect(() => {
    if (!active) return;
    load();
    const id = setInterval(load, 10000);
    return () => {
      clearInterval(id);
      loadAbort.current?.abort();
    };
  }, [active, load, workspaceFilesVersion, excelRefresh, wordRefresh]);

  const handleConfirm = async () => {
    if (!filePath || !target) return;
    setConfirmOpen(false);
    setRestoring(true);
    setLastResult(null);
    try {
      const res = await restoreRevision({
        path: filePath,
        revisionId: target.revision_id,
        expectedVersion: currentVersion,
        sessionId: activeSessionId,
        workspaceId,
      });
      const label = revisionReasonLabel(target.reason, target.label);
      setLastResult({
        ok: true,
        message: `已恢复到「${label}」`,
      });
      refreshOpenDocument(filePath, res.content_version);
      await load();
    } catch (err) {
      setLastResult({
        ok: false,
        message: err instanceof Error ? err.message : "恢复失败（需要当前版本）",
      });
    } finally {
      setRestoring(false);
      setTarget(null);
    }
  };

  const groups = groupRevisions(revisions);
  const fileName = fileBaseName(filePath);

  const askRestore = (rec: WorkbookRevisionItem) => {
    setTarget(rec);
    setConfirmOpen(true);
  };

  const showPreview = async (rec: WorkbookRevisionItem) => {
    if (!filePath) return;
    setPreviewLoading(true);
    try {
      const data = await fetchRevisionPreview({
        path: filePath,
        revisionId: rec.revision_id,
        sessionId: activeSessionId,
        workspaceId,
      });
      const cells: { address: string; value: string }[] = [];
      for (const [index, paragraph] of (data.paragraphs ?? []).entries()) {
        if (paragraph.text) cells.push({ address: `P${index + 1}`, value: paragraph.text });
        if (cells.length >= 100) break;
      }
      for (const [address, cell] of Object.entries(data.windows?.[0]?.cells ?? {})) {
        const value = cell.f ? `公式: ${cell.f}` : cell.v == null ? "" : String(cell.v);
        if (value) cells.push({ address, value });
        if (cells.length >= 100) break;
      }
      setPreview({ revision: rec, cells });
    } catch (err) {
      setLastResult({ ok: false, message: err instanceof Error ? err.message : "历史版本预览失败" });
    } finally {
      setPreviewLoading(false);
    }
  };

  const removeCheckpoint = async (rec: WorkbookRevisionItem) => {
    if (!filePath || rec.reason !== "checkpoint") return;
    if (typeof window !== "undefined" && !window.confirm(`删除检查点「${revisionReasonLabel(rec.reason, rec.label)}」？此操作不会删除自动历史。`)) return;
    setRestoring(true);
    try {
      await deleteRevision({ path: filePath, revisionId: rec.revision_id, sessionId: activeSessionId, workspaceId });
      setLastResult({ ok: true, message: "检查点已删除" });
      await load();
    } catch (err) {
      setLastResult({ ok: false, message: err instanceof Error ? err.message : "检查点删除失败" });
    } finally {
      setRestoring(false);
    }
  };

  return (
    <>
      <div className="flex h-full flex-col">
        <div className="flex-1 overflow-y-auto px-3 py-3 space-y-3">
          {!filePath && (
            <EmptyState
              title="还没有打开文件"
              body="从左侧打开工作簿或文档后，这里会按时间列出它的写入快照。"
            />
          )}
          {filePath && (
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                <p className="text-sm font-medium truncate" title={filePath}>
                  {fileName}
                </p>
                <p className="text-[11px] text-muted-foreground">
                  {revisions.length > 0
                    ? `${revisions.length} 个快照 · 整份文件可一键回到某一刻`
                    : "写入成功后会留下「编辑前 / 编辑后」快照"}
                </p>
              </div>
              {loading && <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground shrink-0 mt-1" />}
            </div>
          )}
          {loadError && (
            <p className="text-xs text-destructive flex items-center gap-1">
              <AlertTriangle className="h-3.5 w-3.5" />
              {loadError}
            </p>
          )}
          {sortedEmpty(filePath, loading, loadError, groups) && (
            <EmptyState
              title="还没有写入记录"
              body="用对话、表格编辑或恢复成功改过这个文件后，这里会出现可恢复的快照。"
            />
          )}
          {groups.length > 0 && (
            <div className="relative">
              {groups.map((group, index) => {
                const newest = group.items[0];
                const newestIsCurrent = newest ? isCurrentRevision(newest, currentVersion) : false;
                return (
                  <div key={group.key} className="relative pl-5 pb-4 last:pb-0">
                    {index < groups.length - 1 && (
                      <div className="absolute left-[7px] top-3 bottom-0 w-px bg-border" />
                    )}
                    <div
                      className={`absolute left-[3px] top-1.5 h-2.5 w-2.5 rounded-full border-2 bg-background ${
                        newestIsCurrent
                          ? "border-[var(--em-primary)] bg-[var(--em-primary)]/20"
                          : "border-muted-foreground/40"
                      }`}
                    />
                    <div
                      className={`rounded-xl border p-3 space-y-2 ${
                        newestIsCurrent
                          ? "border-[var(--em-primary)]/35 bg-[var(--em-primary)]/[0.04]"
                          : "border-border/70 bg-card"
                      }`}
                    >
                      <div className="flex items-start justify-between gap-2">
                        <div className="min-w-0">
                          <div className="flex items-center gap-1.5">
                            <span className="text-xs font-medium">{group.title}</span>
                            {newestIsCurrent && (
                              <span className="text-[10px] px-1.5 py-px rounded-full bg-[var(--em-primary)]/15 text-[var(--em-primary)]">
                                当前
                              </span>
                            )}
                          </div>
                          <p className="text-[11px] text-muted-foreground">
                            {formatRelativeTime(group.createdAt)}
                          </p>
                        </div>
                      </div>
                      <div className="space-y-1.5">
                        {group.items.map((rec) => {
                          const current = isCurrentRevision(rec, currentVersion);
                          const open = expandedId === rec.revision_id;
                          return (
                            <div
                              key={rec.revision_id}
                              className="rounded-lg bg-muted/40 px-2.5 py-2"
                            >
                              <div className="flex items-center gap-2">
                                <button
                                  type="button"
                                  className="min-w-0 flex-1 text-left"
                                  onClick={() => setExpandedId(open ? null : rec.revision_id)}
                                >
                                  <div className="flex items-center gap-1.5">
                                    <span className={`text-[11px] font-medium ${reasonTone(rec.reason)}`}>
                                      {revisionReasonLabel(rec.reason, rec.label)}
                                    </span>
                                    {current && group.items.length > 1 && (
                                      <span className="text-[10px] text-[var(--em-primary)]">当前文件</span>
                                    )}
                                  </div>
                                  {open && (
                                    <p className="mt-1 text-[10px] font-mono text-muted-foreground break-all">
                                      {rec.revision_id}
                                      {rec.content_version
                                        ? ` · ${shortContentVersion(rec.content_version)}`
                                        : ""}
                                    </p>
                                  )}
                                </button>
                                {current ? (
                                  <span className="text-[10px] text-muted-foreground shrink-0">正在使用</span>
                                ) : (
                                  <div className="flex items-center gap-1">
                                    <Button
                                      variant="ghost"
                                      size="sm"
                                      className="h-7 text-[11px] px-2"
                                      disabled={previewLoading}
                                      onClick={() => void showPreview(rec)}
                                    >
                                      <Eye className="h-3 w-3 mr-1" />预览
                                    </Button>
                                    <Button
                                      variant="outline"
                                      size="sm"
                                      className="h-7 text-[11px] px-2"
                                      disabled={restoring}
                                      onClick={() => askRestore(rec)}
                                    >
                                      <RotateCcw className="h-3 w-3 mr-1" />恢复
                                    </Button>
                                    {rec.reason === "checkpoint" && (
                                      <Button
                                        variant="ghost"
                                        size="sm"
                                        className="h-7 w-7 p-0 text-muted-foreground hover:text-destructive"
                                        disabled={restoring}
                                        onClick={() => void removeCheckpoint(rec)}
                                        title="删除检查点"
                                      >
                                        <Trash2 className="h-3 w-3" />
                                      </Button>
                                    )}
                                  </div>
                                )}
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
          {lastResult && (
            <div className={`text-xs flex items-center gap-1 ${lastResult.ok ? "text-emerald-600" : "text-destructive"}`}>
              {lastResult.ok ? <CheckCircle2 className="h-3.5 w-3.5" /> : <AlertTriangle className="h-3.5 w-3.5" />}
              {lastResult.message}
            </div>
          )}
        </div>
      </div>
      <OverlayCard open={confirmOpen} onOpenChange={setConfirmOpen} size="sm" tone="warning">
        <OverlayCardHeader
          title="恢复文件版本"
          description={
            target
              ? `用「${revisionReasonLabel(target.reason, target.label)}」覆盖当前的「${fileName}」。之后仍可用更新的快照再改回来。`
              : ""
          }
          onClose={() => setConfirmOpen(false)}
        />
        <OverlayCardFooter>
          <OverlayCardAction action="ghost" onClick={() => setConfirmOpen(false)}>取消</OverlayCardAction>
          <OverlayCardAction action="destructive" onClick={() => void handleConfirm()}>
            {isMobile ? "恢复" : "确认恢复"}
          </OverlayCardAction>
        </OverlayCardFooter>
      </OverlayCard>
      <OverlayCard open={Boolean(preview)} onOpenChange={(open) => { if (!open) setPreview(null); }} size="md">
        <OverlayCardHeader
          title={preview ? `历史预览 · ${revisionReasonLabel(preview.revision.reason, preview.revision.label)}` : "历史预览"}
          description={preview ? `revision ${preview.revision.revision_id}` : ""}
          onClose={() => setPreview(null)}
        />
        <div className="max-h-[50vh] overflow-auto px-4 pb-3">
          {preview?.cells.length ? (
            <div className="grid grid-cols-[90px_1fr] gap-x-3 gap-y-1 text-xs">
              {preview.cells.map((cell) => <Fragment key={cell.address}><span className="font-mono text-muted-foreground">{cell.address}</span><span className="break-all">{cell.value}</span></Fragment>)}
            </div>
          ) : <p className="text-xs text-muted-foreground">该窗口没有非空单元格。</p>}
        </div>
      </OverlayCard>
    </>
  );
}

function sortedEmpty(
  filePath: string | null,
  loading: boolean,
  loadError: string | null,
  groups: { key: string }[],
): boolean {
  return Boolean(filePath && !loading && !loadError && groups.length === 0);
}

function EmptyState({ title, body }: { title: string; body: string }) {
  return (
    <div className="flex flex-col items-center justify-center text-center px-4 py-10 text-muted-foreground">
      <History className="h-8 w-8 mb-2 opacity-30" />
      <p className="text-xs font-medium text-foreground/80">{title}</p>
      <p className="mt-1 text-[11px] leading-5 max-w-[220px]">{body}</p>
    </div>
  );
}
