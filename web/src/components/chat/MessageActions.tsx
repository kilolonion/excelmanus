"use client";

import React, { useCallback, useState } from "react";
import { Copy, Check, ThumbsUp, ThumbsDown, RotateCcw, ArrowRightLeft } from "lucide-react";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
  DropdownMenuSeparator,
  DropdownMenuLabel,
} from "@/components/ui/dropdown-menu";
import { apiGet } from "@/lib/api";
import { useUIStore } from "@/stores/ui-store";
import type { AssistantBlock, ModelInfo } from "@/lib/types";

interface MessageActionsProps {
  blocks: AssistantBlock[];
  onRetry?: () => void;
  onRetryWithModel?: (modelName: string) => void;
  isStreaming?: boolean;
}

/**
 * Extract plain text content from assistant message blocks.
 * Excludes tool calls, thinking, token stats, etc.
 */
function extractPlainText(blocks: AssistantBlock[]): string {
  return blocks
    .filter((b) => b.type === "text")
    .map((b) => (b as Extract<AssistantBlock, { type: "text" }>).content)
    .join("\n\n")
    .trim();
}

function extractProvider(baseUrl: string | undefined): string {
  if (!baseUrl) return "unknown";
  try {
    const hostname = new URL(baseUrl).hostname;
    const parts = hostname.split(".");
    if (parts.length >= 2) return parts[parts.length - 2];
    return parts[0] || "unknown";
  } catch {
    return "unknown";
  }
}

interface ProviderGroup {
  provider: string;
  models: ModelInfo[];
}

function groupByProvider(models: ModelInfo[]): ProviderGroup[] {
  const map = new Map<string, ModelInfo[]>();
  for (const m of models) {
    const provider = extractProvider(m.base_url);
    if (!map.has(provider)) map.set(provider, []);
    map.get(provider)!.push(m);
  }
  return Array.from(map.entries()).map(([provider, models]) => ({
    provider,
    models,
  }));
}

export const MessageActions = React.memo(function MessageActions({
  blocks,
  onRetry,
  onRetryWithModel,
  isStreaming,
}: MessageActionsProps) {
  const [copied, setCopied] = useState(false);
  const [feedback, setFeedback] = useState<"up" | "down" | null>(null);
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [modelsLoaded, setModelsLoaded] = useState(false);
  const currentModel = useUIStore((s) => s.currentModel);

  const handleCopy = useCallback(() => {
    const text = extractPlainText(blocks);
    if (!text) return;
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    }).catch(() => {});
  }, [blocks]);

  const handleFeedback = useCallback((type: "up" | "down") => {
    setFeedback((prev) => (prev === type ? null : type));
    // TODO：后端 API 就绪后发送反馈
  }, []);

  const fetchModelsOnce = useCallback(() => {
    if (modelsLoaded) return;
    setModelsLoaded(true);
    apiGet<{ models: ModelInfo[] }>("/models")
      .then((data) => setModels(data.models))
      .catch(() => {});
  }, [modelsLoaded]);

  const hasText = blocks.some((b) => b.type === "text");
  if (!hasText || isStreaming) return null;

  const groups = groupByProvider(models);
  const canRetry = !!onRetry && !isStreaming;

  return (
    <div
      className="flex items-center gap-1 mt-1.5 opacity-0 group-hover/msg:opacity-100 transition-opacity duration-200 touch-show"
      role="toolbar"
      aria-label="消息操作"
    >
      <ActionButton
        onClick={handleCopy}
        active={copied}
        activeColor="text-emerald-500"
        label={copied ? "已复制" : "复制"}
      >
        {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
      </ActionButton>

      {canRetry && (
        <ActionButton
          onClick={onRetry}
          active={false}
          activeColor=""
          label="重试"
        >
          <RotateCcw className="h-3.5 w-3.5" />
        </ActionButton>
      )}

      {canRetry && onRetryWithModel && (
        <DropdownMenu onOpenChange={(open) => { if (open) fetchModelsOnce(); }}>
          <DropdownMenuTrigger asChild>
            <button
              type="button"
              className="h-8 w-8 inline-flex items-center justify-center rounded-md transition-colors text-muted-foreground hover:text-foreground hover:bg-muted/60"
              aria-label="切换模型重试"
              title="切换模型重试"
            >
              <ArrowRightLeft className="h-3.5 w-3.5" />
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start" className="w-64 max-h-[40vh] overflow-y-auto">
            <DropdownMenuLabel className="text-xs text-muted-foreground font-normal">
              选择模型重试
            </DropdownMenuLabel>
            <DropdownMenuSeparator />
            {groups.map((group, gi) => (
              <div key={group.provider}>
                {gi > 0 && <DropdownMenuSeparator />}
                <DropdownMenuLabel className="text-[10px] uppercase tracking-wider text-muted-foreground font-normal">
                  {group.provider}
                </DropdownMenuLabel>
                {group.models.map((m) => (
                  <DropdownMenuItem
                    key={m.name}
                    onClick={() => onRetryWithModel(m.name)}
                    className="flex items-center gap-2 py-1.5 cursor-pointer"
                  >
                    <span className={`text-sm flex-1 truncate ${
                      m.name === currentModel ? "font-semibold" : ""
                    }`}>
                      {m.name}
                    </span>
                    {m.name === currentModel && (
                      <span className="text-[10px] text-muted-foreground">当前</span>
                    )}
                  </DropdownMenuItem>
                ))}
              </div>
            ))}
            {models.length === 0 && (
              <DropdownMenuItem disabled className="text-xs">
                加载中...
              </DropdownMenuItem>
            )}
          </DropdownMenuContent>
        </DropdownMenu>
      )}

      <ActionButton
        onClick={() => handleFeedback("up")}
        active={feedback === "up"}
        activeColor="text-[var(--em-primary)]"
        label="有用"
      >
        <ThumbsUp className="h-3.5 w-3.5" />
      </ActionButton>

      <ActionButton
        onClick={() => handleFeedback("down")}
        active={feedback === "down"}
        activeColor="text-[var(--em-error)]"
        label="无用"
      >
        <ThumbsDown className="h-3.5 w-3.5" />
      </ActionButton>
    </div>
  );
});

function ActionButton({
  onClick,
  active,
  activeColor,
  label,
  children,
}: {
  onClick: () => void;
  active: boolean;
  activeColor: string;
  label: string;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`h-8 w-8 inline-flex items-center justify-center rounded-md transition-colors ${
        active
          ? activeColor
          : "text-muted-foreground hover:text-foreground hover:bg-muted/60"
      }`}
      aria-label={label}
      title={label}
    >
      {children}
    </button>
  );
}
