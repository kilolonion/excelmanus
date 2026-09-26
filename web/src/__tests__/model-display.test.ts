import { describe, expect, it } from "vitest";
import { cleanModelDescription, isPlaceholderModelId, isSameModelReference } from "@/lib/model-display";

describe("isPlaceholderModelId", () => {
  it("flags leftover test models", () => {
    expect(isPlaceholderModelId("test-model")).toBe(true);
    expect(isPlaceholderModelId("DeepSeek")).toBe(false);
    expect(isPlaceholderModelId("")).toBe(false);
  });
});

describe("model display de-duplication", () => {
  it("removes generated OAuth model prefixes from descriptions", () => {
    expect(cleanModelDescription(
      "deepseek-v4.1-flash — WorkBuddy 订阅登录（无需 API Key）",
      ["workbuddy-global/deepseek-v4.1-flash", "deepseek-v4.1-flash"],
    )).toBe("WorkBuddy 订阅登录（无需 API Key）");
    expect(cleanModelDescription("Codex 5.2 - OAuth 登录（无需 API Key）", ["gpt-5.2-codex"]))
      .toBe("OAuth 登录（无需 API Key）");
    expect(cleanModelDescription("公司专用账号", ["deepseek-v4.1-flash"])).toBe("公司专用账号");
  });

  it("recognizes equivalent IDs after subscription prefixes and punctuation formatting", () => {
    expect(isSameModelReference("workbuddy-global/deepseek-v4.1-flash", "deepseek-v4.1-flash")).toBe(true);
    expect(isSameModelReference("deepseek_v4.1_flash", "deepseek-v4.1-flash")).toBe(true);
    expect(isSameModelReference("deepseek-v4-flash", "deepseek-v4.1-flash")).toBe(false);
  });
});
