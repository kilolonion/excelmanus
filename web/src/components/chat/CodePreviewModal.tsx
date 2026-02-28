"use client";

import React, { useEffect, useState, useCallback } from "react";
import { X, Download, Copy, Check, FileCode, FileText, Loader2, ExternalLink } from "lucide-react";
import { buildApiUrl } from "@/lib/api";
import { useSessionStore } from "@/stores/session-store";
import hljs from "highlight.js";

// Import highlight.js styles
import "highlight.js/styles/github-dark.css";

interface CodePreviewModalProps {
  filePath: string;
  filename: string;
  trigger?: React.ReactNode;
}

const CODE_EXTENSIONS = new Set([
  ".js", ".jsx", ".ts", ".tsx", ".json", ".py", ".rb", ".go", ".rs",
  ".java", ".c", ".cpp", ".h", ".hpp", ".cs", ".php", ".swift", ".kt",
  ".scala", ".sh", ".bash", ".zsh", ".sql", ".html", ".css", ".scss",
  ".less", ".xml", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf",
  ".md", ".markdown", ".txt", ".log", ".env", ".gitignore", ".dockerignore",
  ".graphql", ".gql", ".vue", ".svelte", ".jsx", ".tsx", ".ex", ".exs",
  ".erl", ".hs", ".ml", ".fs", ".clj", ".lua", ".r", ".dart", ".groovy",
]);

const CODE_LANGUAGE_MAP: Record<string, string> = {
  ".js": "javascript",
  ".jsx": "javascript",
  ".ts": "typescript",
  ".tsx": "typescript",
  ".py": "python",
  ".rb": "ruby",
  ".rs": "rust",
  ".java": "java",
  ".c": "c",
  ".cpp": "cpp",
  ".h": "c",
  ".hpp": "cpp",
  ".cs": "csharp",
  ".php": "php",
  ".swift": "swift",
  ".kt": "kotlin",
  ".scala": "scala",
  ".go": "go",
  ".sh": "bash",
  ".bash": "bash",
  ".zsh": "bash",
  ".sql": "sql",
  ".html": "html",
  ".css": "css",
  ".scss": "scss",
  ".less": "less",
  ".xml": "xml",
  ".yaml": "yaml",
  ".yml": "yaml",
  ".toml": "toml",
  ".ini": "ini",
  ".cfg": "ini",
  ".conf": "ini",
  ".md": "markdown",
  ".markdown": "markdown",
  ".txt": "plaintext",
  ".log": "plaintext",
  ".env": "bash",
  ".gitignore": "plaintext",
  ".dockerignore": "plaintext",
  ".graphql": "graphql",
  ".gql": "graphql",
  ".vue": "xml",
  ".svelte": "xml",
  ".ex": "erlang",
  ".exs": "erlang",
  ".erl": "erlang",
  ".hs": "haskell",
  ".ml": "ocaml",
  ".fs": "fsharp",
  ".clj": "clojure",
  ".lua": "lua",
  ".r": "r",
  ".dart": "dart",
  ".groovy": "groovy",
};

function isCodeFile(filename: string): boolean {
  const ext = filename.slice(filename.lastIndexOf(".")).toLowerCase();
  return CODE_EXTENSIONS.has(ext);
}

function getLanguage(filename: string): string {
  const ext = filename.slice(filename.lastIndexOf(".")).toLowerCase();
  return CODE_LANGUAGE_MAP[ext] || "plaintext";
}

