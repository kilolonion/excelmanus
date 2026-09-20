"use client";

import { useCallback, useEffect, useRef, useState, type Dispatch, type MutableRefObject, type RefObject, type SetStateAction } from "react";
import { FileSpreadsheet, FolderOpen, Sparkles } from "lucide-react";
import { buildApiUrl, getAuthHeaders } from "@/lib/api";
import { useExcelStore } from "@/stores/excel-store";
import { useSessionStore } from "@/stores/session-store";
import { AT_TOP_LEVEL, type MentionData, type PopoverMode } from "./chat-input-constants";
import { CommandPopover, type PopoverItem } from "./CommandPopover";
import {
  detectAtMentionTrigger,
  formatFileMention,
  scheduleTextareaCursor,
  trackRecentExcelFile,
  truncateMention,
} from "./chat-input-insert";

export function useChatMentions() {
  const [mentionData, setMentionData] = useState<MentionData | null>(null);
  const [atCategory, setAtCategory] = useState<string | null>(null);
  const cache = useRef(new Map<string, { at: number; data: MentionData }>());
  const pending = useRef<{ key: string; controller: AbortController; promise: Promise<void> } | null>(null);
  useEffect(() => () => pending.current?.controller.abort(), []);

  const fetchMentionData = useCallback((subpath?: string) => {
    const sessionId = useSessionStore.getState().activeSessionId;
    const version = useExcelStore.getState().workspaceFilesVersion;
    const key = JSON.stringify([sessionId, version, subpath ?? ""]);
    if (pending.current && pending.current.key !== key) {
      pending.current.controller.abort();
      pending.current = null;
    }
    const hit = cache.current.get(key);
    if (hit && Date.now() - hit.at < 30_000) {
      setMentionData(hit.data);
      return Promise.resolve();
    }
    if (pending.current?.key === key) return pending.current.promise;
    const request = { key, controller: new AbortController(), promise: Promise.resolve() };
    pending.current = request;
    request.promise = (async () => {
      try {
      const params = new URLSearchParams();
      if (subpath) params.set("path", subpath);
      if (sessionId) params.set("session_id", sessionId);
      const qs = params.toString();
      const res = await fetch(`${buildApiUrl("/mentions")}${qs ? `?${qs}` : ""}`, {
        headers: { ...getAuthHeaders() },
        signal: request.controller.signal,
      });
      if (res.ok && !request.controller.signal.aborted && useSessionStore.getState().activeSessionId === sessionId) {
        const data = await res.json();
        if (request.controller.signal.aborted) return;
        if (cache.current.size >= 20) cache.current.clear();
        cache.current.set(key, { at: Date.now(), data });
        setMentionData(data);
      }
    } catch {
      // 后端不可用
    } finally {
      if (pending.current === request) pending.current = null;
    }
    })();
    return request.promise;
  }, []);

  return { mentionData, fetchMentionData, atCategory, setAtCategory };
}

export function buildMentionPopoverItems(
  popover: PopoverMode,
  popoverFilter: string,
  atCategory: string | null,
  mentionData: MentionData | null,
): PopoverItem[] {
  if (popover === "at") {
    const filter = popoverFilter.toLowerCase();
    const items: PopoverItem[] = [];

    for (const cat of AT_TOP_LEVEL) {
      if (!filter || cat.key.includes(filter) || cat.label.includes(filter)) {
        items.push({
          command: `@${cat.key}`,
          description: cat.description,
          icon: cat.icon,
          hasChildren: true,
        });
      }
    }

    if (filter && mentionData) {
      for (const f of mentionData.files) {
        if (f.toLowerCase().includes(filter)) {
          items.push({ command: formatFileMention({ path: f }), description: "文件", icon: <FileSpreadsheet className="h-3.5 w-3.5" /> });
        }
      }
      for (const s of mentionData.skills) {
        if (s.name.toLowerCase().includes(filter)) {
          items.push({ command: `@skill:${s.name}`, description: s.description || "技能", icon: <Sparkles className="h-3.5 w-3.5" /> });
        }
      }
    }
    return items;
  }

  if (popover === "at-sub" && atCategory && mentionData) {
    const filter = popoverFilter.toLowerCase();
    const items: PopoverItem[] = [];

    if (atCategory === "file") {
      for (const f of mentionData.files) {
        if (!filter || f.toLowerCase().includes(filter)) {
          const icon = f.endsWith("/")
            ? <FolderOpen className="h-3.5 w-3.5" />
            : <FileSpreadsheet className="h-3.5 w-3.5" />;
          items.push({ command: formatFileMention({ path: f }), description: f.endsWith("/") ? "目录" : "文件", icon });
        }
      }
      if (items.length === 0) {
        items.push({ command: "", description: "工作区无匹配文件", icon: <FileSpreadsheet className="h-3.5 w-3.5 opacity-30" /> });
      }
    } else if (atCategory === "skill") {
      for (const s of mentionData.skills) {
        if (!filter || s.name.toLowerCase().includes(filter)) {
          items.push({ command: `@skill:${s.name}`, description: s.description || "技能包", icon: <Sparkles className="h-3.5 w-3.5" /> });
        }
      }
    }
    return items;
  }

  return [];
}

