"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";
import { Check, Copy } from "lucide-react";
import { ensureHljs, highlightCode } from "@/lib/hljs-utils";

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
      if (cancelled) return;
      const html = highlightCode(code, language);
      if (!cancelled) setHighlightedHtml(html);
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
      {/* 标题栏 */}
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

      {/* 代码主体 */}
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