export function CodePreviewModal({
  filePath,
  filename,
  trigger,
}: CodePreviewModalProps) {
  const [open, setOpen] = useState(false);
  const [content, setContent] = useState<string>("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string>("");
  const [copied, setCopied] = useState(false);
  const activeSessionId = useSessionStore((s) => s.activeSessionId);

  const fetchContent = useCallback(async () => {
    if (!open) return;
    
    setLoading(true);
    setError("");
    
    try {
      const sessionParam = activeSessionId ? `&session_id=${activeSessionId}` : "";
      const response = await fetch(
        buildApiUrl(`/files/read?path=${encodeURIComponent(filePath)}${sessionParam}`)
      );
      
      if (!response.ok) {
        throw new Error("无法读取文件");
      }
      
      const data = await response.json();
      setContent(data.content || "");
    } catch (err) {
      setError(err instanceof Error ? err.message : "读取文件失败");
    } finally {
      setLoading(false);
    }
  }, [filePath, open, activeSessionId]);

  useEffect(() => {
    if (open) {
      fetchContent();
    }
  }, [open, fetchContent]);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(content);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Copy failed
    }
  };

  const handleDownload = async () => {
    const { downloadFile } = await import("@/lib/api");
    downloadFile(filePath, filename, activeSessionId ?? undefined).catch(() => {});
  };

  // 点击 trigger 打开对话框
  const handleTriggerClick = (e: React.MouseEvent | React.TouchEvent) => {
    e.stopPropagation();
    setOpen(true);
  };

  const language = getLanguage(filename);
  const highlightedCode = content
    ? hljs.highlight(content, { language }).value
    : "";

  if (!open) {
    return (
      <div onClick={handleTriggerClick} onTouchEnd={handleTriggerClick}>
        {trigger}
      </div>
    );
  }

  const lineCount = content ? content.split("\n").length : 0;

  return (
    <div 
      className="fixed inset-0 z-50 flex items-center justify-center"
      onClick={() => setOpen(false)}
    >
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/80" />
      
      {/* Close button */}
      <button
        onClick={() => setOpen(false)}
        className="absolute top-4 right-4 z-10 p-2 rounded-full bg-white/10 hover:bg-white/20 text-white transition-colors"
      >
        <X className="w-5 h-5" />
      </button>

      {/* Header */}
      <div className="absolute top-4 left-4 z-10 flex items-center gap-3 px-4 py-2 rounded-lg bg-black/60 backdrop-blur-sm">
        {language === "markdown" ? (
          <FileText className="w-4 h-4 text-white/70" />
        ) : (
          <FileCode className="w-4 h-4 text-white/70" />
        )}
        <span className="text-sm text-white font-medium truncate max-w-[300px]" title={filename}>
          {filename}
        </span>
        <span className="text-xs text-white/50 bg-white/10 px-2 py-0.5 rounded">
          {language}
        </span>
      </div>

      {/* Toolbar */}
      <div className="absolute top-4 left-1/2 -translate-x-1/2 z-10 flex items-center gap-2 px-3 py-2 rounded-full bg-black/60 backdrop-blur-sm">
        <button
          onClick={(e) => { e.stopPropagation(); handleCopy(); }}
          disabled={!content || loading}
          className="p-1.5 rounded-full hover:bg-white/20 text-white disabled:opacity-40 transition-colors"
          title="复制内容"
        >
          {copied ? <Check className="w-4 h-4 text-green-400" /> : <Copy className="w-4 h-4" />}
        </button>
        <button
          onClick={(e) => { e.stopPropagation(); handleDownload(); }}
          className="p-1.5 rounded-full hover:bg-white/20 text-white transition-colors"
          title="下载"
        >
          <Download className="w-4 h-4" />
        </button>
      </div>

      {/* Content */}
      <div 
        className="relative w-[95vw] h-[90vh] max-w-[1200px] overflow-hidden rounded-lg bg-[#0d1117]"
        onClick={(e) => e.stopPropagation()}
      >
        {loading ? (
          <div className="absolute inset-0 flex items-center justify-center">
            <Loader2 className="w-8 h-8 animate-spin text-white/40" />
          </div>
        ) : error ? (
          <div className="absolute inset-0 flex items-center justify-center text-red-400">
            <span className="text-sm">{error}</span>
          </div>
        ) : (
          <div className="h-full overflow-auto">
            <pre className="m-0 p-4 text-sm leading-relaxed">
              <code
                className={`hljs language-${language}`}
                dangerouslySetInnerHTML={{ __html: highlightedCode }}
              />
            </pre>
          </div>
        )}
      </div>

      {/* Footer */}
      <div className="absolute bottom-4 left-1/2 -translate-x-1/2 flex items-center gap-4 text-xs text-white/50">
        <span>{lineCount} 行</span>
        <span>点击空白处关闭</span>
      </div>
    </div>
  );
}

// Export utility function for checking if a file is previewable
export { isCodeFile, getLanguage };