interface MentionSelectContext {
  item: { command: string; hasChildren?: boolean };
  popover: PopoverMode;
  text: string;
  setText: Dispatch<SetStateAction<string>>;
  tokenMapRef: MutableRefObject<Map<string, string>>;
  setConfirmedTokens: Dispatch<SetStateAction<Set<string>>>;
  setAtCategory: (category: string | null) => void;
  setPopover: (mode: PopoverMode) => void;
  setPopoverFilter: (filter: string) => void;
  fetchMentionData: (subpath?: string) => void;
  closePopover: () => void;
  textareaRef: RefObject<HTMLTextAreaElement | null>;
}

export function applyMentionSelection(ctx: MentionSelectContext): boolean {
  const {
    item,
    popover,
    text,
    setText,
    tokenMapRef,
    setConfirmedTokens,
    setAtCategory,
    setPopover,
    setPopoverFilter,
    fetchMentionData,
    closePopover,
    textareaRef,
  } = ctx;

  if (popover === "at" && item.hasChildren) {
    const category = item.command.replace("@", "");
    setAtCategory(category);
    setPopover("at-sub");
    setPopoverFilter("");
    fetchMentionData();
    textareaRef.current?.focus();
    return true;
  }

  if (popover === "at" || popover === "at-sub") {
    if (!item.command) return true;
    if (item.command.endsWith("/")) {
      const folderPath = item.command.replace(/^@(?:file:)?/, "");
      fetchMentionData(folderPath);
      setPopoverFilter("");
      textareaRef.current?.focus();
      return true;
    }
    const [displayCmd, fullCmd] = truncateMention(item.command);
    if (displayCmd !== fullCmd) tokenMapRef.current.set(displayCmd, fullCmd);
    setConfirmedTokens((prev) => new Set(prev).add(displayCmd));
    const lastAtIdx = text.lastIndexOf("@");
    const before = text.slice(0, lastAtIdx);
    setText(before + displayCmd + " ");
    const mentionName = item.command.replace(/^@(?:file:|folder:|skill:|mcp:)?/, "");
    trackRecentExcelFile(mentionName, mentionName.split("/").pop() || mentionName);
    closePopover();
    textareaRef.current?.focus();
    return true;
  }

  return false;
}

export function maybeOpenMentionPopover(
  value: string,
  fetchMentionData: (subpath?: string) => void,
  setPopover: (mode: PopoverMode) => void,
  setPopoverFilter: (filter: string) => void,
): boolean {
  const afterAt = detectAtMentionTrigger(value);
  if (afterAt === null) return false;
  fetchMentionData();
  setPopover("at");
  setPopoverFilter(afterAt);
  return true;
}

interface ChatMentionListProps {
  popover: PopoverMode;
  popoverItems: PopoverItem[];
  selectedIndex: number;
  setSelectedIndex: (i: number) => void;
  selectPopoverItem: (item: PopoverItem) => void;
  popoverRef: RefObject<HTMLDivElement | null>;
  atCategory: string | null;
  onBackToAt: () => void;
  setText: Dispatch<SetStateAction<string>>;
  textareaRef: RefObject<HTMLTextAreaElement | null>;
  tokenMapRef: MutableRefObject<Map<string, string>>;
  setConfirmedTokens: Dispatch<SetStateAction<Set<string>>>;
  autoResize: () => void;
  insertMentionTokens: (fullTokens: string[], afterInsert?: () => void) => void;
}

