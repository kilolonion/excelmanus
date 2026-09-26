"use client";

import { type ReactNode } from "react";
import { Check, ChevronDown, X } from "lucide-react";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { glassMenuItemClass, glassMenuPanelClass } from "@/components/ui/menu-panel";
import { cn } from "@/lib/utils";

export const FIELD_CLASS = "h-9 text-xs rounded-lg";

/** 一个表单字段：标签、控件、可选提示。 */
export function Field({
  label,
  hint,
  required,
  extra,
  children,
}: {
  label: string;
  hint?: string;
  required?: boolean;
  extra?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="space-y-1">
      <div className="flex items-center gap-1.5 min-h-[1.125rem]">
        <label className="text-[11px] font-medium text-foreground/80">
          {label}
          {required ? <span className="ml-0.5 text-destructive/80">*</span> : null}
        </label>
        {extra}
      </div>
      {children}
      {hint ? <p className="text-[11px] text-muted-foreground leading-relaxed">{hint}</p> : null}
    </div>
  );
}

/** 配置项作用域标记：「此模型」随模型档案保存，「全局」对所有模型生效。 */
export function ScopeTag({ children, tone = "muted" }: { children: ReactNode; tone?: "muted" | "primary" }) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full px-1.5 py-0.5 text-[9px] font-medium leading-none",
        tone === "primary"
          ? "bg-[var(--em-primary-alpha-12)] text-[var(--em-primary)]"
          : "bg-muted text-muted-foreground",
      )}
    >
      {children}
    </span>
  );
}

export function GlassSelect({
  value,
  options,
  onChange,
  ariaLabel,
  ariaDescribedBy,
  id,
  disabled,
  placeholder = "请选择",
  align = "end",
  className,
}: {
  value: string;
  options: { value: string; label: string; icon?: ReactNode }[];
  onChange: (value: string) => void;
  ariaLabel: string;
  ariaDescribedBy?: string;
  id?: string;
  disabled?: boolean;
  placeholder?: string;
  align?: "start" | "end";
  className?: string;
}) {
  const current = options.find((option) => option.value === value);
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild disabled={disabled}>
        <button
          type="button"
          id={id}
          aria-label={ariaLabel}
          aria-describedby={ariaDescribedBy}
          className={cn(
            "inline-flex items-center gap-1.5 w-full h-9 rounded-lg border border-input bg-background px-2.5 text-left text-xs hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-60 disabled:hover:bg-transparent disabled:cursor-not-allowed",
            className,
          )}
        >
          {current?.icon}
          <span className={cn("flex-1 truncate", !current && "text-muted-foreground")}>{current?.label ?? placeholder}</span>
          <ChevronDown className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align={align}
        sideOffset={6}
        collisionPadding={12}
        className={cn(glassMenuPanelClass, "z-[80] min-w-[16rem] max-w-[calc(100vw-1.5rem)] overflow-y-auto")}
      >
        {options.map((option) => (
          <DropdownMenuItem
            key={option.value || "empty"}
            className={glassMenuItemClass}
            onSelect={() => onChange(option.value)}
          >
            {option.icon}
            <span className="flex-1">{option.label}</span>
            {option.value === value && (
              <Check className="h-3.5 w-3.5" style={{ color: "var(--em-primary)" }} />
            )}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

export function StatusCallout({
  tone,
  icon,
  title,
  detail,
  onDismiss,
}: {
  tone: "ok" | "error";
  icon: ReactNode;
  title: string;
  detail?: string | null;
  onDismiss?: () => void;
}) {
  return (
    <div
      className={cn(
        "rounded-xl px-3 py-2.5 text-xs border",
        tone === "ok"
          ? "bg-emerald-500/[0.08] text-emerald-700 dark:text-emerald-400 border-emerald-500/20"
          : "bg-destructive/[0.06] text-destructive border-destructive/20",
      )}
    >
      <div className="flex items-start gap-2">
        <span className="mt-0.5 shrink-0">{icon}</span>
        <div className="flex-1 min-w-0 space-y-1">
          <p className="font-medium leading-snug">{title}</p>
          {detail ? (
            <p
              className={cn(
                "leading-relaxed",
                tone === "error"
                  ? "text-amber-700 dark:text-amber-400"
                  : "text-emerald-700/80 dark:text-emerald-400/80",
              )}
            >
              {detail}
            </p>
          ) : null}
        </div>
        {onDismiss ? (
          <button
            type="button"
            className="shrink-0 mt-0.5 text-current/60 hover:text-current"
            onClick={onDismiss}
            aria-label="关闭提示"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        ) : null}
      </div>
    </div>
  );
}
