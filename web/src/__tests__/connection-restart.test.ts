import { describe, expect, it } from "vitest";
import {
  healthResponseIsUp,
  restartShouldReload,
} from "@/stores/connection-store";

describe("healthResponseIsUp", () => {
  it("treats draining as down", () => {
    expect(healthResponseIsUp(true, "draining")).toBe(false);
    expect(healthResponseIsUp(true, "ok")).toBe(true);
    expect(healthResponseIsUp(false, "ok")).toBe(false);
  });
});

describe("restartShouldReload", () => {
  it("reloads after downtime even if fingerprint is unchanged", () => {
    expect(
      restartShouldReload({
        probeOk: true,
        versionChanged: false,
        sawDown: true,
        requireVersionChange: true,
      }),
    ).toBe(true);
  });

  it("does not treat still-up same fingerprint as upgrade success", () => {
    expect(
      restartShouldReload({
        probeOk: true,
        versionChanged: false,
        sawDown: false,
        requireVersionChange: true,
      }),
    ).toBe(false);
  });

  it("reloads on fingerprint change", () => {
    expect(
      restartShouldReload({
        probeOk: true,
        versionChanged: true,
        sawDown: false,
        requireVersionChange: true,
      }),
    ).toBe(true);
  });
});
