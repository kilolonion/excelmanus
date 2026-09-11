import type { Dispatch, MutableRefObject, RefObject, SetStateAction } from "react";
import { useCallback } from "react";
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

export const EXCEL_FILE_EXTS = [".xlsx", ".xls", ".xlsm", ".xlsb", ".csv"];

export function trackRecentExcelFile(path: string, filename: string) {
  const extLower = filename.slice(filename.lastIndexOf(".")).toLowerCase();
  if (EXCEL_FILE_EXTS.includes(extLower)) {
    useExcelStore.getState().addRecentFile({ path, filename });
  }
}

export function toDisplayMentionTokens(
  fullTokens: string[],
  tokenMap: Map<string, string>,
): string[] {
  return fullTokens.map((fullToken) => {
    const [display, full] = truncateMention(fullToken);
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
