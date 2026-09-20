"use client";

import type { ReactNode } from "react";
import { CheckCircle2, ChevronDown, Loader2, Save } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { JEV_GATE_OPTIONS, parseJevGate, type JevEntryTone, type JevGate } from "@/lib/jev-settings";
import { cn } from "@/lib/utils";

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
  const selected = JEV_GATE_OPTIONS.find((option) => option.value === value) ?? JEV_GATE_OPTIONS[0];

  return (
    <DropdownMenu modal={false}>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          aria-label="Jev 功能模式"
          className="group inline-flex h-9 w-full flex-shrink-0 items-center gap-2 rounded-lg border border-input bg-background px-2.5 text-sm text-foreground shadow-[0_1px_2px_rgba(24,58,40,0.04)] transition-[border-color,background-color,box-shadow] hover:border-[var(--em-primary-alpha-25)] hover:bg-muted/30 focus-visible:border-[var(--em-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--em-primary-alpha-15)] data-[state=open]:border-[var(--em-primary)] data-[state=open]:bg-[var(--em-primary-alpha-06)] data-[state=open]:shadow-[0_0_0_3px_var(--em-primary-alpha-10)] sm:h-8 sm:w-32"
        >
          <span
            className={cn(
              "h-2 w-2 shrink-0 rounded-full ring-2 ring-background",
              value === "off" && "bg-muted-foreground/45",
              value === "shadow" && "bg-amber-500",
              value === "enforce" && "bg-[var(--em-primary)]",
            )}
          />
          <span className="flex-1 text-left">{selected.label}</span>
          <ChevronDown className="h-3.5 w-3.5 shrink-0 text-muted-foreground transition-transform duration-200 group-data-[state=open]:rotate-180" />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="end"
        sideOffset={6}
        className="w-[var(--radix-dropdown-menu-trigger-width)] min-w-32 rounded-xl border-border/80 bg-popover/95 p-1.5 shadow-xl backdrop-blur-sm"
      >
        <DropdownMenuRadioGroup
          value={value}
          onValueChange={(nextValue) => onChange(parseJevGate(nextValue))}
        >
          {JEV_GATE_OPTIONS.map((option) => (
            <DropdownMenuRadioItem
              key={option.value}
              value={option.value}
              className="rounded-lg py-2 pr-2.5 pl-7 text-sm transition-colors focus:bg-[var(--em-primary-alpha-10)] focus:text-foreground data-[state=checked]:bg-[var(--em-primary-alpha-06)] data-[state=checked]:font-medium data-[state=checked]:[&_svg]:!text-[var(--em-primary)]"
            >
              {option.label}
            </DropdownMenuRadioItem>
          ))}
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
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
