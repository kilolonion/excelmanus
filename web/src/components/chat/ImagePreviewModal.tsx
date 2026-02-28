"use client";

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  X, Download, ZoomIn, ZoomOut, RotateCw, Maximize2,
  Loader2, ImageIcon, ExternalLink, ChevronRight,
} from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
import { useSessionStore } from "@/stores/session-store";
import { buildApiUrl } from "@/lib/api";
import { useAuthImage } from "@/hooks/use-auth-image";

/* ─── types ─── */
interface ImagePreviewModalProps {
  imagePath: string;
  filename: string;
  trigger?: React.ReactNode;
}

/* ─── constants ─── */
const MIN_ZOOM = 0.1;
const MAX_ZOOM = 8;
const ZOOM_STEP = 0.25;
const WHEEL_ZOOM_FACTOR = 0.001;

/* ─── helpers ─── */
function pathToBreadcrumb(filePath: string): string[] {
  const parts = filePath.replace(/^\/+/, "").split("/");
  return parts.length > 4 ? ["...", ...parts.slice(-3)] : parts;
}

/* ─── framer-motion variants ─── */
const backdropVariants = {
  hidden: { opacity: 0 },
  visible: { opacity: 1, transition: { duration: 0.18, ease: "easeOut" as const } },
  exit: { opacity: 0, transition: { duration: 0.12, ease: "easeIn" as const } },
};
const panelVariants = {
  hidden: { opacity: 0, scale: 0.97, y: 8 },
  visible: { opacity: 1, scale: 1, y: 0, transition: { duration: 0.22, ease: [0.16, 1, 0.3, 1] as const } },
  exit: { opacity: 0, scale: 0.98, y: 4, transition: { duration: 0.12, ease: "easeIn" as const } },
};

