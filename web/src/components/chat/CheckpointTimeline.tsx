"use client";

import { useEffect, useState, useCallback } from "react";
import { History, Loader2, RotateCcw, AlertTriangle, CheckCircle2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  OverlayCard,
  OverlayCardAction,
  OverlayCardFooter,
  OverlayCardHeader,
} from "@/components/ui/overlay-card";
import { SlidePanel } from "@/components/ui/slide-panel";
import { useSessionStore } from "@/stores/session-store";
import { useExcelStore } from "@/stores/excel-store";
import { isWordDocumentPath, useWordStore } from "@/stores/word-store";
import { useIsMobile } from "@/hooks/use-mobile";
import {
  fetchRevisions,
  invalidateSnapshotCache,
  restoreRevision,
  type WorkbookRevisionItem,
} from "@/lib/api";

export function revisionReasonLabel(reason: string, label: string): string {
  if (label) return label;
  if (reason === "beforeEdit") return "编辑前";
  if (reason === "afterEdit") return "编辑后";
  if (reason === "beforeRestore") return "恢复前";
  if (reason === "checkpoint") return "检查点";
  return reason;
}

function formatWhen(iso: string | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString();
}

function refreshOpenDocument(path: string, contentVersion: string | null) {
  invalidateSnapshotCache(path);
  const excel = useExcelStore.getState();
  excel.setContentVersion(path, contentVersion);
  excel.bumpWorkspaceFilesVersion();
  useExcelStore.setState((s) => ({ refreshCounter: s.refreshCounter + 1 }));
  if (isWordDocumentPath(path)) {
    useWordStore.getState().triggerRefresh();
  }
}

export function RevisionTimeline() {
  const activeSessionId = useSessionStore((s) => s.activeSessionId);
  const excelPath = useExcelStore((s) => s.activeFilePath);
  const excelOpen = useExcelStore((s) => s.panelOpen);
  const workspaceFilesVersion = useExcelStore((s) => s.workspaceFilesVersion);
  const excelRefresh = useExcelStore((s) => s.refreshCounter);
  const wordPath = useWordStore((s) => s.activeDocPath);
  const wordOpen = useWordStore((s) => s.panelOpen);
  const wordRefresh = useWordStore((s) => s.refreshCounter);
  const activeFilePath =
    (excelOpen && excelPath) || (wordOpen && wordPath) || excelPath || wordPath;
  const isMobile = useIsMobile();
  const [panelOpen, setPanelOpen] = useState(false);
  const [revisions, setRevisions] = useState<WorkbookRevisionItem[]>([]);
  const [currentVersion, setCurrentVersion] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [restoring, setRestoring] = useState(false);
  const [target, setTarget] = useState<WorkbookRevisionItem | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [lastResult, setLastResult] = useState<{ ok: boolean; message: string } | null>(null);

  const load = useCallback(async () => {
    if (!activeFilePath) {
      setRevisions([]);
      setCurrentVersion(null);
      setLoadError(null);
      return;
    }
    setLoading(true);
    try {
      const data = await fetchRevisions(activeFilePath, activeSessionId ?? undefined);
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
  }, [activeFilePath, activeSessionId]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    if (!panelOpen) return;
    load();
    const id = setInterval(load, 10000);
    return () => clearInterval(id);
  }, [panelOpen, load, workspaceFilesVersion, excelRefresh, wordRefresh]);

  const handleConfirm = async () => {
    if (!activeFilePath || !target) return;
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
        path: activeFilePath,
        revisionId: target.revision_id,
        expectedVersion: currentVersion,
        sessionId: activeSessionId,
      });
      setLastResult({
        ok: true,
        message: `已恢复到 ${target.revision_id.slice(0, 8)}，当前 ${res.content_version.slice(0, 19)}`,
      });
      refreshOpenDocument(activeFilePath, res.content_version);
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

  const sorted = [...revisions].sort((a, b) => b.sequence - a.sequence);

  return (
    <>
      <Button
        variant="ghost"
        size="icon"
        className="h-7 w-7 p-0"
        title="文件版本"
        aria-label="文件版本"
        onClick={() => setPanelOpen(true)}
      >
        <History className="h-3.5 w-3.5" />
      </Button>
      <SlidePanel
        open={panelOpen}
        onClose={() => setPanelOpen(false)}
        title="文件版本"
        icon={<History className="h-4 w-4" style={{ color: "var(--em-primary)" }} />}
        width={400}
      >
        <div className="p-3 space-y-3">
          {!activeFilePath && (
            <p className="text-xs text-muted-foreground">打开一个工作簿后可查看版本时间线。</p>
          )}
          {activeFilePath && (
            <p className="text-[11px] text-muted-foreground break-all">
              {activeFilePath}
              {currentVersion ? ` · ${currentVersion.slice(0, 19)}` : ""}
            </p>
          )}
          {loading && <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />}
          {loadError && (
            <p className="text-xs text-destructive flex items-center gap-1">
              <AlertTriangle className="h-3.5 w-3.5" />
              {loadError}
            </p>
          )}
          {sorted.length === 0 && activeFilePath && !loading && !loadError && (
            <p className="text-xs text-muted-foreground">
              还没有写入记录。用对话、表格编辑或恢复成功改过这个文件后，这里会留下「编辑前 / 编辑后」版本。
            </p>
          )}
          {sorted.map((rec) => (
            <div key={rec.revision_id} className="rounded-lg border border-border/60 p-3 space-y-1">
              <div className="flex items-center justify-between gap-2">
                <span className="text-xs font-mono">{rec.revision_id}</span>
                <Badge variant="outline" className="h-4 px-1 text-[9px]">
                  {revisionReasonLabel(rec.reason, rec.label)}
                </Badge>
              </div>
              <div className="text-[10px] text-muted-foreground">
                {rec.created_at ? formatWhen(rec.created_at) : `seq ${rec.sequence}`}
                {rec.content_version ? ` · ${rec.content_version.slice(7, 19) || rec.content_version.slice(0, 19)}` : ""}
              </div>
              <Button
                variant="outline"
                size="sm"
                className="h-7 text-xs"
                disabled={restoring}
                onClick={() => {
                  setTarget(rec);
                  setConfirmOpen(true);
                }}
              >
                <RotateCcw className="h-3 w-3 mr-1" />
                恢复到此版本
              </Button>
            </div>
          ))}
          {lastResult && (
            <div className={`text-xs flex items-center gap-1 ${lastResult.ok ? "text-emerald-600" : "text-destructive"}`}>
              {lastResult.ok ? <CheckCircle2 className="h-3.5 w-3.5" /> : <AlertTriangle className="h-3.5 w-3.5" />}
              {lastResult.message}
            </div>
          )}
        </div>
      </SlidePanel>
      <OverlayCard open={confirmOpen} onOpenChange={setConfirmOpen} size="sm" tone="warning">
        <OverlayCardHeader
          title="恢复文件版本"
          description={
            target
              ? `将用「${revisionReasonLabel(target.reason, target.label)}」覆盖当前文件。若文件刚被改过，恢复会被拒绝，请刷新后再试。`
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

/** @deprecated overlay turn checkpoints are gone; alias for layout imports */
export const CheckpointTimeline = RevisionTimeline;
