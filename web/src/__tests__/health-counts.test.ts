import { describe, expect, it } from "vitest";
import {
  healthSkillpackCount,
  healthToolCount,
  type HealthData,
} from "@/stores/health-hub-store";

function sample(partial: Partial<HealthData>): HealthData {
  return {
    status: "ok",
    version: "1.0.0",
    model: "m",
    tools: [],
    skillpacks: [],
    active_sessions: 0,
    ...partial,
  };
}

describe("health counts", () => {
  it("prefers tool_count over tools.length", () => {
    expect(healthToolCount(sample({ tool_count: 12, tools: [] }))).toBe(12);
    expect(healthToolCount(sample({ tools: ["a", "b"] }))).toBe(2);
  });

  it("prefers skillpack_count over skillpacks.length", () => {
    expect(healthSkillpackCount(sample({ skillpack_count: 4, skillpacks: [] }))).toBe(4);
    expect(healthSkillpackCount(sample({ skillpacks: ["x"] }))).toBe(1);
  });
});