function ChatMentionSync({
  setText,
  textareaRef,
  tokenMapRef,
  setConfirmedTokens,
  autoResize,
  insertMentionTokens,
}: Pick<
  ChatMentionListProps,
  "setText" | "textareaRef" | "tokenMapRef" | "setConfirmedTokens" | "autoResize" | "insertMentionTokens"
>) {
  const pendingFileMention = useExcelStore((s) => s.pendingFileMention);
  const clearPendingFileMention = useExcelStore((s) => s.clearPendingFileMention);
  const pendingFileMentions = useExcelStore((s) => s.pendingFileMentions);
  const clearPendingFileMentions = useExcelStore((s) => s.clearPendingFileMentions);
  const pendingTemplateMessage = useExcelStore((s) => s.pendingTemplateMessage);
  const clearPendingTemplateMessage = useExcelStore((s) => s.clearPendingTemplateMessage);

  useEffect(() => {
    if (!pendingFileMention) return;
    const { path, filename } = pendingFileMention;
    insertMentionTokens([formatFileMention({ path })], autoResize);
    trackRecentExcelFile(path, filename);
    clearPendingFileMention();
  }, [pendingFileMention, clearPendingFileMention, autoResize, insertMentionTokens]);

  useEffect(() => {
    if (!pendingFileMentions || pendingFileMentions.length === 0) return;
    const displayTokens: string[] = [];
    for (const { path, filename } of pendingFileMentions) {
      displayTokens.push(formatFileMention({ path }));
      trackRecentExcelFile(path, filename);
    }
    insertMentionTokens(displayTokens, autoResize);
    clearPendingFileMentions();
  }, [pendingFileMentions, clearPendingFileMentions, autoResize, insertMentionTokens]);

  useEffect(() => {
    if (!pendingTemplateMessage) return;
    const template = pendingTemplateMessage;
    clearPendingTemplateMessage();

    const mentionRegex = /@file:\S+/g;
    const mentionTokens: string[] = [];
    let match;
    while ((match = mentionRegex.exec(template)) !== null) {
      const [displayToken, fullToken] = truncateMention(match[0]);
      if (displayToken !== fullToken) tokenMapRef.current.set(displayToken, fullToken);
      mentionTokens.push(displayToken);
    }
    setConfirmedTokens((prev) => {
      const next = new Set(prev);
      for (const t of mentionTokens) next.add(t);
      return next;
    });
    setText(template);

    const textarea = textareaRef.current;
    const gapPatterns = [/与 (?=进行)/, /和 (?=的差异)/];
    let cursorPos = template.length;
    for (const pattern of gapPatterns) {
      const gapMatch = pattern.exec(template);
      if (gapMatch) {
        cursorPos = gapMatch.index + gapMatch[0].length;
        break;
      }
    }
    scheduleTextareaCursor(textarea, cursorPos, autoResize);
  }, [pendingTemplateMessage, clearPendingTemplateMessage, autoResize, setConfirmedTokens, setText, textareaRef, tokenMapRef]);

  return null;
}

export function ChatMentionList({
  popover,
  popoverItems,
  selectedIndex,
  setSelectedIndex,
  selectPopoverItem,
  popoverRef,
  atCategory,
  onBackToAt,
  setText,
  textareaRef,
  tokenMapRef,
  setConfirmedTokens,
  autoResize,
  insertMentionTokens,
}: ChatMentionListProps) {
  const isMentionPopover = popover === "at" || popover === "at-sub";

  return (
    <>
      <ChatMentionSync
        setText={setText}
        textareaRef={textareaRef}
        tokenMapRef={tokenMapRef}
        setConfirmedTokens={setConfirmedTokens}
        autoResize={autoResize}
        insertMentionTokens={insertMentionTokens}
      />
      {isMentionPopover && (
        <CommandPopover
          popover={popover}
          popoverItems={popoverItems}
          selectedIndex={selectedIndex}
          setSelectedIndex={setSelectedIndex}
          selectPopoverItem={selectPopoverItem}
          popoverRef={popoverRef}
          activeSlashCmd={null}
          atCategory={atCategory}
          onBackToSlash={() => {}}
          onBackToAt={onBackToAt}
        />
      )}
    </>
  );
}
