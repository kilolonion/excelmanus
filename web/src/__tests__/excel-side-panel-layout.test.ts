import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const testDir = dirname(fileURLToPath(import.meta.url));
const layoutSrc = readFileSync(join(testDir, "../app/client-layout.tsx"), "utf8");
const panelSrc = readFileSync(join(testDir, "../components/excel/ExcelSidePanel.tsx"), "utf8");
describe("Excel side panel layout", () => {
  it("shrinks the shared title-and-chat column", () => {
    expect(layoutSrc).toMatch(/<\/main>\s*<JevTimelineDrawer \/>\s*<WorkspaceOverlays \/>/);
    expect(layoutSrc).toContain("{hasExcelPanel && <ExcelSidePanel />}");
    expect(layoutSrc).not.toMatch(/\{children\}[\s\S]*?<ExcelSidePanel \/>[\s\S]*?<\/main>/);
  });

  it("keeps the docked panel full-height beside the main column", () => {
    expect(panelSrc).toContain(
      'relative flex flex-col h-full flex-shrink-0 border-l border-border bg-background',
    );
    expect(panelSrc).not.toContain("em-excel-panel-docked");
  });

  it("docks at every non-mobile width so header and composer share one boundary", () => {
    expect(panelSrc).toContain("const useFloatingMode = isMobile || isFloatingByResize");
    expect(panelSrc).not.toContain("const useFloatingMode = !isDesktop || isFloatingByResize");
  });
});
