"use client";

import { useEffect, useState, useCallback } from "react";
import { Loader2, RotateCcw, AlertTriangle, CheckCircle2, History } from "lucide-react";
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
  invalidateWorkbookCaches,
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
  invalidateWorkbookCaches({ workspaceKey, relative: path });
  excel.setContentVersion(path, contentVersion, workspaceKey);
  excel.bumpWorkspaceFilesVersion();
  useExcelStore.setState((s) => ({ refreshCounter: s.refreshCounter + 1 }));
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
  active = true,
}: {
  filePath: string | null;
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

  useEffect(() => {
    setRevisions([]);
    setCurrentVersion(null);
    setLastResult(null);
    setExpandedId(null);
    setLoadError(null);
    setTarget(null);
    setConfirmOpen(false);
  }, [filePath]);

  const load = useCallback(async () => {
    if (!filePath) {
      setRevisions([]);
      setCurrentVersion(null);
      setLoadError(null);
      return;
    }
    setLoading(true);
    try {
      const data = await fetchRevisions(filePath, activeSessionId ?? undefined);
      setRevisions(data.revisions);
      setCurrentVersion(data.content_version);
      setLoadError(null);
    } catch (err) {
      setRevisions([]);
      setCurrentVersion(null);
      setLoadError(err instanceof Error ? err.message : "无法读取文件版本");
    } finally {
      setLoading(false);
    }
  }, [filePath, activeSessionId]);

  useEffect(() => {
    if (!active) return;
    load();
    const id = setInterval(load, 10000);
    return () => clearInterval(id);
  }, [active, load, workspaceFilesVersion, excelRefresh, wordRefresh]);

  const handleConfirm = async () => {
    if (!filePath || !target) return;
    setConfirmOpen(false);
    if (!currentVersion) {
      setLastResult({ ok: false, message: "无法读取当前版本，请刷新后重试" });
      setTarget(null);
      return;
    }
    setRestoring(true);
    setLastResult(null);
    try {
      const res = await restoreRevision({
        path: filePath,
        revisionId: target.revision_id,
        expectedVersion: currentVersion,
        sessionId: activeSessionId,
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
                                  <Button
                                    variant="outline"
                                    size="sm"
                                    className="h-7 text-[11px] px-2"
                                    disabled={restoring}
                                    onClick={() => askRestore(rec)}
                                  >
                                    <RotateCcw className="h-3 w-3 mr-1" />
                                    恢复
                                  </Button>
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
