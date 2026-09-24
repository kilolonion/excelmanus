import type { AssistantBlock, Message } from "@/lib/types";

export type FailureActionType = "retry" | "open_settings" | "copy_diagnostic";

export type FailureAction = {
  type: FailureActionType;
  label: string;
};

/** 重试同一请求在结构上不可能成功的错误码。 */
export const NO_RETRY_CODES = new Set(["session_not_found"]);

export const FAILURE_ACTION_LABELS: Record<FailureActionType, string> = {
  retry: "继续",
  open_settings: "检查模型设置",
  copy_diagnostic: "复制诊断 ID",
};

export const FAILURE_STAGE_LABELS: Record<string, string> = {
  initializing: "初始化",
  preparing: "准备请求",
  routing: "任务分析",
  calling_llm: "模型通信",
  streaming: "流式接收",
  connecting: "建立连接",
  reconnecting: "重新连接",
  queued: "排队等待",
  save_command: "保存命令",
  subscribe_resume: "重连恢复",
  llm_retrying: "模型重试",
  tool_execution: "工具执行",
  context_building: "上下文构建",
  session_init: "会话初始化",
  file_processing: "文件处理",
  sandbox_exec: "沙箱执行",
  mcp_call: "MCP 调用",
};

const ADMIN_CONTACT_RE = /如问题持续，请联系(系统)?管理员。?/g;

/** 会话仍在时，用户应能一键重发原消息（含附件）。 */
export function canOfferRetry(code?: string): boolean {
  return !code || !NO_RETRY_CODES.has(code);
}

export function displayFailureTitle(code: string, title: string): string {
  if (code === "internal_error" && (!title || title === "内部错误")) {
    return "回复未完成";
  }
  return title || "回复未完成";
}

export function displayFailureMessage(message: string): string {
  const cleaned = message.replace(ADMIN_CONTACT_RE, "").trim();
  return cleaned || "服务处理出现异常，请稍后重试。";
}

/**
 * 合成恢复动作，不盲从后端 actions。
 * retryable 只影响主按钮顺序：暂态失败「继续」在前，配置类失败「检查设置」在前。
 */
export function resolveFailureActions(opts: {
  code?: string;
  retryable?: boolean;
}): FailureAction[] {
  const copy: FailureAction = {
    type: "copy_diagnostic",
    label: FAILURE_ACTION_LABELS.copy_diagnostic,
  };
  if (!canOfferRetry(opts.code)) {
    return [copy];
  }
  const retry: FailureAction = {
    type: "retry",
    label: FAILURE_ACTION_LABELS.retry,
  };
  const settings: FailureAction = {
    type: "open_settings",
    label: opts.code === "model_oauth_expired" ? "重新登录订阅账号" : FAILURE_ACTION_LABELS.open_settings,
  };
  return opts.retryable ? [retry, settings, copy] : [settings, retry, copy];
}

export function isFailureGuidanceBlock(
  block: AssistantBlock,
): block is Extract<AssistantBlock, { type: "failure_guidance" }> {
  return block.type === "failure_guidance";
}

type FailureGuidanceBlock = Extract<AssistantBlock, { type: "failure_guidance" }>;

const OAUTH_LOGIN_MESSAGE = "当前订阅登录凭证已失效或被撤销。请在模型设置的「订阅」页面重新登录对应账号，再继续对话。";

/** 兼容旧历史和浏览器缓存里把 OAuth 撤销显示成 API Key 错误的卡片。 */
function normalizeFailureGuidance(block: FailureGuidanceBlock): FailureGuidanceBlock {
  const oauthExpired = block.code === "model_oauth_expired" || block.title === "订阅登录已失效"
    || /token_revoked|invalidated oauth token|expired oauth token|oauth token expired/i.test(block.message);
  if (!oauthExpired || (block.code === "model_oauth_expired" && block.message === OAUTH_LOGIN_MESSAGE
    && block.title === "订阅登录已失效" && block.category === "model" && !block.retryable)) return block;
  return {
    ...block, category: "model", code: "model_oauth_expired", title: "订阅登录已失效",
    message: OAUTH_LOGIN_MESSAGE, retryable: false,
    actions: resolveFailureActions({ code: "model_oauth_expired", retryable: false }),
  };
}

/** 诊断 ID 在历史文本和 SSE 之间保持稳定，category 在文本恢复时会丢失。 */
export function isSameFailure(a: FailureGuidanceBlock, b: FailureGuidanceBlock): boolean {
  if (a.diagnosticId && b.diagnosticId) return a.diagnosticId === b.diagnosticId;
  return !!a.message && a.title === b.title && a.message === b.message;
}

