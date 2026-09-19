"use client";

import { useCallback, useRef, useState, type ReactNode } from "react";
import { Plus } from "lucide-react";
import { useDropzone } from "react-dropzone";
import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { useExcelStore } from "@/stores/excel-store";
import { ACCEPTED_EXTENSIONS } from "./chat-input-constants";
import {
  WORKSPACE_FILE_MIME,
  isWorkspaceFileDrag,
  parseWorkspaceDroppedFiles,
  shouldCancelComposerNativeDrop,
} from "./chat-drop";

interface ChatDropzoneProps {
  onNativeFiles: (files: File[]) => void;
  onExcelFiles: (files: { path: string; filename: string }[]) => void;
  children: ReactNode;
  highlighted?: boolean;
}

export function ChatDropzone({ onNativeFiles, onExcelFiles, children, highlighted }: ChatDropzoneProps) {
  const [excelDragOver, setExcelDragOver] = useState(false);
  const excelDragCounter = useRef(0);

  const onDrop = useCallback((accepted: File[]) => {
    onNativeFiles(accepted);
  }, [onNativeFiles]);

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: ACCEPTED_EXTENSIONS,
    noClick: true,
    noKeyboard: true,
  });

  const root = getRootProps();
  const draggingFileCount = () => useExcelStore.getState().draggingFileCount;

  const resetExcelDrag = () => {
    excelDragCounter.current = 0;
    setExcelDragOver(false);
  };

  const handleWorkspaceDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      e.stopPropagation();
      const files = parseWorkspaceDroppedFiles(e.dataTransfer.getData(WORKSPACE_FILE_MIME));
      if (files.length === 0) return;
      onExcelFiles(files);
    },
    [onExcelFiles],
  );

  return (
    <div
      {...root}
      data-coach-id="coach-chat-input"
      onDragEnter={(e) => {
        if (isWorkspaceFileDrag(e.dataTransfer.types, draggingFileCount())) {
          excelDragCounter.current += 1;
          setExcelDragOver(true);
          return;
        }
        root.onDragEnter?.(e);
      }}
      onDragOverCapture={(e) => {
        if (shouldCancelComposerNativeDrop(e.dataTransfer.types, draggingFileCount())) {
          e.preventDefault();
          if (isWorkspaceFileDrag(e.dataTransfer.types, draggingFileCount())) {
            e.dataTransfer.dropEffect = "copy";
          }
        }
      }}
      onDragOver={(e) => {
        if (isWorkspaceFileDrag(e.dataTransfer.types, draggingFileCount())) {
          e.preventDefault();
          e.dataTransfer.dropEffect = "copy";
          return;
        }
        root.onDragOver?.(e);
      }}
      onDragLeave={(e) => {
        if (isWorkspaceFileDrag(e.dataTransfer.types, draggingFileCount())) {
          excelDragCounter.current -= 1;
          if (excelDragCounter.current <= 0) {
            resetExcelDrag();
          }
          return;
        }
        root.onDragLeave?.(e);
      }}
      onDropCapture={(e) => {
        if (shouldCancelComposerNativeDrop(e.dataTransfer.types, draggingFileCount())) {
          e.preventDefault();
        }
      }}
      onDrop={(e) => {
        if (isWorkspaceFileDrag(e.dataTransfer.types, draggingFileCount()) || e.dataTransfer.getData(WORKSPACE_FILE_MIME)) {
          resetExcelDrag();
          handleWorkspaceDrop(e);
          return;
        }
        resetExcelDrag();
        root.onDrop?.(e);
      }}
      className={`em-chat-input-shell relative rounded-[20px] border bg-background transition-all duration-200 chat-input-ring ${
        isDragActive || excelDragOver
          ? "border-[var(--em-primary-light)] bg-[var(--em-primary)]/5 shadow-lg shadow-[var(--em-primary)]/10"
          : highlighted
            ? "border-[var(--em-primary-light)] shadow-[0_0_0_3px_var(--em-primary-alpha-15)]"
            : "border-border/60 shadow-[0_1px_8px_rgba(0,0,0,0.06)] dark:shadow-[0_1px_8px_rgba(0,0,0,0.25)] hover:shadow-[0_2px_12px_rgba(0,0,0,0.1)] dark:hover:shadow-[0_2px_12px_rgba(0,0,0,0.35)] focus-within:shadow-[0_2px_14px_rgba(0,0,0,0.12)] dark:focus-within:shadow-[0_2px_14px_rgba(0,0,0,0.45)] focus-within:border-border"
      }`}
    >
      <input {...getInputProps()} />
      {(isDragActive || excelDragOver) && (
        <div className="pointer-events-none absolute inset-0 z-40 flex items-center justify-center rounded-[20px] bg-[var(--em-primary-alpha-06)] border-2 border-dashed border-[var(--em-primary-light)] backdrop-blur-[2px]">
          <div className="flex flex-col items-center gap-1.5 text-[var(--em-primary)]">
            <Plus className="h-6 w-6" />
            <span className="text-sm font-medium">
              {excelDragOver && draggingFileCount() > 1
                ? `拖放 ${draggingFileCount()} 个文件到这里`
                : "拖放文件到这里"}
            </span>
            <span className="text-[10px] text-muted-foreground">支持工作区文件、本地上传与图片</span>
          </div>
        </div>
      )}
      {children}
    </div>
  );
}

interface ChatUploadButtonProps {
  onPick: (files: File[]) => void;
}

export function ChatUploadButton({ onPick }: ChatUploadButtonProps) {
  const fileInputRef = useRef<HTMLInputElement>(null);

  return (
    <>
      <TooltipProvider delayDuration={400}>
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="icon"
              className="touch-compact h-9 w-9 sm:h-8 sm:w-8 rounded-full flex-shrink-0 text-muted-foreground hover:text-foreground"
              aria-label="添加文件"
              onClick={() => fileInputRef.current?.click()}
            >
              <Plus className="h-4 w-4" />
            </Button>
          </TooltipTrigger>
          <TooltipContent side="top" className="text-xs">
            添加文件
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>
      <input
        ref={fileInputRef}
        type="file"
        className="hidden"
        multiple
        onChange={(e) => {
          if (e.target.files) {
            onPick(Array.from(e.target.files));
          }
          e.target.value = "";
        }}
      />
    </>
  );
}
