import { apiDelete, apiGet, apiPost, apiPut } from "./api";
import type { ModelConfig, ModelProfileInput } from "./model-config";
import type { ModelInfo } from "./types";
import { useUIStore } from "@/stores/ui-store";

// A write is visible to every consumer only after the server commits it.
// Keep notification here so closing a form cannot suppress synchronization.
export async function commitModelChange<T>(request: Promise<T>): Promise<T> {
  const result = await request;
  useUIStore.getState().bumpModelProfiles();
  return result;
}

const reads = new Map<string, { version: number; request: Promise<unknown> }>();

function readModels<T>(path: string): Promise<T> {
  const version = useUIStore.getState().modelProfileVersion;
  const pending = reads.get(path);
  if (pending?.version === version) return pending.request as Promise<T>;
  const request = apiGet<T>(path, { direct: true }).finally(() => {
    if (reads.get(path)?.request === request) reads.delete(path);
  });
  reads.set(path, { version, request });
  return request;
}

export const fetchModelConfig = () => readModels<ModelConfig>("/config/models");
export const fetchAvailableModels = () => readModels<{ models: ModelInfo[] }>("/models");

function profilePayload(input: ModelProfileInput): ModelProfileInput {
  const body = { ...input, name: input.name.trim(), model: input.model.trim() };
  delete body.default_input_modalities;
  delete body.capability_metadata;
  delete body.effective_input_modalities;
  if (body.input_modalities !== undefined) delete body.vision_mode;
  if (!body.name || !body.model) throw new Error("请填写模型名称和 Model ID");
  if (body.max_context_tokens !== undefined && (
    !Number.isInteger(body.max_context_tokens) || body.max_context_tokens < 0 || body.max_context_tokens > 2147483647
  )) throw new Error("上下文上限必须是 0 到 2147483647 之间的整数");
  if (body.max_output_tokens !== undefined && (
    !Number.isInteger(body.max_output_tokens) || body.max_output_tokens < 0 || body.max_output_tokens > 2147483647
  )) throw new Error("最大输出长度必须是 0 到 2147483647 之间的整数");
  // An empty key preserves saved credentials; masked previews are never secrets.
  if (!body.api_key?.trim() || /[*•]{3,}/.test(body.api_key)) delete body.api_key;
  else body.api_key = body.api_key.trim();
  if (body.base_url) body.base_url = body.base_url.trim();
  return body;
}

export function createModelProfile(input: ModelProfileInput) {
  return commitModelChange(apiPost("/config/models/profiles", profilePayload(input), { direct: true }));
}

export function updateModelProfile(name: string, input: ModelProfileInput) {
  return commitModelChange(apiPut(`/config/models/profiles/${encodeURIComponent(name)}`, profilePayload(input), { direct: true }));
}

export function deleteModelProfile(name: string) {
  return commitModelChange(apiDelete(`/config/models/profiles/${encodeURIComponent(name)}`, { direct: true }));
}

export async function activateModelProfile(name: string) {
  const result = await apiPut("/models/active", { name }, { direct: true });
  const ui = useUIStore.getState();
  ui.setCurrentModel(name);
  ui.setVisionCapable(null);
  ui.bumpModelProfiles();
  return result;
}
