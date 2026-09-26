import type { AssistantBlock, Message, TaskItem } from "./types";

const REASONING_FIELDS = ["reasoning_content", "thinking", "reasoning", "thinking_text", "reasoning_details"];
const REASONING_TYPES = new Set(["thinking", "reasoning", "reasoning_content", "reasoning_text", "reasoning.text", "reasoning.summary", "summary_text"]);

function record(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown> : undefined;
}

/** Read only visible text; replay signatures and encrypted reasoning are not UI content. */
function reasoningText(value: unknown, depth = 0): string {
  if (typeof value === "string") return value;
  if (depth >= 8) return "";
  if (Array.isArray(value)) return value.map((part) => reasoningText(part, depth + 1)).filter(Boolean).join("\n");
  const part = record(value);
  if (!part || part.type === "reasoning.encrypted" || part.type === "redacted_thinking") return "";
  for (const key of ["text", "thinking", "reasoning_content", "summary_text", "summary", "content"]) {
    const text = reasoningText(part[key], depth + 1);
    if (text) return text;
  }
  return "";
}

/** Mirror the provider's visible reasoning projection for cold history loads. */
export function historyAssistantContent(message: Record<string, unknown>): { thinking: string; text: string } {
  let thinking = "";
  for (const field of REASONING_FIELDS) {
    thinking = reasoningText(message[field]);
    if (thinking) break;
  }
  if (typeof message.content === "string") return { thinking, text: message.content };
  const thoughts: string[] = [];
  const text: string[] = [];
  for (const value of Array.isArray(message.content) ? message.content : []) {
    if (typeof value === "string") { text.push(value); continue; }
    const part = record(value);
    if (!part || part.type === "reasoning.encrypted" || part.type === "redacted_thinking") continue;
    if (REASONING_TYPES.has(String(part.type)) || part.thought === true || typeof part.thought === "string") {
      thoughts.push(reasoningText(part) || (typeof part.thought === "string" ? part.thought : ""));
    } else if (part.type == null || part.type === "text" || part.type === "output_text") {
      text.push(reasoningText(part.text));
    }
  }
  return { thinking: thinking || thoughts.filter(Boolean).join("\n"), text: text.join("") };
}

export function normalizeTaskItems(payload: unknown): TaskItem[] {
  const rawItems = Array.isArray(payload) ? payload : record(payload)?.items;
  if (!Array.isArray(rawItems)) return [];
  return rawItems.map((raw, index) => {
    const item = record(raw) ?? {};
    const verification = typeof item.verification === "string"
      ? item.verification : record(item.verification)?.expected;
    return {
      content: [typeof raw === "string" ? raw : null, item.content, item.title, item.description]
        .find((value): value is string => typeof value === "string" && Boolean(value)) || `任务 ${index + 1}`,
      status: typeof item.status === "string" && item.status ? item.status : "pending",
      index: typeof item.index === "number" ? item.index : index,
      verification: typeof verification === "string" ? verification || undefined : undefined,
    };
  });
}

export function applyTaskStatusPatch(items: TaskItem[], index: number | null, status: string): TaskItem[] {
  if (index === null || !status) return items;
  return items.map((item) => item.index === index ? { ...item, status } : item);
}

/** Restore a separate snapshot on every successful task operation.
 * Server snapshots include the prefix outside the page; argument replay supports older servers.
 * Run again after joining pages so older creations can supply missing snapshots.
 */
export function restoreTaskLists(messages: Message[]): Message[] {
  let items: TaskItem[] = [];
  return messages.map((message) => {
    if (message.role !== "assistant") return message;
    const blocks = message.blocks.map((block): AssistantBlock => {
      if (block.type !== "tool_call" || block.status !== "success") return block;
      if (block.taskList?.length) {
        items = block.taskList;
      } else if (block.name === "task_create" && Array.isArray(block.args.subtasks)) {
        items = normalizeTaskItems(block.args.subtasks).map((item) => ({ ...item, status: "pending" }));
      } else if (block.name === "task_update" && typeof block.args.task_index === "number"
        && typeof block.args.status === "string") {
        items = applyTaskStatusPatch(items, block.args.task_index, block.args.status);
      } else return block;
      return items.length ? { ...block, taskList: items } : block;
    });
    // Legacy SSE used one mutable card per reply. Its final state must not
    // replace the individual historical snapshots or duplicate them.
    const hasSnapshots = blocks.some((block) => block.type === "tool_call" && block.taskList?.length);
    return { ...message, blocks: hasSnapshots ? blocks.filter((block) => block.type !== "task_list") : blocks };
  });
}
