const CODEX_OAUTH_MODEL_PREFIX = "openai-codex/";
const PLACEHOLDER_MODEL_IDS = new Set(["test-model", "dummy-model", "placeholder-model"]);

export function isPlaceholderModelId(id: string | null | undefined): boolean {
  return Boolean(id) && PLACEHOLDER_MODEL_IDS.has(id.trim().toLowerCase());
}

/**
 * Keep raw model ids for storage/API, but hide Codex OAuth provider prefix in UI.
 */
export function formatModelIdForDisplay(modelId: string | null | undefined): string {
  if (!modelId) return "";
  return modelId.startsWith(CODEX_OAUTH_MODEL_PREFIX)
    ? modelId.slice(CODEX_OAUTH_MODEL_PREFIX.length)
    : modelId;
}

export function displayModelLabel(m: {
  name: string;
  display_name?: string | null;
}): string {
  return formatModelIdForDisplay(m.display_name || m.name);
}
