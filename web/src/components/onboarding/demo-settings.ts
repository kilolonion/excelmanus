/**
 * Demo data for settings tour.
 * Provides mock rules/memories that render alongside real data when the
 * settings tour is active. Purely frontend — no backend API calls.
 */

// ── Demo Rules ──

export interface DemoRule {
  id: string;
  content: string;
  enabled: boolean;
  created_at: string;
  _demo: true;
}

export const DEMO_RULES: DemoRule[] = [
  {
    id: "__demo_rule_1__",
    content: "不要删除原始数据列，新增列放在末尾",
    enabled: true,
    created_at: new Date().toISOString(),
    _demo: true,
  },
  {
    id: "__demo_rule_2__",
    content: "输出结果中的数值保留两位小数",
    enabled: true,
    created_at: new Date().toISOString(),
    _demo: true,
  },
];

// ── Demo Memories ──

export interface DemoMemory {
  id: string;
  content: string;
  category: string;
  timestamp: string;
  source: string;
  _demo: true;
}

export const DEMO_MEMORIES: DemoMemory[] = [
  {
    id: "__demo_mem_1__",
    content: "用户偏好：输出使用中文，表格标题使用粗体，日期格式为 YYYY-MM-DD",
    category: "user_pref",
    timestamp: new Date().toISOString(),
    source: "auto_extract",
    _demo: true,
  },
  {
    id: "__demo_mem_2__",
    content: "项目上下文：当前正在分析 Q2 销售数据，主表为「销售数据.xlsx」Sheet1，包含月份/销售额/成本/利润四列",
    category: "general",
    timestamp: new Date().toISOString(),
    source: "auto_extract",
    _demo: true,
  },
];

// ── Demo Skills ──

export interface DemoSkill {
  name: string;
  description: string;
  source: string;
  writable: boolean;
  _demo: true;
}

export const DEMO_SKILLS: DemoSkill[] = [
  {
    name: "财务报表格式化",
    description: "自动识别财务报表结构，统一数字格式、添加汇总行、设置打印区域",
    source: "system",
    writable: false,
    _demo: true,
  },
  {
    name: "数据清洗助手",
    description: "去除重复行、修复日期格式、填充空值、标准化列名",
    source: "user",
    writable: true,
    _demo: true,
  },
];

// ── Demo state flag (module-level, controlled by tour-effects) ──

let _settingsDemoActive = false;
const _listeners = new Set<() => void>();

export function isSettingsDemoActive(): boolean {
  return _settingsDemoActive;
}

export function activateSettingsDemo(): void {
  if (_settingsDemoActive) return;
  _settingsDemoActive = true;
  _listeners.forEach((fn) => fn());
}

export function deactivateSettingsDemo(): void {
  if (!_settingsDemoActive) return;
  _settingsDemoActive = false;
  _listeners.forEach((fn) => fn());
}

/** Subscribe to demo state changes. Returns unsubscribe function. */
export function onSettingsDemoChange(fn: () => void): () => void {
  _listeners.add(fn);
  return () => _listeners.delete(fn);
}
