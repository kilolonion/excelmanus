"use client";

import { useEffect, useRef } from "react";
import { cn } from "@/lib/utils";

export const PROGRESS_FROM = 8;
export const PROGRESS_TO = 66;
export const PROGRESS_MS = 4200;
let progressStartedAt: number | null = null;

const SHEET_FILL = "#ffffff";
const SHEET_STROKE = "#dce7e0";
const SHEET_PRIMARY = "#0b6b4f";

function SheetPreview({ highlight = false }: { highlight?: boolean }) {
  return (
    <svg
      viewBox="0 0 148 108"
      aria-hidden="true"
    >
      <rect
        x="0.75"
        y="0.75"
        width="146.5"
        height="106.5"
        rx="14"
        fill={SHEET_FILL}
        stroke={SHEET_STROKE}
      />
      <path
        d="M14.75 0.75h118.5a14 14 0 0 1 14 14V22H0.75V14.75a14 14 0 0 1 14-14Z"
        fill={SHEET_PRIMARY}
      />
      {[29.6, 58.4, 87.2, 116].map((x) => (
        <line
          key={x}
          x1={x}
          y1="22"
          x2={x}
          y2="107.25"
          stroke={SHEET_STROKE}
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
          stroke={SHEET_STROKE}
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
          fill={SHEET_PRIMARY}
          opacity="0.88"
        />
      ) : null}
    </svg>
  );
}

export function LoadingBrandMark({ className }: { className?: string }) {
  return (
    <div className={cn("em-splash-mark", className)} aria-hidden="true">
      <div className="em-splash-mark-glow" />

      <div className="em-splash-sheet em-splash-sheet-left">
        <SheetPreview />
      </div>
      <div className="em-splash-sheet em-splash-sheet-right">
        <SheetPreview highlight />
      </div>

      <div className="em-splash-logo-wrap">
        <svg className="em-splash-arc" viewBox="0 0 162 162">
          <circle
            cx="81"
            cy="81"
            r="73"
            fill="none"
            stroke="rgba(11,107,79,0.12)"
            strokeWidth="3"
          />
          <circle
            cx="81"
            cy="81"
            r="73"
            fill="none"
            stroke={SHEET_PRIMARY}
            strokeWidth="3.5"
            strokeLinecap="round"
            strokeDasharray="118 340"
            transform="rotate(-22 81 81)"
          />
        </svg>
        <div className="em-splash-logo">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src="/icon.png"
            alt=""
            width={80}
            height={80}
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
    <div className={cn("em-splash-progress", className)}>
      <div
        ref={fillRef}
        className="em-splash-progress-fill"
        style={{ width: `${PROGRESS_FROM}%` }}
      />
    </div>
  );
}

export function LoadingStatusSpinner({ className }: { className?: string }) {
  return (
    <span
      className={cn("em-splash-spinner", className)}
      aria-hidden="true"
    />
  );
}

/** 等待页/品牌条用的轻量字标：5KB PNG + 文字，避免 351KB 描边 SVG。 */
export function BrandWordmark({ className }: { className?: string }) {
  return (
    <span className={cn("em-splash-wordmark", className)}>
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src="/icon.png"
        alt=""
        width={28}
        height={28}
      />
      <span className="em-splash-wordmark-text">
        ExcelManus
      </span>
    </span>
  );
}
