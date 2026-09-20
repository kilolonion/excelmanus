import { describe, expect, it } from "vitest";
import { inferModelBrand } from "@/lib/provider-brand";

describe("inferModelBrand", () => {
  it("recognizes a brand from a friendly model name behind a custom gateway", () => {
    expect(inferModelBrand({
      name: "DeepSeek V4.1 Flash",
      model: "company-fast-model",
      base_url: "https://gateway.example.com/v1",
    })).toBe("deepseek");
    expect(inferModelBrand({
      name: "bench-qwen-hunnu",
      model: "Edu",
      base_url: "https://gateway.example.com/v1",
    })).toBe("qwen");
  });

  it("uses resolved model ids and common model-family aliases", () => {
    expect(inferModelBrand({
      name: "coding",
      model: "router/default",
      resolved_model: "anthropic/claude-sonnet-5",
    })).toBe("anthropic");
    expect(inferModelBrand({ model: "meta-llama/llama-4-maverick" })).toBe("meta");
    expect(inferModelBrand({ model: "glm-5.3" })).toBe("zhipu");
  });

  it("prefers the model brand over the transport provider", () => {
    expect(inferModelBrand({
      provider: "openrouter",
      model: "deepseek/deepseek-v4",
      base_url: "https://openrouter.ai/api/v1",
    })).toBe("deepseek");
  });

  it("falls back to an explicit provider or endpoint when the model is custom", () => {
    expect(inferModelBrand({ provider: "google", model: "company-model" })).toBe("gemini");
    expect(inferModelBrand({ base_url: "https://api.deepseek.com/v1" })).toBe("deepseek");
    expect(inferModelBrand({ base_url: "https://gateway.example.com/v1" })).toBe("example");
  });
});
