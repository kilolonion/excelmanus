const SUBSCRIPTION_MODEL_PREFIXES = ["openai-codex/", "workbuddy-cn/", "workbuddy-global/", "workbuddy/", "antigravity/"];
const PLACEHOLDER_MODEL_IDS = new Set(["test-model", "dummy-model", "placeholder-model"]);

export function isPlaceholderModelId(id: string | null | undefined): boolean {
  if (typeof id !== "string") return false;
  return PLACEHOLDER_MODEL_IDS.has(id.trim().toLowerCase());
}

/**
 * Keep raw model ids for storage/API, but hide subscription OAuth provider prefix in UI.
 */
export function formatModelIdForDisplay(modelId: string | null | undefined): string {
  if (!modelId) return "";
  const prefix = SUBSCRIPTION_MODEL_PREFIXES.find((p) => modelId.startsWith(p));
  return prefix ? modelId.slice(prefix.length) : modelId;
}

export function displayModelLabel(m: {
  name: string;
  display_name?: string | null;
}): string {
  return formatModelIdForDisplay(m.display_name || m.name);
}
