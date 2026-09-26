import { describe, expect, it } from "vitest";
import {
  findProfileByModelId,
  filterDetectedModels,
  filterModelPickerList,
  formatProviderModelLabel,
  getProfileProviderId,
  groupProfilesByProvider,
  isMaskedApiKey,
  isProfileConnected,
  pickDefaultProfile,
  siblingDraftFromProfile,
  uniqueSiblingProfileName,
} from "@/components/settings/model/helpers";
import type { ProfileEntry } from "@/components/settings/model/types";

function profile(partial: Partial<ProfileEntry> & Pick<ProfileEntry, "name" | "model">): ProfileEntry {
  return {
    api_key: "",
    base_url: "",
    description: "",
    protocol: "auto",
    thinking_mode: "auto",
    service_tier: "",
    model_family: "",
    custom_extra_body: "",
    custom_extra_headers: "",
    canonical_model: "",
    ...partial,
  };
}

describe("groupProfilesByProvider", () => {
  it("groups openai and deepseek profiles and keeps custom ones separate", () => {
    const profiles = [
      profile({ name: "gpt5", model: "gpt-6-astra", base_url: "https://api.openai.com/v1", api_key: "sk-test" }),
      profile({ name: "gpt-mini", model: "gpt-4.1-mini", base_url: "https://api.openai.com/v1", api_key: "sk-test" }),
      profile({ name: "ds", model: "deepseek-flash", base_url: "https://api.deepseek.com/v1", api_key: "sk-ds" }),
      profile({ name: "local", model: "my-local", base_url: "http://127.0.0.1:1234/v1" }),
    ];

    const groups = groupProfilesByProvider(profiles);
    expect(groups.map((g) => g.id)).toEqual(["openai", "deepseek", "custom:127.0.0.1"]);
    expect(groups[0].profiles.map((p) => p.name)).toEqual(["gpt5", "gpt-mini"]);
    expect(groups[0].label).toBe("OpenAI");
    expect(groups[0].logoId).toBe("openai");
    expect(groups[1].label).toBe("DeepSeek");
    expect(groups[1].logoId).toBe("deepseek");
    expect(groups[2].label).toBe("local");
    expect(groups[2].logoId).toBe("unknown");
  });

  it("keeps custom endpoints as their own provider even when the model looks branded", () => {
    const profiles = [
      profile({
        name: "Claude 官方",
        model: "claude-sonnet-5",
        base_url: "https://proxy.example.com/v1",
        description: "公司专用账号",
        api_key: "sk-test",
      }),
      profile({
        name: "Claude 备用",
        model: "claude-opus-4",
        base_url: "https://proxy.example.com/v1",
        api_key: "sk-test",
      }),
    ];
    const groups = groupProfilesByProvider(profiles);
    expect(groups).toHaveLength(1);
    expect(groups[0].id).toBe("custom:proxy.example.com");
    expect(groups[0].label).toBe("Claude 官方");
    expect(groups[0].logoId).toBe("anthropic");
    expect(groups[0].profiles.map((p) => p.name)).toEqual(["Claude 官方", "Claude 备用"]);
    expect(formatProviderModelLabel(profiles[0])).toBe("Claude 官方 · claude-sonnet-5");
  });

  it("uses a branded logo for a custom MiMo connection while keeping its configured group name", () => {
    const groups = groupProfilesByProvider([
      profile({
        name: "小米 MiMo",
        model: "mimo-v2.6-pro-ultraspeed",
        base_url: "https://proxy.example.com/v1",
        api_key: "sk-test",
      }),
    ]);
    expect(groups[0].id).toBe("custom:proxy.example.com");
    expect(groups[0].label).toBe("小米 MiMo");
    expect(groups[0].logoId).toBe("mimo");
  });

  it("still groups official endpoints by brand", () => {
    const anthropic = profile({
      name: "Claude 官方",
      model: "claude-sonnet-5",
      base_url: "https://api.anthropic.com",
    });
    expect(getProfileProviderId(anthropic)).toBe("anthropic");
    expect(formatProviderModelLabel(anthropic)).toBe("Anthropic · claude-sonnet-5");
  });

  it("formats provider · model labels and hides Codex prefix", () => {
    const openai = profile({ name: "gpt5", model: "gpt-6-astra", base_url: "https://api.openai.com/v1" });
    const codex = profile({ name: "codex", model: "openai-codex/gpt-6-astra" });
    expect(formatProviderModelLabel(openai)).toBe("OpenAI · gpt-6-astra");
    expect(formatProviderModelLabel(codex)).toBe("OpenAI Codex · gpt-6-astra");
  });

  it("treats Codex and keyed profiles as connected", () => {
    expect(isProfileConnected(profile({ name: "a", model: "gpt-6-astra", api_key: "sk-1" }))).toBe(true);
    expect(isProfileConnected(profile({ name: "b", model: "openai-codex/gpt-6-astra" }))).toBe(true);
    expect(isProfileConnected(profile({ name: "c", model: "gpt-6-astra" }))).toBe(false);
  });

  it("picks the active profile in a group when setting default", () => {
    const group = groupProfilesByProvider([
      profile({ name: "one", model: "gpt-6-astra", base_url: "https://api.openai.com/v1" }),
      profile({ name: "two", model: "gpt-4.1-mini", base_url: "https://api.openai.com/v1" }),
    ])[0];
    expect(pickDefaultProfile(group, "two")?.name).toBe("two");
    expect(pickDefaultProfile(group, "missing")?.name).toBe("one");
    expect(getProfileProviderId(group.profiles[0])).toBe("openai");
    expect(findProfileByModelId(group.profiles, "gpt-4.1-mini")?.name).toBe("two");
  });
});

