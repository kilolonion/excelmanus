"use client";

import { useEffect, useRef, useCallback, useState } from "react";
import { useWordStore, type WordSnapshot } from "@/stores/word-store";
import { fetchWordSnapshot } from "@/lib/api";

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
  if (typeof window !== "undefined") {
    const schedule =
      (window as any).requestIdleCallback ??
      ((cb: () => void) => setTimeout(cb, 2000));
    schedule(() => {
      getUniverDocModules();
    });
  }
}

interface UniverDocProps {
  fileUrl: string;
  onContentEdit?: (paragraphIndex: number, newText: string) => void;
}

function extractPathFromUrl(url: string): string {
  try {
    const u = new URL(url, window.location.origin);
    return u.searchParams.get("path") || "";
  } catch {
    return "";
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
    0: 28, // Title
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

    // Paragraph separator (\n in Univer Doc dataStream)
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

  // Univer Doc dataStream must end with \r\n
  dataStream += "\r\n";

  return {
    id: docId,
    body: {
      dataStream,
      textRuns,
      paragraphs,
    },
    documentStyle: {
      pageSize: { width: 595, height: 842 }, // A4
      marginTop: 72,
      marginBottom: 72,
      marginLeft: 90,
      marginRight: 90,
    },
  };
}

export function UniverDoc({ fileUrl, onContentEdit }: UniverDocProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const univerRef = useRef<any>(null);
  const docIdRef = useRef<string>(createDocId());
  const loadVersionRef = useRef(0);
  const refreshCounter = useWordStore((s) => s.refreshCounter);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const filePath = extractPathFromUrl(fileUrl);

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

        const snapshot = await fetchWordSnapshot(filePath);
        if (loadVersion !== loadVersionRef.current) return;

        if (!snapshot.paragraphs?.length) {
          setError("文档为空");
          setLoading(false);
          return;
        }

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
        const docData = snapshotToDocData(snapshot, docId);

        api.createUniverDoc(docData);
        if (loadVersion !== loadVersionRef.current) return;

        setLoading(false);
      } catch (err: any) {
        if (loadVersion !== loadVersionRef.current) return;
        console.error("Error loading Word data:", err);
        setError(err.message || "加载失败");
        setLoading(false);
      }
    },
    [filePath]
  );

  useEffect(() => {
    if (!containerRef.current) return;

    let disposed = false;
    let api: any = null;

    const init = async () => {
      try {
        const dataPromise = filePath
          ? fetchWordSnapshot(filePath).catch(() => null)
          : Promise.resolve(null);

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
        if (prefetchedData && prefetchedData.paragraphs?.length) {
          const loadVersion = ++loadVersionRef.current;
          try {
            const docId = createDocId();
            docIdRef.current = docId;
            const docData = snapshotToDocData(prefetchedData, docId);
            univerAPI.createUniverDoc(docData);
            if (loadVersion === loadVersionRef.current) setLoading(false);
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

    init();

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
  }, [loadData]);

  useEffect(() => {
    if (refreshCounter > 0 && univerRef.current) {
      loadData(univerRef.current);
    }
  }, [refreshCounter, loadData]);

  return (
    <div className="relative w-full h-full min-h-[400px] bg-white dark:bg-gray-800">
      <div
        ref={containerRef}
        className="w-full h-full bg-white dark:bg-gray-800"
        data-univer-doc-container
        style={{ position: "relative" }}
      />
      {loading && (
        <div className="absolute inset-0 flex items-center justify-center bg-background/60 z-10">
          <span className="text-sm text-muted-foreground animate-pulse">
            加载文档数据...
          </span>
        </div>
      )}
      {error && (
        <div className="absolute inset-0 flex items-center justify-center bg-background/80 z-10">
          <span className="text-sm text-destructive">{error}</span>
        </div>
      )}
    </div>
  );
}
