export type { ModelConfig, ProfileEntry } from "@/lib/model-config";

export interface ModelCapabilities {
  model: string;
  base_url: string;
  healthy: boolean | null;
  health_error: string;
  supports_tool_calling: boolean | null;
  supports_vision: boolean | null;
  supports_thinking: boolean | null;
  thinking_type: string;
  detected_at: string;
  probe_errors: Record<string, string>;
  manual_override: boolean;
  last_success_at?: string;
  fresh_until?: string;
  stale_until?: string;
  source?: string;
  evidence?: Record<string, string>;
  probe_version?: number;
}

export interface ProbeJobTarget {
  name: string;
  model: string;
  base_url: string;
  state: string;
  capabilities: ModelCapabilities | null;
}

export interface ProbeJobSnapshot {
  job_id: string;
  state: string;
  targets_total: number;
  targets_done: number;
  targets: ProbeJobTarget[];
}

export interface ProviderPreset {
  id: string;
  label: string;
  icon: string;
  model: string;
  base_url: string;
  protocol: string;
  thinking_mode: string;
  model_family: string;
  description: string;
  purchaseUrl: string;
}

export interface CodexModelEntry {
  modelId: string;
  publicId: string;
  profileName: string;
  displayName: string;
  proOnly: boolean;
}
