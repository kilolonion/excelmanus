"use client";

import { useState } from "react";
import { Terminal, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ScrollArea } from "@/components/ui/scroll-area";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

interface CommandResultDialogProps {
  open: boolean;
  onClose: () => void;
  command: string;
  result: string;
  format: "markdown" | "text";
}

export function CommandResultDialog({
  open,
  onClose,
  command,
  result,
  format,
}: CommandResultDialogProps) {
  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="max-w-lg max-h-[70vh] p-0">
        <DialogHeader className="px-5 pt-5 pb-2">
          <DialogTitle className="flex items-center gap-2 text-sm">
            <Terminal className="h-4 w-4" style={{ color: "var(--em-primary)" }} />
            <code className="font-mono text-xs bg-muted px-2 py-0.5 rounded">{command}</code>
          </DialogTitle>
        </DialogHeader>
        <ScrollArea className="px-5 pb-5" style={{ maxHeight: "calc(70vh - 80px)" }}>
          {format === "markdown" ? (
            <div className="prose prose-sm max-w-none text-foreground">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{result}</ReactMarkdown>
            </div>
          ) : (
            <pre className="text-sm whitespace-pre-wrap text-foreground">{result}</pre>
          )}
        </ScrollArea>
      </DialogContent>
    </Dialog>
  );
}

// 用于管理命令结果弹窗状态的 Hook
export function useCommandResult() {
  const [state, setState] = useState<{
    open: boolean;
    command: string;
    result: string;
    format: "markdown" | "text";
  }>({ open: false, command: "", result: "", format: "text" });

  const show = (command: string, result: string, format: "markdown" | "text" = "text") => {
    setState({ open: true, command, result, format });
  };

  const close = () => {
    setState((s) => ({ ...s, open: false }));
  };

  return { state, show, close };
}
