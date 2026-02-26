"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";
import { Check, Copy } from "lucide-react";

/**
 * Lazily loaded highlight.js core + common languages.
 * We load asynchronously to avoid blocking the initial bundle.
 */
let _hljs: typeof import("highlight.js").default | null = null;
let _hljsLoading: Promise<void> | null = null;

function ensureHljs(): Promise<void> {
  if (_hljs) return Promise.resolve();
  if (_hljsLoading) return _hljsLoading;
  _hljsLoading = import("highlight.js/lib/core").then(async (mod) => {
    const hljs = mod.default;
    // 注册常用语言
    const langs = await Promise.all([
      import("highlight.js/lib/languages/python"),
      import("highlight.js/lib/languages/javascript"),
      import("highlight.js/lib/languages/typescript"),
      import("highlight.js/lib/languages/json"),
      import("highlight.js/lib/languages/bash"),
      import("highlight.js/lib/languages/sql"),
      import("highlight.js/lib/languages/xml"),
      import("highlight.js/lib/languages/css"),
      import("highlight.js/lib/languages/markdown"),
      import("highlight.js/lib/languages/yaml"),
      import("highlight.js/lib/languages/shell"),
      import("highlight.js/lib/languages/plaintext"),
    ]);
    const names = [
      "python", "javascript", "typescript", "json", "bash",
      "sql", "xml", "css", "markdown", "yaml", "shell", "plaintext",
    ];
    langs.forEach((l, i) => hljs.registerLanguage(names[i], l.default));
    // 别名
    hljs.registerLanguage("py", langs[0].default);
    hljs.registerLanguage("js", langs[1].default);
    hljs.registerLanguage("ts", langs[2].default);
    hljs.registerLanguage("sh", langs[4].default);
    hljs.registerLanguage("zsh", langs[4].default);
    hljs.registerLanguage("html", langs[6].default);
    hljs.registerLanguage("yml", langs[9].default);
    hljs.registerLanguage("txt", langs[11].default);
    _hljs = hljs;
  });
  return _hljsLoading;
}

// ------------------------------------------------------------------
// 彩虹括号：后处理 hljs HTML 为括号上色，按括号类型分三色族、按嵌套深度循环
// ------------------------------------------------------------------

const CURLY_COLORS = ["#e5a100", "#d7ba7d", "#c08b30", "#ffd700"];  // {} gold
const ROUND_COLORS = ["#569cd6", "#4fc1ff", "#0078d4", "#9cdcfe"];  // () blue
const SQUARE_COLORS = ["#c586c0", "#da70d6", "#d16969", "#ce9178"]; // [] pink

const OPEN_COLORS: Record<string, string[]> = {
  "{": CURLY_COLORS, "(": ROUND_COLORS, "[": SQUARE_COLORS,
};
const CLOSE_COLORS: Record<string, string[]> = {
  "}": CURLY_COLORS, ")": ROUND_COLORS, "]": SQUARE_COLORS,
};
const CLOSE_TO_OPEN: Record<string, string> = { "}": "{", ")": "(", "]": "[" };

