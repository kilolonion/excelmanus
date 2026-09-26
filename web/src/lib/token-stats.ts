import type { AssistantBlock } from "./types";

export type TokenStatsBlock = Extract<AssistantBlock, { type: "token_stats" }>;

export function tokenStatsFromUsage(data: Record<string, unknown>): TokenStatsBlock {
  const cached = data.cached_tokens;
  return {
    type: "token_stats",
    promptTokens: Number(data.prompt_tokens) || 0,
    completionTokens: Number(data.completion_tokens) || 0,
    totalTokens: Number(data.total_tokens) || 0,
    iterations: Number(data.iterations) || 0,
    cachedTokens: typeof cached === "number" && Number.isFinite(cached) && cached >= 0
      ? cached : null,
  };
}

/** Sum tokens before calculating the rate; missing usage must not become a cache miss. */
export function mergeTokenStats(a: TokenStatsBlock, b: TokenStatsBlock): TokenStatsBlock {
  return {
    type: "token_stats",
    promptTokens: a.promptTokens + b.promptTokens,
    completionTokens: a.completionTokens + b.completionTokens,
    totalTokens: a.totalTokens + b.totalTokens,
    iterations: a.iterations + b.iterations,
    cachedTokens: a.cachedTokens == null || b.cachedTokens == null
      ? null : a.cachedTokens + b.cachedTokens,
  };
}

export function formatCacheHit(stats: Pick<TokenStatsBlock, "cachedTokens" | "promptTokens">): string {
  if (stats.cachedTokens == null) return "缓存命中 未提供";
  const count = `缓存命中 ${stats.cachedTokens.toLocaleString()} tokens`;
  return stats.promptTokens > 0
    ? `${count} (${(stats.cachedTokens / stats.promptTokens * 100).toFixed(1)}%)`
    : count;
}
