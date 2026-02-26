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
  Loader2,
  AlertCircle,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { Pencil, Search as SearchIcon, ClipboardList as ClipboardListIcon } from "lucide-react";
import { useDropzone } from "react-dropzone";
import { useChatStore } from "@/stores/chat-store";
import { useUIStore } from "@/stores/ui-store";
import { useExcelStore } from "@/stores/excel-store";
import { buildApiUrl, apiGet, apiPut, uploadFile } from "@/lib/api";
import { UndoPanel } from "@/components/modals/UndoPanel";
import type { ModelInfo, AttachedFile } from "@/lib/types";

const ACCEPTED_EXTENSIONS = {
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": [".xlsx"],
  "application/vnd.ms-excel": [".xls"],
  "text/csv": [".csv"],
  "image/png": [".png"],
  "image/jpeg": [".jpg", ".jpeg"],
};

const IMAGE_EXTS = new Set([".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"]);
function isImageFile(name: string): boolean {
  const ext = name.slice(name.lastIndexOf(".")).toLowerCase();
  return IMAGE_EXTS.has(ext);
}

// 斜杠命令（对应 CLI _STATIC_SLASH_COMMANDS + control_commands）
const SLASH_COMMANDS: { command: string; description: string; icon: React.ReactNode; args?: string[] }[] = [
  // 基础命令
  { command: "/help", description: "显示帮助", icon: <HelpCircle className="h-3.5 w-3.5" /> },
  { command: "/skills", description: "查看技能包", icon: <Sparkles className="h-3.5 w-3.5" /> },
  { command: "/history", description: "对话历史摘要", icon: <HistoryIcon className="h-3.5 w-3.5" /> },
  { command: "/clear", description: "清除对话历史", icon: <Trash2 className="h-3.5 w-3.5" /> },
  { command: "/mcp", description: "MCP Server 状态", icon: <Terminal className="h-3.5 w-3.5" /> },
  { command: "/save", description: "保存对话记录", icon: <Save className="h-3.5 w-3.5" /> },
  { command: "/config", description: "环境变量配置", icon: <Settings className="h-3.5 w-3.5" />, args: ["list", "set", "get", "delete"] },
  // 控制命令
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

// 在对话框中展示结果而非作为聊天发送的命令
// 注意：仅检查精确匹配。/model 单独使用是展示，但 /model <name> 是操作。
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

// 直接执行前端操作的命令（不会发送到聊天）
const FRONTEND_ACTIONS: Record<string, string> = {
  "/stop": "stop",
  "/clear": "clear",
  "/accept": "accept",
  "/reject": "reject",
};

function friendlyUploadError(err: unknown): string {
  const msg = err instanceof Error ? err.message : String(err);
  if (/413|too large|过大/i.test(msg)) return "文件过大，请压缩后重试";
  if (/extension|格式|不支持|unsupported/i.test(msg)) return "不支持该文件格式";
  if (/quota|配额|空间/i.test(msg)) return "存储空间不足";
  if (/401|403|权限/i.test(msg)) return "没有上传权限";
  if (/network|fetch|连接/i.test(msg)) return "网络连接失败，请检查网络后重试";
  return "上传失败，请重试";
}

interface ChatInputProps {
  onSend: (text: string, files?: AttachedFile[]) => void;
  onCommandResult?: (command: string, result: string, format: "markdown" | "text") => void;
  disabled?: boolean;
  isStreaming?: boolean;
  onStop?: () => void;
}

type PopoverMode = null | "slash" | "slash-args" | "slash-skills" | "slash-model" | "at" | "at-sub";

// 参数选择后自动执行的命令（无需额外按 Enter）
const AUTO_EXEC_ARGS = new Set(["on", "off", "status", "build", "approve", "reject", "apply", "list"]);

interface MentionData {
  tools: string[];
  skills: { name: string; description: string }[];
  files: string[];
}

const CHAT_MODE_COLORS: Record<string, { text: string; bg: string }> = {
  write: { text: "var(--em-primary)", bg: "var(--em-primary-alpha-10)" },
  read:  { text: "var(--em-cyan)",    bg: "color-mix(in srgb, var(--em-cyan) 10%, transparent)" },
  plan:  { text: "var(--em-gold)",    bg: "color-mix(in srgb, var(--em-gold) 12%, transparent)" },
};

const CHAT_MODES = [
  { key: "write" as const, label: "写入", icon: Pencil },
  { key: "read" as const, label: "读取", icon: SearchIcon },
  { key: "plan" as const, label: "计划", icon: ClipboardListIcon },
];

function ChatModeTabs() {
  const chatMode = useUIStore((s) => s.chatMode);
  const setChatMode = useUIStore((s) => s.setChatMode);
  return (
    <div className="flex items-center gap-0.5 px-3 pt-1.5 pb-0">
      {CHAT_MODES.map(({ key, label, icon: Icon }) => (
        <button
          key={key}
          onClick={() => setChatMode(key)}
          className={`inline-flex items-center gap-1 px-2.5 sm:py-1 py-0.5 rounded-lg text-xs font-medium transition-colors ${
            chatMode === key
              ? ""
              : "text-muted-foreground hover:text-foreground hover:bg-accent/40"
          }`}
          style={
            chatMode === key
              ? { color: CHAT_MODE_COLORS[key].text, backgroundColor: CHAT_MODE_COLORS[key].bg }
              : undefined
          }
        >
          <Icon className="sm:h-3 sm:w-3 h-2.5 w-2.5" />
          {label}
        </button>
      ))}
    </div>
  );
}

export function ChatInput({ onSend, onCommandResult, disabled, isStreaming, onStop }: ChatInputProps) {
  const [text, setText] = useState("");
  const [files, setFiles] = useState<AttachedFile[]>([]);
  const [popover, setPopover] = useState<PopoverMode>(null);
  const [popoverFilter, setPopoverFilter] = useState("");
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [activeSlashCmd, setActiveSlashCmd] = useState<string | null>(null);
  const [atCategory, setAtCategory] = useState<string | null>(null);
  const [mentionData, setMentionData] = useState<MentionData | null>(null);
  const [modelList, setModelList] = useState<ModelInfo[]>([]);
  const currentModel = useUIStore((s) => s.currentModel);
  const setCurrentModel = useUIStore((s) => s.setCurrentModel);
  const visionCapable = useUIStore((s) => s.visionCapable);
  const [confirmedTokens, setConfirmedTokens] = useState<Set<string>>(new Set());
  const [undoPanelOpen, setUndoPanelOpen] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);
  const backdropRef = useRef<HTMLDivElement>(null);
  const isComposingRef = useRef(false);

  // 图片缩略图的稳定预览 URL — 避免每次重渲染都创建新的
  // blob URL，防止移动浏览器在用户编辑时回收预览。
  const previewUrlCache = useRef(new Map<File, string>());

  const getPreviewUrl = useCallback((file: File): string => {
    let url = previewUrlCache.current.get(file);
    if (!url) {
      url = URL.createObjectURL(file);
      previewUrlCache.current.set(file, url);
    }
    return url;
  }, []);

  // 撤销已移除文件的 URL
  useEffect(() => {
    const currentFiles = new Set(files.map((af) => af.file));
    previewUrlCache.current.forEach((url, file) => {
      if (!currentFiles.has(file)) {
        URL.revokeObjectURL(url);
        previewUrlCache.current.delete(file);
      }
    });
  }, [files]);

  // 卸载时撤销所有预览 URL
  useEffect(() => {
    const cache = previewUrlCache.current;
    return () => {
      cache.forEach((url) => URL.revokeObjectURL(url));
      cache.clear();
    };
  }, []);

  // 构建已知斜杠命令名称的 Set 用于快速查找
  const slashCommandNames = useMemo(
    () => new Set(SLASH_COMMANDS.map((c) => c.command)),
    []
  );

  // 为已确认的 @提及和 /命令渲染蓝色高亮标签
  const renderHighlightedText = useCallback(
    (raw: string): React.ReactNode => {
      if (!raw) return "\u200B"; // 零宽空格保持高度
      // 从已确认的 token 和已知斜杠命令构建正则
      const escaped: string[] = [];
      confirmedTokens.forEach((t) => escaped.push(t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
      slashCommandNames.forEach((c) => escaped.push(c.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
      if (escaped.length === 0) return raw + "\n"; // 尾部 \n 使背景与高度同步
      // 最长优先排序以避免部分匹配
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

  // 同步文本区滚动到背景层
  const syncScroll = useCallback(() => {
    if (textareaRef.current && backdropRef.current) {
      backdropRef.current.scrollTop = textareaRef.current.scrollTop;
      backdropRef.current.scrollLeft = textareaRef.current.scrollLeft;
    }
  }, []);

  // 根据内容自动调整输入框高度
  const autoResize = useCallback(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    const next = Math.min(el.scrollHeight, 180);
    el.style.height = `${next}px`;
    el.style.overflow = next >= 180 ? "auto" : "hidden";
    syncScroll();
  }, [syncScroll]);

  // 从后端获取提及数据，支持 path 参数用于子目录
  const fetchMentionData = useCallback(async (subpath?: string) => {
    try {
      const params = subpath ? `?path=${encodeURIComponent(subpath)}` : "";
      const res = await fetch(`${buildApiUrl("/mentions")}${params}`);
      if (res.ok) {
        const data = await res.json();
        setMentionData(data);
      }
    } catch {
      // 后端不可用
    }
  }, []);

  // 获取模型列表用于 /model 内联选择器
  const fetchModelList = useCallback(async () => {
    try {
      const data = await apiGet<{ models: ModelInfo[] }>("/models");
      setModelList(data.models);
    } catch {
      // 后端不可用
    }
  }, []);

  // 预先上传单个附件。成功/失败时更新状态。
  const triggerUpload = useCallback(async (id: string, file: File) => {
    try {
      const result = await uploadFile(file);
      setFiles((prev) =>
        prev.map((f) =>
          f.id === id ? { ...f, status: "success" as const, uploadResult: result } : f
        )
      );
    } catch (err) {
      const error = friendlyUploadError(err);
      setFiles((prev) =>
        prev.map((f) =>
          f.id === id ? { ...f, status: "failed" as const, error } : f
        )
      );
    }
  }, []);

  // 重试失败的上传
  const retryUpload = useCallback(
    (id: string, file: File) => {
      setFiles((prev) =>
        prev.map((f) =>
          f.id === id ? { ...f, status: "uploading" as const, error: undefined } : f
        )
      );
      triggerUpload(id, file);
    },
    [triggerUpload]
  );

  // 按 id 移除附件
  const removeFile = useCallback((id: string) => {
    setFiles((prev) => prev.filter((f) => f.id !== id));
  }, []);

  // 在当前光标位置插入 @文件名 提及。
  // 图片文件仅作为附件添加（无文本提及），避免在移动端擑大输入区。
  // 文件在附加时预先上传；错误内联显示。
  const insertFileMentions = useCallback((newFiles: File[]) => {
    const attached: AttachedFile[] = newFiles.map((f) => ({
      id: `${Date.now()}-${Math.random().toString(36).slice(2)}`,
      file: f,
      status: "uploading" as const,
    }));
    setFiles((prev) => [...prev, ...attached]);
    // 为每个文件触发预上传
    for (const af of attached) {
      triggerUpload(af.id, af.file);
    }
    // 仅为非图片文件（Excel/CSV）插入文本提及
    const docFiles = newFiles.filter((f) => !isImageFile(f.name));
    if (docFiles.length > 0) {
      setConfirmedTokens((prev) => {
        const next = new Set(prev);
        docFiles.forEach((f) => next.add(`@${f.name}`));
        return next;
      });
      const textarea = textareaRef.current;
      const cursorPos = textarea?.selectionStart ?? text.length;
      const before = text.slice(0, cursorPos);
      const after = text.slice(cursorPos);
      const mentions = docFiles.map((f) => `@${f.name}`).join(" ");
      const needsSpace = before.length > 0 && !before.endsWith(" ") && !before.endsWith("\n");
      const prefix = needsSpace ? " " : "";
      const newText = before + prefix + mentions + " " + after;
      setText(newText);
      const newCursorPos = (before + prefix + mentions + " ").length;
      requestAnimationFrame(() => {
        textarea?.focus();
        textarea?.setSelectionRange(newCursorPos, newCursorPos);
      });
    }
  }, [text, triggerUpload]);

  // 监听来自 excel-store 的已确认 Excel 范围选择
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

    // 加入最近文件列表
    const extLower = filename.slice(filename.lastIndexOf(".")).toLowerCase();
    if ([".xlsx", ".xls", ".csv"].includes(extLower)) {
      useExcelStore.getState().addRecentFile({
        path: filePath,
        filename,
      });
    }

    clearPendingSelection();
  }, [pendingSelection, clearPendingSelection, text]);

  // 监听侧边栏 @ 按钮的快捷添加文件提及
  const pendingFileMention = useExcelStore((s) => s.pendingFileMention);
  const clearPendingFileMention = useExcelStore((s) => s.clearPendingFileMention);

  useEffect(() => {
    if (!pendingFileMention) return;
    const { path, filename } = pendingFileMention;
    const mention = `@file:${filename}`;

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
      autoResize();
    });

    // 记录到最近文件
    const extLower = filename.slice(filename.lastIndexOf(".")).toLowerCase();
    if ([".xlsx", ".xls", ".csv"].includes(extLower)) {
      useExcelStore.getState().addRecentFile({ path, filename });
    }

    clearPendingFileMention();
  }, [pendingFileMention, clearPendingFileMention, text, autoResize]);

  const onDrop = useCallback((accepted: File[]) => {
    insertFileMentions(accepted);
  }, [insertFileMentions]);

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: ACCEPTED_EXTENSIONS,
    noClick: true,
    noKeyboard: true,
  });

  // 处理来自 ExcelFilesBar 的拖拽（自定义数据格式，非原生文件）
  const handleExcelDrop = useCallback(
    (e: React.DragEvent) => {
      const excelData = e.dataTransfer.getData("application/x-excel-file");
      if (!excelData) return; // 非来自侧边栏，交给 dropzone 处理
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
        // 无效数据，忽略
      }
    },
    [text]
  );

  // 弹出层的过滤项
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
      // 顶级分类 + 跨所有项目的直接搜索
      const filter = popoverFilter.toLowerCase();
      const items: { command: string; description: string; icon: React.ReactNode; hasChildren?: boolean }[] = [];

      // 先显示分类
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

      // 如果有过滤条件，也直接搜索子项
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

  // 项目变化时重置选中索引
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

    // 检测输入开头的 /
    if (value === "/") {
      setPopover("slash");
      setPopoverFilter("");
      return;
    }

    if (value.startsWith("/") && popover === "slash") {
      setPopoverFilter(value.slice(1));
      return;
    }

    // 检测斜杠命令后的空格 → 显示参数
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

    // 在技能/模型子选择器中，将任何文本视为过滤条件
    if (popover === "slash-skills" || popover === "slash-model") {
      setPopoverFilter(value);
      return;
    }

    // 检测任意位置的 @
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
      // 特殊处理：/undo → 打开撤销面板
      if (item.command === "/undo") {
        closePopover();
        setText("");
        setUndoPanelOpen(true);
        return;
      }
      // 特殊处理：/skills → 进入技能选择器
      if (item.command === "/skills") {
        fetchMentionData();
        setPopover("slash-skills");
        setPopoverFilter("");
        setText("");
        textareaRef.current?.focus();
        return;
      }
      // 特殊处理：/model → 进入模型选择器
      if (item.command === "/model") {
        fetchModelList();
        setPopover("slash-model");
        setPopoverFilter("");
        setText("");
        textareaRef.current?.focus();
        return;
      }
      // 如果命令有参数，进入参数子菜单
      const cmd = SLASH_COMMANDS.find((c) => c.command === item.command);
      if (cmd?.args) {
        setActiveSlashCmd(item.command);
        setPopover("slash-args");
        setPopoverFilter("");
        setText(item.command + " ");
        textareaRef.current?.focus();
        return;
      }
      // 无参数 → 填充文本并关闭
      setText(item.command + " ");
      closePopover();
    } else if (popover === "slash-args") {
      // 立即自动执行开关参数（on/off/status 等）
      const argPart = item.command.split(" ").slice(1).join(" ");
      if (AUTO_EXEC_ARGS.has(argPart)) {
        closePopover();
        setText("");
        // 触发命令
        handleSendCommand(item.command);
        return;
      }
      setText(item.command + " ");
      closePopover();
    } else if (popover === "slash-skills") {
      if (!item.command) return;
      // 插入技能命令并关闭
      setText(item.command + " ");
      closePopover();
      textareaRef.current?.focus();
      return;
    } else if (popover === "slash-model") {
      if (!item.command) return;
      // 通过 API 直接切换模型
      handleModelSwitch(item.command);
      return;
    } else if (popover === "at" && item.hasChildren) {
      // 进入子级别（如 @file → 显示文件列表）
      const category = item.command.replace("@", "");
      setAtCategory(category);
      setPopover("at-sub");
      setPopoverFilter("");
      fetchMentionData(); // 刷新根目录
      textareaRef.current?.focus();
      return;
    } else if (popover === "at" || popover === "at-sub") {
      if (!item.command) return; // 占位符"无匹配"
      // 如果是文件夹，进入其中
      if (item.command.endsWith("/")) {
        const folderPath = item.command.replace(/^@(?:file:)?/, "");
        fetchMentionData(folderPath); // 获取子文件夹内容
        setPopoverFilter("");
        textareaRef.current?.focus();
        return;
      }
      // 跟踪已确认的 @提及用于高亮
      setConfirmedTokens((prev) => new Set(prev).add(item.command));
      const lastAtIdx = text.lastIndexOf("@");
      const before = text.slice(0, lastAtIdx);
      setText(before + item.command + " ");
      // 在最近文件栏中跟踪 Excel 文件
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

  // 处理内联选择器的模型切换
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

  // 程序化发送斜杠命令（用于自动执行参数）
  const handleSendCommand = async (command: string) => {
    const trimmed = command.trim();
    // 先检查前端操作
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
    // 展示命令 → 在对话框中显示
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
      } catch { /* 继续向下执行 */ }
    }
    // 其他全部 → 作为聊天发送
    onSend(trimmed);
  };

  const handleSend = async () => {
    const trimmed = text.trim();
    if (!trimmed && files.length === 0) return;
    closePopover();

    if (trimmed.startsWith("/")) {
      // 0) /undo（无参数）→ 打开撤销面板
      if (trimmed === "/undo") {
        setText("");
        requestAnimationFrame(autoResize);
        setUndoPanelOpen(true);
        return;
      }

      // 1) 仅前端操作（/stop, /clear）
      const action = FRONTEND_ACTIONS[trimmed.split(" ")[0]];
      if (action === "stop") {
        onStop?.();
        setText("");
        requestAnimationFrame(autoResize);
        return;
      }
      if (action === "clear") {
        const { currentSessionId } = useChatStore.getState();
        useChatStore.getState().clearMessages();
        setText("");
        requestAnimationFrame(autoResize);
        if (currentSessionId) {
          fetch(buildApiUrl(`/sessions/${currentSessionId}/clear`), { method: "POST" }).catch(() => {});
        }
        if (onCommandResult) {
          onCommandResult("/clear", "对话历史已清除", "text");
        }
        return;
      }
      if ((action === "accept" || action === "reject") && trimmed.split(" ").length === 1) {
        // 无显式 ID 的 /accept 或 /reject → 从待审批中自动填充
        const state = useChatStore.getState();
        const pending = state.pendingApproval;
        if (pending) {
          state.dismissApproval(pending.id);
          const cmd = `/${action} ${pending.id}`;
          onSend(cmd);
          setText("");
          requestAnimationFrame(autoResize);
        } else {
          if (onCommandResult) {
            onCommandResult(`/${action}`, "当前没有待审批的操作", "text");
          }
          setText("");
        }
        return;
      }

      // 2) /model <name> → 直接切换模型
      if (trimmed.startsWith("/model ") && !DISPLAY_COMMANDS.has(trimmed)) {
        const modelName = trimmed.slice("/model ".length).trim();
        if (modelName) {
          setText("");
          handleModelSwitch(modelName);
          return;
        }
      }

      // 3) 展示类命令 → 在对话框中显示（仅精确匹配）
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
          // 回退到作为聊天发送
        }
      }
    }

    // 3) 其他全部 → 作为聊天消息发送
    onSend(trimmed, files.length > 0 ? files : undefined);
    setText("");
    setFiles([]);
    setConfirmedTokens(new Set());
    // 清空文本后重置文本区高度
    requestAnimationFrame(autoResize);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    // IME 组合期间忽略 Enter（如中文拼音输入）
    if (isComposingRef.current) return;

    // 弹出层导航
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


  return (
    <div
      {...getRootProps()}
      onDrop={(e) => {
        // 在 dropzone 之前拦截 Excel 侧边栏拖拽
        if (e.dataTransfer.types.includes("application/x-excel-file")) {
          handleExcelDrop(e);
          return;
        }
        // 让 dropzone 处理原生文件拖放
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

      {/* Drag overlay */}
      {isDragActive && (
        <div className="absolute inset-0 z-40 flex items-center justify-center rounded-[20px] bg-[var(--em-primary-alpha-06)] border-2 border-dashed border-[var(--em-primary-light)] backdrop-blur-[2px]">
          <div className="flex flex-col items-center gap-1.5 text-[var(--em-primary)]">
            <Plus className="h-6 w-6" />
            <span className="text-sm font-medium">拖放文件到这里</span>
            <span className="text-[10px] text-muted-foreground">支持 xlsx、xls、csv、图片</span>
          </div>
        </div>
      )}

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
              // 主斜杠菜单中为 /skills 和 /model 显示下钻箭头
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


      {/* Chat Mode Tabs */}
      <ChatModeTabs />

      {/* File attachment chips */}
      {files.length > 0 && (
        <div className="flex flex-col gap-1 px-4 sm:px-14 pt-2.5 pb-0">
          {/* 视觉能力不可用警告 */}
          {files.some((af) => isImageFile(af.file.name)) && !visionCapable && (
            <div className="flex items-center gap-1.5 text-[11px] text-amber-600 dark:text-amber-400 bg-amber-50 dark:bg-amber-950/30 rounded-md px-2 py-1">
              <AlertCircle className="h-3 w-3 flex-shrink-0" />
              <span>当前模型不支持图片识别，图片将无法被分析</span>
            </div>
          )}
          <div className="flex flex-wrap gap-1.5">
            {files.map((af) =>
              isImageFile(af.file.name) ? (
                /* Image thumbnail chip */
                <span
                  key={af.id}
                  className={`relative inline-flex items-end rounded-lg overflow-hidden bg-muted/40 border ${
                    af.status === "failed"
                      ? "border-2 border-destructive/60"
                      : "border-border/40"
                  }`}
                  style={{ maxWidth: "80px" }}
                >
                  <img
                    src={getPreviewUrl(af.file)}
                    alt={af.file.name}
                    className={`h-14 w-full object-cover ${af.status === "failed" ? "opacity-50" : ""}`}
                  />
                  {af.status === "uploading" && (
                    <div className="absolute inset-0 flex items-center justify-center bg-black/30">
                      <Loader2 className="h-4 w-4 text-white animate-spin" />
                    </div>
                  )}
                  {af.status === "failed" && (
                    <div
                      className="absolute inset-0 flex flex-col items-center justify-center bg-black/40 cursor-pointer"
                      onClick={() => retryUpload(af.id, af.file)}
                    >
                      <RotateCcw className="h-3.5 w-3.5 text-white" />
                      <span className="text-[8px] text-white mt-0.5">重试</span>
                    </div>
                  )}
                  <button
                    type="button"
                    className="touch-compact absolute top-0.5 right-0.5 h-5 w-5 flex items-center justify-center rounded-full bg-black/60 text-white hover:bg-black/80 transition-colors shadow-sm"
                    onClick={() => removeFile(af.id)}
                  >
                    <X className="h-2.5 w-2.5" />
                  </button>
                </span>
              ) : (
                /* Document file chip */
                <span
                  key={af.id}
                  className={`inline-flex items-center gap-1 rounded-full text-xs font-medium pl-2.5 pr-1 py-0.5 max-w-[200px] ${
                    af.status === "failed"
                      ? "bg-destructive/10 text-destructive"
                      : "bg-[var(--em-primary-alpha-10)] text-[var(--em-primary)]"
                  }`}
                  title={af.error}
                >
                  {af.status === "uploading" && (
                    <Loader2 className="h-3 w-3 animate-spin flex-shrink-0" />
                  )}
                  {af.status === "failed" && (
                    <AlertCircle className="h-3 w-3 flex-shrink-0" />
                  )}
                  <span className="truncate">{af.file.name}</span>
                  {af.status === "failed" && (
                    <button
                      type="button"
                      className="rounded-full p-0.5 hover:bg-destructive/20 transition-colors flex-shrink-0"
                      onClick={() => retryUpload(af.id, af.file)}
                    >
                      <RotateCcw className="h-3 w-3" />
                    </button>
                  )}
                  <button
                    type="button"
                    className={`rounded-full p-0.5 transition-colors flex-shrink-0 ${
                      af.status === "failed"
                        ? "hover:bg-destructive/20"
                        : "hover:bg-[var(--em-primary-alpha-20)]"
                    }`}
                    onClick={() => removeFile(af.id)}
                  >
                    <X className="h-3 w-3" />
                  </button>
                </span>
              )
            )}
          </div>
          {/* Inline error messages for failed uploads */}
          {files.some((af) => af.status === "failed") && (
            <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-[11px] text-destructive">
              {files
                .filter((af) => af.status === "failed")
                .map((af) => (
                  <span key={af.id}>
                    {af.file.name}: {af.error}
                  </span>
                ))}
            </div>
          )}
        </div>
      )}

      {/* Main input row: [+] textarea [send] */}
      <div className="flex items-end gap-1 px-1.5 py-1.5">
        {/* Attach button (left) */}
        <TooltipProvider delayDuration={400}>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                className="h-8 w-8 rounded-full flex-shrink-0 text-muted-foreground hover:text-foreground"
                onClick={() => fileInputRef.current?.click()}
              >
                <Plus className="h-4 w-4" />
              </Button>
            </TooltipTrigger>
            <TooltipContent side="top" className="text-xs">
              添加文件
            </TooltipContent>
          </Tooltip>
        </TooltipProvider>
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
              disabled={disabled || (!text.trim() && files.length === 0) || files.some((af) => af.status === "uploading")}
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
