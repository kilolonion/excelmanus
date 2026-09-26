"use client";

import { useEffect, useMemo, useState, type CSSProperties } from "react";
import type { FUniver } from "@univerjs/core/facade";
import { apiFetch, buildWorkbookObjectImageUrl, getAuthHeaders } from "@/lib/api";
import type { WorkbookDrawingObject, WorkbookObservation } from "@/lib/workbook-observation";
import type { WorkspaceFileRef } from "@/lib/workspace-file-ref";

interface Props {
  api: FUniver | null;
  view: WorkbookObservation | null;
  container: HTMLElement | null;
  fileRef?: WorkspaceFileRef | null;
  sessionId?: string | null;
}

function objectKey(item: WorkbookDrawingObject): string {
  return `${item.kind}:${item.index ?? item.id ?? item.target_cell ?? "0"}`;
}

function useImageSource(url: string | null) {
  const [source, setSource] = useState<string | null>(null);
  useEffect(() => {
    if (!url) { setSource(null); return; }
    let disposed = false;
    let local: string | null = null;
    void apiFetch(url, { headers: getAuthHeaders(), credentials: "include" })
      .then((response) => { if (!response.ok) throw new Error("image request failed"); return response.blob(); })
      .then((blob) => { if (!disposed) { local = URL.createObjectURL(blob); setSource(local); } })
      .catch(() => { if (!disposed) setSource(null); });
    return () => { disposed = true; if (local) URL.revokeObjectURL(local); };
  }, [url]);
  return source;
}

function numericValues(item: WorkbookDrawingObject): number[] {
  return (item.chart_data?.series?.[0]?.values || []).map((value) => Number(value)).filter(Number.isFinite);
}

function ChartSvg({ item }: { item: WorkbookDrawingObject }) {
  const series = item.chart_data?.series || [];
  const values = series.flatMap((entry) => (entry.values || []).map(Number).filter(Number.isFinite));
  const max = Math.max(...values, 1);
  const min = Math.min(...values, 0);
  const type = String(item.chart_type || "").toLowerCase();
  const title = item.chart_data?.title || "";
  const first = series[0];
  if (!values.length) return <div className="h-full w-full rounded bg-white/90 p-2 text-[10px] text-muted-foreground">图表数据不可用</div>;
  const points = numericValues(item);
  const line = points.map((value, index) => `${8 + (index * 84) / Math.max(points.length - 1, 1)},${52 - ((value - min) / Math.max(max - min, 1)) * 42}`).join(" ");
  const bars = points.map((value, index) => {
    const x = 8 + (index * 84) / Math.max(points.length, 1);
    const height = ((value - min) / Math.max(max - min, 1)) * 42;
    return <rect key={index} x={x} y={52 - height} width={Math.max(2, 72 / Math.max(points.length, 1))} height={height} rx="0.7" fill="var(--em-primary)" opacity=".75" />;
  });
  return (
    <svg viewBox="0 0 100 60" preserveAspectRatio="none" className="h-full w-full rounded bg-background/95 shadow-sm">
      <rect x="0" y="0" width="100" height="60" fill="white" opacity=".94" />
      {title && <text x="50" y="7" textAnchor="middle" fontSize="4" fill="#334155">{title}</text>}
      <line x1="8" y1="52" x2="94" y2="52" stroke="#94a3b8" strokeWidth=".4" />
      <line x1="8" y1="10" x2="8" y2="52" stroke="#94a3b8" strokeWidth=".4" />
      {type.includes("pie") ? (
        <g transform="translate(50 32)">{points.slice(0, 12).map((value, index) => {
          const start = points.slice(0, index).reduce((sum, v) => sum + Math.max(v, 0), 0) / Math.max(points.reduce((sum, v) => sum + Math.max(v, 0), 0), 1) * Math.PI * 2 - Math.PI / 2;
          const end = start + Math.max(value, 0) / Math.max(points.reduce((sum, v) => sum + Math.max(v, 0), 0), 1) * Math.PI * 2;
          const large = end - start > Math.PI ? 1 : 0;
          const path = `M 0 0 L ${28 * Math.cos(start)} ${28 * Math.sin(start)} A 28 28 0 ${large} 1 ${28 * Math.cos(end)} ${28 * Math.sin(end)} Z`;
          return <path key={index} d={path} fill={`hsl(${index * 47 % 360} 70% 55%)`} stroke="white" strokeWidth=".4" />;
        })}</g>
      ) : type.includes("line") || type.includes("area") || type.includes("scatter") ? (
        <><polyline points={line} fill={type.includes("area") ? "var(--em-primary)" : "none"} fillOpacity=".12" stroke="var(--em-primary)" strokeWidth="1.2" />{points.map((value, index) => <circle key={index} cx={8 + (index * 84) / Math.max(points.length - 1, 1)} cy={52 - ((value - min) / Math.max(max - min, 1)) * 42} r="1.3" fill="var(--em-primary)" />)}</>
      ) : bars}
      {first?.categories?.length ? <text x="94" y="58" textAnchor="end" fontSize="3" fill="#64748b">{String(first.categories.at(-1) ?? "")}</text> : null}
    </svg>
  );
}

