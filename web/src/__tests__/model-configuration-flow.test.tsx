// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useAdminModelSettings } from "@/components/settings/model/useAdminModelSettings";
import { useModelConfig } from "@/components/settings/model/useModelConfig";
import { useSubscriptionProvider } from "@/components/settings/model/useSubscriptionProvider";
import { useSubscriptionAccount } from "@/components/settings/model/useSubscriptionAccount";
import { useOAuthLogin } from "@/components/settings/model/useSubscriptionLogin";
import { useModelSelection } from "@/hooks/use-model-selection";
import { subscriptionOAuthExchange } from "@/lib/auth-api";
import { createModelProfile, deleteModelProfile, updateModelProfile } from "@/lib/model-config-api";
import { PlaceholderAlert } from "@/components/modals/PlaceholderAlert";
import { settingsCache } from "@/lib/settings-cache";
import { useOnboardingStore } from "@/stores/onboarding-store";
import { useUIStore } from "@/stores/ui-store";
import { EMPTY_PROFILE_DRAFT } from "@/components/settings/model/helpers";
import { ProviderGuideStep } from "@/components/onboarding/steps/ProviderGuideStep";
import { ProviderSection } from "@/components/settings/model/ProviderSection";
import { AdminModelContext } from "@/components/settings/model/admin-model-context";
import type { ProfileEntry } from "@/lib/model-config";

const { apiGet, apiPost, apiPut, apiDelete, listRemoteModels } = vi.hoisted(() => ({
  apiGet: vi.fn(), apiPost: vi.fn(), apiPut: vi.fn(), apiDelete: vi.fn(), listRemoteModels: vi.fn(),
}));
vi.mock("@/lib/api", () => ({ apiGet, apiPost, apiPut, apiDelete, listRemoteModels,
  checkModelPlaceholder: () => apiGet("/config/models/check-placeholder"),
  testModelConnection: vi.fn(), getManageToken: () => "", buildApiUrl: (path: string) => path,
}));

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((r) => { resolve = r; });
  return { promise, resolve };
}
const profile = (name: string, model = name): ProfileEntry => ({ ...EMPTY_PROFILE_DRAFT, name, model, api_key: "sk-***", base_url: "https://api.example.com/v1" });
const preset = { label: "Example", base_url: "https://example.com", protocol: "openai", thinking_mode: "auto", model_family: "" };
let profiles: ProfileEntry[];
let active: string;
let connected: boolean;
const read = async (path: string) => {
  if (path === "/config/models/check-placeholder") return { has_placeholder: profiles.length === 0, items: [] };
  if (path === "/config/models") return { profiles: [...profiles], active };
  if (path === "/models") return { models: profiles.map((p) => ({ ...p, active: p.name === active, supports_vision: true })) };
  if (path === "/config/models/capabilities/all") return { items: [] };
  if (path === "/thinking") return { effort: "medium", budget: 0, effective_budget: 0 };
  if (path.endsWith("/status")) return { provider: "antigravity", status: connected ? "connected" : "disconnected" };
  if (path.endsWith("/models")) return { models: [{ model: "gemini", public_model_id: "antigravity/gemini", profile_name: "antigravity/gemini" }] };
  throw new Error(`Unexpected read ${path}`);
};

