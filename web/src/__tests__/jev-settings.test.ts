import { describe, expect, it } from "vitest";
import {
  EMPTY_JEV_DRAFT,
  JEV_GATEWAY_MODEL,
  JEV_MODELS,
  JEV_PROVIDER_KEYS,
  JEV_PROVIDER_PRESETS,
  JEV_ROLE_KEYS,
  buildJevPayload,
  draftFromJevPreset,
  jevCatalogOptions,
  jevChatEnabledFromRuntime,
  jevConfiguredFromRuntime,
  jevEntryDetail,
  jevEntryStatus,
  jevGateLabel,
  jevModelsForProvider,
  jevProviderDetail,
  jevRoleDetail,
  resolveJevCatalogModel,
  validateJevProvider,
} from "@/lib/jev-settings";

describe("jev-settings", () => {
  it("validates connection fields before submitting a provider", () => {
    const draft = draftFromJevPreset(JEV_PROVIDER_PRESETS[0]);
    expect(validateJevProvider(draft)).toBeNull();
    expect(validateJevProvider({ ...draft, name: " " })).toBeTruthy();
    expect(validateJevProvider({ ...draft, model: " " })).toBeTruthy();
    for (const base_url of ["", "api.typesafe.ai", "file:///tmp", "https://user:secret@example.test", "https://example.test?api_key=secret"]) {
      expect(validateJevProvider({ ...draft, base_url })).toBeTruthy();
    }
    expect(validateJevProvider({ ...draft, base_url: "http://localhost:9000/evaluate" })).toBeNull();
  });
  it("only sends known settings from drafts with extra fields", () => {
    const draft = { ...EMPTY_JEV_DRAFT, unknown_option: true, jev_mode_hint: false };
    expect(buildJevPayload(draft, EMPTY_JEV_DRAFT)).toEqual({ jev_mode_hint: false });
    expect(buildJevPayload(draft, EMPTY_JEV_DRAFT, JEV_ROLE_KEYS)).toEqual({ jev_mode_hint: false });
  });

  it("labels gates", () => {
    expect(jevGateLabel("off")).toBe("关闭");
    // 旧版 shadow 值归一为 enforce（仅观察态已移除）
    expect(jevGateLabel("shadow")).toBe("生效");
    expect(jevGateLabel("enforce")).toBe("生效");
    expect(jevGateLabel("unknown")).toBe("关闭");
  });

  it("keeps TypeSafe and Vercel as separate presets", () => {
    expect(JEV_PROVIDER_PRESETS.map((item) => item.id)).toEqual(["typesafe", "vercel"]);
    expect(draftFromJevPreset(JEV_PROVIDER_PRESETS[0]).protocol).toBe("typesafe");
    expect(draftFromJevPreset(JEV_PROVIDER_PRESETS[1]).protocol).toBe("gateway");
    expect(jevModelsForProvider(JEV_PROVIDER_PRESETS[0]).map((item) => item.id)).toEqual(
      JEV_MODELS.map((item) => item.id),
    );
    expect(jevModelsForProvider(JEV_PROVIDER_PRESETS[1]).map((item) => item.id)).toEqual([
      JEV_GATEWAY_MODEL,
    ]);
  });

  it("lists models from added providers only", () => {
    const typesafe = {
      id: "typesafe",
      name: "TypeSafe",
      protocol: "typesafe" as const,
      base_url: "https://api.typesafe.ai",
      model: "jev-1.13.0",
      configured: true,
      last4: "4f2a",
    };
    expect(resolveJevCatalogModel("jev-latest", [typesafe]).label).toBe("Jev Latest");
    expect(jevCatalogOptions("jev-1.13.0", [typesafe]).map((item) => item.id)).toEqual(
      JEV_MODELS.map((item) => item.id),
    );
    expect(jevCatalogOptions("jev-1.14.0", [typesafe])[0].id).toBe("jev-1.14.0");
    expect(jevCatalogOptions("", []).length).toBe(0);
  });

  it("shows idle entry before a key exists", () => {
    expect(jevEntryStatus({ configured: false, enabled: "shadow" })).toEqual({
      tone: "idle",
      chip: "未配置",
    });
    expect(jevEntryDetail({ configured: false, model: "jev-1.13.0" })).toBe(
      "添加密钥后即可使用",
    );
    expect(jevProviderDetail({ configured: false })).toBe("添加密钥后即可使用");
    expect(jevRoleDetail({ configured: false, model: "jev-latest" })).toBe(
      "Jev Latest · 先在供应商中配置密钥",
    );
  });

  it("keeps connection status separate from the master gate", () => {
    expect(jevEntryStatus({ configured: true, enabled: "off" })).toEqual({
      tone: "ready",
      chip: "已连接 · 关闭",
    });
    // 旧版 shadow 值按 enforce 处理（仅观察态已移除）
    expect(jevEntryStatus({ configured: true, enabled: "shadow" })).toEqual({
      tone: "enforce",
      chip: "生效",
    });
    expect(jevEntryStatus({ configured: true, enabled: "enforce" })).toEqual({
      tone: "enforce",
      chip: "生效",
    });
    expect(jevEntryDetail({ configured: true, last4: "4f2a", model: "jev-1.13.0" })).toBe(
      "jev-1.13.0 · 密钥 ···4f2a",
    );
    expect(jevProviderDetail({ configured: true, last4: "4f2a" })).toBe("密钥 ···4f2a");
    expect(jevRoleDetail({ configured: true, model: "jev-1.13.0", providerName: "TypeSafe" })).toBe(
      "Jev 1.13.0 · TypeSafe",
    );
  });

  it("chatEnabled requires gate on and a configured key", () => {
    expect(jevChatEnabledFromRuntime({ jev_enabled: "shadow" })).toBe(false);
    expect(jevChatEnabledFromRuntime({
      jev_experimental_enabled: true,
      jev_enabled: "shadow",
      ai_gateway: { configured: true },
    })).toBe(true);
    expect(jevChatEnabledFromRuntime({
      jev_experimental_enabled: true,
      jev_enabled: "enforce",
      jev_providers: [{ id: "custom-1", configured: true }],
    })).toBe(true);
    expect(jevChatEnabledFromRuntime({
      jev_experimental_enabled: false,
      jev_enabled: "enforce",
      ai_gateway: { configured: true },
    })).toBe(false);
    expect(jevChatEnabledFromRuntime({
      jev_enabled: "enforce",
      ai_gateway: { configured: true },
    })).toBe(false);
    // 总闸 off：密钥已配也完全断开
    expect(jevChatEnabledFromRuntime({
      jev_experimental_enabled: true,
      jev_enabled: "off",
      ai_gateway: { configured: true },
      jev_providers: [{ id: "vercel", configured: true }],
    })).toBe(false);
  });

  it("chatEnabled resolves the active provider like the backend", () => {
    const providers = [
      { id: "typesafe", configured: false },
      { id: "vercel", configured: true },
    ];
    // 未指定激活提供商 → 第一个已配密钥
    expect(jevChatEnabledFromRuntime({
      jev_experimental_enabled: true,
      jev_enabled: "shadow",
      jev_providers: providers,
    })).toBe(true);
    // 显式选中无密钥提供商、只有 vercel 旧版密钥 → 未连接
    expect(jevChatEnabledFromRuntime({
      jev_experimental_enabled: true,
      jev_enabled: "shadow",
      jev_active_provider: "typesafe",
      jev_providers: providers,
      ai_gateway: { configured: true },
      typesafe: { configured: false },
    })).toBe(false);
    // 显式选中无密钥提供商、直连旧版密钥仍在 → 已连接
    expect(jevChatEnabledFromRuntime({
      jev_experimental_enabled: true,
      jev_enabled: "shadow",
      jev_active_provider: "typesafe",
      jev_providers: [
        { id: "typesafe", configured: true },
        { id: "vercel", configured: true },
      ],
      ai_gateway: { configured: true },
      typesafe: { configured: true },
    })).toBe(true);
    // 显式 id 不存在 → 落到第一个已配密钥
    expect(jevChatEnabledFromRuntime({
      jev_experimental_enabled: true,
      jev_enabled: "shadow",
      jev_active_provider: "missing",
      jev_providers: providers,
    })).toBe(true);
  });

  it("configured follows the active provider rather than any configured provider", () => {
    expect(jevConfiguredFromRuntime({
      jev_active_provider: "typesafe",
      typesafe: { configured: false },
      ai_gateway: { configured: true },
      jev_providers: [
        { id: "typesafe", protocol: "typesafe", configured: false },
        { id: "vercel", protocol: "gateway", configured: true },
      ],
    })).toBe(false);
  });

  it("splits provider and role payloads", () => {
    const draft = {
      ...EMPTY_JEV_DRAFT,
      jev_model: "jev-latest",
      jev_timeout_seconds: 2,
      jev_active_provider: "vercel",
    };
    expect(buildJevPayload(draft, EMPTY_JEV_DRAFT, JEV_PROVIDER_KEYS)).toEqual({
      jev_timeout_seconds: 2,
      jev_active_provider: "vercel",
    });
    expect(buildJevPayload(draft, EMPTY_JEV_DRAFT, JEV_ROLE_KEYS)).toEqual({
      jev_model: "jev-latest",
      jev_active_provider: "vercel",
    });
  });
});
