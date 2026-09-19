import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const srcDir = join(dirname(fileURLToPath(import.meta.url)), "../components/chat");
const layoutSrc = readFileSync(
  join(dirname(fileURLToPath(import.meta.url)), "../app/client-layout.tsx"),
  "utf8",
);

describe("Jev timeline layout", () => {
  const src = readFileSync(join(srcDir, "JevTimeline.tsx"), "utf8");

  it("keeps stage chips from shrinking and wrapping mid-word", () => {
    const rail = src.slice(src.indexOf("function StageRail"), src.indexOf("function VerticalTimeline"));
    expect(rail).toContain('className="shrink-0"');
    expect(rail).toContain("whitespace-nowrap");
    expect(rail).toContain("overflow-x-auto");
    expect(rail).not.toContain("min-w-[4.5rem]");
    expect(rail).not.toContain("flex-1");
  });

  it("docks as an in-flow sidebar on desktop instead of a full-screen overlay", () => {
    expect(src).toContain("useIsDesktop");
    expect(src).toContain('data-testid="jev-desktop-sidebar"');
    expect(src).toContain("flex-shrink-0");
    expect(src).toContain("border-l");
    expect(src).toContain("headerExtra");
  });

  it("sits next to the chat column in the app shell", () => {
    expect(layoutSrc).toMatch(/<\/main>\s*<JevTimelineDrawer \/>/);
  });

  it("hides the rail, header button, and drawer unless chatEnabled", () => {
    expect(src).toContain("if (!chatEnabled) return null");
    expect(src).toContain("chatEnabled && (open || pinned)");
  });
});
