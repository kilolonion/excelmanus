import { describe, expect, it } from "vitest";
import { readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { getSpotlightRect, getTourCardMaxHeight, placeTourCard } from "@/components/onboarding/tour-layout";
import { getTourScenes } from "@/components/onboarding/tour-steps";

function collectTsxSources(root: string): string {
  return readdirSync(root, { withFileTypes: true }).map((entry) => {
    const path = join(root, entry.name);
    if (entry.isDirectory()) return collectTsxSources(path);
    return entry.name.endsWith(".tsx") ? readFileSync(path, "utf8") : "";
  }).join("\n");
}

describe("tour viewport placement", () => {
  for (const [width, height, top] of [[320, 568, 0], [390, 340, 90], [844, 280, 0], [768, 1024, 0], [1024, 700, 0], [1440, 900, 0]]) {
    it(`keeps navigation in ${width}x${height} viewport at offset ${top}`, () => {
      const v = { left: 0, top, width, height };
      const targets = [null, { left: 4, top: top + height - 55, right: width - 4, bottom: top + height - 5, width: width - 8, height: 50 }];
      for (const target of targets) {
        const p = placeTourCard(target, v, 380, 540, "bottom");
        expect(p.left).toBeGreaterThanOrEqual(0);
        expect(p.left + p.width).toBeLessThanOrEqual(width);
        expect(p.top).toBeGreaterThanOrEqual(top);
        expect(p.top + Math.min(540, p.maxHeight)).toBeLessThanOrEqual(top + height);
      }
    });
  }
  it("moves above a bottom composer when the requested side would overlap", () => {
    const target = { left: 0, top: 650, right: 800, bottom: 760, width: 800, height: 110 };
    const p = placeTourCard(target, { left: 0, top: 0, width: 800, height: 800 }, 380, 360, "bottom");
    expect(p.top + 360).toBeLessThan(target.top);
  });
  it("keeps chapter and step identity during phone/desktop breakpoint changes", () => {
    const ids = (mobile: boolean) => getTourScenes(mobile).map((s) => [s.id, s.steps.map((step) => [step.title, step.practice])]);
    expect(ids(true)).toEqual(ids(false));
  });

  it("clips spotlight padding to the safe visual viewport", () => {
    const viewport = { left: 10, top: 20, width: 300, height: 400 };
    expect(getSpotlightRect(
      { left: 10, top: 20, right: 60, bottom: 70, width: 50, height: 50 },
      viewport,
      8,
    )).toEqual({ left: 10, top: 20, width: 58, height: 58 });
    expect(getSpotlightRect(
      { left: 280, top: 390, right: 310, bottom: 420, width: 30, height: 30 },
      viewport,
      8,
    )).toEqual({ left: 272, top: 382, width: 38, height: 38 });
  });

  it("keeps the default mobile card compact and grants more room only for an opened practice", () => {
    expect(getTourCardMaxHeight(844, true, false)).toBe(320);
    expect(getTourCardMaxHeight(568, true, false)).toBeCloseTo(272.64);
    expect(getTourCardMaxHeight(390, true, false)).toBe(220);
    expect(getTourCardMaxHeight(844, true, true)).toBeCloseTo(590.8);
    expect(getTourCardMaxHeight(568, true, true)).toBeCloseTo(397.6);
    expect(getTourCardMaxHeight(844, false, false)).toBe(824);
  });

  it("uses focused controls instead of full-height mobile surfaces", () => {
    const steps = getTourScenes(true).flatMap((scene) => scene.steps);
    const byTitle = new Map(steps.map((step) => [step.title, step.target]));
    expect(byTitle.get("把文件带进任务")).toBe("coach-sidebar-file-tools");
    expect(byTitle.get("查看表格与切换工作表")).toBe("coach-workbook-entry");
    expect(byTitle.get("上传与多种文件")).toBe("coach-upload-button");
    expect(byTitle.get("订阅与授权")).toBe("coach-settings-subtab-subscription");
    expect(byTitle.get("按需调整系统设置")).toBe("coach-settings-runtime-compaction");
    expect(byTitle.get("更新与重新查看引导")).toBe("coach-settings-tab-version");
    expect(steps.map((step) => step.target)).not.toContain("coach-sidebar");
  });

  it("keeps every declared target backed by a static component anchor", () => {
    const testDir = dirname(fileURLToPath(import.meta.url));
    const source = collectTsxSources(join(testDir, "..", "components"));
    const anchors = new Set(
      Array.from(
        source.matchAll(/(?:data-coach-id|coachId)\s*(?:=|:)\s*(?:\{\s*)?["'`]([^"'`]+)["'`]/g),
        (match) => match[1],
      ),
    );
    const targets = new Set(getTourScenes(false).flatMap((scene) => scene.steps.map((step) => step.target)));
    expect(Array.from(targets).filter((target) => !anchors.has(target))).toEqual([]);
  });
});
