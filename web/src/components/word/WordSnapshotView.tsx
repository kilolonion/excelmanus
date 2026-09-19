"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { fetchWordSnapshot } from "@/lib/api";
import {
  toWordSnapshotViewModel,
  type WordSnapshotParagraphView,
  type WordSnapshotRunView,
  type WordSnapshotTableView,
  type WordSnapshotViewModel,
} from "@/lib/word-snapshot";
import { useWordStore } from "@/stores/word-store";

interface WordSnapshotViewProps {
  fileUrl: string;
}

const HEADING_FONT_PX: Record<number, number> = {
  0: 28,
  1: 24,
  2: 20,
  3: 16,
  4: 14,
  5: 12,
  6: 12,
};

function parseWordFileUrl(url: string): { path: string; sessionId?: string } {
  try {
    const parsedUrl = new URL(url, "http://local.invalid");
    return {
      path: parsedUrl.searchParams.get("path") || "",
      sessionId: parsedUrl.searchParams.get("session_id") || undefined,
    };
  } catch {
    return { path: "" };
  }
}

function headingFontSize(level?: number): number | undefined {
  if (level === undefined) return undefined;
  return HEADING_FONT_PX[level] ?? HEADING_FONT_PX[6];
}

function RunSpan({ run }: { run: WordSnapshotRunView }) {
  const style: CSSProperties = {};
  if (run.bold) style.fontWeight = 700;
  if (run.italic) style.fontStyle = "italic";
  if (run.underline) style.textDecoration = "underline";
  if (run.color) style.color = run.color;
  if (run.sizePt != null) style.fontSize = `${run.sizePt}pt`;
  return <span style={style}>{run.text}</span>;
}

function SnapshotParagraph({ paragraph }: { paragraph: WordSnapshotParagraphView }) {
  const fontSize = headingFontSize(paragraph.headingLevel);
  const style: CSSProperties = {};
  if (fontSize != null) {
    style.fontSize = fontSize;
    style.fontWeight = 700;
  }

  return (
    <p className="min-h-[1em] whitespace-pre-wrap break-words text-sm leading-6" style={style}>
      {paragraph.runs.map((run, index) => (
        <RunSpan key={index} run={run} />
      ))}
    </p>
  );
}

function SnapshotTable({ table }: { table: WordSnapshotTableView }) {
  return (
    <figure className="my-3">
      <figcaption className="mb-1 text-[11px] text-muted-foreground">
        表格 {table.index + 1}
      </figcaption>
      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-xs">
          <tbody>
            {table.data.map((row, rowIndex) => (
              <tr key={rowIndex}>
                {row.map((cell, cellIndex) => (
                  <td key={cellIndex} className="border border-border px-2 py-1">
                    {cell}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </figure>
  );
}

function SnapshotBody({ model }: { model: WordSnapshotViewModel }) {
  const { stats } = model;

  return (
    <article className="space-y-3 px-4 py-3">
      <div className="space-y-1 text-[11px] leading-4 text-muted-foreground">
        <p>
          正文 {stats.totalParagraphs} 段 · 表格 {stats.totalTables} 个
        </p>
        {stats.truncated && <p>仅显示前 {stats.returnedParagraphs} 段</p>}
      </div>
      {model.paragraphs.map((paragraph, index) => (
        <SnapshotParagraph key={index} paragraph={paragraph} />
      ))}
      {model.tables.map((table) => (
        <SnapshotTable key={table.index} table={table} />
      ))}
    </article>
  );
}

export function WordSnapshotView({ fileUrl }: WordSnapshotViewProps) {
  const loadVersionRef = useRef(0);
  const refreshCounter = useWordStore((s) => s.refreshCounter);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [retryNonce, setRetryNonce] = useState(0);
  const [model, setModel] = useState<WordSnapshotViewModel | null>(null);

  const { path: filePath, sessionId } = useMemo(() => parseWordFileUrl(fileUrl), [fileUrl]);
  const parseError = filePath ? null : "无法解析文件路径";

  useEffect(() => {
    if (!filePath) return;

    const loadVersion = ++loadVersionRef.current;
    let cancelled = false;

    void (async () => {
      try {
        const snapshot = await fetchWordSnapshot(filePath, { sessionId });
        if (cancelled || loadVersion !== loadVersionRef.current) return;
        setModel(toWordSnapshotViewModel(snapshot));
        setError(null);
        setLoading(false);
      } catch (err: unknown) {
        if (cancelled || loadVersion !== loadVersionRef.current) return;
        console.error("Error loading Word snapshot:", err);
        setError(err instanceof Error ? err.message : "加载失败");
        setLoading(false);
      }
    })();

    return () => {
      cancelled = true;
      loadVersionRef.current += 1;
    };
  }, [filePath, refreshCounter, retryNonce, sessionId]);

  const handleRetry = useCallback(() => {
    setError(null);
    setLoading(true);
    setRetryNonce((value) => value + 1);
  }, []);

  const displayError = parseError ?? error;
  const showLoading = Boolean(filePath) && loading;

  return (
    <div className="relative h-full min-h-[400px] w-full overflow-y-auto bg-background">
      {model && <SnapshotBody model={model} />}
      {showLoading && (
        <div className="absolute inset-0 z-10 flex items-center justify-center bg-background/60">
          <span className="animate-pulse text-sm text-muted-foreground">
            加载文档数据...
          </span>
        </div>
      )}
      {displayError && (
        <div className="absolute inset-0 z-10 flex items-center justify-center bg-background/80">
          <div className="flex flex-col items-center gap-3 px-6 text-center">
            <span className="text-sm text-destructive">{displayError}</span>
            {!parseError && (
              <button
                type="button"
                onClick={handleRetry}
                className="rounded border border-border px-3 py-1.5 text-xs text-foreground transition-colors hover:bg-muted"
              >
                重试
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
