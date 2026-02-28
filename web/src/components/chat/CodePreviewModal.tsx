"use client";

import React, { useEffect, useState, useCallback, useRef, useMemo } from "react";
import { createPortal } from "react-dom";
import {
  X, Download, Copy, Check, FileCode, Loader2,
  Search, ChevronUp, ChevronDown, ChevronRight, Eye, Code2,
} from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
import { useVirtualizer } from "@tanstack/react-virtual";
import { buildApiUrl } from "@/lib/api";
import { useSessionStore } from "@/stores/session-store";
import { useAuthStore } from "@/stores/auth-store";
import { useExcelStore } from "@/stores/excel-store";
import { ensureHljs, highlightCode } from "@/lib/hljs-utils";

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
  ".js": "javascript", ".jsx": "javascript", ".ts": "typescript", ".tsx": "typescript",
  ".py": "python", ".rb": "ruby", ".rs": "rust", ".java": "java",
  ".c": "c", ".cpp": "cpp", ".h": "c", ".hpp": "cpp", ".cs": "csharp",
  ".php": "php", ".swift": "swift", ".kt": "kotlin", ".scala": "scala", ".go": "go",
  ".sh": "bash", ".bash": "bash", ".zsh": "bash", ".sql": "sql",
  ".html": "html", ".css": "css", ".scss": "scss", ".less": "less",
  ".xml": "xml", ".yaml": "yaml", ".yml": "yaml", ".toml": "toml",
  ".ini": "ini", ".cfg": "ini", ".conf": "ini",
  ".md": "markdown", ".markdown": "markdown", ".txt": "plaintext", ".log": "plaintext",
  ".env": "bash", ".gitignore": "plaintext", ".dockerignore": "plaintext",
  ".graphql": "graphql", ".gql": "graphql", ".vue": "xml", ".svelte": "xml",
  ".ex": "erlang", ".exs": "erlang", ".erl": "erlang", ".hs": "haskell",
  ".ml": "ocaml", ".fs": "fsharp", ".clj": "clojure", ".lua": "lua",
  ".r": "r", ".dart": "dart", ".groovy": "groovy",
};

/* ─── Language color mapping (VS Code–style) ─── */
const LANG_COLORS: Record<string, string> = {
  python: "#3572A5", javascript: "#f1e05a", typescript: "#3178c6", json: "#292929",
  ruby: "#701516", rust: "#dea584", java: "#b07219", c: "#555555",
  cpp: "#f34b7d", csharp: "#178600", php: "#4F5D95", swift: "#F05138",
  kotlin: "#A97BFF", scala: "#c22d40", go: "#00ADD8", bash: "#89e051",
  sql: "#e38c00", html: "#e34c26", css: "#563d7c", scss: "#c6538c",
  less: "#1d365d", xml: "#0060ac", yaml: "#cb171e", toml: "#9c4221",
  ini: "#d1dbe0", markdown: "#083fa1", plaintext: "#999999",
  graphql: "#e10098", erlang: "#B83998", haskell: "#5e5086",
  ocaml: "#3be133", fsharp: "#b845fc", clojure: "#db5855",
  lua: "#000080", r: "#198CE7", dart: "#00B4AB", groovy: "#4298b8",
};

function isCodeFile(filename: string): boolean {
  const ext = filename.slice(filename.lastIndexOf(".")).toLowerCase();
  return CODE_EXTENSIONS.has(ext);
}

function getLanguage(filename: string): string {
  const ext = filename.slice(filename.lastIndexOf(".")).toLowerCase();
  return CODE_LANGUAGE_MAP[ext] || "plaintext";
}

function getLangColor(lang: string): string {
  return LANG_COLORS[lang] || "#999";
}

/* ─── Breadcrumb from path ─── */
function pathToBreadcrumb(filePath: string): string[] {
  const parts = filePath.replace(/^\/+/, "").split("/");
  return parts.length > 4 ? ["...", ...parts.slice(-3)] : parts;
}

/* ─── framer-motion variants ─── */
const backdropVariants = {
  hidden: { opacity: 0 },
  visible: { opacity: 1, transition: { duration: 0.18, ease: "easeOut" as const } },
  exit: { opacity: 0, transition: { duration: 0.12, ease: "easeIn" as const } },
};
const panelVariants = {
  hidden: { opacity: 0, scale: 0.97, y: 8 },
  visible: { opacity: 1, scale: 1, y: 0, transition: { duration: 0.22, ease: [0.16, 1, 0.3, 1] as const } },
  exit: { opacity: 0, scale: 0.98, y: 4, transition: { duration: 0.12, ease: "easeIn" as const } },
};

