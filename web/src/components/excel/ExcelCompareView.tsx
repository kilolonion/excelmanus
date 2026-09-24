"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import dynamic from "next/dynamic";
import { ArrowLeft, ArrowLeftRight, Link2, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useIsMobile } from "@/hooks/use-mobile";
import { useExcelStore, type SharedColumn } from "@/stores/excel-store";
import { useSessionStore } from "@/stores/session-store";
import {
  buildExcelFileUrl,
  fetchExcelCompare,
} from "@/lib/api";
import { activeFileRef } from "@/lib/workspace-file-ref";
import { displayFileName } from "@/lib/file-identity";

const UniverSheet = dynamic(
  () => import("./UniverSheet").then((m) => ({ default: m.UniverSheet })),
  {
    ssr: false,
    loading: () => (
      <div className="flex items-center justify-center h-full text-sm text-muted-foreground">
        加载 Excel 引擎...
      </div>
    ),
  },
);

function overlapColor(ratio: number): string {
  if (ratio >= 0.8) return "text-green-600 dark:text-green-400";
  if (ratio >= 0.5) return "text-amber-600 dark:text-amber-400";
  return "text-muted-foreground";
}

function matchBadge(type: string): string {
  switch (type) {
    case "exact":
      return "精确";
    case "normalized":
      return "归一化";
    case "value_overlap":
      return "值重叠";
    default:
      return type;
  }
}

function SharedColumnBar({ columns }: { columns: SharedColumn[] }) {
  if (columns.length === 0) return null;
  return (
    <div className="em-surface-note border-t border-border bg-muted/30 px-3 py-2 flex flex-wrap items-center gap-2 text-[11px]">
      <Link2 className="h-3.5 w-3.5 text-muted-foreground flex-shrink-0" />
      <span className="text-muted-foreground font-medium">关联列:</span>
      {columns.slice(0, 6).map((col, i) => (
        <span
          key={i}
          className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-background border border-border"
        >
          <span className="font-mono font-medium text-foreground/80">
            {col.col_a === col.col_b ? col.col_a : `${col.col_a} ↔ ${col.col_b}`}
          </span>
          <span className={`tabular-nums ${overlapColor(col.overlap_ratio)}`}>
            {(col.overlap_ratio * 100).toFixed(0)}%
          </span>
          <span className="text-muted-foreground/60 text-[9px]">
            {matchBadge(col.match_type)}
          </span>
        </span>
      ))}
      {columns.length > 6 && (
        <span className="text-muted-foreground/60">+{columns.length - 6} 更多</span>
      )}
    </div>
  );
}

