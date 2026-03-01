"use client";

import { useState } from "react";
import {
  X,
  Loader2,
  AlertCircle,
  RotateCcw,
  ChevronDown,
} from "lucide-react";
import { useIsMobile } from "@/hooks/use-mobile";
import { isImageFile } from "./chat-input-constants";
import type { AttachedFile } from "@/lib/types";

interface FileAttachmentChipsProps {
  files: AttachedFile[];
  visionCapable: boolean;
  getPreviewUrl: (file: File) => string;
  retryUpload: (id: string, file: File) => void;
  removeFile: (id: string) => void;
}

export function FileAttachmentChips({
  files,
  visionCapable,
  getPreviewUrl,
  retryUpload,
  removeFile,
}: FileAttachmentChipsProps) {
  const isMobile = useIsMobile();
  const [expanded, setExpanded] = useState(false);

  if (files.length === 0) return null;

  const MOBILE_VISIBLE_COUNT = 2;
  const shouldCollapse = isMobile && files.length > MOBILE_VISIBLE_COUNT && !expanded;
  const visibleFiles = shouldCollapse ? files.slice(0, MOBILE_VISIBLE_COUNT) : files;
  const hiddenCount = files.length - visibleFiles.length;

  return (
    <div className="flex flex-col gap-1 px-3 sm:px-14 pt-1.5 pb-0">
      {/* 视觉能力不可用警告 */}
      {files.some((af) => isImageFile(af.file.name)) && !visionCapable && (
        <div className="flex items-center gap-1.5 text-[11px] text-amber-600 dark:text-amber-400 bg-amber-50 dark:bg-amber-950/30 rounded-md px-2 py-1">
          <AlertCircle className="h-3 w-3 flex-shrink-0" />
          <span>当前模型不支持图片识别，图片将无法被分析</span>
        </div>
      )}
      <div className="flex flex-wrap gap-1">
        {visibleFiles.map((af) =>
          isImageFile(af.file.name) ? (
            /* Image thumbnail chip */
            <span
              key={af.id}
              className={`relative inline-flex items-end rounded-lg overflow-hidden bg-muted/40 border ${
                af.status === "failed"
                  ? "border-2 border-destructive/60"
                  : "border-border/40"
              }`}
              style={{ maxWidth: "80px" }}
            >
              <img
                src={getPreviewUrl(af.file)}
                alt={af.file.name}
                className={`h-12 w-full object-cover ${af.status === "failed" ? "opacity-50" : ""}`}
              />
              {af.status === "uploading" && (
                <div className="absolute inset-0 flex items-center justify-center bg-black/30">
                  <Loader2 className="h-4 w-4 text-white animate-spin" />
                </div>
              )}
              {af.status === "failed" && (
                <div
                  className="absolute inset-0 flex flex-col items-center justify-center bg-black/40 cursor-pointer"
                  onClick={() => retryUpload(af.id, af.file)}
                >
                  <RotateCcw className="h-3.5 w-3.5 text-white" />
                  <span className="text-[8px] text-white mt-0.5">重试</span>
                </div>
              )}
              <button
                type="button"
                className="touch-compact absolute top-0.5 right-0.5 h-5 w-5 flex items-center justify-center rounded-full bg-black/60 text-white hover:bg-black/80 transition-colors shadow-sm"
                onClick={() => removeFile(af.id)}
              >
                <X className="h-2.5 w-2.5" />
              </button>
            </span>
          ) : (
            /* Document file chip */
            <span
              key={af.id}
              className={`inline-flex items-center gap-1 rounded-full text-xs font-medium pl-2.5 pr-1 py-0.5 max-w-[200px] ${
                af.status === "failed"
                  ? "bg-destructive/10 text-destructive"
                  : "bg-[var(--em-primary-alpha-10)] text-[var(--em-primary)]"
              }`}
              title={af.error}
            >
              {af.status === "uploading" && (
                <Loader2 className="h-3 w-3 animate-spin flex-shrink-0" />
              )}
              {af.status === "failed" && (
                <AlertCircle className="h-3 w-3 flex-shrink-0" />
              )}
              <span className="truncate">{af.file.name}</span>
              {af.status === "failed" && (
                <button
                  type="button"
                  className="rounded-full p-0.5 hover:bg-destructive/20 transition-colors flex-shrink-0"
                  onClick={() => retryUpload(af.id, af.file)}
                >
                  <RotateCcw className="h-3 w-3" />
                </button>
              )}
              <button
                type="button"
                className={`rounded-full p-0.5 transition-colors flex-shrink-0 ${
                  af.status === "failed"
                    ? "hover:bg-destructive/20"
                    : "hover:bg-[var(--em-primary-alpha-20)]"
                }`}
                onClick={() => removeFile(af.id)}
              >
                <X className="h-3 w-3" />
              </button>
            </span>
          )
        )}
        {/* 移动端折叠 badge */}
        {hiddenCount > 0 && (
          <button
            type="button"
            onClick={() => setExpanded(true)}
            className="inline-flex items-center gap-0.5 rounded-full text-[11px] font-medium px-2.5 py-1 bg-muted text-muted-foreground hover:bg-muted/80 transition-colors"
          >
            +{hiddenCount} 个文件
            <ChevronDown className="h-3 w-3" />
          </button>
        )}
      </div>
      {/* Inline error messages for failed uploads */}
      {files.some((af) => af.status === "failed") && (
        <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-[11px] text-destructive">
          {files
            .filter((af) => af.status === "failed")
            .map((af) => (
              <span key={af.id}>
                {af.file.name}: {af.error}
              </span>
            ))}
        </div>
      )}
    </div>
  );
}