interface TabItem {
  filePath: string;
  filename: string;
}

const LINE_HEIGHT = 20;

export function CodePreviewModal({
  filePath,
  filename,
  trigger,
}: CodePreviewModalProps) {
  const [open, setOpen] = useState(false);
  const [activeFile, setActiveFile] = useState<TabItem>({ filePath, filename });
  const [content, setContent] = useState<string>("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string>("");
  const [copied, setCopied] = useState(false);
  const [activeLine, setActiveLine] = useState<number>(-1);
  const [mdPreview, setMdPreview] = useState(false);
  const contentCache = useRef(new Map<string, string>());
  const activeSessionId = useSessionStore((s) => s.activeSessionId);

  // Search state
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [searchMatches, setSearchMatches] = useState<number[]>([]);
  const [searchIdx, setSearchIdx] = useState(0);
  const searchInputRef = useRef<HTMLInputElement>(null);

  const previewTabs = useExcelStore((s) => s.previewTabs);
  const addPreviewTab = useExcelStore((s) => s.addPreviewTab);
  const removePreviewTab = useExcelStore((s) => s.removePreviewTab);

  // ── Virtualizer ──
  const scrollContainerRef = useRef<HTMLDivElement>(null);

  // ── Fetch file content (with cache) ──
  const fetchFile = useCallback(async (path: string) => {
    const cached = contentCache.current.get(path);
    if (cached !== undefined) {
      setContent(cached);
      setLoading(false);
      setError("");
      return;
    }
    setLoading(true);
    setError("");
    try {
      const sessionParam = activeSessionId ? `&session_id=${activeSessionId}` : "";
      const token = useAuthStore.getState().accessToken;
      const headers: Record<string, string> = {};
      if (token) headers["Authorization"] = `Bearer ${token}`;
      const response = await fetch(
        buildApiUrl(`/files/read?path=${encodeURIComponent(path)}${sessionParam}`),
        { headers },
      );
      if (!response.ok) throw new Error("无法读取文件");
      const data = await response.json();
      const text = data.content || "";
      contentCache.current.set(path, text);
      setContent(text);
    } catch (err) {
      setError(err instanceof Error ? err.message : "读取文件失败");
    } finally {
      setLoading(false);
    }
  }, [activeSessionId]);

  // ── Highlight with shared module (lazy) ──
  const [highlightedLines, setHighlightedLines] = useState<string[]>([]);
  useEffect(() => {
    if (!content) { setHighlightedLines([]); return; }
    let cancelled = false;
    ensureHljs().then(() => {
      if (cancelled) return;
      const lang = getLanguage(activeFile.filename);
      const html = highlightCode(content, lang);
      if (!cancelled) {
        setHighlightedLines(html ? html.split("\n") : content.split("\n"));
      }
    });
    return () => { cancelled = true; };
  }, [content, activeFile.filename]);

  const lineCount = highlightedLines.length;

  // ── Virtualizer instance ──
  const virtualizer = useVirtualizer({
    count: lineCount,
    getScrollElement: () => scrollContainerRef.current,
    estimateSize: () => LINE_HEIGHT,
    overscan: 20,
  });

  // ── Open → register tab + fetch + reset state ──
  useEffect(() => {
    if (open) {
      addPreviewTab({ filePath, filename });
      setActiveFile({ filePath, filename });
      fetchFile(filePath);
      setActiveLine(-1);
      setMdPreview(false);
      setSearchOpen(false);
      setSearchQuery("");
      setSearchMatches([]);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  // ── Body scroll lock ──
  useEffect(() => {
    if (!open) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { document.body.style.overflow = prev; };
  }, [open]);

  // ── Keyboard shortcuts ──
  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        if (searchOpen) { setSearchOpen(false); setSearchQuery(""); setSearchMatches([]); }
        else setOpen(false);
      }
      if ((e.metaKey || e.ctrlKey) && e.key === "f") {
        e.preventDefault();
        setSearchOpen(true);
        setTimeout(() => searchInputRef.current?.focus(), 50);
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [open, searchOpen]);

  // ── Search logic ──
  useEffect(() => {
    if (!searchQuery.trim() || !content) { setSearchMatches([]); setSearchIdx(0); return; }
    const q = searchQuery.toLowerCase();
    const rawLines = content.split("\n");
    const matches: number[] = [];
    rawLines.forEach((line, i) => { if (line.toLowerCase().includes(q)) matches.push(i); });
    setSearchMatches(matches);
    setSearchIdx(0);
    if (matches.length > 0) virtualizer.scrollToIndex(matches[0], { align: "center" });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchQuery, content]);

  const searchNext = useCallback(() => {
    if (searchMatches.length === 0) return;
    const next = (searchIdx + 1) % searchMatches.length;
    setSearchIdx(next);
    virtualizer.scrollToIndex(searchMatches[next], { align: "center" });
  }, [searchIdx, searchMatches, virtualizer]);

  const searchPrev = useCallback(() => {
    if (searchMatches.length === 0) return;
    const prev = (searchIdx - 1 + searchMatches.length) % searchMatches.length;
    setSearchIdx(prev);
    virtualizer.scrollToIndex(searchMatches[prev], { align: "center" });
  }, [searchIdx, searchMatches, virtualizer]);

  // ── Tab actions ──
  const handleTabClick = useCallback((tab: TabItem) => {
    setActiveFile(tab);
    fetchFile(tab.filePath);
    setActiveLine(-1);
    setMdPreview(false);
    setSearchOpen(false);
    setSearchQuery("");
  }, [fetchFile]);

  const handleTabClose = useCallback((e: React.MouseEvent, tab: TabItem) => {
    e.stopPropagation();
    removePreviewTab(tab.filePath);
    if (tab.filePath === activeFile.filePath) {
      const remaining = useExcelStore.getState().previewTabs.filter((t) => t.filePath !== tab.filePath);
      if (remaining.length > 0) {
        const next = remaining[remaining.length - 1];
        setActiveFile(next);
        fetchFile(next.filePath);
      } else {
        setOpen(false);
      }
    }
  }, [activeFile.filePath, removePreviewTab, fetchFile]);

  // ── Toolbar actions ──
  const handleCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(content);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch { /* noop */ }
  }, [content]);

  const handleDownload = useCallback(async () => {
    const { downloadFile } = await import("@/lib/api");
    downloadFile(activeFile.filePath, activeFile.filename, activeSessionId ?? undefined).catch(() => {});
  }, [activeFile, activeSessionId]);

  // ── Derived state ──
  const language = getLanguage(activeFile.filename);
  const langColor = getLangColor(language);
  const isMarkdown = language === "markdown";
  const breadcrumb = useMemo(() => pathToBreadcrumb(activeFile.filePath), [activeFile.filePath]);
  const lineDigits = useMemo(() => String(lineCount).length, [lineCount]);
  const searchMatchSet = useMemo(() => new Set(searchMatches), [searchMatches]);
  const currentSearchLine = searchMatches.length > 0 ? searchMatches[searchIdx] : -1;

  /* ─── Portal overlay ─── */
  const overlay = (
    <AnimatePresence>
      {open && (
        <motion.div
          key="code-preview-overlay"
          className="fixed inset-0 z-[9999] flex items-center justify-center"
          onClick={() => setOpen(false)}
          initial="hidden"
          animate="visible"
          exit="exit"
        >
          {/* Backdrop */}
          <motion.div
            className="absolute inset-0 bg-black/50 dark:bg-black/70 backdrop-blur-sm"
            variants={backdropVariants}
          />

          {/* Panel */}
          <motion.div
            className="relative flex flex-col w-[95vw] h-[85vh] max-w-[1200px] overflow-hidden rounded-xl bg-white dark:bg-[#1e1e1e] border border-gray-200 dark:border-gray-700 shadow-[0_20px_60px_-12px_rgba(0,0,0,0.25)]"
            variants={panelVariants}
            onClick={(e) => e.stopPropagation()}
          >
            {/* ── Tab bar ── */}
            <div className="flex items-center bg-[#f3f3f3] dark:bg-[#252526] border-b border-gray-200 dark:border-gray-700 min-h-[36px] select-none overflow-x-auto scrollbar-none">
              <div className="flex items-center flex-1 min-w-0">
                {previewTabs.map((tab) => {
                  const isActive = tab.filePath === activeFile.filePath;
                  const tabLang = getLanguage(tab.filename);
                  const tabColor = getLangColor(tabLang);
                  return (
                    <div
                      key={tab.filePath}
                      onClick={() => handleTabClick(tab)}
                      className={`group relative flex items-center gap-1.5 px-3 h-[36px] text-[12px] cursor-pointer shrink-0 border-r border-gray-200/60 dark:border-gray-700/60 transition-colors ${
                        isActive
                          ? "bg-white dark:bg-[#1e1e1e] text-gray-800 dark:text-gray-200"
                          : "text-gray-500 dark:text-gray-400 hover:bg-gray-100/60 dark:hover:bg-[#2a2a2a]"
                      }`}
                    >
                      {isActive && (
                        <div className="absolute top-0 left-0 right-0 h-[2px] bg-[var(--em-primary)]" />
                      )}
                      {/* Language color dot */}
                      <span
                        className="w-2.5 h-2.5 rounded-full shrink-0 ring-1 ring-black/5 dark:ring-white/10"
                        style={{ backgroundColor: tabColor }}
                      />
                      <span className="truncate max-w-[120px]">{tab.filename}</span>
                      <button
                        onClick={(e) => handleTabClose(e, tab)}
                        className="ml-0.5 p-0.5 rounded opacity-0 group-hover:opacity-60 hover:!opacity-100 hover:bg-gray-200 dark:hover:bg-gray-600 transition-all"
                        title="关闭"
                      >
                        <X className="w-3 h-3" />
                      </button>
                    </div>
                  );
                })}
              </div>
              {/* Close all button */}
              <button
                onClick={(e) => { e.stopPropagation(); setOpen(false); }}
                className="shrink-0 p-2 mx-1 rounded text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 hover:bg-gray-200/60 dark:hover:bg-gray-600/40 transition-all"
                title="关闭 (Esc)"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            {/* ── Breadcrumb + Toolbar bar ── */}
            <div className="flex items-center gap-1.5 px-3 h-[32px] bg-[#fafafa] dark:bg-[#1e1e1e] border-b border-gray-100 dark:border-gray-800 text-[11px] text-gray-400 dark:text-gray-500 select-none overflow-hidden">
              {breadcrumb.map((seg, i) => (
                <React.Fragment key={i}>
                  {i > 0 && <ChevronRight className="w-3 h-3 shrink-0 opacity-50" />}
                  <span className={i === breadcrumb.length - 1 ? "text-gray-600 dark:text-gray-300 font-medium truncate" : "truncate"}>
                    {seg}
                  </span>
                </React.Fragment>
              ))}
              <div className="flex-1" />
              {/* Action buttons — prominent */}
              <div className="flex items-center gap-1">
                {isMarkdown && !loading && !error && (
                  <button
                    onClick={() => setMdPreview(!mdPreview)}
                    className={`flex items-center gap-1.5 px-2.5 py-1 rounded-md text-[11px] font-medium transition-all ${
                      mdPreview
                        ? "bg-[var(--em-primary-alpha-10)] text-[var(--em-primary)] border border-[var(--em-primary-alpha-20)]"
                        : "bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-300 border border-gray-200 dark:border-gray-700 hover:bg-gray-200/80 dark:hover:bg-gray-700"
                    }`}
                    title={mdPreview ? "显示源码" : "预览 Markdown"}
                  >
                    {mdPreview ? <Code2 className="w-3.5 h-3.5" /> : <Eye className="w-3.5 h-3.5" />}
                    <span>{mdPreview ? "源码" : "预览"}</span>
                  </button>
                )}
                <button
                  onClick={() => { setSearchOpen(!searchOpen); if (!searchOpen) setTimeout(() => searchInputRef.current?.focus(), 50); }}
                  className={`flex items-center gap-1.5 px-2.5 py-1 rounded-md text-[11px] font-medium transition-all ${
                    searchOpen
                      ? "bg-[var(--em-primary-alpha-10)] text-[var(--em-primary)] border border-[var(--em-primary-alpha-20)]"
                      : "bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-300 border border-gray-200 dark:border-gray-700 hover:bg-gray-200/80 dark:hover:bg-gray-700"
                  }`}
                  title="搜索 (Ctrl+F)"
                >
                  <Search className="w-3.5 h-3.5" />
                  <span>搜索</span>
                </button>
              </div>
            </div>

            {/* ── Search bar (animated) ── */}
            <AnimatePresence>
              {searchOpen && (
                <motion.div
                  initial={{ height: 0, opacity: 0 }}
                  animate={{ height: 36, opacity: 1 }}
                  exit={{ height: 0, opacity: 0 }}
                  transition={{ duration: 0.15 }}
                  className="overflow-hidden shrink-0"
                >
                  <div className="flex items-center gap-2 px-3 h-[36px] bg-[#f5f5f5] dark:bg-[#252526] border-b border-gray-200 dark:border-gray-700">
                    <Search className="w-3.5 h-3.5 text-gray-400 shrink-0" />
                    <input
                      ref={searchInputRef}
                      value={searchQuery}
                      onChange={(e) => setSearchQuery(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") { e.shiftKey ? searchPrev() : searchNext(); }
                        if (e.key === "Escape") { setSearchOpen(false); setSearchQuery(""); setSearchMatches([]); }
                      }}
                      placeholder="搜索…"
                      className="flex-1 bg-white dark:bg-[#3c3c3c] border border-gray-200 dark:border-gray-600 rounded px-2 py-1 text-[12px] text-gray-800 dark:text-gray-200 placeholder:text-gray-400 outline-none focus:border-[var(--em-primary)] focus:ring-1 focus:ring-[var(--em-primary)]/30 transition-all"
                    />
                    <span className="text-[11px] text-gray-400 tabular-nums whitespace-nowrap">
                      {searchMatches.length > 0 ? `${searchIdx + 1}/${searchMatches.length}` : searchQuery ? "无结果" : ""}
                    </span>
                    <button onClick={searchPrev} className="p-1 rounded hover:bg-gray-200 dark:hover:bg-gray-600 transition-colors" title="上一个">
                      <ChevronUp className="w-3.5 h-3.5 text-gray-500" />
                    </button>
                    <button onClick={searchNext} className="p-1 rounded hover:bg-gray-200 dark:hover:bg-gray-600 transition-colors" title="下一个">
                      <ChevronDown className="w-3.5 h-3.5 text-gray-500" />
                    </button>
                    <button
                      onClick={() => { setSearchOpen(false); setSearchQuery(""); setSearchMatches([]); }}
                      className="p-1 rounded hover:bg-gray-200 dark:hover:bg-gray-600 transition-colors"
                      title="关闭搜索"
                    >
                      <X className="w-3.5 h-3.5 text-gray-500" />
                    </button>
                  </div>
                </motion.div>
              )}
            </AnimatePresence>

            {/* ── Content area ── */}
            <div className="flex-1 overflow-hidden bg-white dark:bg-[#1e1e1e]">
              {loading ? (
                <div className="flex flex-col items-center justify-center h-full gap-3">
                  <Loader2 className="w-7 h-7 animate-spin text-[var(--em-primary)] opacity-40" />
                  <span className="text-xs text-gray-400">加载中…</span>
                </div>
              ) : error ? (
                <div className="flex flex-col items-center justify-center h-full gap-3 text-gray-400 dark:text-gray-500">
                  <div className="w-16 h-16 rounded-xl bg-gray-100 dark:bg-gray-800 flex items-center justify-center">
                    <FileCode className="w-8 h-8" />
                  </div>
                  <span className="text-sm font-medium">{error}</span>
                  <span className="text-xs text-gray-300 dark:text-gray-600">按 Esc 关闭</span>
                </div>
              ) : mdPreview && isMarkdown ? (
                <MarkdownPreview content={content} />
              ) : (
                /* ── Virtualized code view ── */
                <div ref={scrollContainerRef} className="h-full overflow-auto">
                  <div
                    style={{ height: virtualizer.getTotalSize(), position: "relative" }}
                    className="font-mono text-[13px] leading-[20px]"
                  >
                    {virtualizer.getVirtualItems().map((vItem) => {
                      const lineIdx = vItem.index;
                      const isActive_l = lineIdx === activeLine;
                      const isSearchMatch = searchMatchSet.has(lineIdx);
                      const isCurrentSearch = lineIdx === currentSearchLine;
                      return (
                        <div
                          key={vItem.key}
                          data-index={vItem.index}
                          ref={virtualizer.measureElement}
                          style={{
                            position: "absolute",
                            top: 0,
                            left: 0,
                            width: "100%",
                            transform: `translateY(${vItem.start}px)`,
                            minHeight: LINE_HEIGHT,
                          }}
                          className={`flex cursor-pointer transition-colors duration-75 ${
                            isCurrentSearch
                              ? "bg-amber-200/40 dark:bg-amber-500/20"
                              : isSearchMatch
                                ? "bg-amber-100/30 dark:bg-amber-600/10"
                                : isActive_l
                                  ? "bg-[var(--em-primary-alpha-06)]"
                                  : "hover:bg-gray-50 dark:hover:bg-[#ffffff06]"
                          }`}
                          onClick={() => setActiveLine(lineIdx === activeLine ? -1 : lineIdx)}
                        >
                          {/* Line number */}
                          <span
                            className={`select-none text-right pr-4 pl-4 shrink-0 tabular-nums border-r ${
                              isActive_l
                                ? "text-gray-600 dark:text-gray-300 bg-gray-100/60 dark:bg-[#ffffff0a] border-gray-200 dark:border-gray-700"
                                : "text-gray-400 dark:text-gray-600 bg-gray-50/80 dark:bg-[#1e1e1e] border-gray-100 dark:border-gray-800"
                            }`}
                            style={{ minWidth: `${lineDigits * 0.6 + 2}em` }}
                          >
                            {lineIdx + 1}
                          </span>
                          {/* Code content */}
                          <span className="pl-4 pr-4 whitespace-pre flex-1 min-w-0">
                            <code
                              className="hljs"
                              dangerouslySetInnerHTML={{ __html: highlightedLines[lineIdx] || "\u00a0" }}
                            />
                          </span>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}
            </div>

            {/* ── Status bar (VS Code–style) ── */}
            <div className="flex items-center justify-between px-3 h-[24px] bg-[var(--em-primary)] text-white text-[11px] select-none shrink-0">
              <div className="flex items-center gap-3">
                <span
                  className="w-2 h-2 rounded-full ring-1 ring-white/20"
                  style={{ backgroundColor: langColor }}
                />
                <span className="opacity-90 truncate max-w-[200px]" title={activeFile.filename}>
                  {activeFile.filename}
                </span>
                <span className="opacity-60 uppercase text-[10px]">{language}</span>
                <span className="opacity-60 tabular-nums">{lineCount} 行</span>
                {activeLine >= 0 && (
                  <span className="opacity-70 tabular-nums">行 {activeLine + 1}</span>
                )}
                <span className="opacity-60">UTF-8</span>
              </div>
              <div className="flex items-center gap-1">
                <button
                  onClick={(e) => { e.stopPropagation(); handleCopy(); }}
                  disabled={!content || loading}
                  className="flex items-center gap-1 px-1.5 py-0.5 rounded opacity-80 hover:opacity-100 hover:bg-white/15 disabled:opacity-30 transition-all"
                  title="复制内容"
                >
                  {copied ? <Check className="w-3 h-3" /> : <Copy className="w-3 h-3" />}
                  <span>{copied ? "已复制" : "复制"}</span>
                </button>
                <button
                  onClick={(e) => { e.stopPropagation(); handleDownload(); }}
                  className="flex items-center gap-1 px-1.5 py-0.5 rounded opacity-80 hover:opacity-100 hover:bg-white/15 transition-all"
                  title="下载"
                >
                  <Download className="w-3 h-3" />
                  <span>下载</span>
                </button>
              </div>
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );

  return (
    <>
      <span className="inline" onClick={(e) => { e.stopPropagation(); setOpen(true); }}>
        {trigger}
      </span>
      {typeof window !== "undefined" && createPortal(overlay, document.body)}
    </>
  );
}

/* ─── Markdown preview sub-component (lazy react-markdown) ─── */
function MarkdownPreview({ content }: { content: string }) {
  const [MdRenderer, setMdRenderer] = useState<React.ComponentType<{ children: string }> | null>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      import("react-markdown"),
      import("remark-gfm"),
    ]).then(([mdMod, gfmMod]) => {
      if (cancelled) return;
      const ReactMarkdown = mdMod.default;
      const remarkGfm = gfmMod.default;
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const Wrapper = ({ children }: { children: string }) => (
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{children}</ReactMarkdown>
      );
      setMdRenderer(() => Wrapper);
    });
    return () => { cancelled = true; };
  }, []);

  if (!MdRenderer) {
    return (
      <div className="flex items-center justify-center h-full">
        <Loader2 className="w-5 h-5 animate-spin text-gray-400" />
      </div>
    );
  }

  return (
    <div className="h-full overflow-auto px-8 py-6">
      <article className="prose prose-sm dark:prose-invert max-w-none">
        <MdRenderer>{content}</MdRenderer>
      </article>
    </div>
  );
}

// Export utility function for checking if a file is previewable
export { isCodeFile, getLanguage };
