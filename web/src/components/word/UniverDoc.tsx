"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { fetchWordSnapshot } from "@/lib/api";
import { useWordStore, type WordSnapshot } from "@/stores/word-store";

let _univerDocModuleCache: Promise<{
  createUniver: any;
  LocaleType: any;
  UniverDocsCorePreset: any;
  docsCoreZhCN: any;
}> | null = null;

function getUniverDocModules() {
  if (!_univerDocModuleCache) {
    _univerDocModuleCache = Promise.all([
      import("@univerjs/presets"),
      import("@univerjs/preset-docs-core"),
      import("@univerjs/preset-docs-core/locales/zh-CN"),
      import("@univerjs/preset-docs-core/lib/index.css"),
    ]).then(([presetsMod, docsCoreMod, zhCNMod]) => ({
      createUniver: presetsMod.createUniver,
      LocaleType: presetsMod.LocaleType,
      UniverDocsCorePreset: docsCoreMod.UniverDocsCorePreset,
      docsCoreZhCN: zhCNMod.default,
    }));
  }

  return _univerDocModuleCache;
}

export function prefetchUniverDocModules() {
  if (typeof window === "undefined") return;

  const windowWithIdleCallback = window as Window & {
    requestIdleCallback?: (callback: () => void) => number;
  };
  const schedule =
    windowWithIdleCallback.requestIdleCallback ??
    ((callback: () => void) => window.setTimeout(callback, 2000));

  schedule(() => {
    void getUniverDocModules();
  });
}

interface UniverDocProps {
  fileUrl: string;
}

function parseWordFileUrl(url: string): { path: string; sessionId?: string } {
  try {
    const parsedUrl = new URL(url, window.location.origin);
    return {
      path: parsedUrl.searchParams.get("path") || "",
      sessionId: parsedUrl.searchParams.get("session_id") || undefined,
    };
  } catch {
    return { path: "" };
  }
}

function createDocId(): string {
  return `doc-preview-${Math.random().toString(36).slice(2, 10)}`;
}

/**
 * Convert a WordSnapshot to Univer Doc IDocumentData format.
 *
 * Univer Doc uses a flat "body.dataStream" string as document content,
 * with "body.textRuns" for inline formatting and "body.paragraphs"
 * for paragraph-level properties.
 */
function snapshotToDocData(
  snapshot: WordSnapshot,
  docId: string
): Record<string, any> {
  let dataStream = "";
  const textRuns: any[] = [];
  const paragraphs: any[] = [];
  let offset = 0;

  const HEADING_FONT_SIZES: Record<number, number> = {
    0: 28,
    1: 24,
    2: 20,
    3: 16,
    4: 14,
    5: 12,
  };

  for (const para of snapshot.paragraphs) {
    const paragraphStart = offset;

    if (para.runs && para.runs.length > 0) {
      for (const run of para.runs) {
        const text = run.text;
        if (!text) continue;

        const ts: any = {};
        if (run.bold) ts.bl = 1;
        if (run.italic) ts.it = 1;
        if (run.underline) ts.ul = { s: 1 };
        if (run.size_pt) ts.fs = run.size_pt;
        if (run.font_name) ts.ff = run.font_name;
        if (run.color) ts.cl = { rgb: `#${run.color}` };

        if (Object.keys(ts).length > 0) {
          textRuns.push({ st: offset, ed: offset + text.length, ts });
        }

        dataStream += text;
        offset += text.length;
      }
    } else {
      dataStream += para.text;
      offset += para.text.length;
    }

    dataStream += "\n";
    offset += 1;

    const paragraphProps: any = { startIndex: paragraphStart };

    if (para.heading_level !== undefined) {
      const fontSize = HEADING_FONT_SIZES[para.heading_level] || 12;
      textRuns.push({
        st: paragraphStart,
        ed: offset - 1,
        ts: { fs: fontSize, bl: 1 },
      });
    }

    if (para.alignment) {
      const alignMap: Record<string, number> = {
        left: 0,
        center: 1,
        right: 2,
        justify: 3,
      };
      paragraphProps.paragraphStyle = {
        horizontalAlign: alignMap[para.alignment] ?? 0,
      };
    }

    paragraphs.push(paragraphProps);
  }

  dataStream += "\r\n";

  return {
    id: docId,
    body: {
      dataStream,
      textRuns,
      paragraphs,
    },
    documentStyle: {
      pageSize: { width: 595, height: 842 },
      marginTop: 72,
      marginBottom: 72,
      marginLeft: 90,
      marginRight: 90,
    },
  };
}

