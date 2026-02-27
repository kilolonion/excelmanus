import React from "react";
import {
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
  FolderOpen,
} from "lucide-react";

export const ACCEPTED_EXTENSIONS = {
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": [".xlsx"],
  "application/vnd.ms-excel": [".xls"],
  "text/csv": [".csv"],
  "image/png": [".png"],
  "image/jpeg": [".jpg", ".jpeg"],
};

const IMAGE_EXTS = new Set([".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"]);
export function isImageFile(name: string): boolean {
  const ext = name.slice(name.lastIndexOf(".")).toLowerCase();
  return IMAGE_EXTS.has(ext);
}

// 斜杠命令（对应 CLI _STATIC_SLASH_COMMANDS + control_commands）
export const SLASH_COMMANDS: { command: string; description: string; icon: React.ReactNode; args?: string[] }[] = [
  // 基础命令
  { command: "/help", description: "显示帮助", icon: React.createElement(HelpCircle, { className: "h-3.5 w-3.5" }) },
  { command: "/skills", description: "查看技能包", icon: React.createElement(Sparkles, { className: "h-3.5 w-3.5" }) },
  { command: "/history", description: "对话历史摘要", icon: React.createElement(HistoryIcon, { className: "h-3.5 w-3.5" }) },
  { command: "/clear", description: "清除对话历史", icon: React.createElement(Trash2, { className: "h-3.5 w-3.5" }) },
  { command: "/mcp", description: "MCP Server 状态", icon: React.createElement(Terminal, { className: "h-3.5 w-3.5" }) },
  { command: "/save", description: "保存对话记录", icon: React.createElement(Save, { className: "h-3.5 w-3.5" }) },
  { command: "/config", description: "环境变量配置", icon: React.createElement(Settings, { className: "h-3.5 w-3.5" }), args: ["list", "set", "get", "delete"] },
  // 控制命令
  { command: "/model", description: "查看/切换模型", icon: React.createElement(Sparkles, { className: "h-3.5 w-3.5" }), args: ["list"] },
  { command: "/subagent", description: "子代理控制", icon: React.createElement(Bot, { className: "h-3.5 w-3.5" }), args: ["status", "on", "off", "list", "run"] },
  { command: "/fullaccess", description: "权限控制", icon: React.createElement(ShieldCheck, { className: "h-3.5 w-3.5" }), args: ["status", "on", "off"] },
  { command: "/backup", description: "备份沙盒控制", icon: React.createElement(Layers, { className: "h-3.5 w-3.5" }), args: ["status", "on", "off", "apply", "list"] },
  { command: "/compact", description: "上下文压缩", icon: React.createElement(RotateCcw, { className: "h-3.5 w-3.5" }), args: ["status", "on", "off"] },
  { command: "/plan", description: "计划模式", icon: React.createElement(ClipboardList, { className: "h-3.5 w-3.5" }), args: ["status", "on", "off", "approve", "reject"] },
  { command: "/registry", description: "文件注册表", icon: React.createElement(FolderOpen, { className: "h-3.5 w-3.5" }), args: ["status", "scan"] },
  { command: "/accept", description: "确认操作", icon: React.createElement(CheckCircle2, { className: "h-3.5 w-3.5" }) },
  { command: "/reject", description: "拒绝操作", icon: React.createElement(XCircle, { className: "h-3.5 w-3.5" }) },
  { command: "/undo", description: "回滚操作", icon: React.createElement(Undo2, { className: "h-3.5 w-3.5" }) },
  { command: "/stop", description: "停止当前生成", icon: React.createElement(StopCircle, { className: "h-3.5 w-3.5" }) },
];

// @ mention top-level categories
export interface MentionCategory {
  key: string;
  label: string;
  icon: React.ReactNode;
  description: string;
}

export const AT_TOP_LEVEL: MentionCategory[] = [
  { key: "file", label: "文件", icon: React.createElement(FileSpreadsheet, { className: "h-3.5 w-3.5" }), description: "引用工作区文件" },
  { key: "tool", label: "工具", icon: React.createElement(Wrench, { className: "h-3.5 w-3.5" }), description: "指定使用的工具" },
  { key: "skill", label: "技能", icon: React.createElement(Sparkles, { className: "h-3.5 w-3.5" }), description: "调用技能包" },
];

// 在对话框中展示结果而非作为聊天发送的命令
export const DISPLAY_COMMANDS = new Set([
  "/help", "/skills", "/mcp", "/history",
  "/model", "/model list",
  "/config", "/config list", "/config get",
  "/subagent list", "/subagent status",
  "/fullaccess status",
  "/backup list", "/backup status",
  "/compact status",
  "/plan status",
  "/registry status",
]);

// 直接执行前端操作的命令（不会发送到聊天）
export const FRONTEND_ACTIONS: Record<string, string> = {
  "/stop": "stop",
  "/clear": "clear",
  "/accept": "accept",
  "/reject": "reject",
};

// 参数选择后自动执行的命令（无需额外按 Enter）
export const AUTO_EXEC_ARGS = new Set(["on", "off", "status", "build", "approve", "reject", "apply", "list"]);

export function friendlyUploadError(err: unknown): string {
  const msg = err instanceof Error ? err.message : String(err);
  if (/413|too large|过大/i.test(msg)) return "文件过大，请压缩后重试";
  if (/extension|格式|不支持|unsupported/i.test(msg)) return "不支持该文件格式";
  if (/quota|配额|空间/i.test(msg)) return "存储空间不足";
  if (/401|403|权限/i.test(msg)) return "没有上传权限";
  if (/network|fetch|连接/i.test(msg)) return "网络连接失败，请检查网络后重试";
  return "上传失败，请重试";
}

const UPLOADABLE_EXTS = [".xlsx", ".xls", ".csv", ".png", ".jpg", ".jpeg"];
const _FILE_URL_RE = new RegExp(
  `^https?://\\S+(?:${UPLOADABLE_EXTS.map((e) => e.replace(".", "\\.")).join("|")})(?:[?#].*)?$`,
  "i"
);

/** 检测文本是否为可上传的文件 URL */
export function detectFileUrl(text: string): string | null {
  const trimmed = text.trim();
  if (_FILE_URL_RE.test(trimmed)) return trimmed;
  return null;
}

export interface MentionData {
  tools: string[];
  skills: { name: string; description: string }[];
  files: string[];
}

export type PopoverMode = null | "slash" | "slash-args" | "slash-skills" | "slash-model" | "at" | "at-sub";
