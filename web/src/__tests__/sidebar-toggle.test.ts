import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const testDir = dirname(fileURLToPath(import.meta.url));
const sidebarSrc = readFileSync(
  join(testDir, "../components/sidebar/Sidebar.tsx"),
  "utf8",
);

describe("conversation sidebar toggle", () => {
  it("binds the desktop sidebar width directly to the open state", () => {
    expect(sidebarSrc).toContain(
      'const desktopSidebarWidth = sidebarOpen ? "320px" : "0px"',
    );
    expect(sidebarSrc).toContain(
      "width: isMobile ? mobileSidebarWidth : desktopSidebarWidth",
    );
    expect(sidebarSrc).toContain(
      "minWidth: isMobile ? mobileSidebarWidth : desktopSidebarWidth",
    );
    expect(sidebarSrc).not.toContain("animate={sidebarAnimate}");
  });

  it("uses idempotent open and close actions for the two controls", () => {
    expect(sidebarSrc).toContain("onClick={() => setSidebarOpen(true)}");
    expect(sidebarSrc).toContain(
      "const closeSidebar = useCallback(() => setSidebarOpen(false), [setSidebarOpen])",
    );
  });
});
