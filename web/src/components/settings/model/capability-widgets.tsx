"use client";

import type { ReactNode } from "react";
import { Wrench, ImageIcon, Brain } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Switch } from "@/components/ui/switch";
import type { ModelCapabilities } from "./types";

export function CapabilityBadges({ caps, visionMode }: { caps: ModelCapabilities | null; visionMode?: string }) {
  const items: { key: string; label: string; icon: ReactNode; value: boolean | null }[] = [
    { key: "tools", label: "工具", icon: <Wrench className="h-2.5 w-2.5" />, value: caps?.supports_tool_calling ?? null },
    { key: "vision", label: "视觉", icon: <ImageIcon className="h-2.5 w-2.5" />, value: visionMode === "true" ? true : visionMode === "false" ? false : caps?.supports_vision ?? null },
    { key: "thinking", label: "思考", icon: <Brain className="h-2.5 w-2.5" />, value: caps?.supports_thinking ?? null },
  ];

  return (
    <span className="inline-flex items-center gap-1 shrink-0 whitespace-nowrap">
      {items.map((item) => {
        let cls: string;
        let tip: string;

        if (item.value === true) {
          cls = "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400";
          tip = `${item.label}: 支持`;
        } else if (item.value === false) {
          cls = "bg-rose-500/10 text-rose-400/80 dark:text-rose-400/70";
          tip = `${item.label}: 不支持`;
        } else {
          cls = "bg-muted/60 text-muted-foreground/50";
          tip = `${item.label}: 未探测`;
        }

        return (
          <span
            key={item.key}
            title={tip}
            className={`inline-flex items-center gap-0.5 rounded-md px-1.5 py-0.5 text-[9px] leading-none font-medium transition-colors ${cls}`}
          >
            {item.icon}
            <span>{item.label}</span>
          </span>
        );
      })}
    </span>
  );
}

function EvidenceBadge({ value, evidence }: { value: boolean | null; evidence?: string }) {
  if (value === true) {
    return (
      <Badge className="text-[9px] h-4 bg-emerald-500/15 text-emerald-600 border-emerald-500/20">
        {evidence === "user_override" ? "手动声明" : evidence ? "实测通过" : "支持（来源待核）"}
      </Badge>
    );
  }
  if (value === false) {
    return (
      <Badge variant="secondary" className="text-[9px] h-4">
        {evidence === "user_override" ? "手动声明不支持" : "不支持"}
      </Badge>
    );
  }
  return (
    <Badge variant="outline" className="text-[9px] h-4">
      未知
    </Badge>
  );
}

/**
 * 统一配置表单里的能力行：一行同时表达实测结果、手动声明和开关，
 * 不再单独成卡，随表单一次保存。
 */
export function CapabilityToggleRow({
  icon,
  label,
  desc,
  value,
  evidence,
  error,
  hint,
  disabled = false,
  onToggle,
}: {
  icon: ReactNode;
  label: string;
  desc: string;
  value: boolean | null;
  evidence?: string;
  error?: string;
  hint?: string;
  disabled?: boolean;
  onToggle: (value: boolean) => void;
}) {
  return (
    <div className="flex items-start gap-2.5 border-b border-border/50 py-2.5 last:border-b-0">
      <span
        className="mt-0.5 shrink-0"
        style={{ color: value === true ? "var(--em-primary)" : value === false ? "var(--destructive, #ef4444)" : "var(--muted-foreground)" }}
      >
        {icon}
      </span>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-1.5 flex-wrap">
          <span className="text-xs font-medium">{label}</span>
          <EvidenceBadge value={value} evidence={evidence} />
          {hint ? <span className="text-[9px] text-muted-foreground">{hint}</span> : null}
        </div>
        <p className="mt-0.5 text-[11px] text-muted-foreground leading-relaxed break-words">{desc}</p>
        {error && (
          <p className="mt-0.5 text-[10px] text-destructive truncate" title={error}>
            {error}
          </p>
        )}
      </div>
      <Switch
        checked={value === true}
        onCheckedChange={onToggle}
        disabled={disabled}
        className="flex-shrink-0 mt-0.5"
      />
    </div>
  );
}
