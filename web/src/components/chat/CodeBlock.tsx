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
    // Register commonly needed languages
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
    // Aliases
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

  // Highlight on mount / when code or language changes
  useEffect(() => {
    let cancelled = false;
    ensureHljs().then(() => {
      if (cancelled || !_hljs) return;
      try {
        const result = language && _hljs.getLanguage(language)
          ? _hljs.highlight(code, { language })
          : _hljs.highlightAuto(code);
        if (!cancelled) setHighlightedHtml(result.value);
      } catch {
        // Fallback: no highlighting
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
