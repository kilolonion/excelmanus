"use client";

import { useState, useEffect, useCallback } from "react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogFooter,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { CornerDownLeft, Check } from "lucide-react";

const STORAGE_KEY = "em_rollback_file_preference";

export type RollbackFilePreference = "always_rollback" | "never_rollback" | null;

function loadPreference(): RollbackFilePreference {
  if (typeof window === "undefined") return null;
  const val = localStorage.getItem(STORAGE_KEY);
  if (val === "always_rollback" || val === "never_rollback") return val;
  return null;
}

function savePreference(pref: RollbackFilePreference) {
  if (typeof window === "undefined") return;
  if (pref === null) {
    localStorage.removeItem(STORAGE_KEY);
  } else {
    localStorage.setItem(STORAGE_KEY, pref);
  }
}

export function getRollbackFilePreference(): RollbackFilePreference {
  return loadPreference();
}

interface RollbackConfirmDialogProps {
  open: boolean;
  onConfirm: (rollbackFiles: boolean) => void;
  onCancel: () => void;
}

export function RollbackConfirmDialog({
  open,
  onConfirm,
  onCancel,
}: RollbackConfirmDialogProps) {
  const [dontAskAgain, setDontAskAgain] = useState(false);

  useEffect(() => {
    if (open) {
      setDontAskAgain(false);
    }
  }, [open]);

  const handleConfirm = useCallback(
    (rollbackFiles: boolean) => {
      if (dontAskAgain) {
        savePreference(rollbackFiles ? "always_rollback" : "never_rollback");
      }
      onConfirm(rollbackFiles);
    },
    [dontAskAgain, onConfirm]
  );

  // 快捷键：Enter = 回滚，Shift+Enter = 不回滚，Esc = 取消
  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        e.stopPropagation();
        handleConfirm(true);
      } else if (e.key === "Enter" && e.shiftKey) {
        e.preventDefault();
        e.stopPropagation();
        handleConfirm(false);
      }
    };
    window.addEventListener("keydown", handler, true);
    return () => window.removeEventListener("keydown", handler, true);
  }, [open, handleConfirm]);

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onCancel()}>
      <DialogContent
        className="max-w-[480px] rounded-xl"
        showCloseButton={false}
        onOpenAutoFocus={(e) => e.preventDefault()}
      >
        <DialogHeader>
          <DialogTitle className="text-base font-semibold">
            从历史消息重新提交？
          </DialogTitle>
          <DialogDescription className="text-sm text-muted-foreground">
            重新提交将回退文件到该消息之前的状态，并清除之后的所有对话。
          </DialogDescription>
        </DialogHeader>

        <label
          className="flex items-center gap-1.5 text-xs text-muted-foreground cursor-pointer select-none"
          onClick={() => setDontAskAgain(!dontAskAgain)}
        >
          <span
            role="checkbox"
            aria-checked={dontAskAgain}
            className={[
              "inline-flex items-center justify-center shrink-0 rounded-[3px] border transition-colors",
              "h-[14px] w-[14px]",
              dontAskAgain
                ? "bg-primary border-primary text-primary-foreground"
                : "border-muted-foreground/40 bg-transparent",
            ].join(" ")}
          >
            {dontAskAgain && <Check className="h-[10px] w-[10px]" strokeWidth={2.5} />}
          </span>
          不再询问
        </label>

        <DialogFooter className="gap-2 sm:gap-2">
          <Button
            variant="ghost"
            size="sm"
            onClick={onCancel}
            className="text-muted-foreground"
          >
            取消
            <kbd className="ml-1.5 text-[10px] text-muted-foreground/60 font-normal hidden sm:inline">
              esc
            </kbd>
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => handleConfirm(false)}
          >
            不回退改动
            <span className="ml-1.5 items-center gap-0.5 text-[10px] text-muted-foreground/60 hidden sm:inline-flex">
              <span>⇧</span>
              <CornerDownLeft className="h-2.5 w-2.5" />
            </span>
          </Button>
          <Button size="sm" onClick={() => handleConfirm(true)}>
            回退并重发
            <CornerDownLeft className="ml-1.5 h-3 w-3 opacity-60 hidden sm:inline" />
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
