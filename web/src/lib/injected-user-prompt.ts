/**
 * Backend injects skill catalog / invocation as user-role messages for the model.
 * They must not appear in the chat UI or be used as the edit-resend payload.
 */

const SYSTEM_REMINDER_BLOCK_RE = /<system-reminder>[\s\S]*?<\/system-reminder>/g;
const AVAILABLE_SKILLS_BLOCK_RE = /<available_skills>[\s\S]*?<\/available_skills>/g;
// Current catalog is a <system-reminder>. The auto-match trailer is historical only.
const SKILL_CATALOG_TRAILER_RE =
  /\n*(?:This catalog contains summaries only|If the user already invoked a skill with \/name|If the user names a skill, or the task clearly matches a skill's description)[\s\S]*$/;
const SKILL_INVOCATION_BLOCK_RE =
  /<skill-invocation\b[\s\S]*?<\/skill-invocation>/g;

export function stripInjectedUserPromptBlocks(content: string): string {
  return content
    .replace(SYSTEM_REMINDER_BLOCK_RE, "")
    .replace(AVAILABLE_SKILLS_BLOCK_RE, "")
    .replace(SKILL_CATALOG_TRAILER_RE, "")
    .replace(SKILL_INVOCATION_BLOCK_RE, "")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

export function isPureInjectedUserPrompt(content: string): boolean {
  const trimmed = content.trim();
  if (!trimmed) return false;
  if (
    !trimmed.startsWith("<available_skills>") &&
    !trimmed.startsWith("<system-reminder>") &&
    !trimmed.startsWith("<skill-invocation")
  ) {
    return false;
  }
  return stripInjectedUserPromptBlocks(trimmed) === "";
}

export function isHiddenBackendUserMessage(msg: Record<string, unknown>): boolean {
  if (msg._ui_hidden === true) return true;
  const content = msg.content;
  if (typeof content === "string") {
    return isPureInjectedUserPrompt(content);
  }
  if (!Array.isArray(content)) return false;
  const text = content
    .filter((part): part is Record<string, unknown> => !!part && typeof part === "object")
    .filter((part) => part.type === "text" && typeof part.text === "string")
    .map((part) => part.text as string)
    .join("\n");
  return isPureInjectedUserPrompt(text);
}
