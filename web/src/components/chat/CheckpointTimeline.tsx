"use client";

import { useEffect, useState, useCallback, useRef, type CSSProperties } from "react";
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
  type RevisionPreviewResponse,
} from "@/lib/api";
import { workspaceKeyFromSession } from "@/lib/workspace-file-ref";
import { flushWorkbookEdits, hasPendingWorkbookEdits, isWorkbookEditPaused } from "@/lib/excel-cell-edit";
import { RevisionWorkbookPreview } from "@/components/excel/RevisionWorkbookPreview";
import { displayFilePath } from "@/lib/file-identity";
import {
  fileBaseName,
  formatRelativeTime,
  groupRevisions,
  isCurrentRevision,
  revisionReasonLabel,
  shortContentVersion,
} from "@/lib/revision-display";

function refreshOpenDocument(path: string, workspaceKey: string, contentVersion: string | null) {
  const excel = useExcelStore.getState();
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

interface RevisionTimelineProps {
  filePath: string | null;
  workspaceId?: string | null;
  active?: boolean;
}

export function RevisionTimelinePanel(props: RevisionTimelineProps) {
  const activeSessionId = useSessionStore((s) => s.activeSessionId);
  const session = useSessionStore((s) => s.sessions.find((item) => item.id === activeSessionId));
  const workspaceId = props.workspaceId ?? session?.workspaceId;
  const sessionId = workspaceId && session?.workspaceId !== workspaceId ? null : activeSessionId;
  const workspaceKey = workspaceId ? `id:${workspaceId}` : workspaceKeyFromSession(session);
  // Async results from an old file/session cannot populate a newly selected file.
  return <RevisionTimelineContent key={`${workspaceKey}|${sessionId}|${props.filePath}`} {...props}
    workspaceId={workspaceId} activeSessionId={sessionId} workspaceKey={workspaceKey} />;
}

function RevisionTimelineContent({ filePath, workspaceId, active = true, activeSessionId, workspaceKey }: RevisionTimelineProps & {
  activeSessionId: string | null;
  workspaceKey: string;
}) {
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
  const [preview, setPreview] = useState<{ revision: WorkbookRevisionItem; data: RevisionPreviewResponse } | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const loadGeneration = useRef(0);
  const loadAbort = useRef<AbortController | null>(null);
  const previewAbort = useRef<AbortController | null>(null);
  const alive = useRef(true);
  const actionPending = useRef(false);
  const restoreVersion = useRef<string | null>(null);
  const activeRef = useRef(active);
  activeRef.current = active;
  const [historyLimit, setHistoryLimit] = useState(100);
  const [total, setTotal] = useState(0);

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      loadGeneration.current++;
      loadAbort.current?.abort();
      previewAbort.current?.abort();
    };
  }, []);

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
        limit: historyLimit,
        signal: controller.signal,
      });
      if (controller.signal.aborted || !alive.current || generation !== loadGeneration.current) return;
      setRevisions(data.revisions);
      setTotal(data.total ?? data.revisions.length);
      setCurrentVersion(data.content_version);
      setLoadError(null);
    } catch (err) {
      if (controller.signal.aborted || generation !== loadGeneration.current) return;
      setLoadError(err instanceof Error ? err.message : "无法读取文件版本");
    } finally {
      if (generation === loadGeneration.current) setLoading(false);
    }
  }, [filePath, activeSessionId, workspaceId, historyLimit]);

  useEffect(() => {
    if (!active) {
      previewAbort.current?.abort();
      setPreview(null);
      setConfirmOpen(false);
      return;
    }
    load();
    const id = setInterval(load, 10000);
    return () => {
      clearInterval(id);
      loadAbort.current?.abort();
    };
  }, [active, load, workspaceFilesVersion, excelRefresh, wordRefresh]);

  const handleConfirm = async () => {
    if (!filePath || !target || actionPending.current) return;
    const file = { workspaceKey, relative: filePath };
    if (hasPendingWorkbookEdits(file) || isWorkbookEditPaused(file)) {
      setConfirmOpen(false);
      setLastResult({ ok: false, message: "仍有未保存的编辑，请保存或处理冲突后再恢复。" });
      return;
    }
    actionPending.current = true;
    setConfirmOpen(false);
    setRestoring(true);
    setLastResult(null);
    try {
      const res = await restoreRevision({
        path: filePath,
        revisionId: target.revision_id,
        expectedVersion: restoreVersion.current,
        sessionId: activeSessionId,
        workspaceId,
      });
      refreshOpenDocument(filePath, workspaceKey, res.content_version);
      if (!alive.current) return;
      const label = revisionReasonLabel(target.reason, target.label);
      setLastResult({
        ok: true,
        message: `已恢复到「${label}」`,
      });
      await load();
    } catch (err) {
      if (!alive.current) return;
      setLastResult({
        ok: false,
        message: err instanceof Error ? err.message : "恢复失败（需要当前版本）",
      });
    } finally {
      actionPending.current = false;
      if (alive.current) { setRestoring(false); setTarget(null); }
    }
  };

  const groups = groupRevisions(revisions);
  const fileName = fileBaseName(filePath);

  const askRestore = async (rec: WorkbookRevisionItem) => {
    if (!filePath || actionPending.current) return;
    actionPending.current = true;
    setRestoring(true);
    try {
      const file = { workspaceKey, relative: filePath };
      await flushWorkbookEdits(file);
      if (!alive.current) return;
      if (hasPendingWorkbookEdits(file) || isWorkbookEditPaused(file)) throw new Error("仍有未保存的编辑，请保存或处理冲突后再恢复。");
      const latest = await fetchRevisions(filePath, { sessionId: activeSessionId ?? undefined, workspaceId, limit: 1 });
      if (!alive.current || !activeRef.current) return;
      // Capture the version the user confirms. Polling must never silently
      // authorize overwriting an edit made while the confirmation is open.
      restoreVersion.current = latest.content_version;
      setTarget(rec);
      setConfirmOpen(true);
    } catch (err) {
      if (alive.current) setLastResult({ ok: false, message: err instanceof Error ? err.message : "无法准备恢复" });
    } finally {
      actionPending.current = false;
      if (alive.current) setRestoring(false);
    }
  };

  const showPreview = async (rec: WorkbookRevisionItem, sheet?: string) => {
    if (!filePath) return;
    previewAbort.current?.abort();
    const controller = new AbortController();
    previewAbort.current = controller;
    setPreviewLoading(true);
    try {
      const data = await fetchRevisionPreview({
        path: filePath,
        revisionId: rec.revision_id,
        sessionId: activeSessionId,
        workspaceId,
        sheet,
        signal: controller.signal,
      });
      if (controller.signal.aborted || !alive.current) return;
      setPreview({ revision: rec, data });
    } catch (err) {
      if (controller.signal.aborted || !alive.current) return;
      setLastResult({ ok: false, message: err instanceof Error ? err.message : "历史版本预览失败" });
    } finally {
      if (alive.current && previewAbort.current === controller) setPreviewLoading(false);
    }
  };

  const removeCheckpoint = async (rec: WorkbookRevisionItem) => {
    if (!filePath || rec.reason !== "checkpoint" || actionPending.current) return;
    if (typeof window !== "undefined" && !window.confirm(`删除检查点「${revisionReasonLabel(rec.reason, rec.label)}」？此操作不会删除自动历史。`)) return;
    actionPending.current = true;
    setRestoring(true);
    try {
      await deleteRevision({ path: filePath, revisionId: rec.revision_id, sessionId: activeSessionId, workspaceId });
      if (!alive.current) return;
      setLastResult({ ok: true, message: "检查点已删除" });
      await load();
    } catch (err) {
      if (alive.current) setLastResult({ ok: false, message: err instanceof Error ? err.message : "检查点删除失败" });
    } finally {
      actionPending.current = false;
      if (alive.current) setRestoring(false);
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
                <p className="text-sm font-medium truncate" title={displayFilePath(filePath)}>
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
                                {current && (
                                  <span className="text-[10px] text-muted-foreground shrink-0">正在使用</span>
                                )}
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
                                      disabled={restoring || current}
                                      onClick={() => void askRestore(rec)}
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
          {total > revisions.length && <div className="text-xs text-muted-foreground">
            已显示最近 {revisions.length} / {total} 个快照。
            {historyLimit < 500 && <Button variant="ghost" disabled={loading} onClick={() => setHistoryLimit((n) => Math.min(500, n + 100))}>加载更早版本</Button>}
          </div>}
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
          <OverlayCardAction action="destructive" disabled={restoring} onClick={() => void handleConfirm()}>
            {isMobile ? "恢复" : "确认恢复"}
          </OverlayCardAction>
        </OverlayCardFooter>
      </OverlayCard>
      <OverlayCard open={Boolean(preview)} onOpenChange={(open) => { if (!open) { previewAbort.current?.abort(); setPreview(null); } }} size="lg" style={{ "--overlay-card-width": "min(1000px, 95vw)" } as CSSProperties}>
        <OverlayCardHeader
          title={preview ? `历史预览 · ${revisionReasonLabel(preview.revision.reason, preview.revision.label)}` : "历史预览"}
          description={preview ? `${fileName} · ${formatRelativeTime(preview.revision.created_at)}` : ""}
          onClose={() => { previewAbort.current?.abort(); setPreview(null); }}
        />
        <div className="max-h-[50vh] overflow-auto px-4 pb-3">
          {preview?.data.regions?.length ? <RevisionWorkbookPreview data={preview.data} loading={previewLoading} onSheet={(sheet) => void showPreview(preview.revision, sheet)} />
            : preview?.data.paragraphs?.length ? <div className="space-y-2 text-sm">{preview.data.paragraphs.map((p, i) => <p key={i}>{p.text}</p>)}</div>
            : <p className="text-xs text-muted-foreground">该窗口没有内容。</p>}
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