function DrawingItem({ item, sheet, api, root, fileRef, sessionId, version }: { item: WorkbookDrawingObject; sheet: string; api: FUniver; root: HTMLElement; fileRef?: WorkspaceFileRef | null; sessionId?: string | null; version: string }) {
  const anchor = item.target_cell;
  const [rect, setRect] = useState<{ left: number; top: number; width: number; height: number } | null>(null);
  const workbook = api.getActiveWorkbook?.();
  const worksheet = workbook?.getSheetByName?.(sheet);
  useEffect(() => {
    if (!worksheet || !anchor || !root) return;
    const update = () => {
      try {
        const cell = worksheet.getRange(anchor);
        const cellRect = cell.getCellRect?.();
        const rootRect = root.getBoundingClientRect();
        if (cellRect && Number.isFinite(cellRect.left)) setRect({ left: cellRect.left - rootRect.left, top: cellRect.top - rootRect.top, width: cellRect.width, height: cellRect.height });
      } catch { /* grid may be between workbook swaps */ }
    };
    update();
    const resize = new ResizeObserver(update);
    resize.observe(root);
    const sub = api.addEvent?.(api.Event.Scroll, update);
    const sheetSub = api.addEvent?.(api.Event.ActiveSheetChanged, update);
    return () => { resize.disconnect(); sub?.dispose?.(); sheetSub?.dispose?.(); };
  }, [api, worksheet, anchor, root]);
  const bounds = item.bounds || {};
  const zoom = (() => { try { return Number(worksheet?.getZoom?.() || 1); } catch { return 1; } })();
  const style: CSSProperties = {
    position: "absolute", left: rect?.left ?? -10000, top: rect?.top ?? -10000,
    width: Math.max(24, (Number(bounds.width) || Number(item.source_size_px?.width) || rect?.width || 120) * zoom),
    height: Math.max(24, (Number(bounds.height) || Number(item.source_size_px?.height) || rect?.height || 80) * zoom),
    zIndex: 12, pointerEvents: "none", overflow: "hidden", borderRadius: 2,
  };
  const externalSource = typeof item.asset?.url === "string" ? item.asset.url : null;
  const downloadedSource = useImageSource(item.kind === "image" && !externalSource && fileRef && item.index != null ? buildWorkbookObjectImageUrl({ path: fileRef.relative, workspaceKey: fileRef.workspaceKey, workspaceId: fileRef.workspaceId, sessionId, sheet, index: item.index, expectedVersion: version }) : null);
  const source = externalSource || downloadedSource;
  return <div aria-label={item.kind === "image" ? "表内图片" : "表内图表"} style={style} data-workbook-object={item.kind}>
    {item.kind === "image" ? (source ? <img src={source} alt="表内图片" className="h-full w-full object-contain" /> : <div className="h-full w-full bg-muted/30" />) : <ChartSvg item={item} />}
  </div>;
}

export function WorkbookDrawingOverlay({ api, view, container, fileRef, sessionId }: Props) {
  const sheet = api?.getActiveWorkbook?.()?.getActiveSheet?.()?.getSheetName?.() || view?.active_sheet;
  const objects = useMemo(() => {
    if (!view || !sheet) return [];
    const seen = new Set<string>();
    return view.regions.filter((region) => region.sheet === sheet).flatMap((region) => [...(region.objects || []), ...(region.cell_images || [])]).filter((item) => {
      const key = objectKey(item); if (seen.has(key)) return false; seen.add(key); return item.kind === "image" || item.kind === "chart";
    });
  }, [view, sheet]);
  if (!api || !container || !view || !sheet) return null;
  return <>{objects.map((item) => <DrawingItem key={objectKey(item)} item={item} sheet={sheet} api={api} root={container} fileRef={fileRef} sessionId={sessionId} version={view.content_version} />)}</>;
}
