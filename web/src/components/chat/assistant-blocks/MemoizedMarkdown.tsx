"use client";

import { ChevronDown, ChevronUp } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { MentionHighlighter } from "../MentionHighlighter";
import { baseMarkdownComponents } from "../MarkdownComponents";
import { isWorkspaceFileHref } from "@/lib/file-kind";
import { displayFileName } from "@/lib/file-identity";
import { openWorkspaceFile } from "@/lib/open-workspace-file";
import React, { useEffect, useRef, useState } from "react";

/**
 * Recursively process React children: replace plain string nodes
 * with MentionHighlighter so @mentions get blue-highlighted.
 */
function processChildren(children: React.ReactNode): React.ReactNode {
  return React.Children.map(children, (child) => {
    if (typeof child === "string") {
      return <MentionHighlighter text={child} />;
    }
    return child;
  });
}

const remarkPluginsStable = [remarkGfm];

function isWorkspaceFileLink(href: string): boolean {
  if (!href) return false;
  if (href.startsWith("./") || href.startsWith("../") || !href.includes("://")) {
    return isWorkspaceFileHref(href);
  }
  return false;
}

function WorkspaceFileLink({ href, children }: { href: string; children: React.ReactNode }) {
  const filename = displayFileName(href) || href;
  return (
    <button
      type="button"
      onClick={(e) => {
        e.preventDefault();
        openWorkspaceFile(href);
      }}
      className="inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-xs font-medium cursor-pointer transition-all border border-[var(--em-primary-alpha-15)] bg-[var(--em-primary-alpha-06)] hover:bg-[var(--em-primary-alpha-15)] hover:border-[var(--em-primary-alpha-20)] text-[var(--em-primary)]"
      title={`打开 ${filename}`}
    >
      <span className="break-all">{children}</span>
    </button>
  );
}

const markdownComponents: React.ComponentProps<typeof ReactMarkdown>["components"] = {
  ...baseMarkdownComponents,
  p({ children }) {
    return <p>{processChildren(children)}</p>;
  },
  li({ children }) {
    return <li>{processChildren(children)}</li>;
  },
  // 拦截链接：工作区文件链接 → 预览/下载按钮，其他 → 普通 <a>
  a({ href, children }) {
    if (href && isWorkspaceFileLink(href)) {
      return <WorkspaceFileLink href={href}>{children}</WorkspaceFileLink>;
    }
    return (
      <a href={href} target="_blank" rel="noopener noreferrer" className="text-[var(--em-primary)] underline">
        {children}
      </a>
    );
  },
};

const MAX_COLLAPSED_HEIGHT_ASSISTANT = 400; // px

export const MemoizedMarkdown = React.memo(function MemoizedMarkdown({
  content,
  isStreamingText,
  defaultExpanded,
}: {
  content: string;
  isStreamingText?: boolean;
  defaultExpanded?: boolean;
}) {
  const contentRef = useRef<HTMLDivElement>(null);
  const [expanded, setExpanded] = useState(defaultExpanded ?? false);
  const [needsExpand, setNeedsExpand] = useState(false);

  useEffect(() => {
    if (contentRef.current) {
      setNeedsExpand(contentRef.current.scrollHeight > MAX_COLLAPSED_HEIGHT_ASSISTANT);
    }
  }, [content]);

  return (
    <div className="relative">
      <div
        ref={contentRef}
        className={`prose prose-sm max-w-none text-foreground text-[13px] leading-relaxed transition-[max-height] duration-300${needsExpand && !expanded && !isStreamingText ? " overflow-hidden" : ""}${isStreamingText ? " streaming-cursor" : ""}`}
        style={{
          maxHeight: needsExpand && !expanded && !isStreamingText ? `${MAX_COLLAPSED_HEIGHT_ASSISTANT}px` : undefined,
        }}
      >
        <ReactMarkdown
          remarkPlugins={remarkPluginsStable}
          components={markdownComponents}
        >
          {content}
        </ReactMarkdown>
      </div>
      {needsExpand && !expanded && !isStreamingText && (
        <div className="relative -mt-8 pt-8 bg-gradient-to-t from-background to-transparent">
          <button
            type="button"
            onClick={() => setExpanded(true)}
            className="flex items-center gap-1 py-1 text-[11px] text-[var(--em-primary)] hover:text-[var(--em-primary-dark)] transition-colors cursor-pointer"
          >
            <ChevronDown className="h-3 w-3" />
            展开全部
          </button>
        </div>
      )}
      {needsExpand && expanded && !isStreamingText && (
        <button
          type="button"
          onClick={() => setExpanded(false)}
          className="flex items-center gap-1 mt-1 py-1 text-[11px] text-[var(--em-primary)] hover:text-[var(--em-primary-dark)] transition-colors cursor-pointer"
        >
          <ChevronUp className="h-3 w-3" />
          收起
        </button>
      )}
    </div>
  );
});