export function ExcelCompareView() {
  const isMobile = useIsMobile();
  const compareFileA = useExcelStore((s) => s.compareFileA);
  const compareFileB = useExcelStore((s) => s.compareFileB);
  const compareSheetA = useExcelStore((s) => s.compareSheetA);
  const compareSheetB = useExcelStore((s) => s.compareSheetB);
  const compareRelationship = useExcelStore((s) => s.compareRelationship);
  const compareReturnPath = useExcelStore((s) => s.compareReturnPath);
  const closeCompare = useExcelStore((s) => s.closeCompare);
  const openCompare = useExcelStore((s) => s.openCompare);
  const setCompareRelationship = useExcelStore((s) => s.setCompareRelationship);
  const activeSessionId = useSessionStore((s) => s.activeSessionId);
  const activeWorkspaceId = useSessionStore(
    (s) => s.sessions.find((item) => item.id === s.activeSessionId)?.workspaceId ?? null,
  );

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sheetsA, setSheetsA] = useState<string[]>([]);
  const [sheetsB, setSheetsB] = useState<string[]>([]);
  const [activeTabA, setActiveTabA] = useState<string | null>(compareSheetA);
  const [activeTabB, setActiveTabB] = useState<string | null>(compareSheetB);
  const [mobilePane, setMobilePane] = useState<"A" | "B">("A");

  const fileNameA = displayFileName(compareFileA ?? "") || "文件 A";
  const fileNameB = displayFileName(compareFileB ?? "") || "文件 B";

  const fileUrlA = useMemo(
    () => (compareFileA ? buildExcelFileUrl(compareFileA, activeSessionId, activeWorkspaceId) : ""),
    [compareFileA, activeSessionId, activeWorkspaceId],
  );
  const fileUrlB = useMemo(
    () => (compareFileB ? buildExcelFileUrl(compareFileB, activeSessionId, activeWorkspaceId) : ""),
    [compareFileB, activeSessionId, activeWorkspaceId],
  );

  const sharedColumns = compareRelationship?.sharedColumns ?? [];

  // 加载对比数据（sheet 列表 + 关系）
  const loadCompareData = useCallback(() => {
    if (!compareFileA || !compareFileB) return undefined;
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    void (async () => {
      try {
        const data = await fetchExcelCompare(compareFileA, compareFileB, {
          sessionId: activeSessionId ?? undefined,
          workspaceId: activeWorkspaceId,
          signal: controller.signal,
        });
        if (controller.signal.aborted) return;
        setSheetsA(data.file_a.sheets.map((s) => s.name) || []);
        setSheetsB(data.file_b.sheets.map((s) => s.name) || []);
        if (data.file_a.sheets?.length) setActiveTabA((prev) => prev ?? data.file_a.sheets[0].name);
        if (data.file_b.sheets?.length) setActiveTabB((prev) => prev ?? data.file_b.sheets[0].name);
        if (data.relationships?.shared_columns?.length) {
          setCompareRelationship({
            fileA: compareFileA,
            fileB: compareFileB,
            sharedColumns: data.relationships.shared_columns as SharedColumn[],
          });
        }
      } catch (err: unknown) {
        if (!controller.signal.aborted) setError(err instanceof Error ? err.message : "加载失败");
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    })();
    return () => controller.abort();
  }, [compareFileA, compareFileB, activeSessionId, activeWorkspaceId, setCompareRelationship]);

  useEffect(() => {
    return loadCompareData();
  }, [loadCompareData]);

  const handleClose = useCallback(() => {
    closeCompare();
  }, [closeCompare]);

  const handleSwap = useCallback(() => {
    if (compareFileA && compareFileB) {
      openCompare(compareFileB, compareFileA, compareRelationship ?? undefined);
    }
  }, [compareFileA, compareFileB, compareRelationship, openCompare]);

  // Escape 键关闭对比视图
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") closeCompare();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [closeCompare]);

  if (!compareFileA || !compareFileB) return null;

  // ── 移动端：Tab 切换模式 ──
  if (isMobile) {
    return (
      <div className="flex flex-col h-full">
        {/* 顶栏 */}
        <div className="em-surface-header flex items-center gap-2 border-b border-border bg-muted/20 flex-shrink-0">
          <Button variant="ghost" size="sm" onClick={handleClose} className="h-8 gap-1.5 text-xs">
            <ArrowLeft className="h-3.5 w-3.5" />
            返回
          </Button>
          <div className="h-4 w-px bg-border" />
          <ArrowLeftRight className="h-3.5 w-3.5 text-muted-foreground flex-shrink-0" />
          <span className="text-xs font-medium truncate">文件对比</span>
        </div>

        {/* 文件 Tab */}
        <div className="em-surface-tabs flex border-b border-border bg-muted/20">
          <button
            onClick={() => setMobilePane("A")}
            className={`flex-1 px-3 py-2 text-xs font-medium border-b-2 transition-colors truncate ${
              mobilePane === "A"
                ? "border-[var(--em-primary)] text-foreground"
                : "border-transparent text-muted-foreground"
            }`}
          >
            {fileNameA}
          </button>
          <button
            onClick={() => setMobilePane("B")}
            className={`flex-1 px-3 py-2 text-xs font-medium border-b-2 transition-colors truncate ${
              mobilePane === "B"
                ? "border-[var(--em-primary)] text-foreground"
                : "border-transparent text-muted-foreground"
            }`}
          >
            {fileNameB}
          </button>
        </div>

        {/* 内容 */}
        <div className="flex-1 min-h-0">
          {loading ? (
            <div className="flex items-center justify-center h-full gap-2 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              加载中...
            </div>
          ) : error ? (
            <div className="flex items-center justify-center h-full text-sm text-destructive">{error}</div>
          ) : mobilePane === "A" ? (
            <UniverSheet fileUrl={fileUrlA} fileRef={compareFileA ? activeFileRef(compareFileA) : null} sessionId={activeSessionId} initialSheet={activeTabA ?? undefined} readOnly />
          ) : (
            <UniverSheet fileUrl={fileUrlB} fileRef={compareFileB ? activeFileRef(compareFileB) : null} sessionId={activeSessionId} initialSheet={activeTabB ?? undefined} readOnly />
          )}
        </div>

        <SharedColumnBar columns={sharedColumns} />
      </div>
    );
  }

  // ── 桌面端：左右分栏 ──
  return (
    <div className="flex flex-col h-full">
      {/* 顶栏 */}
      <div className="em-surface-header flex items-center gap-2 border-b border-border bg-muted/20 flex-shrink-0">
        <Button variant="ghost" size="sm" onClick={handleClose} className="h-7 gap-1.5 text-xs">
          <ArrowLeft className="h-3.5 w-3.5" />
          {compareReturnPath ? "返回表格" : "返回聊天"}
        </Button>
        <div className="h-4 w-px bg-border" />
        <ArrowLeftRight className="h-3.5 w-3.5 flex-shrink-0" style={{ color: "var(--em-primary)" }} />
        <span className="text-sm font-medium truncate">{fileNameA}</span>
        <button
          onClick={handleSwap}
          className="p-0.5 rounded hover:bg-muted transition-colors text-muted-foreground hover:text-foreground"
          title="交换左右文件"
        >
          <ArrowLeftRight className="h-3.5 w-3.5" />
        </button>
        <span className="text-sm font-medium truncate">{fileNameB}</span>
        <div className="flex-1" />
        {sharedColumns.length > 0 && (
          <span className="text-[10px] px-2 py-0.5 rounded-full font-medium"
            style={{ backgroundColor: "var(--em-primary-alpha-10)", color: "var(--em-primary)" }}>
            {sharedColumns.length} 个关联列
          </span>
        )}
      </div>

      {/* 分栏内容 */}
      {loading ? (
        <div className="flex-1 flex items-center justify-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
          正在加载对比数据...
        </div>
      ) : error ? (
        <div className="flex-1 flex items-center justify-center text-sm text-destructive">{error}</div>
      ) : (
        <div className="flex-1 min-h-0 flex">
          {/* 左侧 — 文件 A */}
          <div className="flex-1 min-w-0 flex flex-col border-r border-border">
            {/* Sheet tabs */}
            {sheetsA.length > 1 && (
              <div className="em-surface-tabs flex items-center border-b border-border overflow-x-auto scrollbar-none flex-shrink-0">
                {sheetsA.map((sn) => (
                  <button
                    key={sn}
                    onClick={() => setActiveTabA(sn)}
                    className={`px-3 py-1.5 text-[11px] font-medium border-b-2 transition-colors shrink-0 ${
                      activeTabA === sn
                        ? "border-[var(--em-primary)] text-foreground"
                        : "border-transparent text-muted-foreground hover:text-foreground"
                    }`}
                  >
                    {sn}
                  </button>
                ))}
              </div>
            )}
            {/* 文件标题 */}
            <div className="em-compare-pane-label px-3 py-1 border-b text-[10px] font-medium flex items-center gap-1.5 flex-shrink-0">
              <span className="uppercase tracking-wider">A</span>
              <span className="truncate font-normal text-foreground/70">{fileNameA}</span>
            </div>
            <div className="flex-1 min-h-0">
              <UniverSheet fileUrl={fileUrlA} fileRef={compareFileA ? activeFileRef(compareFileA) : null} sessionId={activeSessionId} initialSheet={activeTabA ?? undefined} readOnly />
            </div>
          </div>

          {/* 右侧 — 文件 B */}
          <div className="flex-1 min-w-0 flex flex-col">
            {sheetsB.length > 1 && (
              <div className="em-surface-tabs flex items-center border-b border-border overflow-x-auto scrollbar-none flex-shrink-0">
                {sheetsB.map((sn) => (
                  <button
                    key={sn}
                    onClick={() => setActiveTabB(sn)}
                    className={`px-3 py-1.5 text-[11px] font-medium border-b-2 transition-colors shrink-0 ${
                      activeTabB === sn
                        ? "border-[var(--em-primary)] text-foreground"
                        : "border-transparent text-muted-foreground hover:text-foreground"
                    }`}
                  >
                    {sn}
                  </button>
                ))}
              </div>
            )}
            <div className="em-compare-pane-label px-3 py-1 border-b text-[10px] font-medium flex items-center gap-1.5 flex-shrink-0">
              <span className="uppercase tracking-wider">B</span>
              <span className="truncate font-normal text-foreground/70">{fileNameB}</span>
            </div>
            <div className="flex-1 min-h-0">
              <UniverSheet fileUrl={fileUrlB} fileRef={compareFileB ? activeFileRef(compareFileB) : null} sessionId={activeSessionId} initialSheet={activeTabB ?? undefined} readOnly />
            </div>
          </div>
        </div>
      )}

      {/* 关联列摘要栏 */}
      <SharedColumnBar columns={sharedColumns} />
    </div>
  );
}
