import type { ModelInfo } from "@/lib/types";
import { getProfileProviderId, getProviderGroupLabel } from "@/components/settings/model/helpers";

/** Match the provider groups shown in model settings, including custom gateways. */
export function modelProviderId(model: ModelInfo): string {
  if (model.provider?.trim()) return model.provider.trim();
  return getProfileProviderId({
    name: model.name,
    model: model.model,
    base_url: model.base_url || "",
    protocol: model.protocol || "auto",
    model_family: model.model_family || "",
  });
}

export function modelProviderLabels(models: ModelInfo[]): Map<string, string> {
  const labels = new Map<string, string>();
  for (const model of models) {
    const id = modelProviderId(model);
    if (!labels.has(id)) labels.set(id, getProviderGroupLabel(id, model.name));
  }
  return labels;
}

export function modelProviderLabel(models: ModelInfo[], providerId: string): string {
  return modelProviderLabels(models).get(providerId) || getProviderGroupLabel(providerId);
}
