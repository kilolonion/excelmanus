import { describe, expect, it } from "vitest";
import { isPlaceholderModelId } from "@/lib/model-display";

describe("isPlaceholderModelId", () => {
  it("flags leftover test models", () => {
    expect(isPlaceholderModelId("test-model")).toBe(true);
    expect(isPlaceholderModelId("DeepSeek")).toBe(false);
    expect(isPlaceholderModelId("")).toBe(false);
  });
});