/* ─── component ─── */
export function ImagePreviewModal({
  imagePath,
  filename,
  trigger,
}: ImagePreviewModalProps) {
  const [open, setOpen] = useState(false);
  const [zoom, setZoom] = useState(1);
  const [rotation, setRotation] = useState(0);
  const [imgLoaded, setImgLoaded] = useState(false);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [dragging, setDragging] = useState(false);
  const dragStart = useRef({ x: 0, y: 0, panX: 0, panY: 0 });
  const activeSessionId = useSessionStore((s) => s.activeSessionId);

  // ── authenticated image loading ──
  const imageApiPath = `/files/image?path=${encodeURIComponent(imagePath)}&session_id=${activeSessionId || ""}`;
  const { blobUrl, loading: fetchLoading, error: fetchError } = useAuthImage(imageApiPath, open);
  const loading = fetchLoading || (!imgLoaded && !fetchError && open);
  const error = fetchError;

  // ── reset on close ──
  useEffect(() => {
    if (!open) {
      setZoom(1);
      setRotation(0);
      setImgLoaded(false);
      setPan({ x: 0, y: 0 });
      setDragging(false);
    }
  }, [open]);

  // ── body scroll lock ──
  useEffect(() => {
    if (!open) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { document.body.style.overflow = prev; };
  }, [open]);

  // ── ESC to close ──
  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [open]);

  // ── zoom helpers ──
  const clampZoom = useCallback((z: number) => Math.min(Math.max(z, MIN_ZOOM), MAX_ZOOM), []);

  const handleZoomIn = useCallback(() => {
    setZoom((prev) => clampZoom(prev + ZOOM_STEP));
  }, [clampZoom]);

  const handleZoomOut = useCallback(() => {
    setZoom((prev) => clampZoom(prev - ZOOM_STEP));
  }, [clampZoom]);

  const handleRotate = useCallback(() => {
    setRotation((prev) => (prev + 90) % 360);
  }, []);

  const handleReset = useCallback(() => {
    setZoom(1);
    setRotation(0);
    setPan({ x: 0, y: 0 });
  }, []);

  const handleDownload = useCallback(async () => {
    try {
      const { downloadFile } = await import("@/lib/api");
      downloadFile(imagePath, filename, activeSessionId ?? undefined).catch(() => {});
    } catch {
      // silent
    }
  }, [imagePath, filename, activeSessionId]);

  // ── scroll wheel zoom ──
  const handleWheel = useCallback((e: React.WheelEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setZoom((prev) => clampZoom(prev * (1 - e.deltaY * WHEEL_ZOOM_FACTOR)));
  }, [clampZoom]);

  // ── drag to pan (when zoomed) ──
  const handlePointerDown = useCallback((e: React.PointerEvent) => {
    if (zoom <= 1) return;
    e.preventDefault();
    setDragging(true);
    dragStart.current = { x: e.clientX, y: e.clientY, panX: pan.x, panY: pan.y };
    (e.target as HTMLElement).setPointerCapture(e.pointerId);
  }, [zoom, pan]);

  const handlePointerMove = useCallback((e: React.PointerEvent) => {
    if (!dragging) return;
    const dx = e.clientX - dragStart.current.x;
    const dy = e.clientY - dragStart.current.y;
    setPan({ x: dragStart.current.panX + dx, y: dragStart.current.panY + dy });
  }, [dragging]);

  const handlePointerUp = useCallback(() => {
    setDragging(false);
  }, []);

  const rawImageUrl = buildApiUrl(imageApiPath);
  const isPanned = zoom > 1;
  const zoomPercent = Math.round(zoom * 100);
  const breadcrumb = useMemo(() => pathToBreadcrumb(imagePath), [imagePath]);
  const isZoomed = zoom !== 1;
  const isRotated = rotation !== 0;
  const isTransformed = isZoomed || isRotated;

  /* ─── Toolbar button helper ─── */
  const ToolBtn = useCallback(({ onClick, active, disabled, title, children }: {
    onClick: (e: React.MouseEvent) => void;
    active?: boolean;
    disabled?: boolean;
    title: string;
    children: React.ReactNode;
  }) => (
    <button
      onClick={(e) => { e.stopPropagation(); onClick(e); }}
      disabled={disabled}
      className={`flex items-center gap-1.5 px-2.5 py-1 rounded-md text-[11px] font-medium transition-all disabled:opacity-30 disabled:cursor-not-allowed ${
        active
          ? "bg-[var(--em-primary-alpha-10)] text-[var(--em-primary)] border border-[var(--em-primary-alpha-20)]"
          : "bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-300 border border-gray-200 dark:border-gray-700 hover:bg-gray-200/80 dark:hover:bg-gray-700"
      }`}
      title={title}
    >
      {children}
    </button>
  ), []);

  /* ─── portal overlay ─── */
  const overlay = (
    <AnimatePresence>
      {open && (
        <motion.div
          key="image-preview-overlay"
          className="fixed inset-0 z-[9999] flex items-center justify-center"
          onClick={() => setOpen(false)}
          initial="hidden"
          animate="visible"
          exit="exit"
        >
          {/* Backdrop */}
          <motion.div
            className="absolute inset-0 bg-black/50 dark:bg-black/70 backdrop-blur-sm"
            variants={backdropVariants}
          />

          {/* Panel */}
          <motion.div
            className="relative flex flex-col w-[95vw] h-[85vh] max-w-[1200px] overflow-hidden rounded-xl bg-white dark:bg-[#1e1e1e] border border-gray-200 dark:border-gray-700 shadow-[0_20px_60px_-12px_rgba(0,0,0,0.25)]"
            variants={panelVariants}
            onClick={(e) => e.stopPropagation()}
          >
            {/* ── Tab bar ── */}
            <div className="flex items-center bg-[#f3f3f3] dark:bg-[#252526] border-b border-gray-200 dark:border-gray-700 min-h-[36px] select-none">
              <div className="flex items-center flex-1 min-w-0">
                <div className="group relative flex items-center gap-1.5 px-3 h-[36px] text-[12px] cursor-default shrink-0 border-r border-gray-200/60 dark:border-gray-700/60 bg-white dark:bg-[#1e1e1e] text-gray-800 dark:text-gray-200">
                  <div className="absolute top-0 left-0 right-0 h-[2px] bg-[var(--em-primary)]" />
                  {/* Image type color dot */}
                  <span className="w-2.5 h-2.5 rounded-full shrink-0 ring-1 ring-black/5 dark:ring-white/10 bg-[#e44d26]" />
                  <span className="truncate max-w-[200px]">{filename}</span>
                </div>
              </div>
              {/* Close button */}
              <button
                onClick={(e) => { e.stopPropagation(); setOpen(false); }}
                className="shrink-0 p-2 mx-1 rounded text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 hover:bg-gray-200/60 dark:hover:bg-gray-600/40 transition-all"
                title="关闭 (Esc)"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            {/* ── Breadcrumb + Toolbar bar ── */}
            <div className="flex items-center gap-1.5 px-3 h-[32px] bg-[#fafafa] dark:bg-[#1e1e1e] border-b border-gray-100 dark:border-gray-800 text-[11px] text-gray-400 dark:text-gray-500 select-none overflow-hidden">
              {breadcrumb.map((seg, i) => (
                <React.Fragment key={i}>
                  {i > 0 && <ChevronRight className="w-3 h-3 shrink-0 opacity-50" />}
                  <span className={i === breadcrumb.length - 1 ? "text-gray-600 dark:text-gray-300 font-medium truncate" : "truncate"}>
                    {seg}
                  </span>
                </React.Fragment>
              ))}
              <div className="flex-1" />
              {/* Action buttons — prominent */}
              <div className="flex items-center gap-1">
                <ToolBtn onClick={handleZoomOut} disabled={zoom <= MIN_ZOOM} title="缩小">
                  <ZoomOut className="w-3.5 h-3.5" />
                </ToolBtn>
                <span className="text-[10px] text-gray-500 dark:text-gray-400 tabular-nums w-8 text-center font-medium">{zoomPercent}%</span>
                <ToolBtn onClick={handleZoomIn} disabled={zoom >= MAX_ZOOM} title="放大">
                  <ZoomIn className="w-3.5 h-3.5" />
                </ToolBtn>
                <ToolBtn onClick={handleRotate} active={isRotated} title="旋转 90°">
                  <RotateCw className="w-3.5 h-3.5" />
                </ToolBtn>
                {isTransformed && (
                  <ToolBtn onClick={handleReset} title="重置视图">
                    <Maximize2 className="w-3.5 h-3.5" />
                    <span>重置</span>
                  </ToolBtn>
                )}
                <ToolBtn onClick={handleDownload} title="下载">
                  <Download className="w-3.5 h-3.5" />
                  <span>下载</span>
                </ToolBtn>
                <a
                  href={rawImageUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  onClick={(e) => e.stopPropagation()}
                  className="flex items-center gap-1.5 px-2.5 py-1 rounded-md text-[11px] font-medium transition-all bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-300 border border-gray-200 dark:border-gray-700 hover:bg-gray-200/80 dark:hover:bg-gray-700"
                  title="新窗口打开"
                >
                  <ExternalLink className="w-3.5 h-3.5" />
                </a>
              </div>
            </div>

            {/* ── Image area ── */}
            <div
              className="relative flex-1 overflow-hidden bg-gray-50 dark:bg-[#1e1e1e] flex items-center justify-center"
              onDoubleClick={handleReset}
              onWheel={handleWheel}
              onPointerDown={handlePointerDown}
              onPointerMove={handlePointerMove}
              onPointerUp={handlePointerUp}
              style={{ cursor: isPanned ? (dragging ? "grabbing" : "grab") : "default" }}
            >
              {/* checkerboard pattern for transparent images */}
              <div
                className="absolute inset-0 opacity-[0.03] dark:opacity-[0.06]"
                style={{
                  backgroundImage: "linear-gradient(45deg, #808080 25%, transparent 25%), linear-gradient(-45deg, #808080 25%, transparent 25%), linear-gradient(45deg, transparent 75%, #808080 75%), linear-gradient(-45deg, transparent 75%, #808080 75%)",
                  backgroundSize: "20px 20px",
                  backgroundPosition: "0 0, 0 10px, 10px -10px, -10px 0px",
                }}
              />
              {loading && (
                <div className="flex flex-col items-center justify-center gap-3 z-10">
                  <Loader2 className="w-7 h-7 animate-spin text-[var(--em-primary)] opacity-40" />
                  <span className="text-xs text-gray-400">加载中…</span>
                </div>
              )}
              {error ? (
                <div className="flex flex-col items-center justify-center gap-3 text-gray-400 dark:text-gray-500 z-10">
                  <div className="w-16 h-16 rounded-xl bg-gray-100 dark:bg-gray-800 flex items-center justify-center">
                    <ImageIcon className="w-8 h-8" />
                  </div>
                  <span className="text-sm font-medium">无法加载图片</span>
                  <span className="text-xs text-gray-300 dark:text-gray-600">按 Esc 关闭</span>
                </div>
              ) : (
                blobUrl && (
                  <img
                    src={blobUrl}
                    alt={filename}
                    className="max-w-full max-h-full object-contain select-none z-10"
                    draggable={false}
                    style={{
                      transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom}) rotate(${rotation}deg)`,
                      transition: dragging ? "none" : "transform 0.2s cubic-bezier(0.16, 1, 0.3, 1)",
                      display: imgLoaded ? "block" : "none",
                    }}
                    onLoad={() => setImgLoaded(true)}
                    onError={() => setImgLoaded(false)}
                  />
                )
              )}
            </div>

            {/* ── Status bar (VS Code–style) ── */}
            <div className="flex items-center justify-between px-3 h-[24px] bg-[var(--em-primary)] text-white text-[11px] select-none shrink-0">
              <div className="flex items-center gap-3">
                <span className="w-2 h-2 rounded-full ring-1 ring-white/20 bg-[#e44d26]" />
                <span className="opacity-90 truncate max-w-[200px]" title={filename}>
                  {filename}
                </span>
                <span className="opacity-60 uppercase text-[10px]">image</span>
                <span className="opacity-60 tabular-nums">{zoomPercent}%</span>
                {rotation !== 0 && (
                  <span className="opacity-60 tabular-nums">{rotation}°</span>
                )}
              </div>
              <div className="flex items-center gap-3 opacity-60 text-[10px]">
                <span>滚轮缩放</span>
                <span>双击重置</span>
                <span>Esc 关闭</span>
              </div>
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );

  return (
    <>
      <div onClick={(e) => { e.stopPropagation(); setOpen(true); }}>
        {trigger}
      </div>
      {typeof window !== "undefined" && createPortal(overlay, document.body)}
    </>
  );
}
