"use client";

import type { ReactNode } from "react";
import { CheckCircle2, Loader2, Save } from "lucide-react";
import { Button } from "@/components/ui/button";
import { JEV_GATE_OPTIONS, parseJevGate, type JevEntryTone, type JevGate } from "@/lib/jev-settings";
import { cn } from "@/lib/utils";

const SELECT_CLASS =
  "w-full sm:w-32 h-9 sm:h-8 text-sm rounded-md border border-input bg-background px-2 flex-shrink-0";

export function JevAvatar({ className }: { className?: string }) {
  return (
    <span
      className={cn(
        "inline-flex h-8 w-8 items-center justify-center rounded-xl shrink-0 text-[13px] font-semibold border",
        className,
      )}
      style={{
        backgroundColor: "color-mix(in srgb, var(--em-gold) 16%, white)",
        color: "color-mix(in srgb, var(--em-gold) 55%, #3d2a00)",
        borderColor: "color-mix(in srgb, var(--em-gold) 45%, transparent)",
      }}
      aria-hidden
    >
      J
    </span>
  );
}

export function JevStatusChip({ tone, chip }: { tone: JevEntryTone; chip: string }) {
  const className = {
    idle: "bg-muted text-muted-foreground",
    ready: "bg-emerald-50 text-emerald-700 dark:bg-emerald-950/50 dark:text-emerald-400",
    shadow: "bg-amber-50 text-amber-800 dark:bg-amber-950/40 dark:text-amber-300",
    enforce: "bg-[var(--em-primary-alpha-12)] text-[var(--em-primary)]",
  }[tone];
  return (
    <span className={cn("rounded-full px-1.5 py-px text-[10px] font-medium", className)}>
      {chip}
    </span>
  );
}

export function JevGateSelect({
  value,
  onChange,
}: {
  value: JevGate;
  onChange: (value: JevGate) => void;
}) {
  return (
    <select
      className={SELECT_CLASS}
      value={value}
      onChange={(event) => onChange(parseJevGate(event.target.value))}
    >
      {JEV_GATE_OPTIONS.map((option) => (
        <option key={option.value} value={option.value}>
          {option.label}
        </option>
      ))}
    </select>
  );
}

export function JevFieldRow({
  label,
  desc,
  children,
}: {
  label: string;
  desc: string;
  children: ReactNode;
}) {
  return (
    <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2 sm:gap-4 px-3 py-2.5">
      <div className="min-w-0">
        <p className="text-sm font-medium">{label}</p>
        <p className="text-[11px] text-muted-foreground mt-0.5">{desc}</p>
      </div>
      <div className="flex justify-end shrink-0">{children}</div>
    </div>
  );
}

export function JevSaveBar({
  hasChanges,
  saving,
  saved,
  error,
  onSave,
}: {
  hasChanges: boolean;
  saving: boolean;
  saved: boolean;
  error: string | null;
  onSave: () => void;
}) {
  return (
    <>
      {error && <p className="px-3 pt-2 text-xs text-destructive">{error}</p>}
      <div className="flex justify-end px-3 py-3">
        <Button
          size="sm"
          disabled={!hasChanges || saving}
          onClick={onSave}
          className="gap-1.5"
        >
          {saving ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : saved ? (
            <CheckCircle2 className="h-3.5 w-3.5" />
          ) : (
            <Save className="h-3.5 w-3.5" />
          )}
          {saved ? "已保存" : "保存"}
        </Button>
      </div>
    </>
  );
}
