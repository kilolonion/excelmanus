export interface ModelSection {
  api_key?: string;
  base_url?: string;
  model?: string;
  enabled?: boolean;
  protocol?: string;
}

export interface ProfileEntry {
  name: string;
  model: string;
  api_key: string;
  base_url: string;
  description: string;
  protocol: string;
  thinking_mode: string;
  model_family: string;
  custom_extra_body: string;
  custom_extra_headers: string;
}

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

export interface ModelConfig {
  profiles: ProfileEntry[];
  active?: string | null;
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
