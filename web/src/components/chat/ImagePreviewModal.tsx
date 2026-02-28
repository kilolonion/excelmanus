"use client";

import React, { useEffect, useState } from "react";
import { X, Download, ZoomIn, ZoomOut, RotateCw, Loader2, ImageIcon, ExternalLink } from "lucide-react";
import { useSessionStore } from "@/stores/session-store";

interface ImagePreviewModalProps {
  imagePath: string;
  filename: string;
  trigger?: React.ReactNode;
}

export function ImagePreviewModal({
  imagePath,
  filename,
  trigger,
}: ImagePreviewModalProps) {
  const [open, setOpen] = useState(false);
  const [zoom, setZoom] = useState(1);
  const [rotation, setRotation] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const activeSessionId = useSessionStore((s) => s.activeSessionId);

  // Reset state when modal opens/closes
  useEffect(() => {
    if (!open) {
      setZoom(1);
      setRotation(0);
      setLoading(true);
      setError(false);
    }
  }, [open]);

  const handleZoomIn = () => {
    setZoom((prev) => Math.min(prev + 0.25, 3));
  };

  const handleZoomOut = () => {
    setZoom((prev) => Math.max(prev - 0.25, 0.25));
  };

  const handleRotate = () => {
    setRotation((prev) => (prev + 90) % 360);
  };

  const handleDoubleClick = () => {
    setZoom(1);
    setRotation(0);
  };

  const handleDownload = async () => {
    try {
      const { downloadFile } = await import("@/lib/api");
      downloadFile(imagePath, filename, activeSessionId ?? undefined).catch(() => {});
    } catch {
      // Download failed
    }
  };

  // 点击 trigger 打开对话框
  const handleTriggerClick = (e: React.MouseEvent | React.TouchEvent) => {
    e.stopPropagation();
    setOpen(true);
  };

  const imageUrl = `/api/v1/files/image?path=${encodeURIComponent(imagePath)}&session_id=${activeSessionId || ""}`;

  if (!open) {
    return (
      <div onClick={handleTriggerClick} onTouchEnd={handleTriggerClick}>
        {trigger}
      </div>
    );
  }

  return (
    <div 
      className="fixed inset-0 z-50 flex items-center justify-center"
      onClick={() => setOpen(false)}
    >
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/90" />
      
      {/* Close button */}
      <button
        onClick={() => setOpen(false)}
        className="absolute top-4 right-4 z-10 p-2 rounded-full bg-white/10 hover:bg-white/20 text-white transition-colors"
      >
        <X className="w-5 h-5" />
      </button>

      {/* Toolbar */}
      <div className="absolute top-4 left-1/2 -translate-x-1/2 z-10 flex items-center gap-2 px-3 py-2 rounded-full bg-black/60 backdrop-blur-sm">
        <button
          onClick={(e) => { e.stopPropagation(); handleZoomOut(); }}
          disabled={zoom <= 0.25}
          className="p-1.5 rounded-full hover:bg-white/20 text-white disabled:opacity-40 transition-colors"
        >
          <ZoomOut className="w-4 h-4" />
        </button>
        <span className="text-xs text-white/80 w-12 text-center tabular-nums min-w-[3rem]">
          {Math.round(zoom * 100)}%
        </span>
        <button
          onClick={(e) => { e.stopPropagation(); handleZoomIn(); }}
          disabled={zoom >= 3}
          className="p-1.5 rounded-full hover:bg-white/20 text-white disabled:opacity-40 transition-colors"
        >
          <ZoomIn className="w-4 h-4" />
        </button>
        <div className="w-px h-4 bg-white/20 mx-1" />
        <button
          onClick={(e) => { e.stopPropagation(); handleRotate(); }}
          className="p-1.5 rounded-full hover:bg-white/20 text-white transition-colors"
        >
          <RotateCw className="w-4 h-4" />
        </button>
        <div className="w-px h-4 bg-white/20 mx-1" />
        <button
          onClick={(e) => { e.stopPropagation(); handleDownload(); }}
          className="p-1.5 rounded-full hover:bg-white/20 text-white transition-colors"
        >
          <Download className="w-4 h-4" />
        </button>
        <a
          href={imageUrl}
          target="_blank"
          rel="noopener noreferrer"
          onClick={(e) => e.stopPropagation()}
          className="p-1.5 rounded-full hover:bg-white/20 text-white transition-colors"
        >
          <ExternalLink className="w-4 h-4" />
        </a>
      </div>

      {/* Image container */}
      <div 
        className="relative max-w-[95vw] max-h-[90vh] overflow-hidden"
        onClick={(e) => e.stopPropagation()}
        onDoubleClick={handleDoubleClick}
      >
        {loading && (
          <div className="absolute inset-0 flex items-center justify-center">
            <Loader2 className="w-8 h-8 animate-spin text-white/60" />
          </div>
        )}
        {error ? (
          <div className="flex flex-col items-center gap-3 text-white/60 p-8">
            <ImageIcon className="w-16 h-16" />
            <span className="text-sm">图片加载失败</span>
          </div>
        ) : (
          <img
            src={imageUrl}
            alt={filename}
            className="max-w-full max-h-[90vh] object-contain transition-transform duration-200"
            style={{
              transform: `scale(${zoom}) rotate(${rotation}deg)`,
              display: loading ? "none" : "block",
            }}
            onLoad={() => {
              setLoading(false);
              setError(false);
            }}
            onError={() => {
              setLoading(false);
              setError(true);
            }}
          />
        )}
      </div>

      {/* Footer info */}
      <div className="absolute bottom-4 left-1/2 -translate-x-1/2 flex items-center gap-4 text-xs text-white/50">
        <span>{filename}</span>
        {zoom !== 1 && <span>缩放 {Math.round(zoom * 100)}%</span>}
        {rotation !== 0 && <span>旋转 {rotation}°</span>}
      </div>
    </div>
  );
}