function colorizeBracketsHtml(html: string): string {
  const depths: Record<string, number> = { "{": 0, "(": 0, "[": 0 };
  let inTag = false;
  let stringSpanDepth = 0;
  let spanStack: boolean[] = [];  // true 表示该 span 为字符串 span
  let result = "";
  let i = 0;

  while (i < html.length) {
    const ch = html[i];

    // 追踪 HTML 标签以检测 hljs-string 片段
    if (ch === "<") {
      const closeTag = html.startsWith("</span", i);
      if (closeTag) {
        const end = html.indexOf(">", i);
        result += html.slice(i, end + 1);
        i = end + 1;
        const wasString = spanStack.pop();
        if (wasString) stringSpanDepth--;
        continue;
      }
      const spanMatch = html.startsWith("<span", i);
      if (spanMatch) {
        const end = html.indexOf(">", i);
        const tag = html.slice(i, end + 1);
        const isString = tag.includes("hljs-string") || tag.includes("hljs-regexp");
        spanStack.push(isString);
        if (isString) stringSpanDepth++;
        result += tag;
        i = end + 1;
        continue;
      }
      // 其他标签透传
      inTag = true;
      result += ch;
      i++;
      continue;
    }
    if (inTag) {
      if (ch === ">") inTag = false;
      result += ch;
      i++;
      continue;
    }

    // 跳过字符串字面量内的括号
    if (stringSpanDepth > 0) {
      result += ch;
      i++;
      continue;
    }

    // 处理括号的 HTML 实体
    let bracket: string | null = null;
    let consumed = 1;
    if (ch === "{" || ch === "}" || ch === "(" || ch === ")" || ch === "[" || ch === "]") {
      bracket = ch;
    } else if (html.startsWith("&lbrace;", i)) {
      bracket = "{"; consumed = 8;
    } else if (html.startsWith("&rbrace;", i)) {
      bracket = "}"; consumed = 8;
    }

    if (bracket && bracket in OPEN_COLORS) {
      const colors = OPEN_COLORS[bracket];
      const depth = depths[bracket];
      const color = colors[depth % colors.length];
      result += `<span style="color:${color};font-weight:bold">${html.slice(i, i + consumed)}</span>`;
      depths[bracket]++;
      i += consumed;
    } else if (bracket && bracket in CLOSE_COLORS) {
      const openCh = CLOSE_TO_OPEN[bracket];
      depths[openCh] = Math.max(0, depths[openCh] - 1);
      const colors = CLOSE_COLORS[bracket];
      const depth = depths[openCh];
      const color = colors[depth % colors.length];
      result += `<span style="color:${color};font-weight:bold">${html.slice(i, i + consumed)}</span>`;
      i += consumed;
    } else {
      result += ch;
      i++;
    }
  }
  return result;
}

interface CodeBlockProps {
  language?: string;
  code: string;
}

export const CodeBlock = React.memo(function CodeBlock({
  language,
  code,
}: CodeBlockProps) {
  const [copied, setCopied] = useState(false);
  const [highlightedHtml, setHighlightedHtml] = useState<string | null>(null);
  const codeRef = useRef<HTMLElement>(null);

  // 挂载或 code/语言变化时高亮
  useEffect(() => {
    let cancelled = false;
    ensureHljs().then(() => {
      if (cancelled || !_hljs) return;
      try {
        const result = language && _hljs.getLanguage(language)
          ? _hljs.highlight(code, { language })
          : _hljs.highlightAuto(code);
        if (!cancelled) setHighlightedHtml(colorizeBracketsHtml(result.value));
      } catch {
        // 回退：不高亮
        if (!cancelled) setHighlightedHtml(null);
      }
    });
    return () => { cancelled = true; };
  }, [code, language]);

  const handleCopy = useCallback(() => {
    navigator.clipboard.writeText(code).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    }).catch(() => {});
  }, [code]);

  const displayLang = language || "code";

  return (
    <div className="group/code relative my-2 rounded-lg overflow-hidden border border-border/50">
      {/* Header bar */}
      <div className="flex items-center justify-between px-3 py-1.5 bg-zinc-100 dark:bg-zinc-800/80 border-b border-border/40">
        <span className="text-[11px] font-medium text-muted-foreground font-mono select-none">
          {displayLang}
        </span>
        <button
          type="button"
          onClick={handleCopy}
          className="flex items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground transition-colors rounded-md px-2 py-1 hover:bg-muted/60 h-8"
          aria-label={copied ? "已复制" : "复制代码"}
        >
          {copied ? (
            <>
              <Check className="h-3 w-3 text-emerald-500" />
              <span className="text-emerald-500">已复制</span>
            </>
          ) : (
            <>
              <Copy className="h-3 w-3" />
              <span className="hidden sm:inline">复制</span>
            </>
          )}
        </button>
      </div>

      {/* Code body */}
      <div className="overflow-x-auto bg-zinc-50 dark:bg-zinc-900">
        <pre className="!m-0 !rounded-none !bg-transparent p-3">
          {highlightedHtml ? (
            <code
              ref={codeRef}
              className={`hljs text-[12.5px] leading-relaxed font-mono ${language ? `language-${language}` : ""}`}
              dangerouslySetInnerHTML={{ __html: highlightedHtml }}
            />
          ) : (
            <code
              ref={codeRef}
              className="text-[12.5px] leading-relaxed font-mono text-foreground"
            >
              {code}
            </code>
          )}
        </pre>
      </div>
    </div>
  );
});
