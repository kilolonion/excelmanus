"use client";

import { PROVIDER_LOGO_SLUG } from "./constants";

export function ProviderLogo({ id, color }: { id: string; color?: string }) {
  const slug = PROVIDER_LOGO_SLUG[id];
  if (!slug) return null;
  return (
    <span
      className="inline-block h-4 w-4 shrink-0"
      role="img"
      aria-label={id}
      style={{
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
      }}
    />
  );
}
