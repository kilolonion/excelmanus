/** 对话流不展示这些块，避免闪出旧「第 N 轮 / Smart Route」。 */

type ChromeBlock = {
  type: string;
  variant?: string;
  content?: string;
};

export function isHiddenAssistantChrome(block: ChromeBlock): boolean {
  if (block.type === "iteration") return true;
  if (block.type === "status" && block.variant === "route") return true;
  return false;
}

export function blockHasVisibleOutput(block: ChromeBlock): boolean {
  if (isHiddenAssistantChrome(block)) return false;
  if (block.type === "token_stats") return false;
  if (block.type === "text") return Boolean(block.content?.trim());
  return true;
}

export function hasVisibleAssistantOutput(blocks: ChromeBlock[]): boolean {
  return blocks.some(blockHasVisibleOutput);
}

export type AssistantLeadingSurface = "waiting" | "text" | "bubble";

/** 首块可见内容的表面类型，用来分别对齐头像 / 时间戳与纯文本或工具气泡。 */
export function getAssistantLeadingSurface(
  blocks: ChromeBlock[],
  isStreaming: boolean,
): AssistantLeadingSurface {
  if (!hasVisibleAssistantOutput(blocks)) {
    return isStreaming ? "waiting" : "text";
  }
  for (const block of blocks) {
    if (!blockHasVisibleOutput(block)) continue;
    return block.type === "text" ? "text" : "bubble";
  }
  return "text";
}
