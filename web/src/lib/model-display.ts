const SUBSCRIPTION_MODEL_PREFIXES = ["openai-codex/", "workbuddy-cn/", "workbuddy-global/", "workbuddy/", "antigravity/"];
const PLACEHOLDER_MODEL_IDS = new Set(["test-model", "dummy-model", "placeholder-model"]);

function normalizeModelText(value: string): string {
  return value.trim().toLocaleLowerCase().replace(/[\s_.:@/]+/g, "-").replace(/-+/g, "-");
}

/** True when two model labels identify the same model after display formatting. */
export function isSameModelReference(left: string | null | undefined, right: string | null | undefined): boolean {
  if (!left || !right) return false;
  return normalizeModelText(formatModelIdForDisplay(left)) === normalizeModelText(formatModelIdForDisplay(right));
}

/**
 * Remove the model name prefix from generated descriptions such as
 * `deepseek-v4.1-flash — WorkBuddy 订阅登录（无需 API Key）`.
 * Free-form descriptions that do not have this generated prefix are kept.
 */
export function cleanModelDescription(
  description: string | null | undefined,
  labels: Array<string | null | undefined>,
): string {
  const value = description?.trim() || "";
  if (!value) return "";
  const separator = /\s+[—–-]\s+|\s+[·|:]\s+/;
  const match = value.match(separator);
  if (!match || match.index === undefined) return value;
  const head = value.slice(0, match.index).trim();
  const generatedAuthDescription = /(?:订阅|OAuth 登录|无需 API Key)/i.test(value);
  if (generatedAuthDescription || labels.some((label) => label && isSameModelReference(head, label))) {
    return value.slice(match.index + match[0].length).trim();
  }
  return value;
}

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
