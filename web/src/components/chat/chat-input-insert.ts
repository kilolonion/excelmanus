import type { Dispatch, MutableRefObject, RefObject, SetStateAction } from "react";
import { useCallback } from "react";
import { isSpreadsheetFile } from "@/lib/file-kind";
import { useExcelStore } from "@/stores/excel-store";

export function formatFileMention(opts: {
  path: string;
  sheet?: string;
  range?: string;
  version?: string | null;
}): string {
  const path = opts.path.replace(/\\/g, "/").replace(/^\.\//, "");
  let token = `@file:${path}`;
  if (opts.sheet && opts.range) {
    token += `[${opts.sheet}!${opts.range}]`;
  } else if (opts.range) {
    token += `[${opts.range}]`;
  }
  if (opts.version) {
    const version = opts.version.startsWith("sha256:")
      ? opts.version
      : `sha256:${opts.version}`;
    token += `@${version}`;
  }
  return token;
}

/**
 * 截断长文件名的提及 token，保留前缀 + 前 N 字符 + … + 扩展名。
 * 返回 [displayToken, fullToken]；若无需截断则两者相同。
 *
 * 例: "@file:学生成绩_2024_第一学期_期末考试.xlsx[Sheet1!A1:C10]"
 *   → "@file:学生成绩_…考试.xlsx[Sheet1!A1:C10]"
 */
export function truncateMention(
  token: string,
  maxFilenameLen = 16,
): [display: string, full: string] {
  // 匹配 @[file:]<path>[rangeSpec][@sha256:…] 结构
  const m = token.match(/^(@(?:file:)?)(.+?)(\[[^\]]*\])?(@sha256:[0-9a-fA-F]+)?$/);
  if (!m) return [token, token];
  const [, prefix, filename, rangePart = "", versionPart = ""] = m;

  const slash = filename.lastIndexOf("/");
  const dir = slash >= 0 ? filename.slice(0, slash + 1) : "";
  const baseName = slash >= 0 ? filename.slice(slash + 1) : filename;

  if (baseName.length <= maxFilenameLen) return [token, token];

  const dotIdx = baseName.lastIndexOf(".");
  const ext = dotIdx > 0 ? baseName.slice(dotIdx) : "";
  const base = dotIdx > 0 ? baseName.slice(0, dotIdx) : baseName;

  // 保留目录 + 首 6 字符 + … + 末 4 字符 + 扩展名
  const head = base.slice(0, 6);
  const tail = base.slice(-4);
  const short = `${head}…${tail}${ext}`;

  const display = `${prefix}${dir}${short}${rangePart}${versionPart}`;
  return [display, token];
}

const FILE_MENTION_RE =
  /^@file:([^\s\[\]@]+)(?:\[([^\]]*)\])?(?:@(sha256:[0-9a-fA-F]+))?$/;

interface ParsedFileMention {
  path: string;
  rangeSpec: string | null;
}

