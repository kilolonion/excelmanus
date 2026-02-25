"use client";

import { useRef, useState, useCallback, useEffect, useMemo } from "react";
import {
  Plus,
  ArrowUp,
  Square,
  Terminal,
  AtSign,
  FileSpreadsheet,
  Wrench,
  Bot,
  ShieldCheck,
  Layers,
  RotateCcw,
  Sparkles,
  HelpCircle,
  Clock as HistoryIcon,
  Trash2,
  Save,
  Settings,
  ClipboardList,
  CheckCircle2,
  XCircle,
  Undo2,
  StopCircle,
  ChevronRight,
  FolderOpen,
  Check,
  Cpu,
  X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { useDropzone } from "react-dropzone";
import { useChatStore } from "@/stores/chat-store";
import { useUIStore } from "@/stores/ui-store";
import { useExcelStore } from "@/stores/excel-store";
import { buildApiUrl, apiGet, apiPut } from "@/lib/api";
import { UndoPanel } from "@/components/modals/UndoPanel";
import type { ModelInfo } from "@/lib/types";

const ACCEPTED_EXTENSIONS = {
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": [".xlsx"],
  "application/vnd.ms-excel": [".xls"],
  "text/csv": [".csv"],
  "image/png": [".png"],
  "image/jpeg": [".jpg", ".jpeg"],
};

// Slash commands (mirrors CLI _STATIC_SLASH_COMMANDS + control_commands)
const SLASH_COMMANDS: { command: string; description: string; icon: React.ReactNode; args?: string[] }[] = [
  // Base commands
  { command: "/help", description: "显示帮助", icon: <HelpCircle className="h-3.5 w-3.5" /> },
  { command: "/skills", description: "查看技能包", icon: <Sparkles className="h-3.5 w-3.5" /> },
  { command: "/history", description: "对话历史摘要", icon: <HistoryIcon className="h-3.5 w-3.5" /> },
  { command: "/clear", description: "清除对话历史", icon: <Trash2 className="h-3.5 w-3.5" /> },
  { command: "/mcp", description: "MCP Server 状态", icon: <Terminal className="h-3.5 w-3.5" /> },
  { command: "/save", description: "保存对话记录", icon: <Save className="h-3.5 w-3.5" /> },
  { command: "/config", description: "环境变量配置", icon: <Settings className="h-3.5 w-3.5" />, args: ["list", "set", "get", "delete"] },
  // Control commands
  { command: "/model", description: "查看/切换模型", icon: <Sparkles className="h-3.5 w-3.5" />, args: ["list"] },
  { command: "/subagent", description: "子代理控制", icon: <Bot className="h-3.5 w-3.5" />, args: ["status", "on", "off", "list", "run"] },
  { command: "/fullaccess", description: "权限控制", icon: <ShieldCheck className="h-3.5 w-3.5" />, args: ["status", "on", "off"] },
  { command: "/backup", description: "备份沙盒控制", icon: <Layers className="h-3.5 w-3.5" />, args: ["status", "on", "off", "apply", "list"] },
  { command: "/compact", description: "上下文压缩", icon: <RotateCcw className="h-3.5 w-3.5" />, args: ["status", "on", "off"] },
  { command: "/plan", description: "计划模式", icon: <ClipboardList className="h-3.5 w-3.5" />, args: ["status", "on", "off", "approve", "reject"] },
  { command: "/manifest", description: "工作区清单", icon: <FolderOpen className="h-3.5 w-3.5" />, args: ["status", "build"] },
  { command: "/accept", description: "确认操作", icon: <CheckCircle2 className="h-3.5 w-3.5" /> },
  { command: "/reject", description: "拒绝操作", icon: <XCircle className="h-3.5 w-3.5" /> },
  { command: "/undo", description: "回滚操作", icon: <Undo2 className="h-3.5 w-3.5" /> },
  { command: "/stop", description: "停止当前生成", icon: <StopCircle className="h-3.5 w-3.5" /> },
];

// @ mention top-level categories
interface MentionCategory {
  key: string;
  label: string;
  icon: React.ReactNode;
  description: string;
}

const AT_TOP_LEVEL: MentionCategory[] = [
  { key: "file", label: "文件", icon: <FileSpreadsheet className="h-3.5 w-3.5" />, description: "引用工作区文件" },
  { key: "tool", label: "工具", icon: <Wrench className="h-3.5 w-3.5" />, description: "指定使用的工具" },
  { key: "skill", label: "技能", icon: <Sparkles className="h-3.5 w-3.5" />, description: "调用技能包" },
];

// Commands that show results in a dialog instead of sending as chat
// NOTE: Only exact matches are checked. /model alone is display, but /model <name> is action.
const DISPLAY_COMMANDS = new Set([
  "/help", "/skills", "/mcp", "/history",
  "/model", "/model list",
  "/config", "/config list", "/config get",
  "/subagent list", "/subagent status",
  "/fullaccess status",
  "/backup list", "/backup status",
  "/compact status",
  "/plan status",
  "/manifest status",
]);

// Commands that execute a frontend action directly (never sent to chat)
const FRONTEND_ACTIONS: Record<string, string> = {
  "/stop": "stop",
  "/clear": "clear",
  "/accept": "accept",
  "/reject": "reject",
};

interface ChatInputProps {
  onSend: (text: string, files?: File[]) => void;
  onCommandResult?: (command: string, result: string, format: "markdown" | "text") => void;
  disabled?: boolean;
  isStreaming?: boolean;
  onStop?: () => void;
}

type PopoverMode = null | "slash" | "slash-args" | "slash-skills" | "slash-model" | "at" | "at-sub";

// Commands whose args, once selected, should auto-execute (no extra Enter needed)
const AUTO_EXEC_ARGS = new Set(["on", "off", "status", "build", "approve", "reject", "apply", "list"]);

interface MentionData {
  tools: string[];
  skills: { name: string; description: string }[];
  files: string[];
}

export function ChatInput({ onSend, onCommandResult, disabled, isStreaming, onStop }: ChatInputProps) {
  const [text, setText] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [popover, setPopover] = useState<PopoverMode>(null);
  const [popoverFilter, setPopoverFilter] = useState("");
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [activeSlashCmd, setActiveSlashCmd] = useState<string | null>(null);
  const [atCategory, setAtCategory] = useState<string | null>(null);
  const [mentionData, setMentionData] = useState<MentionData | null>(null);
  const [modelList, setModelList] = useState<ModelInfo[]>([]);
  const currentModel = useUIStore((s) => s.currentModel);
  const setCurrentModel = useUIStore((s) => s.setCurrentModel);
  const [confirmedTokens, setConfirmedTokens] = useState<Set<string>>(new Set());
  const [undoPanelOpen, setUndoPanelOpen] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);
  const backdropRef = useRef<HTMLDivElement>(null);
  const isComposingRef = useRef(false);

  // Build a Set of known slash command names for quick lookup
  const slashCommandNames = useMemo(
    () => new Set(SLASH_COMMANDS.map((c) => c.command)),
    []
  );

  // Render text with blue-highlighted chips for confirmed @mentions and /commands
  const renderHighlightedText = useCallback(
    (raw: string): React.ReactNode => {
      if (!raw) return "\u200B"; // zero-width space keeps height
      // Build regex from confirmed tokens + known slash commands
      const escaped: string[] = [];
      confirmedTokens.forEach((t) => escaped.push(t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
      slashCommandNames.forEach((c) => escaped.push(c.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
      if (escaped.length === 0) return raw + "\n"; // trailing \n keeps backdrop height in sync
      // Sort longest first to avoid partial matches
      escaped.sort((a, b) => b.length - a.length);
      const pattern = new RegExp(`(${escaped.join("|")})`, "g");
      const parts = raw.split(pattern);
      const allTokens = new Set([...confirmedTokens, ...slashCommandNames]);
      return (
        <>
          {parts.map((part, i) =>
            allTokens.has(part) ? (
              <mark
                key={i}
                className="rounded"
                style={{
                  backgroundColor: "color-mix(in srgb, var(--em-primary) 18%, transparent)",
                  color: "var(--em-primary)",
                }}
              >
                {part}
              </mark>
            ) : (
              <span key={i}>{part}</span>
            )
          )}
          {"\n"}
        </>
      );
    },
    [confirmedTokens, slashCommandNames]
  );

  // Sync textarea scroll to backdrop
  const syncScroll = useCallback(() => {
    if (textareaRef.current && backdropRef.current) {
      backdropRef.current.scrollTop = textareaRef.current.scrollTop;
      backdropRef.current.scrollLeft = textareaRef.current.scrollLeft;
    }
  }, []);

  // Auto-resize textarea height based on content
  const autoResize = useCallback(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    const next = Math.min(el.scrollHeight, 180);
    el.style.height = `${next}px`;
    el.style.overflow = next >= 180 ? "auto" : "hidden";
    syncScroll();
  }, [syncScroll]);

  // Fetch mention data from backend, supports path param for subfolder
  const fetchMentionData = useCallback(async (subpath?: string) => {
    try {
      const params = subpath ? `?path=${encodeURIComponent(subpath)}` : "";
      const res = await fetch(`${buildApiUrl("/mentions")}${params}`);
      if (res.ok) {
        const data = await res.json();
        setMentionData(data);
      }
    } catch {
      // Backend not available
    }
  }, []);

  // Fetch model list for /model inline picker
  const fetchModelList = useCallback(async () => {
    try {
      const data = await apiGet<{ models: ModelInfo[] }>("/models");
      setModelList(data.models);
    } catch {
      // Backend not available
    }
  }, []);

  // Insert @filename mentions at current cursor position for given files
  const insertFileMentions = useCallback((newFiles: File[]) => {
    setFiles((prev) => [...prev, ...newFiles]);
    // Track tokens for highlighting
    setConfirmedTokens((prev) => {
      const next = new Set(prev);
      newFiles.forEach((f) => next.add(`@${f.name}`));
      return next;
    });
    const textarea = textareaRef.current;
    const cursorPos = textarea?.selectionStart ?? text.length;
    const before = text.slice(0, cursorPos);
    const after = text.slice(cursorPos);
    const mentions = newFiles.map((f) => `@${f.name}`).join(" ");
    const needsSpace = before.length > 0 && !before.endsWith(" ") && !before.endsWith("\n");
    const prefix = needsSpace ? " " : "";
    const newText = before + prefix + mentions + " " + after;
    setText(newText);
    // Move cursor after inserted mentions
    const newCursorPos = (before + prefix + mentions + " ").length;
    requestAnimationFrame(() => {
      textarea?.focus();
      textarea?.setSelectionRange(newCursorPos, newCursorPos);
    });
  }, [text]);

  // Watch for confirmed Excel range selections from the excel-store
  const pendingSelection = useExcelStore((s) => s.pendingSelection);
  const clearPendingSelection = useExcelStore((s) => s.clearPendingSelection);

  useEffect(() => {
    if (!pendingSelection) return;
    const { filePath, sheet, range } = pendingSelection;
    const filename = filePath.split("/").pop() || filePath;
    const token = `@file:${filename}[${sheet}!${range}]`;

    setConfirmedTokens((prev) => new Set(prev).add(token));
    const textarea = textareaRef.current;
    const cursorPos = textarea?.selectionStart ?? text.length;
    const before = text.slice(0, cursorPos);
    const after = text.slice(cursorPos);
    const needsSpace = before.length > 0 && !before.endsWith(" ") && !before.endsWith("\n");
    const prefix = needsSpace ? " " : "";
    const newText = before + prefix + token + " " + after;
    setText(newText);
    const newCursorPos = (before + prefix + token + " ").length;
    requestAnimationFrame(() => {
      textarea?.focus();
      textarea?.setSelectionRange(newCursorPos, newCursorPos);
    });

    // Track in recent files
    const extLower = filename.slice(filename.lastIndexOf(".")).toLowerCase();
    if ([".xlsx", ".xls", ".csv"].includes(extLower)) {
      useExcelStore.getState().addRecentFile({
        path: filePath,
        filename,
      });
    }

    clearPendingSelection();
  }, [pendingSelection, clearPendingSelection, text]);

  const onDrop = useCallback((accepted: File[]) => {
    insertFileMentions(accepted);
  }, [insertFileMentions]);

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: ACCEPTED_EXTENSIONS,
    noClick: true,
    noKeyboard: true,
  });

  // Handle drag from ExcelFilesBar (custom data format, not native files)
  const handleExcelDrop = useCallback(
    (e: React.DragEvent) => {
      const excelData = e.dataTransfer.getData("application/x-excel-file");
      if (!excelData) return; // not from our sidebar, let dropzone handle it
      e.preventDefault();
      e.stopPropagation();
      try {
        const file = JSON.parse(excelData) as { path: string; filename: string };
        const mention = `@file:${file.filename}`;
        setConfirmedTokens((prev) => new Set(prev).add(mention));
        const textarea = textareaRef.current;
        const cursorPos = textarea?.selectionStart ?? text.length;
        const before = text.slice(0, cursorPos);
        const after = text.slice(cursorPos);
        const needsSpace = before.length > 0 && !before.endsWith(" ") && !before.endsWith("\n");
        const prefix = needsSpace ? " " : "";
        const newText = before + prefix + mention + " " + after;
        setText(newText);
        const newCursorPos = (before + prefix + mention + " ").length;
        requestAnimationFrame(() => {
          textarea?.focus();
          textarea?.setSelectionRange(newCursorPos, newCursorPos);
        });
      } catch {
        // invalid data, ignore
      }
    },
    [text]
  );

  // Filtered items for the popover
  const popoverItems = useMemo(() => {
    if (popover === "slash") {
      const filter = popoverFilter.toLowerCase();
      return SLASH_COMMANDS.filter(
        (c) => c.command.toLowerCase().includes(filter) || c.description.includes(filter)
      );
    }
    if (popover === "slash-args" && activeSlashCmd) {
      const cmd = SLASH_COMMANDS.find((c) => c.command === activeSlashCmd);
      if (!cmd?.args) return [];
      const filter = popoverFilter.toLowerCase();
      return cmd.args
        .filter((a) => a.toLowerCase().includes(filter))
        .map((a) => ({ command: `${activeSlashCmd} ${a}`, description: a, icon: cmd.icon }));
    }
    if (popover === "slash-skills") {
      const filter = popoverFilter.toLowerCase();
      const items: { command: string; description: string; icon: React.ReactNode }[] = [];
      if (mentionData) {
        for (const s of mentionData.skills) {
          if (!filter || s.name.toLowerCase().includes(filter) || (s.description || "").toLowerCase().includes(filter)) {
            items.push({ command: `/${s.name}`, description: s.description || "技能包", icon: <Sparkles className="h-3.5 w-3.5" /> });
          }
        }
      }
      if (items.length === 0) {
        items.push({ command: "", description: "暂无可用技能包", icon: <Sparkles className="h-3.5 w-3.5 opacity-30" /> });
      }
      return items;
    }
    if (popover === "slash-model") {
      const filter = popoverFilter.toLowerCase();
      const items: { command: string; description: string; icon: React.ReactNode; isActive?: boolean }[] = [];
      for (const m of modelList) {
        const label = m.name !== m.model ? `${m.name} → ${m.model}` : m.name;
        if (!filter || label.toLowerCase().includes(filter) || (m.description || "").toLowerCase().includes(filter)) {
          items.push({
            command: m.name,
            description: m.description || m.model,
            icon: m.name === currentModel
              ? <Check className="h-3.5 w-3.5" style={{ color: "var(--em-primary)" }} />
              : <Cpu className="h-3.5 w-3.5" />,
            isActive: m.name === currentModel,
          });
        }
      }
      if (items.length === 0) {
        items.push({ command: "", description: "暂无可用模型", icon: <Cpu className="h-3.5 w-3.5 opacity-30" /> });
      }
      return items;
    }
    if (popover === "at") {
      // Top-level categories + direct search across all items
      const filter = popoverFilter.toLowerCase();
      const items: { command: string; description: string; icon: React.ReactNode; hasChildren?: boolean }[] = [];

      // Show categories first
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

      // If there's a filter, also search sub-items directly
      if (filter && mentionData) {
        for (const f of mentionData.files) {
          if (f.toLowerCase().includes(filter)) {
            items.push({ command: `@${f}`, description: "文件", icon: <FileSpreadsheet className="h-3.5 w-3.5" /> });
          }
        }
        for (const t of mentionData.tools) {
          if (t.toLowerCase().includes(filter)) {
            items.push({ command: `@${t}`, description: "工具", icon: <Wrench className="h-3.5 w-3.5" /> });
          }
        }
        for (const s of mentionData.skills) {
          if (s.name.toLowerCase().includes(filter)) {
            items.push({ command: `@${s.name}`, description: s.description || "技能", icon: <Sparkles className="h-3.5 w-3.5" /> });
          }
        }
      }
      return items;
    }
    if (popover === "at-sub" && atCategory && mentionData) {
      const filter = popoverFilter.toLowerCase();
      const items: { command: string; description: string; icon: React.ReactNode }[] = [];

      if (atCategory === "file") {
        for (const f of mentionData.files) {
          if (!filter || f.toLowerCase().includes(filter)) {
            const icon = f.endsWith("/")
              ? <FolderOpen className="h-3.5 w-3.5" />
              : <FileSpreadsheet className="h-3.5 w-3.5" />;
            items.push({ command: `@file:${f}`, description: f.endsWith("/") ? "目录" : "文件", icon });
          }
        }
        if (items.length === 0) {
          items.push({ command: "", description: "工作区无匹配文件", icon: <FileSpreadsheet className="h-3.5 w-3.5 opacity-30" /> });
        }
      } else if (atCategory === "tool") {
        for (const t of mentionData.tools) {
          if (!filter || t.toLowerCase().includes(filter)) {
            items.push({ command: `@tool:${t}`, description: "工具", icon: <Wrench className="h-3.5 w-3.5" /> });
          }
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
  }, [popover, popoverFilter, activeSlashCmd, atCategory, mentionData, modelList, currentModel]);

  // Reset selected index when items change
  useEffect(() => {
    setSelectedIndex(0);
  }, [popoverItems.length]);

  const closePopover = () => {
    setPopover(null);
    setPopoverFilter("");
    setActiveSlashCmd(null);
    setAtCategory(null);
  };

  const handleTextChange = (value: string) => {
    setText(value);
    requestAnimationFrame(autoResize);

    // Detect / at start of input
    if (value === "/") {
      setPopover("slash");
      setPopoverFilter("");
      return;
    }

    if (value.startsWith("/") && popover === "slash") {
      setPopoverFilter(value.slice(1));
      return;
    }

    // Detect space after a slash command → show args
    if (popover === "slash" && value.includes(" ")) {
      const cmd = value.split(" ")[0];
      const matched = SLASH_COMMANDS.find((c) => c.command === cmd);
      if (matched?.args) {
        setActiveSlashCmd(cmd);
        setPopover("slash-args");
        setPopoverFilter(value.split(" ").slice(1).join(" "));
        return;
      }
      closePopover();
      return;
    }

    if (popover === "slash-args") {
      const parts = value.split(" ");
      setPopoverFilter(parts.slice(1).join(" "));
      return;
    }

    // In skill/model sub-pickers, treat any text as filter
    if (popover === "slash-skills" || popover === "slash-model") {
      setPopoverFilter(value);
      return;
    }

    // Detect @ anywhere
    const lastAtIdx = value.lastIndexOf("@");
    if (lastAtIdx >= 0 && (lastAtIdx === 0 || value[lastAtIdx - 1] === " ")) {
      const afterAt = value.slice(lastAtIdx + 1);
      if (!afterAt.includes(" ")) {
        fetchMentionData();
        setPopover("at");
        setPopoverFilter(afterAt);
        return;
      }
    }

    if (popover) closePopover();
  };

  const selectPopoverItem = (item: { command: string; hasChildren?: boolean }) => {
    if (popover === "slash") {
      // Special: /undo → open UndoPanel
      if (item.command === "/undo") {
        closePopover();
        setText("");
        setUndoPanelOpen(true);
        return;
      }
      // Special: /skills → drill into skill picker
      if (item.command === "/skills") {
        fetchMentionData();
        setPopover("slash-skills");
        setPopoverFilter("");
        setText("");
        textareaRef.current?.focus();
        return;
      }
      // Special: /model → drill into model picker
      if (item.command === "/model") {
        fetchModelList();
        setPopover("slash-model");
        setPopoverFilter("");
        setText("");
        textareaRef.current?.focus();
        return;
      }
      // If command has args, drill into args sub-menu
      const cmd = SLASH_COMMANDS.find((c) => c.command === item.command);
      if (cmd?.args) {
        setActiveSlashCmd(item.command);
        setPopover("slash-args");
        setPopoverFilter("");
        setText(item.command + " ");
        textareaRef.current?.focus();
        return;
      }
      // No args → fill text and close
      setText(item.command + " ");
      closePopover();
    } else if (popover === "slash-args") {
      // Auto-execute toggle args (on/off/status etc) immediately
      const argPart = item.command.split(" ").slice(1).join(" ");
      if (AUTO_EXEC_ARGS.has(argPart)) {
        closePopover();
        setText("");
        // Fire the command
        handleSendCommand(item.command);
        return;
      }
      setText(item.command + " ");
      closePopover();
    } else if (popover === "slash-skills") {
      if (!item.command) return;
      // Insert skill command and close
      setText(item.command + " ");
      closePopover();
      textareaRef.current?.focus();
      return;
    } else if (popover === "slash-model") {
      if (!item.command) return;
      // Switch model directly via API
      handleModelSwitch(item.command);
      return;
    } else if (popover === "at" && item.hasChildren) {
      // Drill into sub-level (e.g. @file → show file list)
      const category = item.command.replace("@", "");
      setAtCategory(category);
      setPopover("at-sub");
      setPopoverFilter("");
      fetchMentionData(); // refresh root
      textareaRef.current?.focus();
      return;
    } else if (popover === "at" || popover === "at-sub") {
      if (!item.command) return; // placeholder "no matches"
      // If item is a folder, drill into it
      if (item.command.endsWith("/")) {
        const folderPath = item.command.replace(/^@(?:file:)?/, "");
        fetchMentionData(folderPath); // fetch subfolder contents
        setPopoverFilter("");
        textareaRef.current?.focus();
        return;
      }
      // Track confirmed @mention for highlighting
      setConfirmedTokens((prev) => new Set(prev).add(item.command));
      const lastAtIdx = text.lastIndexOf("@");
      const before = text.slice(0, lastAtIdx);
      setText(before + item.command + " ");
      // Track Excel files in recent files bar
      const mentionName = item.command.replace(/^@(?:file:|folder:|skill:|mcp:|tool:)?/, "");
      const extLower = mentionName.slice(mentionName.lastIndexOf(".")).toLowerCase();
      if ([".xlsx", ".xls", ".csv"].includes(extLower)) {
        useExcelStore.getState().addRecentFile({
          path: mentionName,
          filename: mentionName.split("/").pop() || mentionName,
        });
      }
      closePopover();
    }
    textareaRef.current?.focus();
  };

  // Handle model switch from inline picker
  const handleModelSwitch = async (name: string) => {
    if (name === currentModel) {
      closePopover();
      setText("");
      if (onCommandResult) {
        onCommandResult("/model", `当前已是 **${name}**`, "markdown");
      }
      return;
    }
    try {
      await apiPut("/models/active", { name });
      setCurrentModel(name);
      closePopover();
      setText("");
      if (onCommandResult) {
        onCommandResult("/model", `已切换到 **${name}**`, "markdown");
      }
    } catch {
      closePopover();
      setText("");
      if (onCommandResult) {
        onCommandResult("/model", `切换到 ${name} 失败`, "text");
      }
    }
  };

  // Send a slash command programmatically (used for auto-exec args)
  const handleSendCommand = async (command: string) => {
    const trimmed = command.trim();
    // Check frontend actions first
    const action = FRONTEND_ACTIONS[trimmed.split(" ")[0]];
    if (action === "stop") { onStop?.(); return; }
    if (action === "clear") {
      const { currentSessionId } = useChatStore.getState();
      useChatStore.getState().clearMessages();
      if (currentSessionId) {
        fetch(buildApiUrl(`/sessions/${currentSessionId}/clear`), { method: "POST" }).catch(() => {});
      }
      if (onCommandResult) onCommandResult("/clear", "对话历史已清除", "text");
      return;
    }
    // Display commands → show in dialog
    if (onCommandResult && DISPLAY_COMMANDS.has(trimmed)) {
      try {
        const res = await fetch(buildApiUrl("/command"), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ command: trimmed }),
        });
        if (res.ok) {
          const data = await res.json();
          onCommandResult(trimmed, data.result, data.format || "text");
          return;
        }
      } catch { /* fall through */ }
    }
    // Everything else → send as chat
    onSend(trimmed);
  };

  const handleSend = async () => {
    const trimmed = text.trim();
    if (!trimmed && files.length === 0) return;
    closePopover();

    if (trimmed.startsWith("/")) {
      // 0) /undo (bare) → open UndoPanel
      if (trimmed === "/undo") {
        setText("");
        setUndoPanelOpen(true);
        return;
      }

      // 1) Frontend-only actions (/stop, /clear)
      const action = FRONTEND_ACTIONS[trimmed.split(" ")[0]];
      if (action === "stop") {
        onStop?.();
        setText("");
        return;
      }
      if (action === "clear") {
        const { currentSessionId } = useChatStore.getState();
        useChatStore.getState().clearMessages();
        setText("");
        if (currentSessionId) {
          fetch(buildApiUrl(`/sessions/${currentSessionId}/clear`), { method: "POST" }).catch(() => {});
        }
        if (onCommandResult) {
          onCommandResult("/clear", "对话历史已清除", "text");
        }
        return;
      }
      if ((action === "accept" || action === "reject") && trimmed.split(" ").length === 1) {
        // Bare /accept or /reject without explicit ID → auto-fill from pending approval
        const state = useChatStore.getState();
        const pending = state.pendingApproval;
        if (pending) {
          state.setPendingApproval(null);
          const cmd = `/${action} ${pending.id}`;
          onSend(cmd);
          setText("");
        } else {
          if (onCommandResult) {
            onCommandResult(`/${action}`, "当前没有待审批的操作", "text");
          }
          setText("");
        }
        return;
      }

      // 2) /model <name> → switch model directly
      if (trimmed.startsWith("/model ") && !DISPLAY_COMMANDS.has(trimmed)) {
        const modelName = trimmed.slice("/model ".length).trim();
        if (modelName) {
          setText("");
          handleModelSwitch(modelName);
          return;
        }
      }

      // 3) Display-type commands → show in dialog (exact match only)
      if (onCommandResult && DISPLAY_COMMANDS.has(trimmed)) {
        try {
          const res = await fetch(buildApiUrl("/command"), {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ command: trimmed }),
          });
          if (res.ok) {
            const data = await res.json();
            onCommandResult(trimmed, data.result, data.format || "text");
            setText("");
            return;
          }
        } catch {
          // Fall through to send as chat
        }
      }
    }

    // 3) Everything else → send as chat message
    onSend(trimmed, files.length > 0 ? files : undefined);
    setText("");
    setFiles([]);
    setConfirmedTokens(new Set());
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    // Ignore Enter during IME composition (e.g. Chinese pinyin input)
    if (isComposingRef.current) return;

    // Popover navigation
    if (popover && popoverItems.length > 0) {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setSelectedIndex((i) => (i + 1) % popoverItems.length);
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setSelectedIndex((i) => (i - 1 + popoverItems.length) % popoverItems.length);
        return;
      }
      if (e.key === "Tab" || (e.key === "Enter" && popover)) {
        e.preventDefault();
        selectPopoverItem(popoverItems[selectedIndex]);
        return;
      }
      if (e.key === "Escape") {
        e.preventDefault();
        closePopover();
        return;
      }
    }

    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };


  const removeFile = useCallback((index: number) => {
    setFiles((prev) => prev.filter((_, i) => i !== index));
  }, []);

  return (
    <div
      {...getRootProps()}
      onDrop={(e) => {
        // Intercept Excel sidebar drags before dropzone
        if (e.dataTransfer.types.includes("application/x-excel-file")) {
          handleExcelDrop(e);
          return;
        }
        // Let dropzone handle native file drops
        getRootProps().onDrop?.(e as any);
      }}
      onDragOver={(e) => {
        if (e.dataTransfer.types.includes("application/x-excel-file")) {
          e.preventDefault();
          e.dataTransfer.dropEffect = "copy";
        }
      }}
      className={`relative rounded-[20px] border bg-background transition-all duration-200 chat-input-ring ${
        isDragActive
          ? "border-[var(--em-primary-light)] bg-[var(--em-primary)]/5 shadow-lg shadow-[var(--em-primary)]/10"
          : "border-border/60 shadow-[0_1px_8px_rgba(0,0,0,0.06)] dark:shadow-[0_1px_8px_rgba(0,0,0,0.25)] hover:shadow-[0_2px_12px_rgba(0,0,0,0.1)] dark:hover:shadow-[0_2px_12px_rgba(0,0,0,0.35)] focus-within:shadow-[0_2px_14px_rgba(0,0,0,0.12)] dark:focus-within:shadow-[0_2px_14px_rgba(0,0,0,0.45)] focus-within:border-border"
      }`}
    >
      <input {...getInputProps()} />

      {/* Slash / @ Popover */}
      {popover && popoverItems.length > 0 && (
        <div
          ref={popoverRef}
          className="absolute bottom-full left-0 right-0 mb-2 bg-card border border-border/60 rounded-2xl shadow-xl overflow-hidden z-50"
        >
          <div className="px-3.5 py-2 text-[11px] text-muted-foreground border-b border-border/40 flex items-center gap-1.5">
            {popover === "at" && <><AtSign className="h-3 w-3" /> 提及</>}
            {popover === "at-sub" && (
              <>
                <button
                  className="hover:text-foreground transition-colors"
                  onClick={() => { setPopover("at"); setAtCategory(null); setPopoverFilter(""); }}
                >
                  <AtSign className="h-3 w-3" />
                </button>
                <ChevronRight className="h-2.5 w-2.5" />
                <span className="font-medium text-foreground">{atCategory}</span>
              </>
            )}
            {(popover === "slash" || popover === "slash-args") && <><Terminal className="h-3 w-3" /> 命令</>}
            {popover === "slash-args" && activeSlashCmd && (
              <>
                <ChevronRight className="h-2.5 w-2.5" />
                <span className="font-medium text-foreground">{activeSlashCmd}</span>
              </>
            )}
            {popover === "slash-skills" && (
              <>
                <button
                  className="hover:text-foreground transition-colors"
                  onClick={() => { setPopover("slash"); setPopoverFilter(""); setText("/"); }}
                >
                  <Terminal className="h-3 w-3" />
                </button>
                <ChevronRight className="h-2.5 w-2.5" />
                <Sparkles className="h-3 w-3" />
                <span className="font-medium text-foreground">选择技能</span>
              </>
            )}
            {popover === "slash-model" && (
              <>
                <button
                  className="hover:text-foreground transition-colors"
                  onClick={() => { setPopover("slash"); setPopoverFilter(""); setText("/"); }}
                >
                  <Terminal className="h-3 w-3" />
                </button>
                <ChevronRight className="h-2.5 w-2.5" />
                <Cpu className="h-3 w-3" />
                <span className="font-medium text-foreground">切换模型</span>
              </>
            )}
            <span className="ml-auto text-[10px] opacity-60 hidden sm:inline">↑↓ 导航 · Tab 选择 · Esc 关闭</span>
          </div>
          <div className="max-h-48 sm:max-h-60 overflow-y-auto py-1">
            {popoverItems.map((item, i) => {
              const isActive = "isActive" in item && (item as { isActive?: boolean }).isActive;
              const hasChildren = "hasChildren" in item && (item as { hasChildren?: boolean }).hasChildren;
              // Show drill-down arrow for /skills and /model in main slash menu
              const isDrillable = popover === "slash" && (item.command === "/skills" || item.command === "/model");
              return (
                <button
                  key={item.command || `empty-${i}`}
                  className={`w-full flex items-center gap-2.5 px-3.5 py-2 text-sm text-left transition-colors ${
                    item.command ? (i === selectedIndex ? "bg-[var(--em-primary-alpha-10)]" : "hover:bg-accent/40") : "opacity-50 cursor-default"
                  }`}
                  onPointerEnter={() => item.command && setSelectedIndex(i)}
                  onClick={() => item.command && selectPopoverItem(item)}
                >
                  <span className="text-muted-foreground flex-shrink-0">
                    {item.icon}
                  </span>
                  <span
                    className={`font-mono text-xs flex-1 truncate ${isActive ? "font-semibold" : ""}`}
                    style={{ color: item.command ? "var(--em-primary)" : undefined }}
                  >
                    {item.command || item.description}
                  </span>
                  {item.command && (
                    <span className="text-xs text-muted-foreground truncate">
                      {item.description}
                    </span>
                  )}
                  {(hasChildren || isDrillable) && (
                    <ChevronRight className="h-3 w-3 text-muted-foreground flex-shrink-0" />
                  )}
                </button>
              );
            })}
          </div>
        </div>
      )}


      {/* File attachment chips */}
      {files.length > 0 && (
        <div className="flex flex-wrap gap-1.5 px-4 sm:px-14 pt-2.5 pb-0">
          {files.map((f, i) => (
            <span
              key={`${f.name}-${i}`}
              className="inline-flex items-center gap-1 rounded-full bg-[var(--em-primary-alpha-10)] text-[var(--em-primary)] text-xs font-medium pl-2.5 pr-1 py-0.5"
            >
              {f.name}
              <button
                type="button"
                className="rounded-full p-0.5 hover:bg-[var(--em-primary-alpha-20)] transition-colors"
                onClick={() => removeFile(i)}
              >
                <X className="h-3 w-3" />
              </button>
            </span>
          ))}
        </div>
      )}

      {/* Main input row: [+] textarea [send] */}
      <div className="flex items-end gap-1 px-1.5 py-1.5">
        {/* Attach button (left) */}
        <Button
          variant="ghost"
          size="icon"
          className="h-8 w-8 rounded-full flex-shrink-0 text-muted-foreground hover:text-foreground"
          onClick={() => fileInputRef.current?.click()}
        >
          <Plus className="h-4 w-4" />
        </Button>
        <input
          ref={fileInputRef}
          type="file"
          className="hidden"
          accept=".xlsx,.xls,.csv,.png,.jpg,.jpeg"
          multiple
          onChange={(e) => {
            if (e.target.files) {
              insertFileMentions(Array.from(e.target.files!));
            }
            e.target.value = "";
          }}
        />

        {/* Highlighted backdrop + Textarea overlay */}
        <div className="relative flex-1 min-w-0">
          {/* Backdrop: renders highlighted text behind the transparent textarea */}
          <div
            ref={backdropRef}
            aria-hidden="true"
            className="absolute inset-0 pointer-events-none overflow-hidden whitespace-pre-wrap break-words font-sans"
            style={{ color: "var(--foreground)", padding: "8px 8px", fontSize: "13px", lineHeight: "20px" }}
          >
            {renderHighlightedText(text)}
          </div>
          {/* Actual textarea: text is transparent, caret remains visible */}
          <Textarea
            ref={textareaRef}
            value={text}
            onChange={(e) => handleTextChange(e.target.value)}
            onKeyDown={handleKeyDown}
            onScroll={syncScroll}
            onCompositionStart={() => { isComposingRef.current = true; }}
            onCompositionEnd={() => { isComposingRef.current = false; }}
            placeholder="有问题，尽管问"
            disabled={disabled}
            className="min-h-[36px] max-h-[180px] resize-none border-0 bg-transparent shadow-none
              focus-visible:ring-0 focus-visible:ring-offset-0
              text-transparent caret-foreground selection:bg-[var(--em-primary)]/20 relative z-10"
            style={{ padding: "8px 8px", fontSize: "13px", lineHeight: "20px", fontFamily: "inherit" }}
            rows={1}
          />
        </div>

        {/* Send / Stop button (right) */}
        <div className="flex-shrink-0">
          {isStreaming ? (
            <Button
              size="icon"
              className="h-8 w-8 rounded-full bg-foreground hover:bg-foreground/80"
              onClick={onStop}
            >
              <Square className="h-3 w-3 fill-background text-background" />
            </Button>
          ) : (
            <Button
              size="icon"
              className="h-8 w-8 rounded-full text-white transition-opacity"
              style={{ backgroundColor: "var(--em-primary)" }}
              onClick={handleSend}
              disabled={disabled || (!text.trim() && files.length === 0)}
            >
              <ArrowUp className="h-3.5 w-3.5" />
            </Button>
          )}
        </div>
      </div>
      <UndoPanel open={undoPanelOpen} onClose={() => setUndoPanelOpen(false)} />
    </div>
  );
}
