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
    <div className="border-t border-border bg-muted/30 px-3 py-2 flex flex-wrap items-center gap-2 text-[11px]">
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
  const closeCompare = useExcelStore((s) => s.closeCompare);
  const openCompare = useExcelStore((s) => s.openCompare);
  const setCompareRelationship = useExcelStore((s) => s.setCompareRelationship);
  const activeSessionId = useSessionStore((s) => s.activeSessionId);

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sheetsA, setSheetsA] = useState<string[]>([]);
  const [sheetsB, setSheetsB] = useState<string[]>([]);
  const [activeTabA, setActiveTabA] = useState<string | null>(compareSheetA);
  const [activeTabB, setActiveTabB] = useState<string | null>(compareSheetB);
  const [mobilePane, setMobilePane] = useState<"A" | "B">("A");

  const fileNameA = compareFileA?.split("/").pop() || "文件 A";
  const fileNameB = compareFileB?.split("/").pop() || "文件 B";

  const fileUrlA = useMemo(
    () => (compareFileA ? buildExcelFileUrl(compareFileA, activeSessionId ?? undefined) : ""),
    [compareFileA, activeSessionId],
  );
  const fileUrlB = useMemo(
    () => (compareFileB ? buildExcelFileUrl(compareFileB, activeSessionId ?? undefined) : ""),
    [compareFileB, activeSessionId],
  );

  const sharedColumns = compareRelationship?.sharedColumns ?? [];

  // 加载对比数据（sheet 列表 + 关系）
  const loadCompareData = useCallback(async () => {
    if (!compareFileA || !compareFileB) return;
    setLoading(true);
    setError(null);
    try {
      const data = await fetchExcelCompare(compareFileA, compareFileB, {
        sessionId: activeSessionId ?? undefined,
      });
      setSheetsA(data.file_a.sheets || []);
      setSheetsB(data.file_b.sheets || []);
      if (data.file_a.sheets?.length) setActiveTabA((prev) => prev ?? data.file_a.sheets[0]);
      if (data.file_b.sheets?.length) setActiveTabB((prev) => prev ?? data.file_b.sheets[0]);
      if (data.relationships?.shared_columns?.length) {
        setCompareRelationship({
          fileA: compareFileA,
          fileB: compareFileB,
          sharedColumns: data.relationships.shared_columns as SharedColumn[],
        });
      }
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, [compareFileA, compareFileB, activeSessionId, setCompareRelationship]);

  useEffect(() => {
    loadCompareData();
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
        <div className="flex items-center gap-2 px-3 py-2 border-b border-border bg-muted/20 flex-shrink-0">
          <Button variant="ghost" size="sm" onClick={handleClose} className="h-8 gap-1.5 text-xs">
            <ArrowLeft className="h-3.5 w-3.5" />
            返回
          </Button>
          <div className="h-4 w-px bg-border" />
          <ArrowLeftRight className="h-3.5 w-3.5 text-muted-foreground flex-shrink-0" />
          <span className="text-xs font-medium truncate">文件对比</span>
        </div>

        {/* 文件 Tab */}
        <div className="flex border-b border-border bg-muted/20">
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
            <UniverSheet fileUrl={fileUrlA} initialSheet={activeTabA ?? undefined} />
          ) : (
            <UniverSheet fileUrl={fileUrlB} initialSheet={activeTabB ?? undefined} />
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
      <div className="flex items-center gap-2 px-3 py-2 border-b border-border bg-muted/20 flex-shrink-0">
        <Button variant="ghost" size="sm" onClick={handleClose} className="h-7 gap-1.5 text-xs">
          <ArrowLeft className="h-3.5 w-3.5" />
          返回聊天
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
              <div className="flex items-center bg-muted/20 border-b border-border overflow-x-auto scrollbar-none flex-shrink-0">
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
            <div className="px-3 py-1 bg-blue-50/40 dark:bg-blue-950/20 border-b border-blue-100/50 dark:border-blue-900/30 text-[10px] font-medium text-blue-600 dark:text-blue-400 flex items-center gap-1.5 flex-shrink-0">
              <span className="uppercase tracking-wider">A</span>
              <span className="truncate font-normal text-foreground/70">{fileNameA}</span>
            </div>
            <div className="flex-1 min-h-0">
              <UniverSheet fileUrl={fileUrlA} initialSheet={activeTabA ?? undefined} />
            </div>
          </div>

          {/* 右侧 — 文件 B */}
          <div className="flex-1 min-w-0 flex flex-col">
            {sheetsB.length > 1 && (
              <div className="flex items-center bg-muted/20 border-b border-border overflow-x-auto scrollbar-none flex-shrink-0">
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
            <div className="px-3 py-1 bg-green-50/40 dark:bg-green-950/20 border-b border-green-100/50 dark:border-green-900/30 text-[10px] font-medium text-green-600 dark:text-green-400 flex items-center gap-1.5 flex-shrink-0">
              <span className="uppercase tracking-wider">B</span>
              <span className="truncate font-normal text-foreground/70">{fileNameB}</span>
            </div>
            <div className="flex-1 min-h-0">
              <UniverSheet fileUrl={fileUrlB} initialSheet={activeTabB ?? undefined} />
            </div>
          </div>
        </div>
      )}

      {/* 关联列摘要栏 */}
      <SharedColumnBar columns={sharedColumns} />
    </div>
  );
}
