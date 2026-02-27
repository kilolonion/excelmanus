"use client";

import React from "react";
import type ReactMarkdown from "react-markdown";
import { CodeBlock } from "./CodeBlock";
import { FilePathLink, isFilePath } from "./FilePathLink";

/**
 * Shared markdown custom components for ReactMarkdown.
 * Used by AssistantMessage and CommandResultDialog.
 */

// ── Table components ──
function MdTable({ children }: { children?: React.ReactNode }) {
  return (
    <div className="md-table-wrap my-2">
      <table className="md-table">{children}</table>
    </div>
  );
}

function MdThead({ children }: { children?: React.ReactNode }) {
  return <thead className="md-thead">{children}</thead>;
}

function MdTbody({ children }: { children?: React.ReactNode }) {
  return <tbody className="md-tbody">{children}</tbody>;
}

function MdTr({ children }: { children?: React.ReactNode }) {
  return <tr className="md-tr">{children}</tr>;
}

function MdTh({ children }: { children?: React.ReactNode }) {
  return <th className="md-th">{children}</th>;
}

function MdTd({ children }: { children?: React.ReactNode }) {
  return <td className="md-td">{children}</td>;
}

// ── Blockquote ──
function MdBlockquote({ children }: { children?: React.ReactNode }) {
  return <blockquote className="md-blockquote">{children}</blockquote>;
}

// ── HR ──
function MdHr() {
  return <hr className="md-hr" />;
}

// ── Code (fence + inline) ──
function MdPre({ children }: { children?: React.ReactNode }) {
  return <>{children}</>;
}

function MdCode({
  className,
  children,
  node,
  ...rest
}: {
  className?: string;
  children?: React.ReactNode;
  node?: { position?: unknown };
  [key: string]: unknown;
}) {
  const match = /language-(\w+)/.exec(className || "");
  const codeString = String(children).replace(/\n$/, "");
  if (match || (node?.position && codeString.includes("\n"))) {
    return <CodeBlock language={match?.[1]} code={codeString} />;
  }
  // 检测内联代码中的文件路径并渲染为可点击链接
  if (isFilePath(codeString)) {
    return <FilePathLink filePath={codeString.trim()} variant="code">{children}</FilePathLink>;
  }
  return (
    <code
      className="rounded px-1 py-0.5 text-[12.5px] font-mono bg-[var(--em-primary-alpha-06)] text-[var(--em-primary-dark)]"
      {...rest}
    >
      {children}
    </code>
  );
}

/**
 * Base markdown components (table, blockquote, hr, code).
 * AssistantMessage extends this with p/li/a overrides for @mention highlighting.
 */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export const baseMarkdownComponents: Record<string, any> = {
  table: MdTable,
  thead: MdThead,
  tbody: MdTbody,
  tr: MdTr,
  th: MdTh,
  td: MdTd,
  blockquote: MdBlockquote,
  hr: MdHr,
  pre: MdPre,
  code: MdCode,
};
