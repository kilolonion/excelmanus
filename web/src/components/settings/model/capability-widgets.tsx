"use client";

import { Wrench, ImageIcon, Brain } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Switch } from "@/components/ui/switch";
import type { ModelCapabilities } from "./types";

export function CapabilityBadges({ caps }: { caps: ModelCapabilities | null }) {
  const items: { key: string; label: string; icon: React.ReactNode; value: boolean | null }[] = [
    { key: "tools", label: "工具", icon: <Wrench className="h-2.5 w-2.5" />, value: caps?.supports_tool_calling ?? null },
    { key: "vision", label: "视觉", icon: <ImageIcon className="h-2.5 w-2.5" />, value: caps?.supports_vision ?? null },
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

export function CapabilityRow({
  icon,
  label,
  desc,
  value,
  error,
  onToggle,
}: {
  icon: React.ReactNode;
  label: string;
  desc: string;
  value: boolean | null;
  error?: string;
  onToggle: (v: boolean) => void;
}) {
  return (
    <div className="flex items-center gap-2.5 sm:gap-3 rounded-lg border border-border px-3 py-3 sm:py-2.5">
      <span
        className="flex-shrink-0"
        style={{ color: value === true ? "var(--em-primary)" : value === false ? "var(--destructive, #ef4444)" : "var(--muted-foreground)" }}
      >
        {icon}
      </span>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-1.5">
          <span className="text-sm font-medium">{label}</span>
          {value === true && (
            <Badge className="text-[9px] h-4 bg-emerald-500/15 text-emerald-600 border-emerald-500/20">
              支持
            </Badge>
          )}
          {value === false && (
            <Badge variant="secondary" className="text-[9px] h-4">
              不支持
            </Badge>
          )}
          {value === null && (
            <Badge variant="outline" className="text-[9px] h-4">
              未知
            </Badge>
          )}
        </div>
        <p className="text-[11px] text-muted-foreground leading-relaxed break-words">{desc}</p>
        {error && (
          <p className="text-[10px] text-destructive truncate mt-0.5" title={error}>
            {error}
          </p>
        )}
      </div>
      <Switch
        checked={value === true}
        onCheckedChange={onToggle}
        className="flex-shrink-0"
      />
    </div>
  );
}
