"use client";

import type { CSSProperties } from "react";
import { cn } from "@/lib/utils";
import { PROVIDER_LOGO_SLUG } from "./constants";

function logoMaskStyle(slug: string, color?: string): CSSProperties {
  return {
    color: color || undefined,
    backgroundColor: "currentColor",
    maskImage: `url(/providers/${slug}.svg)`,
    WebkitMaskImage: `url(/providers/${slug}.svg)`,
    maskSize: "contain",
    WebkitMaskSize: "contain",
    maskRepeat: "no-repeat",
    WebkitMaskRepeat: "no-repeat",
    maskPosition: "center",
    WebkitMaskPosition: "center",
  };
}

function tintedBackground(color?: string): string | undefined {
  if (!color) return undefined;
  if (/^#[0-9a-fA-F]{6}$/.test(color)) return `${color}1A`;
  if (/^#[0-9a-fA-F]{3}$/.test(color)) {
    const [r, g, b] = [color[1], color[2], color[3]];
    return `#${r}${r}${g}${g}${b}${b}1A`;
  }
  return undefined;
}

export function providerLogoSlug(id: string): string {
  return PROVIDER_LOGO_SLUG[id] || "default";
}

export function hasProviderLogo(id: string): boolean {
  return Boolean(PROVIDER_LOGO_SLUG[id]);
}

export function providerFallbackInitial(label: string): string {
  return Array.from(label.trim())[0]?.toUpperCase() || "?";
}

export function ProviderLogo({ id, color, className }: { id: string; color?: string; className?: string }) {
  const slug = PROVIDER_LOGO_SLUG[id];
  if (!slug) return null;
  return (
    <span
      className={cn("inline-block h-4 w-4 shrink-0", className)}
      role="img"
      aria-label={id}
      style={logoMaskStyle(slug, color)}
    />
  );
}

export function ProviderAvatar({
  id,
  label,
  color,
  className,
  iconClassName,
}: {
  id: string;
  label?: string;
  color?: string;
  className?: string;
  iconClassName?: string;
}) {
  const slug = PROVIDER_LOGO_SLUG[id];
  const accessibleLabel = label || id;
  return (
    <span
      className={cn("inline-flex items-center justify-center rounded-xl shrink-0", className)}
      style={{
        backgroundColor: tintedBackground(color) || "color-mix(in srgb, var(--muted) 80%, transparent)",
        color: color || "var(--muted-foreground)",
      }}
      role="img"
      aria-label={accessibleLabel}
    >
      {slug ? (
        <span className={cn("inline-block h-4 w-4", iconClassName)} style={logoMaskStyle(slug, color)} />
      ) : (
        <span className={cn("inline-flex h-4 w-4 items-center justify-center text-[10px] font-bold leading-none", iconClassName)}>
          {providerFallbackInitial(accessibleLabel)}
        </span>
      )}
    </span>
  );
}
