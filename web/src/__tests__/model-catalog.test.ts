import { describe, it, expect } from "vitest";
import { MODEL_CATALOG, modelDocumentation, providerDefaults, APP_INPUT_MODALITIES } from "@/lib/model-catalog";
import { PROVIDER_PRESETS, CODEX_MODELS } from "@/components/settings/model/constants";

describe("shared model capability sources", () => {
  it("uses backend-owned defaults without a second frontend model list", () => {
    for (const preset of PROVIDER_PRESETS) {
      expect(preset).toMatchObject(providerDefaults(preset.id));
      expect(modelDocumentation(preset.model)?.source_urls.length).toBeGreaterThan(0);
    }
    expect(CODEX_MODELS.map((m) => m.modelId)).toEqual(MODEL_CATALOG.codex_models.map((m) => m.model));
    expect(CODEX_MODELS.some((m) => m.modelId === "gpt-5-codex-mini")).toBe(false);
  });
  it("keeps full model context separate from input/transport budgets", () => {
    expect(modelDocumentation("qwen-max")).toMatchObject({ context_window: 32768, max_input_tokens: 30720 });
    expect(modelDocumentation("qwen-long")).toMatchObject({ context_window: 10000000, transport_input_limit: 1000000 });
    expect(modelDocumentation("qwen-turbo")?.tool_calling).toBe(false);
  });
  it("does not invent vision or gateway capabilities from names", () => {
    expect(modelDocumentation("gpt-5.3-codex-spark")?.input_modalities).toEqual(["text"]);
    expect(modelDocumentation("future-vision-model")).toBeNull();
    expect(modelDocumentation("antigravity/claude-sonnet-4-6")).toBeNull();
  });
  it("distinguishes native modalities from what this client transports", () => {
    expect(modelDocumentation("mimo-v2.6-flash")?.input_modalities).toEqual(["text", "image", "video", "audio"]);
    expect(APP_INPUT_MODALITIES).toEqual(["text", "image"]);
  });
  it("exposes model-specific efforts and exact sources", () => {
    const doc = modelDocumentation("gpt-5.2-codex")!;
    expect(doc.reasoning.efforts).toEqual(["low", "medium", "high", "xhigh"]);
    expect(doc.reasoning.can_disable).toBe(false);
    expect(doc.source_urls).toContain("https://developers.openai.com/api/docs/models/gpt-5.2-codex");
    expect(doc.verified_at).toBe("2026-09-25");
  });
});