export function UniverDoc({ fileUrl }: UniverDocProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const univerRef = useRef<any>(null);
  const docIdRef = useRef<string>(createDocId());
  const loadVersionRef = useRef(0);
  const refreshCounter = useWordStore((s) => s.refreshCounter);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [retryNonce, setRetryNonce] = useState(0);

  const { path: filePath, sessionId } = useMemo(() => parseWordFileUrl(fileUrl), [fileUrl]);

  const renderSnapshot = useCallback((api: any, snapshot: WordSnapshot) => {
    const previousDocId = docIdRef.current;

    try {
      if (api.getDocument?.(previousDocId)) {
        api.disposeUnit?.(previousDocId);
      }
    } catch {
      // ignore stale doc cleanup errors
    }

    const docId = createDocId();
    docIdRef.current = docId;
    api.createUniverDoc(snapshotToDocData(snapshot, docId));
  }, []);

  const loadData = useCallback(
    async (api: any) => {
      if (!filePath) {
        setError("无法解析文件路径");
        setLoading(false);
        return;
      }

      const loadVersion = ++loadVersionRef.current;

      try {
        setLoading(true);
        setError(null);

        const snapshot = await fetchWordSnapshot(filePath, { sessionId });
        if (loadVersion !== loadVersionRef.current) return;

        renderSnapshot(api, snapshot);
        if (loadVersion !== loadVersionRef.current) return;

        setLoading(false);
      } catch (err: unknown) {
        if (loadVersion !== loadVersionRef.current) return;
        console.error("Error loading Word data:", err);
        setError(err instanceof Error ? err.message : "加载失败");
        setLoading(false);
      }
    },
    [filePath, renderSnapshot, sessionId]
  );

  useEffect(() => {
    if (!containerRef.current) return;

    let disposed = false;
    let api: any = null;

    const init = async () => {
      try {
        setLoading(true);
        setError(null);

        const dataPromise = filePath
          ? fetchWordSnapshot(filePath, { sessionId }).catch(() => null)
          : Promise.resolve<WordSnapshot | null>(null);

        const { createUniver, LocaleType, UniverDocsCorePreset, docsCoreZhCN } =
          await getUniverDocModules();

        if (disposed) return;

        const { univerAPI } = createUniver({
          locale: LocaleType.ZH_CN,
          locales: {
            [LocaleType.ZH_CN]: docsCoreZhCN,
          },
          presets: [
            UniverDocsCorePreset({
              container: containerRef.current!,
            }),
          ],
        });

        if (disposed) {
          univerAPI.dispose();
          return;
        }

        api = univerAPI;
        univerRef.current = univerAPI;

        const prefetchedData = await dataPromise;
        if (disposed) return;

        if (prefetchedData) {
          const loadVersion = ++loadVersionRef.current;
          try {
            renderSnapshot(univerAPI, prefetchedData);
            if (loadVersion === loadVersionRef.current) {
              setLoading(false);
            }
          } catch {
            await loadData(univerAPI);
          }
        } else {
          await loadData(univerAPI);
        }
      } catch (err) {
        console.error("Univer Doc initialization error:", err);
        setError("Univer Doc 引擎初始化失败");
        setLoading(false);
      }
    };

    void init();

    return () => {
      disposed = true;
      loadVersionRef.current += 1;
      if (api) {
        try {
          api.dispose();
        } catch {
          // ignore dispose errors
        }
      }
      univerRef.current = null;
    };
  }, [filePath, loadData, renderSnapshot, retryNonce, sessionId]);

  useEffect(() => {
    if (refreshCounter > 0 && univerRef.current) {
      void loadData(univerRef.current);
    }
  }, [refreshCounter, loadData]);

  const handleRetry = useCallback(() => {
    setError(null);
    setLoading(true);

    if (univerRef.current) {
      void loadData(univerRef.current);
      return;
    }

    setRetryNonce((value) => value + 1);
  }, [loadData]);

  return (
    <div className="relative h-full min-h-[400px] w-full bg-white dark:bg-gray-800">
      <div
        ref={containerRef}
        className="h-full w-full bg-white dark:bg-gray-800"
        data-univer-doc-container
        style={{ position: "relative" }}
      />
      {loading && (
        <div className="absolute inset-0 z-10 flex items-center justify-center bg-background/60">
          <span className="animate-pulse text-sm text-muted-foreground">
            加载文档数据...
          </span>
        </div>
      )}
      {error && (
        <div className="absolute inset-0 z-10 flex items-center justify-center bg-background/80">
          <div className="flex flex-col items-center gap-3 px-6 text-center">
            <span className="text-sm text-destructive">{error}</span>
            <button
              type="button"
              onClick={handleRetry}
              className="rounded border border-border px-3 py-1.5 text-xs text-foreground transition-colors hover:bg-muted"
            >
              重试
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
