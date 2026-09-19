/**
 * 斜杠与前缀归一化：`\`→`/`，去掉重复的 `./` 与前导 `/`。
 * 区分大小写，不做大小写折叠。
 */
function normalizeMentionPath(path: string): string {
  let next = path.replace(/\\/g, "/");
  let prev = "";
  while (next !== prev) {
    prev = next;
    if (next.startsWith("./")) next = next.slice(2);
    if (next.startsWith("/")) next = next.slice(1);
  }
  return next;
}

/** 从文本提取 `@file:<path>` 的 path（去重、保序）。忽略 folder/skill/mcp 与裸 @name。 */
export function extractTypedFileMentions(text: string): string[] {
  if (!text) return [];
  const paths: string[] = [];
  const seen = new Set<string>();
  // 对齐后端 parser：`@file:` 后路径字符集排除空白 / `,;!?[]@`；[range] 与 @sha256: 被字符集截断。
  for (const match of text.matchAll(/@file:([^\s,;!?\[\]@]+)/gi)) {
    const path = match[1];
    if (!path || seen.has(path)) continue;
    seen.add(path);
    paths.push(path);
  }
  return paths;
}

/** 返回 finalText 中指向 knownPaths 之外的 `@file:` 路径（已归一化）。 */
export function findMissingFileMentions(text: string, knownPaths: string[]): string[] {
  const known = new Set(knownPaths.map(normalizeMentionPath));
  const missing: string[] = [];
  const seen = new Set<string>();
  for (const raw of extractTypedFileMentions(text)) {
    const path = normalizeMentionPath(raw);
    if (!path || seen.has(path)) continue;
    seen.add(path);
    if (!known.has(path)) missing.push(path);
  }
  return missing;
}

/**
 * 工作区列表为空时校验不可用，不拦截发送。
 * knownPaths 非空且存在缺失引用时才拦截。
 */
export function shouldBlockMissingFileMentions(
  knownPaths: string[],
  missing: string[],
): boolean {
  if (knownPaths.length === 0) return false;
  return missing.length > 0;
}
