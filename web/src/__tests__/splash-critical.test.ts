import React from "react";
import { renderToString } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { LoadingScreen } from "@/components/ui/LoadingScreen";
import { SPLASH_CRITICAL_CSS } from "@/components/ui/splash-critical";

describe("splash first paint", () => {
  it("inlines layout rules that do not depend on Tailwind", () => {
    expect(SPLASH_CRITICAL_CSS).toContain(".em-splash{");
    expect(SPLASH_CRITICAL_CSS).toContain(".em-splash-sheet{");
    expect(SPLASH_CRITICAL_CSS).toContain("position:absolute");
  });

  it("SSR markup uses splash classes and solid SVG fills", () => {
    const html = renderToString(React.createElement(LoadingScreen));
    expect(html).toContain("em-splash");
    expect(html).toContain("正在准备你的工作空间");
    expect(html).toContain("ExcelManus");
    expect(html).toContain("#ffffff");
    expect(html).not.toContain("var(--card)");
  });
});
