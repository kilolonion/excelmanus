"use client";

import { useState } from "react";
import { Terminal } from "lucide-react";
import {
  OverlayCard,
  OverlayCardBody,
  OverlayCardHeader,
} from "@/components/ui/overlay-card";
import { ScrollArea } from "@/components/ui/scroll-area";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { baseMarkdownComponents } from "@/components/chat/MarkdownComponents";

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
    <OverlayCard open={open} onOpenChange={(v) => !v && onClose()} size="md" tone="muted">
      <OverlayCardHeader
        icon={<Terminal className="h-5 w-5" />}
        title={<code className="font-mono text-xs bg-muted px-2 py-0.5 rounded">{command}</code>}
        description="命令执行结果"
        onClose={onClose}
      />
      <OverlayCardBody className="pb-5">
        <ScrollArea className="max-h-[min(50vh,420px)] pr-2">
          {format === "markdown" ? (
            <div className="prose prose-sm max-w-none text-foreground">
              <ReactMarkdown remarkPlugins={[remarkGfm]} components={baseMarkdownComponents}>{result}</ReactMarkdown>
            </div>
          ) : (
            <pre className="text-sm whitespace-pre-wrap text-foreground">{result}</pre>
          )}
        </ScrollArea>
      </OverlayCardBody>
    </OverlayCard>
  );
}

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
