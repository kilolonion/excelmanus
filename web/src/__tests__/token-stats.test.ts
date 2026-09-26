import { describe, expect, it } from "vitest";
import { formatCacheHit, mergeTokenStats, tokenStatsFromUsage } from "@/lib/token-stats";

describe("cache hit statistics", () => {
  it("calculates a weighted rate from input tokens without counting output or averaging rates", () => {
    const first = tokenStatsFromUsage({ prompt_tokens: 5000, cached_tokens: 4000, completion_tokens: 10, total_tokens: 5010 });
    const second = tokenStatsFromUsage({ prompt_tokens: 10000, cached_tokens: 1000, completion_tokens: 20, total_tokens: 10020 });
    const total = mergeTokenStats(first, second);
    expect(total).toMatchObject({ cachedTokens: 5000, promptTokens: 15000, totalTokens: 15030 });
    expect(formatCacheHit(total)).toBe("缓存命中 5,000 tokens (33.3%)");
  });

  it.each([undefined, null, -1, NaN, Infinity])("keeps missing or invalid usage unknown (%s)", (cached_tokens) => {
    const unknown = tokenStatsFromUsage({ prompt_tokens: 100, cached_tokens });
    const known = tokenStatsFromUsage({ prompt_tokens: 100, cached_tokens: 50 });
    expect(formatCacheHit(mergeTokenStats(known, unknown))).toBe("缓存命中 未提供");
  });

  it("distinguishes zero hits from unavailable data and avoids division by zero", () => {
    expect(formatCacheHit(tokenStatsFromUsage({ prompt_tokens: 100, cached_tokens: 0 })))
      .toBe("缓存命中 0 tokens (0.0%)");
    expect(formatCacheHit({ promptTokens: 0, cachedTokens: 0 })).toBe("缓存命中 0 tokens");
    expect(formatCacheHit({ promptTokens: 100 })).toBe("缓存命中 未提供");
  });
});
