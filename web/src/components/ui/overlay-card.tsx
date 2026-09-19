"use client";

import * as React from "react";
import { X } from "lucide-react";
import { Dialog as DialogPrimitive } from "radix-ui";

import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogDescription,
  DialogOverlay,
  DialogPortal,
  DialogTitle,
} from "@/components/ui/dialog";

export type OverlayTone = "primary" | "warning" | "danger" | "success" | "muted";
export type OverlaySize = "sm" | "md" | "lg";
export type OverlayActionVariant = "primary" | "danger" | "destructive" | "outline" | "ghost";

const SIZE_WIDTH: Record<OverlaySize, string> = {
  sm: "420px",
  md: "500px",
  lg: "560px",
};

export const OVERLAY_TONE: Record<
  OverlayTone,
  { iconWrap: string; icon: string }
> = {
  primary: {
    iconWrap: "bg-[var(--em-primary-alpha-10)] border-[var(--em-primary-alpha-20)]",
    icon: "text-[var(--em-primary)]",
  },
  warning: {
    iconWrap: "bg-amber-500/10 border-amber-500/20",
    icon: "text-amber-600 dark:text-amber-400",
  },
  danger: {
    iconWrap: "bg-red-500/10 border-red-500/20",
    icon: "text-red-500",
  },
  success: {
    iconWrap: "bg-emerald-500/10 border-emerald-500/20",
    icon: "text-emerald-600 dark:text-emerald-400",
  },
  muted: {
    iconWrap: "bg-muted/70 border-border/70",
    icon: "text-muted-foreground",
  },
};

const OverlayToneContext = React.createContext<OverlayTone>("primary");

export function useOverlayTone(): OverlayTone {
  return React.useContext(OverlayToneContext);
}

type OverlayCardProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  size?: OverlaySize;
  tone?: OverlayTone;
  className?: string;
  children: React.ReactNode;
} & Omit<React.ComponentProps<typeof DialogPrimitive.Content>, "className" | "children">;

export function OverlayCard({
  open,
  onOpenChange,
  size = "md",
  tone = "primary",
  className,
  children,
  style,
  ...contentProps
}: OverlayCardProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogPortal>
        <DialogOverlay className="z-[70] bg-black/20 dark:bg-black/40 backdrop-blur-[2px]" />
        <DialogPrimitive.Content
          data-slot="overlay-card"
          className={cn(
            "overlay-card z-[70] flex flex-col outline-none overflow-hidden p-0 gap-0",
            className,
          )}
          style={{
            "--overlay-card-width": SIZE_WIDTH[size],
            paddingBottom: "max(0px, env(safe-area-inset-bottom, 0px))",
            ...style,
          } as React.CSSProperties}
          {...contentProps}
        >
          <div className="overlay-card-handle flex justify-center pt-2.5 pb-0 sm:hidden" aria-hidden>
            <div className="w-10 h-1 rounded-full bg-muted-foreground/20" />
          </div>
          <OverlayToneContext.Provider value={tone}>
            <div className="relative flex min-h-0 flex-1 flex-col">{children}</div>
          </OverlayToneContext.Provider>
        </DialogPrimitive.Content>
      </DialogPortal>
    </Dialog>
  );
}

export function OverlayCardHeader({
  icon,
  eyebrow,
  title,
  description,
  badge,
  actions,
  onClose,
  closeDisabled,
  closeTitle = "关闭",
  className,
}: {
  icon?: React.ReactNode;
  eyebrow?: React.ReactNode;
  title: React.ReactNode;
  description?: React.ReactNode;
  badge?: React.ReactNode;
  actions?: React.ReactNode;
  onClose?: () => void;
  closeDisabled?: boolean;
  closeTitle?: string;
  className?: string;
}) {
  const tone = useOverlayTone();
  const cfg = OVERLAY_TONE[tone];

  return (
    <div
      className={cn(
        "flex items-start gap-3 sm:gap-4 px-5 pt-4 pb-0 sm:px-8 sm:pt-8",
        className,
      )}
    >
      {icon && (
        <div
          className={cn(
            "relative flex items-center justify-center size-10 sm:size-11 rounded-xl border flex-shrink-0",
            cfg.iconWrap,
            cfg.icon,
          )}
        >
          {icon}
        </div>
      )}
      <div className="flex-1 min-w-0">
        {(eyebrow || badge || actions || onClose) && (
          <div className="flex items-center gap-2 flex-wrap mb-1.5">
            {eyebrow && (
              <span className={cn("text-[12px] font-medium", cfg.icon)}>{eyebrow}</span>
            )}
            {badge}
            <div className="flex-1" />
            {(actions || onClose) && (
              <div className="flex items-center gap-0.5 flex-shrink-0 -mr-1">
                {actions}
                {onClose && (
                  <button
                    type="button"
                    onClick={onClose}
                    disabled={closeDisabled}
                    className="text-muted-foreground/50 hover:text-foreground transition-colors p-2 sm:p-1.5 rounded-xl hover:bg-muted/80 active:scale-95 disabled:opacity-40 min-h-11 min-w-11 sm:min-h-0 sm:min-w-0 inline-flex items-center justify-center"
                    title={closeTitle}
                    aria-label={closeTitle}
                  >
                    <X className="h-5 w-5 sm:h-[18px] sm:w-[18px]" />
                  </button>
                )}
              </div>
            )}
          </div>
        )}
        <DialogTitle className="font-semibold text-[20px] sm:text-[22px] text-foreground leading-snug tracking-tight">
          {title}
        </DialogTitle>
        {description && (
          <DialogDescription className="text-sm text-muted-foreground mt-1.5 leading-relaxed">
            {description}
          </DialogDescription>
        )}
      </div>
    </div>
  );
}

