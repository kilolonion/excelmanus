"use client";

import React, { useCallback, useState } from "react";
import { Copy, Check, ThumbsUp, ThumbsDown, RotateCcw, ArrowRightLeft, Loader2, RefreshCw } from "lucide-react";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
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

/** Provider brand colors — keep in sync with TopModelSelector */
const PROVIDER_COLORS: Record<string, string> = {
  openai: "#10a37f",
  deepseek: "#4d6bfe",
  aliyuncs: "#ff6a00",
  dashscope: "#ff6a00",
  anthropic: "#d4a574",
  google: "#4285f4",
  moonshot: "#7c3aed",
  zhipu: "#2563eb",
  baidu: "#2932e1",
  groq: "#f55036",
  mistral: "#ff7000",
  together: "#6366f1",
  cohere: "#39594d",
  siliconflow: "#06b6d4",
};

const PROVIDER_DISPLAY: Record<string, string> = {
  openai: "OpenAI",
  deepseek: "DeepSeek",
  aliyuncs: "阿里云",
  dashscope: "DashScope",
  anthropic: "Anthropic",
  google: "Google",
  moonshot: "Moonshot",
  zhipu: "智谱",
  baidu: "百度",
  groq: "Groq",
  mistral: "Mistral",
  together: "Together",
  cohere: "Cohere",
  siliconflow: "SiliconFlow",
};

function getProviderColor(provider: string): string {
  return PROVIDER_COLORS[provider] || "#888";
}

function getProviderDisplayName(provider: string): string {
  return PROVIDER_DISPLAY[provider] || provider.charAt(0).toUpperCase() + provider.slice(1);
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

function displayLabel(m: ModelInfo): string {
  return m.name === "default" ? m.model : m.name;
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
  const [dropdownOpen, setDropdownOpen] = useState(false);
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
      className={`flex items-center gap-1 mt-1.5 transition-opacity duration-200 touch-show ${dropdownOpen ? "opacity-100" : "opacity-0 group-hover/msg:opacity-100"}`}
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
        <DropdownMenu onOpenChange={(open) => { setDropdownOpen(open); if (open) fetchModelsOnce(); }}>
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
          <DropdownMenuContent align="start" className="w-72 max-w-[calc(100vw-2rem)] p-0 overflow-hidden">
            {/* Header */}
            <div className="px-3 pt-2.5 pb-2 border-b border-border/50">
              <div className="flex items-center gap-2">
                <RefreshCw className="h-3.5 w-3.5" style={{ color: "var(--em-primary)" }} />
                <span className="text-xs font-medium text-foreground/70">选择模型重试</span>
                <span className="text-[10px] text-muted-foreground/50 ml-auto tabular-nums">
                  {models.length} 个可用
                </span>
              </div>
            </div>
            {/* Model list */}
            <div className="max-h-[40vh] overflow-y-auto py-1 model-selector-scroll">
              {groups.map((group, gi) => {
                const color = getProviderColor(group.provider);
                return (
                  <div key={group.provider}>
                    {gi > 0 && <div className="h-px mx-3 my-1 bg-border/30" />}
                    <div className="flex items-center gap-2 px-3 pt-2.5 pb-1">
                      <span
                        className="h-1.5 w-1.5 rounded-full shrink-0"
                        style={{ backgroundColor: color }}
                      />
                      <span
                        className="text-[10px] font-semibold uppercase tracking-widest"
                        style={{ color }}
                      >
                        {getProviderDisplayName(group.provider)}
                      </span>
                    </div>
                    {group.models.map((m) => {
                      const isCurrent = m.name === currentModel;
                      return (
                        <button
                          key={m.name}
                          onClick={() => onRetryWithModel(m.name)}
                          className={[
                            "w-full text-left px-3 py-2 flex items-center gap-3",
                            "transition-all duration-150 ease-out cursor-pointer",
                            "border-l-[3px] border-l-transparent",
                            "hover:bg-accent/50 hover:border-l-[color:var(--em-primary-alpha-25)]",
                            isCurrent ? "bg-[var(--em-primary-alpha-06)] !border-l-[var(--em-primary)]" : "",
                          ].join(" ")}
                        >
                          <span
                            className="h-2 w-2 rounded-full shrink-0"
                            style={{ backgroundColor: color, opacity: 0.6 }}
                          />
                          <div className="flex-1 min-w-0">
                            <div className="flex items-center gap-1.5">
                              <span className={`text-sm leading-tight ${isCurrent ? "font-semibold" : "font-medium"}`}>
                                {displayLabel(m)}
                              </span>
                              {isCurrent && (
                                <span
                                  className="text-[9px] px-1.5 py-px rounded-full font-medium"
                                  style={{
                                    backgroundColor: "var(--em-primary-alpha-10)",
                                    color: "var(--em-primary)",
                                  }}
                                >
                                  当前
                                </span>
                              )}
                            </div>
                            {m.name !== "default" && m.name !== m.model && (
                              <span className="text-[10px] text-muted-foreground/50 font-mono truncate block mt-0.5">
                                {m.model}
                              </span>
                            )}
                          </div>
                          {isCurrent && (
                            <span
                              className="h-5 w-5 rounded-full flex items-center justify-center shrink-0"
                              style={{ backgroundColor: "var(--em-primary-alpha-15)" }}
                            >
                              <Check className="h-3 w-3" style={{ color: "var(--em-primary)" }} />
                            </span>
                          )}
                        </button>
                      );
                    })}
                  </div>
                );
              })}
              {models.length === 0 && (
                <div className="px-3 py-6 text-center">
                  <Loader2 className="h-4 w-4 text-muted-foreground/25 mx-auto mb-1.5 animate-spin" />
                  <p className="text-xs text-muted-foreground/40">加载模型列表...</p>
                </div>
              )}
            </div>
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
