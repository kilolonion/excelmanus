"use client";

import { useState, type KeyboardEvent, type ReactNode } from "react";
import { ChevronRight } from "lucide-react";
import { cn } from "@/lib/utils";

export function SettingsFoldSection({
  title,
  description,
  icon,
  actions,
  coachId,
  defaultOpen = true,
  open: openProp,
  onOpenChange,
  embedded = false,
  children,
}: {
  title: string;
  description: string;
  icon: ReactNode;
  actions?: ReactNode;
  coachId?: string;
  defaultOpen?: boolean;
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  embedded?: boolean;
  children: ReactNode;
}) {
  const [uncontrolledOpen, setUncontrolledOpen] = useState(defaultOpen);
  const open = openProp ?? uncontrolledOpen;

  const setOpen = (next: boolean) => {
    if (openProp === undefined) setUncontrolledOpen(next);
    onOpenChange?.(next);
  };

  const toggle = () => setOpen(!open);

  return (
    <section className={cn("em-settings-section rounded-xl border border-border", embedded && "em-settings-section-embedded")} data-coach-id={coachId}>
      <div
        role="button"
        tabIndex={0}
        aria-expanded={open}
        className="w-full flex items-start gap-2 px-3 py-3 text-left cursor-pointer select-none hover:bg-muted/30 transition-colors"
        onClick={toggle}
        onKeyDown={(event: KeyboardEvent) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            toggle();
          }
        }}
      >
        <span className="mt-0.5 shrink-0">{icon}</span>
        <div className="flex-1 min-w-0">
          <h3 className="font-semibold text-sm">{title}</h3>
          <p className="text-[11px] text-muted-foreground mt-0.5">{description}</p>
        </div>
        {actions ? (
          <div
            className="shrink-0"
            onClick={(event) => event.stopPropagation()}
            onKeyDown={(event) => event.stopPropagation()}
          >
            {actions}
          </div>
        ) : null}
        <ChevronRight
          className={cn(
            "h-4 w-4 text-muted-foreground shrink-0 mt-0.5 transition-transform",
            open && "rotate-90",
          )}
        />
      </div>
      <div hidden={!open}>{children}</div>
    </section>
  );
}
