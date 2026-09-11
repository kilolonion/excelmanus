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
  md: "480px",
  lg: "560px",
};

export const OVERLAY_TONE: Record<
  OverlayTone,
  { strip: string; glow: string; iconWrap: string; icon: string; pulse: string }
> = {
  primary: {
    strip: "bg-[var(--em-primary)]",
    glow: "from-[var(--em-primary-alpha-20)] via-[var(--em-primary-alpha-06)] to-transparent",
    iconWrap: "bg-[var(--em-primary-alpha-10)] border-[var(--em-primary-alpha-25)]",
    icon: "text-[var(--em-primary)]",
    pulse: "bg-[var(--em-primary)]",
  },
  warning: {
    strip: "bg-amber-500",
    glow: "from-amber-500/20 via-amber-500/5 to-transparent",
    iconWrap: "bg-amber-500/10 border-amber-500/20",
    icon: "text-amber-500",
    pulse: "bg-amber-500",
  },
  danger: {
    strip: "bg-red-500",
    glow: "from-red-500/20 via-red-500/5 to-transparent",
    iconWrap: "bg-red-500/10 border-red-500/20",
    icon: "text-red-500",
    pulse: "bg-red-500",
  },
  success: {
    strip: "bg-emerald-500",
    glow: "from-emerald-500/20 via-emerald-500/5 to-transparent",
    iconWrap: "bg-emerald-500/10 border-emerald-500/20",
    icon: "text-emerald-500",
    pulse: "bg-emerald-500",
  },
  muted: {
    strip: "bg-muted-foreground/35",
    glow: "from-muted/50 via-muted/15 to-transparent",
    iconWrap: "bg-muted/70 border-border/70",
    icon: "text-muted-foreground",
    pulse: "bg-muted-foreground",
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
  const toneCfg = OVERLAY_TONE[tone];

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogPortal>
        <DialogOverlay className="z-[70] bg-black/20 dark:bg-black/40 backdrop-blur-[2px]" />
        <DialogPrimitive.Content
          data-slot="overlay-card"
          className={cn(
            "overlay-card z-[70] flex flex-col outline-none overflow-hidden p-0 gap-0 border-0 shadow-none",
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
          <div className={cn("absolute top-0 inset-x-0 h-1.5 rounded-t-3xl", toneCfg.strip)} />
          <div
            className={cn(
              "absolute top-0 inset-x-0 h-24 bg-gradient-to-b pointer-events-none",
              toneCfg.glow,
            )}
          />
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
  title,
  description,
  badge,
  actions,
  onClose,
  closeDisabled,
  closeTitle = "关闭",
  pulse = false,
  className,
}: {
  icon?: React.ReactNode;
  title: React.ReactNode;
  description?: React.ReactNode;
  badge?: React.ReactNode;
  actions?: React.ReactNode;
  onClose?: () => void;
  closeDisabled?: boolean;
  closeTitle?: string;
  pulse?: boolean;
  className?: string;
}) {
  const tone = useOverlayTone();
  const cfg = OVERLAY_TONE[tone];

  return (
    <div
      className={cn(
        "flex items-start gap-3 sm:gap-4 px-5 pt-4 pb-0 sm:px-6 sm:pt-5",
        className,
      )}
    >
      {icon && (
        <div className="relative flex-shrink-0 mt-0.5">
          {pulse && (
            <div
              className={cn("absolute inset-0 rounded-full opacity-20 overlay-icon-pulse", cfg.pulse)}
            />
          )}
          <div
            className={cn(
              "relative flex items-center justify-center size-10 sm:size-11 rounded-full border",
              cfg.iconWrap,
              cfg.icon,
            )}
          >
            {icon}
          </div>
        </div>
      )}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 sm:gap-2.5 flex-wrap">
          <DialogTitle className="font-semibold text-[15px] sm:text-base text-foreground leading-snug">
            {title}
          </DialogTitle>
          {badge}
        </div>
        {description && (
          <DialogDescription className="text-sm text-muted-foreground mt-1 leading-relaxed">
            {description}
          </DialogDescription>
        )}
      </div>
      {(actions || onClose) && (
        <div className="flex items-center gap-0.5 flex-shrink-0 -mr-1">
          {actions}
          {onClose && (
            <button
              type="button"
              onClick={onClose}
              disabled={closeDisabled}
              className="text-muted-foreground/50 hover:text-foreground transition-colors p-2 sm:p-1.5 rounded-xl hover:bg-muted/80 active:scale-95 disabled:opacity-40"
              title={closeTitle}
            >
              <X className="h-5 w-5 sm:h-[18px] sm:w-[18px]" />
            </button>
          )}
        </div>
      )}
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
        "px-5 sm:px-6 pt-4 pb-2 min-h-0 overflow-y-auto overscroll-contain",
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
        "px-5 sm:px-6 py-4 mt-auto border-t border-border/40 bg-muted/15",
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
        "rounded-2xl border border-border/50 bg-muted/20 dark:bg-muted/15 overflow-hidden",
        className,
      )}
    >
      {title && (
        <div className="px-4 py-2 sm:py-2.5 text-[11px] font-semibold text-muted-foreground uppercase tracking-widest border-b border-border/30 bg-muted/30 dark:bg-muted/20">
          {title}
        </div>
      )}
      <div className={cn(padded && "px-4 py-2.5 sm:py-3", bodyClassName)}>{children}</div>
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
          "flex-1 text-white border-0 shadow-md overlay-btn-primary bg-[var(--em-primary)] hover:bg-[var(--em-primary)]/90",
        action === "danger" &&
          "flex-1 text-red-600 dark:text-red-400 border-red-200 dark:border-red-500/30 hover:bg-red-50 dark:hover:bg-red-500/10",
        action === "destructive" &&
          "flex-1 text-white border-0 shadow-md bg-destructive hover:bg-destructive/90",
        action === "outline" && "font-semibold",
        action === "ghost" && "font-medium text-muted-foreground hover:text-foreground",
        className,
      )}
      style={style}
      {...props}
    />
  );
}