beforeEach(() => {
  vi.clearAllMocks();
  settingsCache.clear();
  useUIStore.setState({ modelProfileVersion: 0, currentModel: "", visionCapable: null, configReady: null, configError: null });
  profiles = [profile("original")]; active = "original"; connected = false;
  apiGet.mockImplementation(read);
  apiPost.mockImplementation(async (path, body) => {
    if (path === "/config/models/profiles") profiles.push({ ...EMPTY_PROFILE_DRAFT, ...body, api_key: "sk-***" });
    if (path.endsWith("/oauth/exchange")) { connected = true; profiles.push(profile("My subscription", "antigravity/gemini")); }
    return {};
  });
  apiPut.mockImplementation(async (path, body) => {
    if (path === "/models/active") active = body.name;
    else {
      const name = decodeURIComponent(path.split("/profiles/")[1]);
      profiles = profiles.map((p) => p.name === name ? { ...p, ...body, api_key: "sk-***" } : p);
      if (active === name) active = body.name;
    }
    return {};
  });
  apiDelete.mockImplementation(async (path) => {
    const name = decodeURIComponent(path.split("/profiles/")[1]);
    profiles = profiles.filter((p) => p.name !== name);
    if (active === name) active = profiles[0]?.name || "";
    return {};
  });
  vi.spyOn(window, "open").mockReturnValue(null);
  HTMLElement.prototype.scrollIntoView = vi.fn();
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe("model configuration lifecycle", () => {
  it.each(["openai-codex/gpt-6-astra", "workbuddy-cn/claude-sonnet-4.6", "antigravity/gemini-3-pro"])(
    "edits and restores OAuth model metadata for %s", async (model) => {
      profiles = [{ ...profile("subscription", model), api_key: "", max_context_tokens: 65536, input_modalities: ["text", "image"], default_input_modalities: ["text", "image"] }];
      active = "subscription";
      function ProviderSettings() {
        const settings = useAdminModelSettings();
        return <AdminModelContext.Provider value={settings}><ProviderSection /></AdminModelContext.Provider>;
      }
      const view = render(<ProviderSettings />);
      fireEvent.click(await screen.findByTitle("编辑"));
      expect(screen.getByText("编辑 OAuth 模型")).toBeTruthy();
      expect(screen.queryByText("API Key")).toBeNull();
      expect((screen.getByRole("combobox", { name: "Model ID" }) as HTMLInputElement).disabled).toBe(true);
      expect((screen.getByRole("button", { name: "协议" }) as HTMLButtonElement).disabled).toBe(true);
      const context = screen.getByRole("spinbutton", { name: "上下文上限（tokens）" });
      expect((context as HTMLInputElement).value).toBe("65536");
      fireEvent.change(context, { target: { value: "131072" } });
      expect((screen.getByRole("checkbox", { name: "文本" }) as HTMLInputElement).checked).toBe(true);
      expect((screen.getByRole("checkbox", { name: "图片" }) as HTMLInputElement).checked).toBe(true);
      fireEvent.click(screen.getByRole("checkbox", { name: "文本" }));
      fireEvent.click(screen.getByRole("checkbox", { name: "图片" }));
      fireEvent.click(screen.getByRole("checkbox", { name: "视频" }));
      fireEvent.click(screen.getByRole("checkbox", { name: "音频" }));
      fireEvent.click(screen.getByRole("button", { name: /高级配置/ }));
      fireEvent.change(screen.getByRole("spinbutton", { name: "最大输出长度（tokens）" }), { target: { value: "8192" } });
      fireEvent.click(screen.getByRole("button", { name: "更新" }));
      await waitFor(() => expect(screen.queryByText("编辑 OAuth 模型")).toBeNull());
      expect(apiPut).toHaveBeenCalledWith("/config/models/profiles/subscription", expect.objectContaining({
        model, max_context_tokens: 131072, input_modalities: ["video", "audio"], max_output_tokens: 8192,
      }), { direct: true });
      expect(apiPut.mock.lastCall?.[1]).not.toHaveProperty("api_key");
      expect(apiPut.mock.lastCall?.[1]).not.toHaveProperty("default_input_modalities");
      view.unmount();
      render(<ProviderSettings />);
      fireEvent.click(await screen.findByTitle("编辑"));
      expect((screen.getByRole("spinbutton") as HTMLInputElement).value).toBe("131072");
      expect((screen.getByRole("checkbox", { name: "文本" }) as HTMLInputElement).checked).toBe(false);
      expect((screen.getByRole("checkbox", { name: "图片" }) as HTMLInputElement).checked).toBe(false);
      expect((screen.getByRole("checkbox", { name: "视频" }) as HTMLInputElement).checked).toBe(true);
      expect((screen.getByRole("checkbox", { name: "音频" }) as HTMLInputElement).checked).toBe(true);
      fireEvent.click(screen.getByRole("button", { name: /高级配置/ }));
      expect((screen.getByRole("spinbutton", { name: "最大输出长度（tokens）" }) as HTMLInputElement).value).toBe("8192");
      fireEvent.click(screen.getByRole("button", { name: "恢复自动" }));
      expect((screen.getByRole("checkbox", { name: "文本" }) as HTMLInputElement).checked).toBe(true);
      expect((screen.getByRole("checkbox", { name: "图片" }) as HTMLInputElement).checked).toBe(true);
      expect((screen.getByRole("checkbox", { name: "视频" }) as HTMLInputElement).checked).toBe(false);
      expect((screen.getByRole("checkbox", { name: "音频" }) as HTMLInputElement).checked).toBe(false);
      fireEvent.change(screen.getByRole("spinbutton", { name: "最大输出长度（tokens）" }), { target: { value: "" } });
      fireEvent.click(screen.getByRole("button", { name: "更新" }));
      await waitFor(() => expect(screen.queryByText("编辑 OAuth 模型")).toBeNull());
      expect(apiPut.mock.lastCall?.[1]).toMatchObject({ input_modalities: null, max_output_tokens: 0 });
    },
  );

  it("validates context limits and preserves the OAuth draft after a failed save", async () => {
    profiles = [{ ...profile("subscription", "antigravity/gemini-3-pro"), api_key: "" }];
    active = "subscription";
    function ProviderSettings() {
      const settings = useAdminModelSettings();
      return <AdminModelContext.Provider value={settings}><ProviderSection /></AdminModelContext.Provider>;
    }
    render(<ProviderSettings />);
    fireEvent.click(await screen.findByTitle("编辑"));
    const context = screen.getByRole("spinbutton");
    fireEvent.change(context, { target: { value: "-1" } });
    expect((screen.getByRole("button", { name: "更新" }) as HTMLButtonElement).disabled).toBe(true);
    expect(apiPut).not.toHaveBeenCalled();
    fireEvent.change(context, { target: { value: "32768" } });
    fireEvent.click(screen.getByRole("button", { name: /高级配置/ }));
    const output = screen.getByRole("spinbutton", { name: "最大输出长度（tokens）" });
    fireEvent.change(output, { target: { value: "1.5" } });
    expect((screen.getByRole("button", { name: "更新" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(output, { target: { value: "4096" } });
    apiPut.mockRejectedValueOnce(new Error("写入失败"));
    fireEvent.click(screen.getByRole("button", { name: "更新" }));
    await screen.findByText("写入失败");
    expect(screen.getByText("编辑 OAuth 模型")).toBeTruthy();
    expect((context as HTMLInputElement).value).toBe("32768");
    expect((output as HTMLInputElement).value).toBe("4096");
    fireEvent.click(screen.getByRole("button", { name: "更新" }));
    await waitFor(() => expect(screen.queryByText("编辑 OAuth 模型")).toBeNull());
    expect(profiles[0].max_context_tokens).toBe(32768);
    expect(profiles[0].max_output_tokens).toBe(4096);
  });

  it("keeps the diagnostics vision toggle consistent with profile modality", async () => {
    profiles = [{ ...profile("subscription", "openai-codex/gpt-6-astra"), api_key: "", max_context_tokens: 65536, input_modalities: ["text", "image", "audio", "video"] }];
    const { result } = renderHook(useAdminModelSettings);
    await waitFor(() => expect(result.current.config?.profiles).toHaveLength(1));
    act(() => result.current.beginEditProfile(profiles[0]));
    act(() => result.current.setProfileDraft((draft) => ({ ...draft, description: "未保存备注" })));
    await act(() => result.current.handleCapToggle("subscription", profiles[0].model, profiles[0].base_url, "supports_vision", false));
    expect(profiles[0].input_modalities).toEqual(["text", "audio", "video"]);
    expect(profiles[0].max_context_tokens).toBe(65536);
    expect(result.current.profileDraft.input_modalities).toEqual(["text", "audio", "video"]);
    expect(result.current.profileDraft.description).toBe("未保存备注");
    expect(apiPut.mock.lastCall?.[1]).not.toHaveProperty("api_key");
  });

  it("toggles Fast from the model row, persists both states and preserves connection settings", async () => {
    profiles = [{ ...profile("codex proxy", "gpt-6-astra"), protocol: "openai-responses", canonical_model: "gpt-6-astra", custom_extra_body: '{"temperature":0.5}' }];
    active = "codex proxy";
    function ProviderSettings() {
      const settings = useAdminModelSettings();
      return <AdminModelContext.Provider value={settings}><ProviderSection /></AdminModelContext.Provider>;
    }
    const { unmount } = render(<ProviderSettings />);
    fireEvent.click(await screen.findByText("codex proxy"));
    const toggle = screen.getByRole("button", { name: "快速模式", pressed: false });
    expect(toggle.querySelector("svg")?.getAttribute("fill")).toBe("none");
    expect(screen.getByRole("button", { name: "探测能力" })).toBeTruthy();
    fireEvent.click(toggle);
    await waitFor(() => expect(toggle.getAttribute("aria-busy")).toBe("false"));
    expect(screen.getByRole("button", { name: "快速模式", pressed: true }).querySelector("svg")?.getAttribute("fill")).toBe("currentColor");
    expect(apiPut).toHaveBeenCalledWith("/config/models/profiles/codex%20proxy", expect.objectContaining({
      service_tier: "fast", protocol: "openai-responses", canonical_model: "gpt-6-astra", custom_extra_body: '{"temperature":0.5}',
    }), { direct: true });
    expect(apiPut.mock.lastCall?.[1]).not.toHaveProperty("api_key");
    expect(apiPost).not.toHaveBeenCalled();

    unmount();
    render(<ProviderSettings />);
    fireEvent.click(await screen.findByText("codex proxy"));
    fireEvent.click(screen.getByRole("button", { name: "快速模式", pressed: true }));
    await waitFor(() => expect(screen.getByRole("button", { name: "快速模式", pressed: false }).querySelector("svg")?.getAttribute("fill")).toBe("none"));
    expect(profiles[0].service_tier).toBe("");
  });

  it("serializes Fast toggles and keeps the saved mode unchanged on failure", async () => {
    profiles = [profile("original", "gpt-6-astra")];
    const pending = deferred<object>();
    apiPut.mockReturnValueOnce(pending.promise);
    const { result } = renderHook(useAdminModelSettings);
    await waitFor(() => expect(result.current.config?.profiles).toHaveLength(1));
    const entry = result.current.config!.profiles[0];
    let saving!: Promise<void>;
    act(() => {
      saving = result.current.handleToggleFastMode(entry);
      void result.current.handleToggleFastMode(entry);
    });
    expect(apiPut).toHaveBeenCalledTimes(1);
    expect(result.current.profileBusy).toBe(true);
    await act(async () => { profiles = [{ ...entry, service_tier: "fast" }]; pending.resolve({}); await saving; });
    expect(result.current.config?.profiles[0].service_tier).toBe("fast");
    apiPut.mockRejectedValueOnce(new Error("保存失败"));
    await act(() => result.current.handleToggleFastMode(result.current.config!.profiles[0]));
    expect(result.current.config?.profiles[0].service_tier).toBe("fast");
    expect(result.current.profileError).toBe("保存失败");
    expect(result.current.profileBusy).toBe(false);
  });

  it("keeps an open editor's Fast setting in sync without overwriting its unsaved fields", async () => {
    profiles = [profile("original", "gpt-6-astra")];
    const { result } = renderHook(useAdminModelSettings);
    await waitFor(() => expect(result.current.config?.profiles).toHaveLength(1));
    act(() => result.current.beginEditProfile(result.current.config!.profiles[0]));
    act(() => result.current.setProfileDraft((draft) => ({ ...draft, description: "未保存的说明" })));
    await act(() => result.current.handleToggleFastMode(result.current.config!.profiles[0]));
    expect(result.current.profileDraft.service_tier).toBe("fast");
    expect(result.current.profileDraft.description).toBe("未保存的说明");
    expect(profiles[0].description).toBe("");
    await act(() => result.current.handleUpdateProfile("original"));
    expect(profiles[0]).toMatchObject({ service_tier: "fast", description: "未保存的说明" });
  });

  it("saves Fast mode with the selected model profile", async () => {
    const { result } = renderHook(useAdminModelSettings);
    await waitFor(() => expect(result.current.config?.profiles).toHaveLength(1));
    act(() => {
      result.current.setNewProfile(true);
      result.current.setProfileDraft({ ...profile("fast-gpt", "gpt-5.6-sol"), service_tier: "fast" });
    });
    await act(() => result.current.handleAddProfile());
    expect(apiPost).toHaveBeenCalledWith(
      "/config/models/profiles",
      expect.objectContaining({ name: "fast-gpt", service_tier: "fast" }),
      { direct: true },
    );
    await waitFor(() => expect(result.current.config?.profiles.find((p) => p.name === "fast-gpt")?.service_tier).toBe("fast"));
  });

  it("keeps a failed draft and credentials editable, then updates every picker after save, activation, rename and deletion", async () => {
    const { result } = renderHook(() => ({ settings: useAdminModelSettings(), picker: useModelSelection() }));
    await waitFor(() => expect(result.current.settings.config?.profiles).toHaveLength(1));
    const draft = { ...profile("new"), api_key: "test-secret" };
    act(() => { result.current.settings.setNewProfile(true); result.current.settings.setProfileDraft(draft); });
    apiPost.mockRejectedValueOnce(new Error("保存暂时失败"));
    await act(() => result.current.settings.handleAddProfile());
    expect(result.current.settings.newProfile).toBe(true);
    expect(result.current.settings.profileDraft).toEqual(draft);
    expect(result.current.settings.profileError).toBe("保存暂时失败");
    expect(useUIStore.getState().modelProfileVersion).toBe(0);
    expect(JSON.stringify(settingsCache.get("/config/models"))).not.toContain("test-secret");
    await act(() => result.current.settings.handleAddProfile());
    await waitFor(() => expect(result.current.picker.models).toHaveLength(2));
    expect(result.current.settings.newProfile).toBe(false);
    expect(result.current.settings.profileDraft.api_key).toBe("");
    await act(() => result.current.picker.selectModel("new"));
    await waitFor(() => expect(result.current.settings.config?.active).toBe("new"));
    act(() => {
      result.current.settings.setEditingProfile("new");
      result.current.settings.setProfileDraft({ ...draft, name: "renamed", api_key: "" });
    });
    await act(() => result.current.settings.handleUpdateProfile("new"));
    await waitFor(() => expect(result.current.picker.currentModel).toBe("renamed"));
    expect(apiPut).toHaveBeenLastCalledWith("/config/models/profiles/new", expect.not.objectContaining({ api_key: expect.anything() }), { direct: true });
    await act(() => result.current.settings.handleDeleteProfile("renamed"));
    await waitFor(() => expect(result.current.picker.currentModel).toBe("original"));
    expect(result.current.picker.models).toHaveLength(1);
  });

  it("serializes rapid saves and does not close an editor until the write succeeds", async () => {
    const pending = deferred<object>();
    const { result } = renderHook(useAdminModelSettings);
    await waitFor(() => expect(result.current.config).not.toBeNull());
    act(() => { result.current.setNewProfile(true); result.current.setProfileDraft(profile("new")); });
    apiPost.mockReturnValueOnce(pending.promise);
    let save!: Promise<string | null>;
    act(() => { save = result.current.handleAddProfile(); void result.current.handleAddProfile(); });
    expect(apiPost).toHaveBeenCalledTimes(1);
    expect(result.current.addingProfile).toBe(true);
    expect(result.current.newProfile).toBe(true);
    await act(async () => { pending.resolve({}); await save; });
    expect(result.current.newProfile).toBe(false);
  });

  it("does not let a stale configuration read overwrite a committed change", async () => {
    const stale = deferred<object>();
    apiGet.mockImplementationOnce(() => stale.promise);
    const { result } = renderHook(useModelConfig);
    profiles = [profile("latest")];
    act(() => useUIStore.getState().bumpModelProfiles());
    await waitFor(() => expect(result.current.config?.profiles[0].name).toBe("latest"));
    await act(async () => stale.resolve({ profiles: [profile("stale")] }));
    expect(result.current.config?.profiles[0].name).toBe("latest");
    expect(settingsCache.get<{ profiles: ProfileEntry[] }>("/config/models")?.profiles[0].name).toBe("latest");
  });

  it("reports load failures and recovers explicitly", async () => {
    apiGet.mockRejectedValueOnce(new Error("离线"));
    const { result } = renderHook(useModelConfig);
    await waitFor(() => expect(result.current.loadError).toBe("离线"));
    await act(() => result.current.fetchConfig(true));
    expect(result.current.loadError).toBeNull();
    expect(result.current.config?.profiles).toHaveLength(1);
  });

  it("never writes masked credential previews back to the backend", async () => {
    await updateModelProfile("original", { name: "original", model: "new-id", api_key: "sk-********tail" });
    expect(apiPut.mock.lastCall?.[1]).not.toHaveProperty("api_key");
  });

  it("ignores model discovery completing after the editor was closed", async () => {
    const pending = deferred<{ models: { id: string }[] }>();
    listRemoteModels.mockReturnValueOnce(pending.promise);
    const { result } = renderHook(useAdminModelSettings);
    await waitFor(() => expect(result.current.config).not.toBeNull());
    let finding!: Promise<void>;
    act(() => { finding = result.current.handleFetchRemoteModels("_profile", "https://example.com"); });
    act(() => result.current.resetProfileFormUi());
    await act(async () => { pending.resolve({ models: [{ id: "old" }] }); await finding; });
    expect(result.current.remoteModels).toEqual([]);
    expect(result.current.fetchingModels).toBe(false);
  });
});

describe("subscription configuration lifecycle", () => {
  it("propagates OAuth-created profiles to configuration and chat without remounting", async () => {
    const { result } = renderHook(() => {
      const config = useModelConfig();
      const account = useSubscriptionProvider("antigravity", config.config?.profiles || [], preset);
      const login = useOAuthLogin({ name: "test", messageType: "callback", validateUrl: (url) => url,
        start: async () => ({ authorize_url: "https://accounts.google.com/auth", redirect_uri: "http://localhost/callback", state: "current", mode: "paste" }),
        exchange: (code, state) => subscriptionOAuthExchange("antigravity", code, state), onConnected: account.completeLogin, onError: account.setError, lock: account.lock,
      });
      return { config, account, login, picker: useModelSelection() };
    });
    await waitFor(() => expect(result.current.account.status?.status).toBe("disconnected"));
    await act(() => result.current.login.start());
    expect(useUIStore.getState().modelProfileVersion).toBe(0);
    act(() => result.current.login.setPasteUrl("http://localhost/callback?code=ok&state=current"));
    await act(() => result.current.login.submit());
    await waitFor(() => expect(result.current.picker.models).toHaveLength(2));
    expect(result.current.config.config?.profiles).toHaveLength(2);
    expect(result.current.account.status?.status).toBe("connected");
    expect(result.current.account.profileForModel({ model: "gemini" })?.name).toBe("My subscription");
  });

  it("retains confirmed authorization when reloading account details fails", async () => {
    const { result } = renderHook(() => useSubscriptionAccount("antigravity"));
    await waitFor(() => expect(result.current.loading).toBe(false));
    apiGet.mockRejectedValueOnce(new Error("读取失败"));
    await act(() => result.current.completeLogin());
    expect(result.current.status?.status).toBe("connected");
    expect(result.current.statusError).toBe(true);
  });

  it("ignores a status response arriving after a disconnect", async () => {
    const stale = deferred<object>();
    apiGet.mockImplementationOnce(() => stale.promise);
    const { result } = renderHook(() => useSubscriptionAccount("antigravity"));
    act(() => result.current.setStatus({ provider: "antigravity", status: "disconnected" }));
    await act(async () => stale.resolve({ provider: "antigravity", status: "connected" }));
    expect(result.current.status?.status).toBe("disconnected");
  });

  it("removes renamed subscription profiles by their actual name", async () => {
    connected = true;
    profiles = [profile("My renamed profile", "antigravity/gemini")];
    const { result } = renderHook(() => useSubscriptionProvider("antigravity", profiles, preset));
    await waitFor(() => expect(result.current.status?.status).toBe("connected"));
    const entry = result.current.profileForModel({ model: "gemini" });
    expect(entry?.name).toBe("My renamed profile");
    await act(() => result.current.handleRemoveModel(entry!.name));
    expect(apiDelete).toHaveBeenCalledWith("/config/models/profiles/My%20renamed%20profile", { direct: true });
  });

  it("rejects non-object token JSON without sending it", async () => {
    const { result } = renderHook(() => useSubscriptionProvider("antigravity", [], preset));
    await waitFor(() => expect(result.current.loading).toBe(false));
    act(() => result.current.setTokenInput("null"));
    await act(() => result.current.handleManualConnect());
    expect(apiPost).not.toHaveBeenCalled();
    expect(result.current.error).toContain("JSON 对象");
  });
});

const guide = { ...preset, id: "custom", description: "Test provider", pricing: "", purchaseUrl: "https://example.com", model: "test-model", steps: [] };
describe("onboarding saves", () => {
  it.each([500, 401, 400])("does not overwrite an existing profile after a %s creation error", async (status) => {
    apiPost.mockRejectedValueOnce(Object.assign(new Error("无法保存"), { status }));
    render(<ProviderGuideStep provider={guide} onBack={vi.fn()} onComplete={vi.fn()} />);
    fireEvent.change(screen.getByPlaceholderText("粘贴你的 API Key"), { target: { value: "test-key" } });
    fireEvent.click(screen.getByRole("button", { name: /保存/ }));
    await screen.findByText("无法保存");
    expect(apiPut).not.toHaveBeenCalled();
  });

  it("updates only a confirmed name conflict, then activates the saved model", async () => {
    const complete = vi.fn();
    apiPost.mockRejectedValueOnce(Object.assign(new Error("模型名称已存在"), { status: 409 }));
    render(<ProviderGuideStep provider={guide} onBack={vi.fn()} onComplete={complete} />);
    fireEvent.change(screen.getByPlaceholderText("粘贴你的 API Key"), { target: { value: "test-key" } });
    fireEvent.click(screen.getByRole("button", { name: /保存/ }));
    await waitFor(() => expect(complete).toHaveBeenCalledOnce());
    expect(apiPut.mock.calls.map(([path]) => path)).toEqual(["/config/models/profiles/Example", "/models/active"]);
  });
});


describe("chat readiness after configuring models", () => {
  it("unblocks chat after the first model is saved and blocks it after the last is removed", async () => {
    profiles = []; active = "";
    render(<PlaceholderAlert />);
    await waitFor(() => expect(useUIStore.getState().configReady).toBe(false));
    await act(() => createModelProfile(profile("first")));
    await waitFor(() => expect(useUIStore.getState().configReady).toBe(true));
    expect(useOnboardingStore.getState().backendConfigured).toBe(true);
    await act(() => deleteModelProfile("first"));
    await waitFor(() => expect(useUIStore.getState().configReady).toBe(false));
  });

  it("does not treat an unavailable readiness check as a configured model", async () => {
    apiGet.mockRejectedValueOnce(new Error("offline"));
    render(<PlaceholderAlert />);
    await waitFor(() => expect(useUIStore.getState().configError).toContain("检查失败"));
    expect(useUIStore.getState().configReady).toBeNull();
    act(() => useUIStore.getState().bumpModelProfiles());
    await waitFor(() => expect(useUIStore.getState().configReady).toBe(true));
    expect(useUIStore.getState().configError).toBeNull();
  });
});
