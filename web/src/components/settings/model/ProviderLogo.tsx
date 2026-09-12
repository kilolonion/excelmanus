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
  color,
  className,
  iconClassName,
}: {
  id: string;
  color?: string;
  className?: string;
  iconClassName?: string;
}) {
  const slug = providerLogoSlug(id);
  return (
    <span
      className={cn("inline-flex items-center justify-center rounded-xl shrink-0", className)}
      style={{
        backgroundColor: tintedBackground(color) || "color-mix(in srgb, var(--muted) 80%, transparent)",
        color: color || "var(--muted-foreground)",
      }}
      role="img"
      aria-label={id}
    >
      <span className={cn("inline-block h-4 w-4", iconClassName)} style={logoMaskStyle(slug, color)} />
    </span>
  );
}
