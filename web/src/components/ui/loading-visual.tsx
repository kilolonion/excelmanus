"use client";

import { useEffect, useRef } from "react";
import { cn } from "@/lib/utils";

const PROGRESS_FROM = 8;
const PROGRESS_TO = 66;
const PROGRESS_MS = 4200;
let progressStartedAt: number | null = null;

function SheetPreview({ highlight = false }: { highlight?: boolean }) {
  return (
    <svg
      viewBox="0 0 148 108"
      className="h-auto w-full"
      aria-hidden="true"
    >
      <rect
        x="0.75"
        y="0.75"
        width="146.5"
        height="106.5"
        rx="14"
        fill="var(--card)"
        stroke="var(--em-hairline)"
      />
      <path
        d="M14.75 0.75h118.5a14 14 0 0 1 14 14V22H0.75V14.75a14 14 0 0 1 14-14Z"
        fill="var(--em-primary)"
      />
      {[29.6, 58.4, 87.2, 116].map((x) => (
        <line
          key={x}
          x1={x}
          y1="22"
          x2={x}
          y2="107.25"
          stroke="var(--em-hairline)"
          strokeWidth="1"
        />
      ))}
      {[43.3, 64.6, 85.9].map((y) => (
        <line
          key={y}
          x1="0.75"
          y1={y}
          x2="147.25"
          y2={y}
          stroke="var(--em-hairline)"
          strokeWidth="1"
        />
      ))}
      {highlight ? (
        <rect
          x="88"
          y="66.2"
          width="27"
          height="18.2"
          rx="3"
          fill="var(--em-primary)"
          opacity="0.88"
        />
      ) : null}
    </svg>
  );
}

export function LoadingBrandMark({ className }: { className?: string }) {
  return (
    <div
      className={cn(
        "relative h-[214px] w-[272px] md:h-[236px] md:w-[308px]",
        className,
      )}
      aria-hidden="true"
    >
      <div className="pointer-events-none absolute left-1/2 top-[48%] h-[250px] w-[250px] -translate-x-1/2 -translate-y-1/2 rounded-full bg-[var(--em-primary)] opacity-[0.08] blur-[58px] md:h-[300px] md:w-[300px]" />

      <div className="absolute left-[8px] top-[58px] w-[128px] rotate-[-16deg] opacity-90 shadow-[0_16px_32px_-18px_rgba(33,115,70,0.28)] md:left-[16px] md:top-[62px] md:w-[140px]">
        <SheetPreview />
      </div>
      <div className="absolute right-[4px] top-[34px] w-[128px] rotate-[13deg] opacity-90 shadow-[0_16px_32px_-18px_rgba(33,115,70,0.28)] md:right-[8px] md:top-[30px] md:w-[140px]">
        <SheetPreview highlight />
      </div>

      <div className="absolute left-1/2 top-[48%] flex size-[148px] -translate-x-1/2 -translate-y-1/2 items-center justify-center md:size-[162px]">
        <svg className="loading-arc-spin absolute inset-0" viewBox="0 0 162 162">
          <circle
            cx="81"
            cy="81"
            r="73"
            fill="none"
            stroke="var(--em-primary-alpha-12)"
            strokeWidth="3"
          />
          <circle
            cx="81"
            cy="81"
            r="73"
            fill="none"
            stroke="var(--em-primary)"
            strokeWidth="3.5"
            strokeLinecap="round"
            strokeDasharray="118 340"
            transform="rotate(-22 81 81)"
          />
        </svg>
        <div className="relative z-10 flex size-[92px] items-center justify-center overflow-hidden rounded-full bg-card shadow-[0_10px_28px_rgba(33,115,70,0.16)] ring-1 ring-[var(--em-primary-alpha-10)] md:size-[102px]">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src="/icon.png"
            alt=""
            width={80}
            height={80}
            className="size-[72%] object-contain"
          />
        </div>
      </div>
    </div>
  );
}

export function LoadingProgressBar({ className }: { className?: string }) {
  const fillRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = fillRef.current;
    if (!el) return;

    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      el.style.width = `${PROGRESS_TO}%`;
      return;
    }

    if (progressStartedAt == null) progressStartedAt = performance.now();
    const origin = progressStartedAt;
    let frame = 0;

    const tick = (now: number) => {
      const t = Math.min(1, (now - origin) / PROGRESS_MS);
      const eased = 1 - (1 - t) ** 3;
      const next = PROGRESS_FROM + (PROGRESS_TO - PROGRESS_FROM) * eased;
      const current = Number.parseFloat(el.style.width) || 0;
      el.style.width = `${Math.max(current, next)}%`;
      if (t < 1) frame = requestAnimationFrame(tick);
    };

    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, []);

  return (
    <div
      className={cn(
        "h-[5px] overflow-hidden rounded-full bg-[var(--em-hairline)]",
        className,
      )}
    >
      <div
        ref={fillRef}
        className="h-full rounded-full bg-[var(--em-primary)]"
        style={{ width: `${PROGRESS_FROM}%` }}
      />
    </div>
  );
}

export function LoadingStatusSpinner({ className }: { className?: string }) {
  return (
    <span
      className={cn("loading-status-spinner shrink-0", className)}
      aria-hidden="true"
    />
  );
}

/** 等待页/品牌条用的轻量字标：5KB PNG + 文字，避免 351KB 描边 SVG。 */
export function BrandWordmark({ className }: { className?: string }) {
  return (
    <span className={cn("inline-flex items-center gap-2", className)}>
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src="/icon.png"
        alt=""
        width={28}
        height={28}
        className="size-7 object-contain"
      />
      <span className="text-[17px] font-semibold tracking-tight text-[var(--em-primary)]">
        ExcelManus
      </span>
    </span>
  );
}