describe("isMaskedApiKey", () => {
  it("detects masked and short placeholder keys", () => {
    expect(isMaskedApiKey("")).toBe(false);
    expect(isMaskedApiKey("****")).toBe(true);
    expect(isMaskedApiKey("sk-test-secret-aaaaaaaa")).toBe(false);
    expect(isMaskedApiKey("sk-c********aaaa")).toBe(true);
  });
});

describe("filterDetectedModels", () => {
  const models = [
    { id: "acme/model-2" },
    { id: "acme/model-1" },
    { id: "gpt-4.1" },
  ];

  it("shows every detected model until the list search has a query", () => {
    expect(filterDetectedModels(models, "")).toHaveLength(3);
    expect(filterDetectedModels(models, "  ")).toEqual(models);
  });

  it("does not treat the current Model ID as an implicit filter", () => {
    expect(filterDetectedModels(models, "")).toEqual(models);
    expect(filterDetectedModels(models, "model-1").map((m) => m.id)).toEqual(["acme/model-1"]);
  });
});

describe("filterModelPickerList", () => {
  const models = [
    { id: "acme/model-2" },
    { id: "acme/model-1" },
    { id: "gpt-4.1" },
  ];

  it("shows the full list when the input is an already selected model id", () => {
    expect(filterModelPickerList(models, "acme/model-2")).toEqual(models);
  });

  it("filters when the input is a partial query", () => {
    expect(filterModelPickerList(models, "model-1").map((m) => m.id)).toEqual(["acme/model-1"]);
  });
});

describe("uniqueSiblingProfileName", () => {
  it("prefers the short model id then a numbered suffix", () => {
    expect(uniqueSiblingProfileName("acme/model-2", ["acme-demo"])).toBe("model-2");
    expect(uniqueSiblingProfileName("acme/model-2", ["model-2", "acme-demo"])).toBe("acme/model-2");
    expect(uniqueSiblingProfileName("acme/model-2", ["model-2", "acme/model-2"])).toBe("model-2 2");
  });
});

describe("siblingDraftFromProfile", () => {
  it("copies connection fields and leaves model and key blank", () => {
    const draft = siblingDraftFromProfile(profile({
      name: "acme-demo",
      model: "acme/model-2",
      api_key: "sk-secret",
      base_url: "https://api.example.com/v1",
      protocol: "openai",
      description: "imported",
    }));
    expect(draft).toMatchObject({
      name: "",
      model: "",
      api_key: "",
      base_url: "https://api.example.com/v1",
      protocol: "openai",
      description: "imported",
    });
  });
});