/** 清理旧缓存中的重复卡片，优先保留带有分类和模型信息的 SSE 卡片。 */
export function dedupeFailureGuidance(blocks: AssistantBlock[]): AssistantBlock[] {
  const result: AssistantBlock[] = [];
  const failures: number[] = [];
  for (const incoming of blocks) {
    if (!isFailureGuidanceBlock(incoming)) {
      result.push(incoming);
      continue;
    }
    const block = normalizeFailureGuidance(incoming);
    const index = failures.find((i) => isSameFailure(result[i] as FailureGuidanceBlock, block));
    if (index === undefined) {
      failures.push(result.length);
      result.push(block);
    } else {
      const existing = result[index] as FailureGuidanceBlock;
      const score = (b: FailureGuidanceBlock) =>
        Number(b.category !== "unknown") + Number(!!b.provider) + Number(!!b.model) + Number(!!b.stage);
      if (score(block) > score(existing)) result[index] = block;
    }
  }
  return result.length === blocks.length && result.every((block, i) => block === blocks[i]) ? blocks : result;
}

/** 该轮已有实质回复时，失败卡不再代表当前状态。 */
export function blocksHaveProgress(blocks: AssistantBlock[]): boolean {
  return blocks.some((block) => {
    if (block.type === "text") {
      const text = block.content.trim();
      return text.length > 0 && !hydrateFailureGuidanceFromText(text);
    }
    if (block.type === "tool_call") {
      return block.status === "success" || block.status === "running";
    }
    return block.type === "file_download";
  });
}

export function shouldRenderFailureGuidance(
  isLastMessage: boolean | undefined,
  blocks: AssistantBlock[],
): boolean {
  if (!isLastMessage) return false;
  if (!blocks.some(isFailureGuidanceBlock)) return false;
  return !blocksHaveProgress(blocks);
}

/** 把后端持久化的 `⚠️ 标题\\n描述\\n诊断 ID:` 还原为失败卡片。 */
export function hydrateFailureGuidanceFromText(
  content: string,
): Extract<AssistantBlock, { type: "failure_guidance" }> | null {
  const trimmed = content.trim();
  if (!trimmed.startsWith("⚠️")) return null;
  if (!/诊断 ID:/i.test(trimmed) && !/请稍后重试|请重试/.test(trimmed)) return null;

  const lines = trimmed.split("\n");
  const title = lines[0].replace(/^⚠️\s*/, "").trim() || "回复未完成";
  let diagnosticId = "";
  const body: string[] = [];
  for (const line of lines.slice(1)) {
    const match = line.match(/^诊断 ID:\s*(.+)\s*$/i);
    if (match) {
      diagnosticId = match[1].trim();
      continue;
    }
    body.push(line);
  }
  const message = body.join("\n").trim();
  if (!message && !diagnosticId) return null;
  return normalizeFailureGuidance({
    type: "failure_guidance",
    category: "unknown",
    code: "internal_error",
    title,
    message,
    stage: "",
    retryable: true,
    diagnosticId,
    actions: resolveFailureActions({ code: "internal_error", retryable: true }),
  });
}

/** 仅当最后一条助手消息是可恢复失败时，才在输入框提供重试。 */
export function findLastRetryableFailure(
  messages: Message[],
): { messageId: string; code: string; retryable: boolean; title: string } | null {
  for (let i = messages.length - 1; i >= 0; i--) {
    const message = messages[i];
    if (message.role !== "assistant") continue;
    const guidance = message.blocks.find(isFailureGuidanceBlock);
    if (!guidance) return null;
    if (blocksHaveProgress(message.blocks)) return null;
    if (!canOfferRetry(guidance.code)) return null;
    return {
      messageId: message.id,
      code: guidance.code,
      retryable: guidance.retryable,
      title: displayFailureTitle(guidance.code, guidance.title),
    };
  }
  return null;
}

/**
 * 正常收尾的回合一定以下列终态块结束；除此之外的尾块（tool_call /
 * failure_guidance / thinking / llm_retry / approval_action 等中间态块）
 * 只可能来自异常退出（进程被杀、流中断）或失败。
 * 注意 memory_extracted / verification_report / staging_hint 会在回合结束后
 * 追加或仅存于历史缓存，必须视为终态，否则重开旧会话会误提示继续。
 */
const TERMINAL_TAIL_TYPES: ReadonlySet<AssistantBlock["type"]> = new Set([
  "status",
  "token_stats",
  "file_download",
  "memory_extracted",
  "verification_report",
  "staging_hint",
]);

/**
 * 会话尾部是否处于「可继续」状态（异常退出或失败），用于输入框的
 * 三角形继续按钮——点击后后台发送隐藏的 `continue` 接续执行。
 *
 * - 最后一条是 user 消息：发出后没有回复（进程被杀 / 流中断）；
 * - assistant 没有任何块：回复从未产出；
 * - assistant 尾块是非空 text 之外的非终态块：回合未正常收尾；
 * - assistant 尾块是空文本：回复内容为空。
 */
export function needsContinuationOffer(messages: Message[]): boolean {
  const last = messages[messages.length - 1];
  if (!last) return false;
  if (last.role === "user") return true;
  if (last.role !== "assistant") return false;
  const tail = last.blocks[last.blocks.length - 1];
  if (!tail) return true;
  if (tail.type === "text") return !tail.content.trim();
  return !TERMINAL_TAIL_TYPES.has(tail.type);
}
