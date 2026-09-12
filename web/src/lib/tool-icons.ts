import type { LucideIcon } from "lucide-react";
import {
  ArrowLeftRight,
  Bookmark,
  BookOpen,
  ChartColumn,
  ClipboardList,
  Clock,
  Copy,
  Cpu,
  Download,
  FilePen,
  FilePenLine,
  FilePlus,
  FileSearch,
  FileText,
  FileType,
  Flag,
  FolderOpen,
  GitBranch,
  History,
  Image,
  ListChecks,
  ListPlus,
  LogOut,
  MessageCircleQuestionMark,
  Paintbrush,
  Pencil,
  ScanSearch,
  Search,
  Shapes,
  Sparkles,
  SquareCode,
  SquareFunction,
  Table2,
  Terminal,
  Trash2,
  Users,
  Wrench,
} from "lucide-react";

const TOOL_ICONS: Record<string, LucideIcon> = {
  inspect_spreadsheet: Table2,
  analyze_spreadsheet: ChartColumn,
  compare_spreadsheets: ArrowLeftRight,
  edit_spreadsheet: Pencil,
  format_spreadsheet: Paintbrush,
  manage_spreadsheet_objects: Shapes,
  trace_spreadsheet_formulas: SquareFunction,
  manage_spreadsheet_versions: History,
  run_code: SquareCode,
  run_shell: Terminal,
  write_text_file: FilePlus,
  edit_text_file: FilePen,
  read_text_file: FileText,
  list_directory: FolderOpen,
  copy_file: Copy,
  rename_file: FilePenLine,
  delete_file: Trash2,
  offer_download: Download,
  read_image: Image,
  skill: Sparkles,
  activate_skill: Sparkles,
  introspect_capability: ScanSearch,
  sleep: Clock,
  finish_task: Flag,
  read_word: FileType,
  inspect_word: FileSearch,
  search_word: Search,
  write_word: FilePenLine,
  write_plan: ClipboardList,
  exit_plan_mode: LogOut,
  ask_user: MessageCircleQuestionMark,
  memory_read_topic: BookOpen,
  memory_save: Bookmark,
  parallel_search: Search,
  task_create: ListPlus,
  task_update: ListChecks,
  manage_skills: Wrench,
  delegate: Users,
  list_subagents: GitBranch,
  introspect: Cpu,
};

export function toolIcon(name: string): LucideIcon {
  const exact = TOOL_ICONS[name];
  if (exact) return exact;
  if (name.startsWith("mcp_")) return Wrench;
  if (name.includes("spreadsheet") || name.includes("excel")) return Table2;
  if (name.includes("word")) return FileType;
  if (name.includes("image")) return Image;
  if (name.includes("shell")) return Terminal;
  if (name.includes("code") || name.includes("script")) return SquareCode;
  if (name.includes("search")) return Search;
  if (name.includes("directory") || name.includes("folder")) return FolderOpen;
  if (name.includes("file") || name.includes("text")) return FileText;
  return Wrench;
}

export function toolStatusIconClass(status: string): string {
  if (status === "error") return "text-red-500";
  if (status === "pending") return "text-amber-500";
  if (status === "running" || status === "streaming") {
    return "text-muted-foreground animate-tool-running-pulse";
  }
  return "text-[var(--em-primary)]";
}
