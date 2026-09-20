import { describe, expect, it } from "vitest";
import { placeTourCard } from "@/components/onboarding/tour-layout";
import { getTourScenes } from "@/components/onboarding/tour-steps";

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
});