function parseFileMention(token: string): ParsedFileMention | null {
  const m = token.match(FILE_MENTION_RE);
  if (!m) return null;
  return {
    path: m[1].replace(/\\/g, "/").replace(/^\.\//, ""),
    rangeSpec: m[2] ? m[2] : null,
  };
}

function collapseSingleCellRange(rangeSpec: string): string {
  const bang = rangeSpec.lastIndexOf("!");
  const sheetPrefix = bang >= 0 ? rangeSpec.slice(0, bang + 1) : "";
  const cells = bang >= 0 ? rangeSpec.slice(bang + 1) : rangeSpec;
  const single = cells.match(/^([A-Za-z]+[0-9]+):\1$/i);
  return `${sheetPrefix}${single ? single[1] : cells}`;
}

function mentionFileLabel(fileLabel: string, rangeSpec: string | null): string {
  if (!rangeSpec) return fileLabel;
  return `${fileLabel} · ${collapseSingleCellRange(rangeSpec)}`;
}

function basenameOf(path: string): string {
  const slash = path.lastIndexOf("/");
  return slash >= 0 ? path.slice(slash + 1) : path;
}

function withParentDir(path: string): string | null {
  const parts = path.split("/").filter((p) => p && p !== ".");
  if (parts.length < 2) return null;
  return `${parts[parts.length - 2]}/${parts[parts.length - 1]}`;
}

/**
 * 可见层标签：basename + 可选 ` · Sheet!Range`（单格 B1:B1 → B1）。
 * 不含 `@file:` / 目录 / sha256。无法解析时 display 与 full 均为原文。
 */
export function formatMentionDisplay(fullToken: string): { display: string; full: string } {
  const parsed = parseFileMention(fullToken);
  if (!parsed) return { display: fullToken, full: fullToken };
  return {
    display: mentionFileLabel(basenameOf(parsed.path), parsed.rangeSpec),
    full: fullToken,
  };
}

function resolveUniqueDisplay(
  fullToken: string,
  preferred: string,
  taken: Set<string>,
  tokenMap: Map<string, string>,
): string {
  const available = (label: string) =>
    tokenMap.get(label) === fullToken || !taken.has(label);

  if (available(preferred)) return preferred;

  const parsed = parseFileMention(fullToken);
  if (!parsed) return preferred;

  const dirSegment = withParentDir(parsed.path);
  if (dirSegment) {
    const withDir = mentionFileLabel(dirSegment, parsed.rangeSpec);
    if (available(withDir)) return withDir;
  }

  return mentionFileLabel(parsed.path, parsed.rangeSpec);
}

export function insertTokensIntoText(
  text: string,
  cursorPos: number,
  tokens: string[],
): { newText: string; newCursorPos: number } {
  if (tokens.length === 0) return { newText: text, newCursorPos: cursorPos };
  const before = text.slice(0, cursorPos);
  const after = text.slice(cursorPos);
  const needsSpace = before.length > 0 && !before.endsWith(" ") && !before.endsWith("\n");
  const prefix = needsSpace ? " " : "";
  const mentions = tokens.join(" ");
  const inserted = `${prefix}${mentions} `;
  return {
    newText: before + inserted + after,
    newCursorPos: before.length + inserted.length,
  };
}

export function scheduleTextareaCursor(
  textarea: HTMLTextAreaElement | null,
  pos: number,
  extra?: () => void,
) {
  requestAnimationFrame(() => {
    textarea?.focus();
    textarea?.setSelectionRange(pos, pos);
    extra?.();
  });
}

export function trackRecentExcelFile(path: string, filename: string) {
  if (isSpreadsheetFile(filename)) {
    useExcelStore.getState().addRecentFile({ path, filename });
  }
}

/**
 * 发送前把可见 display 还原为协议串。按 display 长度降序替换，
 * 避免短标签成为长标签的子串（如 `订单与产品.xlsx` 命中
 * `uploads/订单与产品.xlsx · Sheet1!B1`）。占位符两趟是为了防止
 * 已替换的 full token 内部再次命中短 display。
 */
export function applyDisplayReplacements(
  text: string,
  tokenMap: Map<string, string>,
): string {
  if (tokenMap.size === 0) return text;
  const entries = [...tokenMap.entries()].sort((a, b) => b[0].length - a[0].length);
  const marks = entries.map((_, i) => `\u0000EM${i}\u0000`);
  let result = text;
  for (let i = 0; i < entries.length; i++) {
    const display = entries[i][0];
    if (!display) continue;
    result = result.replaceAll(display, marks[i]);
  }
  for (let i = 0; i < entries.length; i++) {
    result = result.replaceAll(marks[i], entries[i][1]);
  }
  return result;
}

export function toDisplayMentionTokens(
  fullTokens: string[],
  tokenMap: Map<string, string>,
): string[] {
  const taken = new Set(tokenMap.keys());
  return fullTokens.map((fullToken) => {
    const { display: preferred, full } = formatMentionDisplay(fullToken);
    const display = resolveUniqueDisplay(fullToken, preferred, taken, tokenMap);
    taken.add(display);
    if (display !== full) tokenMap.set(display, full);
    return display;
  });
}

export function detectAtMentionTrigger(value: string): string | null {
  const lastAtIdx = value.lastIndexOf("@");
  if (lastAtIdx >= 0 && (lastAtIdx === 0 || value[lastAtIdx - 1] === " ")) {
    const afterAt = value.slice(lastAtIdx + 1);
    if (!afterAt.includes(" ")) return afterAt;
  }
  return null;
}

export function useInsertMentionTokens(
  text: string,
  setText: Dispatch<SetStateAction<string>>,
  textareaRef: RefObject<HTMLTextAreaElement | null>,
  tokenMapRef: MutableRefObject<Map<string, string>>,
  setConfirmedTokens: Dispatch<SetStateAction<Set<string>>>,
) {
  return useCallback(
    (fullTokens: string[], afterInsert?: () => void) => {
      const displayTokens = toDisplayMentionTokens(fullTokens, tokenMapRef.current);
      setConfirmedTokens((prev) => {
        const next = new Set(prev);
        displayTokens.forEach((token) => next.add(token));
        return next;
      });
      const textarea = textareaRef.current;
      const cursorPos = textarea?.selectionStart ?? text.length;
      const { newText, newCursorPos } = insertTokensIntoText(text, cursorPos, displayTokens);
      setText(newText);
      scheduleTextareaCursor(textarea, newCursorPos, afterInsert);
    },
    [text, setText, textareaRef, tokenMapRef, setConfirmedTokens],
  );
}
