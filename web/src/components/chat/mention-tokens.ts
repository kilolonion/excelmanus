export interface MentionToken {
  start: number;
  end: number;
  raw: string;
  kind: string;
  value: string;
  rangeSpec?: string;
}

const MENTION_RE =
  /@(?:(file|folder|skill|mcp|tool):([^\s,;!?\[\]]+)(?:\[([^\]]+)\])?)(?=\s|$|[,;!?])/gi;

/** Bare `@filename.ext`, including CJK names such as `@广告与销售数据.csv`. */
const BARE_FILE_RE = /@([^\s@,;!?\[\]]+\.[A-Za-z0-9]+)(?=\s|$|[,;!?])/g;

const BARE_PATH_RE =
  /(?:^|(?<=\s|[：:"'（(]))(\.{0,2}\/)?([\w\u4e00-\u9fff][\w\u4e00-\u9fff./\\~ -]*\.(?:xlsx|xls|csv|tsv|pdf|zip|tar|gz|docx|pptx|txt|json|xml|html|md))(?=\s|$|[,;!?。，；！？：:）)"'])/gi;

export function extractMentions(text: string): MentionToken[] {
  const tokens: MentionToken[] = [];
  const seen = new Set<string>();

  MENTION_RE.lastIndex = 0;
  let m: RegExpExecArray | null;
  while ((m = MENTION_RE.exec(text)) !== null) {
    const key = `${m.index}`;
    if (seen.has(key)) continue;
    seen.add(key);
    tokens.push({
      start: m.index,
      end: m.index + m[0].length,
      raw: m[0],
      kind: m[1].toLowerCase(),
      value: m[2],
      rangeSpec: m[3] || undefined,
    });
  }

  BARE_FILE_RE.lastIndex = 0;
  while ((m = BARE_FILE_RE.exec(text)) !== null) {
    const key = `${m.index}`;
    if (seen.has(key)) continue;
    seen.add(key);
    tokens.push({
      start: m.index,
      end: m.index + m[0].length,
      raw: m[0],
      kind: "bare-file",
      value: m[1],
    });
  }

  BARE_PATH_RE.lastIndex = 0;
  while ((m = BARE_PATH_RE.exec(text)) !== null) {
    const fullPath = (m[1] || "") + m[2];
    const startIdx = m.index + m[0].length - fullPath.length;
    const key = `${startIdx}`;
    if (seen.has(key)) continue;
    const overlaps = tokens.some((t) => startIdx < t.end && startIdx + fullPath.length > t.start);
    if (overlaps) continue;
    seen.add(key);
    tokens.push({
      start: startIdx,
      end: startIdx + fullPath.length,
      raw: fullPath,
      kind: "path",
      value: fullPath,
    });
  }

  tokens.sort((a, b) => a.start - b.start);
  return tokens;
}

export function mentionCapsuleLabel(token: MentionToken): string {
  if (token.kind === "file" || token.kind === "bare-file" || token.kind === "path") {
    const base = token.value.split("/").pop() || token.value;
    return token.rangeSpec ? `${base} ${token.rangeSpec}` : base;
  }
  return token.raw;
}
