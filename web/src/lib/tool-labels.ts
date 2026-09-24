/** 把工具名和参数转成用户能读懂的动作标题与上下文，不编造缺失数字。 */

import { displayFileName } from "@/lib/file-identity";

const WRITE_TOOLS = new Set([
  "apply_spreadsheet_changes",
  "manage_spreadsheet_versions",
  "write_text_file",
  "edit_text_file",
]);

const READ_TOOLS = new Set([
  "observe_spreadsheet",
  "analyze_spreadsheet",
  "compare_spreadsheets",
  "trace_spreadsheet_formulas",
  "read_text_file",
]);

const PATH_KEYS = [
  "file_path",
  "path",
  "source",
  "destination",
  "output_path",
  "report_path",
  "filename",
];

const SHEET_KEYS = ["sheet", "sheet_name", "worksheet"];
const RANGE_KEYS = ["range", "cells", "start_cell", "cell", "affected_range"];

export interface ToolContext {
  filename?: string;
  sheet?: string;
  range?: string;
  cellCount?: number;
}

export function isWriteTool(name: string): boolean {
  return WRITE_TOOLS.has(name);
}

export function isReadTool(name: string): boolean {
  return READ_TOOLS.has(name);
}

export function basenameOf(path: string): string {
  const clean = displayFileName(path);
  if (clean) return clean;
  const trimmed = path.replace(/\\/g, "/");
  const parts = trimmed.split("/");
  return parts[parts.length - 1] || path;
}

function firstString(
  source: Record<string, unknown> | Record<string, string> | undefined,
  keys: string[],
): string | undefined {
  if (!source) return undefined;
  for (const key of keys) {
    const val = source[key];
    if (typeof val === "string" && val.trim()) return val.trim();
  }
  return undefined;
}

function countCellsInRange(range: string): number | undefined {
  const m = range.toUpperCase().match(/^([A-Z]+)(\d+):([A-Z]+)(\d+)$/);
  if (!m) return undefined;
  const col = (letters: string) => {
    let n = 0;
    for (const ch of letters) n = n * 26 + (ch.charCodeAt(0) - 64);
    return n;
  };
  const cols = Math.abs(col(m[3]) - col(m[1])) + 1;
  const rows = Math.abs(Number(m[4]) - Number(m[2])) + 1;
  const total = cols * rows;
  return total > 0 ? total : undefined;
}

function countFromOperations(args: Record<string, unknown> | undefined): number | undefined {
  if (!args) return undefined;
  const ops = args.operations;
  if (!Array.isArray(ops) || ops.length === 0) return undefined;
  let cells = 0;
  let any = false;
  for (const op of ops) {
    if (!op || typeof op !== "object") continue;
    const rec = op as Record<string, unknown>;
    if (Array.isArray(rec.values)) {
      for (const row of rec.values) {
        if (Array.isArray(row)) {
          cells += row.length;
          any = true;
        }
      }
    }
    if (typeof rec.range === "string") {
      const n = countCellsInRange(rec.range);
      if (n) {
        cells += n;
        any = true;
      }
    }
  }
  return any ? cells : undefined;
}

function parseCount(raw: string | undefined): number | undefined {
  if (!raw) return undefined;
  const n = Number(raw.replace(/[^\d.]/g, ""));
  return Number.isFinite(n) && n > 0 ? Math.round(n) : undefined;
}

export function extractToolContext(
  args?: Record<string, unknown>,
  summary?: Record<string, string>,
): ToolContext {
  const path =
    firstString(args as Record<string, unknown> | undefined, PATH_KEYS) ||
    firstString(summary, PATH_KEYS);
  const sheet =
    firstString(args as Record<string, unknown> | undefined, SHEET_KEYS) ||
    firstString(summary, SHEET_KEYS);
  const range =
    firstString(args as Record<string, unknown> | undefined, RANGE_KEYS) ||
    firstString(summary, RANGE_KEYS);

  const fromOps = countFromOperations(args);
  const fromRange = range ? countCellsInRange(range) : undefined;
  const fromSummary = parseCount(summary?.cell_count || summary?.cells || summary?.count);

  return {
    filename: path ? basenameOf(path) : undefined,
    sheet,
    range,
    cellCount: fromOps ?? fromRange ?? fromSummary,
  };
}

export function formatToolContextLine(ctx: ToolContext): string | null {
  const parts: string[] = [];
  if (ctx.filename) parts.push(ctx.filename);
  if (ctx.sheet) parts.push(ctx.sheet);
  if (ctx.range) parts.push(ctx.range);
  if (parts.length === 0 && ctx.cellCount) return `${ctx.cellCount} 个单元格`;
  return parts.length > 0 ? parts.join(" · ") : null;
}

