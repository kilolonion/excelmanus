import data from "../../../excelmanus/model_catalog.json";
import type { ModelModality } from "./model-config";

export interface ModelDocumentation {
  id: string;
  provider: string;
  context_window: number | null;
  max_input_tokens?: number | null;
  transport_input_limit?: number | null;
  max_output_tokens: number | null;
  input_modalities: ModelModality[];
  output_modalities: ModelModality[];
  tool_calling: boolean | null;
  status: string;
  source_urls: string[];
  verified_at: string;
  aliases?: string[];
  reasoning: { supported: boolean | null; dialect: string; can_disable: boolean | null; efforts: string[] };
  match_kind?: "exact" | "alias";
  notes?: string;
}

export interface CapabilityMetadata {
  source: string;
  verified_at: string | null;
  source_urls: string[];
  endpoint_verified: boolean;
  route_documented: boolean;
  status: string;
  documented: ModelDocumentation | null;
  application_input_modalities: ModelModality[];
  effective_input_modalities: ModelModality[];
  local_context_budget: number;
  context_source: string;
}

export const MODEL_CATALOG = data;
export const APP_INPUT_MODALITIES = data.application_input_modalities as ModelModality[];
export function providerDefaults(id: string) {
  return data.providers[id as keyof typeof data.providers];
}
const normalize = (s: string) => s.toLowerCase().replace(/[\s_.:@]+/g, "-").replace(/([a-z])(?=\d)/g, "$1-").replace(/-+/g, "-");
export function modelDocumentation(model: string): ModelDocumentation | null {
  if (/^(antigravity|workbuddy[^/]*)\//.test(model)) return null;
  const key = normalize(model.replace(/^openai-codex\//, ""));
  const exact = (data.models as unknown as ModelDocumentation[]).find((entry) => normalize(entry.id) === key);
  if (exact) return { ...exact, match_kind: "exact" };
  const alias = (data.models as unknown as ModelDocumentation[]).find((entry) =>
    (entry.aliases ?? []).some((id) => normalize(id) === key));
  if (alias) return { ...alias, match_kind: "alias" };
  const namespaced = /^(openai|anthropic|google|deepseek|qwen|minimax)\/(.+)$/.exec(model);
  if (namespaced) {
    const doc = modelDocumentation(namespaced[2]);
    if (doc?.provider === (namespaced[1] === "google" ? "gemini" : namespaced[1])) return doc;
  }
  return null;
}
