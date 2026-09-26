import type { CapabilityMetadata } from "./model-catalog";

export type ModelModality = "text" | "image" | "video" | "audio";

export interface ProfileEntry {
  name: string;
  model: string;
  api_key: string;
  base_url: string;
  description: string;
  protocol: string;
  thinking_mode: string;
  service_tier: "" | "fast";
  model_family: string;
  custom_extra_body: string;
  custom_extra_headers: string;
  /** 智能匹配绑定的已知规范模型名（仅用于本地配置，不改写上游 Model ID） */
  canonical_model: string;
  /** 0 uses the automatic/global context budget. */
  max_context_tokens?: number;
  vision_mode?: "auto" | "true" | "false";
  input_modalities?: ModelModality[] | null;
  /** Read-only resolved defaults returned by the server. */
  default_input_modalities?: ModelModality[];
  max_output_tokens?: number;
  capability_metadata?: CapabilityMetadata;
  effective_input_modalities?: ModelModality[];
}

export interface ModelConfig {
  profiles: ProfileEntry[];
  active?: string | null;
  /** Jev 智能匹配开关（后端 EXCELMANUS_MODEL_CANONICAL_MATCH） */
  canonical_match_enabled?: boolean;
}

export type ModelProfileInput = Pick<ProfileEntry, "name" | "model"> & Partial<Omit<ProfileEntry, "name" | "model">> & { clone_from?: string };