function inspectTitle(args?: Record<string, unknown>): string {
  const mode = typeof args?.mode === "string" ? args.mode : "";
  if (mode === "overview") return "读取工作表结构";
  if (mode === "search") return "搜索单元格";
  if (mode === "capabilities") return "查看表格能力";
  return "读取明细";
}

function editTitle(args?: Record<string, unknown>): string {
  const spec = args?.workbook_spec;
  if (spec) return "编译工作表";
  const ops = args?.operations;
  if (Array.isArray(ops) && ops.length > 0) {
    const kinds = new Set(
      ops
        .map((op) => (op && typeof op === "object" ? (op as { kind?: string }).kind : undefined))
        .filter(Boolean),
    );
    if (kinds.size === 1 && kinds.has("write")) return "写回原工作表";
    if (kinds.has("insert")) return "插入行列";
    if (kinds.has("delete")) return "删除行列";
  }
  return "更新工作表";
}

const STATIC_TITLES: Record<string, string> = {
  analyze_spreadsheet: "分析表格",
  compare_spreadsheets: "对比表格",
  apply_spreadsheet_changes: "管理工作表对象",
  manage_spreadsheet_versions: "管理工作表版本",
  trace_spreadsheet_formulas: "追踪公式",
  run_code: "运行代码",
  run_shell: "运行命令",
  finish_task: "完成任务",
  read_text_file: "读取文件",
  write_text_file: "写入文件",
  edit_text_file: "编辑文件",
  copy_file: "复制文件",
  rename_file: "重命名文件",
  delete_file: "删除文件",
  offer_download: "提供下载",
  sleep: "等待",
  read_image: "读取图片",
  list_directory: "列出目录",
  skill: "加载技能",
  activate_skill: "加载技能",
  introspect_capability: "查看能力",
  inspect_agent: "查看自身配置",
  configure_agent: "调整自身配置",
  read_word: "读取文档",
  inspect_word: "查看文档结构",
  search_word: "搜索文档",
  write_word: "写入文档",
  list_subagents: "查看子代理",
  delegate: "委派子任务",
  delegate_to_subagent: "委派子任务",
  memory_read_topic: "读取记忆",
  memory_save: "保存记忆",
  write_plan: "编写计划",
  exit_plan_mode: "退出计划",
  ask_user: "询问用户",
  show_workbook: "展示表格区域",
};

export function toolActionTitle(name: string, args?: Record<string, unknown>): string {
  if (name === "observe_spreadsheet") return inspectTitle(args);
  if (name === "apply_spreadsheet_changes") return editTitle(args);
  if (STATIC_TITLES[name]) return STATIC_TITLES[name];
  if (name.startsWith("mcp_")) {
    const rest = name.slice(4).replace(/_/g, " ");
    return rest || name;
  }
  return name.replace(/_/g, " ");
}

export function activityGroupTitle(
  tools: { name: string; status: string; args?: Record<string, unknown> }[],
): string {
  if (tools.some((t) => t.status === "pending")) {
    return tools.some((t) => isWriteTool(t.name)) ? "更新工作表" : "等待授权";
  }
  if (tools.length === 0) return "执行步骤";
  if (tools.every((t) => t.name === "finish_task")) return "完成任务";
  if (tools.every((t) => isReadTool(t.name))) return "读取数据";
  if (tools.some((t) => isWriteTool(t.name))) return "更新工作表";
  if (tools.some((t) => t.name === "run_code")) return "处理数据";
  return "执行步骤";
}

export function approvalCopy(toolName: string, ctx: ToolContext): { title: string; description: string } {
  if (isWriteTool(toolName)) {
    return {
      title: "允许写入原文件？",
      description: ctx.sheet
        ? `将结果写入「${ctx.sheet}」，并保留现有格式。`
        : "将结果写入原工作表，并保留现有格式。",
    };
  }
  if (isReadTool(toolName)) {
    return {
      title: "允许读取文件？",
      description: ctx.filename ? `即将读取 ${ctx.filename}。` : "即将读取工作区中的文件。",
    };
  }
  if (toolName === "run_code") {
    return {
      title: "允许运行代码？",
      description: "即将在沙盒中执行代码，可能会读取或修改工作表。",
    };
  }
  return {
    title: "允许执行此操作？",
    description: "该操作需要你的授权后才能继续。",
  };
}