export function OverlayCardBadge({
  className,
  children,
}: {
  className?: string;
  children: React.ReactNode;
}) {
  const tone = useOverlayTone();
  const cfg = OVERLAY_TONE[tone];
  return (
    <span
      className={cn(
        "text-[11px] px-2 sm:px-2.5 py-0.5 rounded-full font-medium border",
        cfg.icon,
        cfg.iconWrap,
        className,
      )}
    >
      {children}
    </span>
  );
}

export function OverlayCardBody({
  className,
  children,
}: {
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <div
      className={cn(
        "px-5 sm:px-8 pt-4 pb-2 min-h-0 overflow-y-auto overscroll-contain",
        className,
      )}
    >
      {children}
    </div>
  );
}

export function OverlayCardFooter({
  className,
  children,
}: {
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <div
      className={cn(
        "flex flex-col-reverse sm:flex-row sm:justify-end gap-2.5 sm:gap-3",
        "shrink-0 bg-card px-5 sm:px-8 py-4 sm:py-5 mt-auto border-t border-[var(--em-hairline)]",
        className,
      )}
    >
      {children}
    </div>
  );
}

export function OverlayCardInset({
  title,
  padded = true,
  className,
  bodyClassName,
  children,
}: {
  title?: React.ReactNode;
  padded?: boolean;
  className?: string;
  bodyClassName?: string;
  children: React.ReactNode;
}) {
  return (
    <div
      className={cn(
        "rounded-2xl border border-[var(--em-hairline)] bg-[var(--em-fill)] dark:bg-muted/15 overflow-hidden",
        className,
      )}
    >
      {title && (
        <div className="px-4 py-2 sm:py-2.5 text-[11px] font-medium text-muted-foreground border-b border-[var(--em-hairline)]">
          {title}
        </div>
      )}
      <div className={cn(padded && "px-4 py-2.5 sm:py-3", bodyClassName)}>{children}</div>
    </div>
  );
}

export function OverlayCardDisclosure({
  icon,
  label,
  extra,
  open,
  onToggle,
  children,
}: {
  icon?: React.ReactNode;
  label: React.ReactNode;
  extra?: React.ReactNode;
  open?: boolean;
  onToggle?: () => void;
  children?: React.ReactNode;
}) {
  return (
    <div className="border-t border-[var(--em-hairline)] first:border-t-0">
      <div className="flex items-center gap-2 min-h-11 py-1">
        <button
          type="button"
          onClick={onToggle}
          className="flex flex-1 items-center gap-2 min-h-11 text-sm text-foreground/80 hover:text-foreground transition-colors text-left"
        >
          {icon && <span className="text-muted-foreground">{icon}</span>}
          <span className="flex-1">{label}</span>
          <span className="text-muted-foreground text-xs">{open ? "▾" : "›"}</span>
        </button>
        {extra}
      </div>
      {open && children && <div className="pb-3 text-sm">{children}</div>}
    </div>
  );
}

export function OverlayCardAction({
  action = "primary",
  className,
  style,
  ...props
}: React.ComponentProps<typeof Button> & { action?: OverlayActionVariant }) {
  const variant =
    action === "primary" || action === "destructive"
      ? "default"
      : action === "danger"
        ? "outline"
        : action;

  return (
    <Button
      variant={variant}
      className={cn(
        "h-12 sm:h-11 rounded-xl text-[15px] sm:text-sm font-semibold transition-all active:scale-[0.97]",
        action === "primary" &&
          "flex-1 text-white border-0 bg-[var(--em-primary)] hover:bg-[var(--em-primary)]/90",
        action === "danger" &&
          "flex-1 border-[var(--em-hairline)] bg-background text-foreground hover:bg-muted/60",
        action === "destructive" &&
          "flex-1 text-white border-0 bg-destructive hover:bg-destructive/90",
        action === "outline" && "font-semibold flex-1 border-[var(--em-hairline)] bg-background",
        action === "ghost" && "font-medium text-muted-foreground hover:text-foreground",
        className,
      )}
      style={style}
      {...props}
    />
  );
}
