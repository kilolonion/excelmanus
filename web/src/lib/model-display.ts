const CODEX_OAUTH_MODEL_PREFIX = "openai-codex/";

/**
 * Keep raw model ids for storage/API, but hide Codex OAuth provider prefix in UI.
 */
export function formatModelIdForDisplay(modelId: string | null | undefined): string {
  if (!modelId) return "";
  return modelId.startsWith(CODEX_OAUTH_MODEL_PREFIX)
    ? modelId.slice(CODEX_OAUTH_MODEL_PREFIX.length)
    : modelId;
}
