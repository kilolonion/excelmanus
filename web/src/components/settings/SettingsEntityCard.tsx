"use client";

import type { KeyboardEvent, ReactNode } from "react";
import { ChevronRight } from "lucide-react";
import { cn } from "@/lib/utils";

export function SettingsEntityCard({
  name,
  icon,
  description,
  badges,
  meta,
  actions,
  expandable = false,
  expanded = false,
  onClick,
  children,
  className,
  headerClassName,
  dashed = false,
}: {
  name: string;
  icon?: ReactNode;
  description?: ReactNode;
  badges?: ReactNode;
  meta?: ReactNode;
  actions?: ReactNode;
  expandable?: boolean;
  expanded?: boolean;
  onClick?: () => void;
  children?: ReactNode;
  className?: string;
  headerClassName?: string;
  dashed?: boolean;
}) {
  return (
    <div
      className={cn(
        "em-settings-section rounded-xl border overflow-hidden",
        dashed
          ? "border-dashed border-[var(--em-primary-alpha-25)] bg-[var(--em-primary-alpha-06)]"
          : "border-border",
        className,
      )}
    >
      <div
        role={onClick ? "button" : undefined}
        tabIndex={onClick ? 0 : undefined}
        className={cn(
          "w-full flex items-start gap-2 px-3 py-2.5 text-left transition-colors",
          onClick && "hover:bg-muted/30 cursor-pointer",
          headerClassName,
        )}
        onClick={onClick}
        onKeyDown={
          onClick
            ? (e: KeyboardEvent) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  onClick();
                }
              }
            : undefined
        }
      >
        {expandable && (
          <span
            className="text-muted-foreground transition-transform flex-shrink-0 mt-0.5"
            style={{ transform: expanded ? "rotate(90deg)" : "rotate(0deg)" }}
            aria-hidden
          >
            <ChevronRight className="h-3.5 w-3.5" />
          </span>
        )}
        {icon ? <span className="flex-shrink-0 mt-0.5">{icon}</span> : null}
        <div className="flex-1 min-w-0">
          <div className="flex items-start gap-2">
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-1.5 flex-wrap">
                <span className="text-sm font-medium leading-snug break-words wrap-anywhere">
                  {name}
                </span>
                {badges}
              </div>
              {description ? (
                <p className="text-[11px] text-muted-foreground leading-relaxed mt-1 break-words">
                  {description}
                </p>
              ) : null}
              {meta ? <div className="mt-1">{meta}</div> : null}
            </div>
            {actions ? (
              <div
                className="flex gap-0.5 shrink-0 -mt-0.5"
                onClick={(e) => e.stopPropagation()}
                onKeyDown={(e) => e.stopPropagation()}
              >
                {actions}
              </div>
            ) : null}
          </div>
        </div>
      </div>
      {children}
    </div>
  );
}
