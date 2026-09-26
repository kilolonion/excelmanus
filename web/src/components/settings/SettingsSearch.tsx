"use client";

import { useId, useState } from "react";
import { ArrowUpRight, Search, X } from "lucide-react";
import { Input } from "@/components/ui/input";
import { useUIStore } from "@/stores/ui-store";
import { useSettingsNavigationStore } from "@/stores/settings-navigation-store";
import { requestModelSubTab, type ModelSubTab } from "./model/model-subtab";
import { RUNTIME_CATEGORIES, RUNTIME_SETTING_GROUPS } from "./runtime-setting-groups";
import { MEMORY_SETTING_GROUPS, MODEL_RUNTIME_SETTING_GROUPS, SECURITY_SETTING_GROUPS, SKILL_SETTING_GROUPS } from "./settings-catalog";
import type { RuntimeSettingGroup } from "@/lib/runtime-settings-form";

interface SearchEntry {
  key: string;
  label: string;
  description: string;
  location: string;
  tab: string;
  category?: string;
  modelTab?: ModelSubTab;
  field?: boolean;
}

function fields(groups: RuntimeSettingGroup[], tab: string, location: string, modelTab?: ModelSubTab): SearchEntry[] {
  return groups.flatMap((group) => group.items.map((item) => ({
    key: item.key, label: item.label, description: item.desc, tab, modelTab,
    category: group.category, field: true,
    location: `${location} / ${RUNTIME_CATEGORIES.find((category) => category.key === group.category)?.label ?? group.title}`,
  })));
}

export const SETTINGS_SEARCH_ENTRIES: SearchEntry[] = [
  ...fields(RUNTIME_SETTING_GROUPS, "runtime", "偏好与运行"),
  ...fields(MODEL_RUNTIME_SETTING_GROUPS, "model", "模型与连接 / 模型配置", "roles"),
  ...fields(SECURITY_SETTING_GROUPS, "access", "访问与安全"),
  ...fields(MEMORY_SETTING_GROUPS, "memory", "扩展与记忆 / 记忆"),
  ...fields(SKILL_SETTING_GROUPS, "skills", "扩展与记忆 / 技能"),
  { key: "connection", label: "添加或编辑模型连接", description: "供应商、API Key、API 地址、协议和连通测试", location: "模型与连接 / 模型连接", tab: "model", modelTab: "providers" },
  { key: "model-profile", label: "配置模型参数", description: "模型档案、Model ID、上下文、输入能力、推理模式与请求参数", location: "模型与连接 / 模型配置", tab: "model", modelTab: "roles" },
  { key: "default-model", label: "选择默认模型", description: "聊天、子任务、摘要使用的模型", location: "模型与连接 / 模型配置", tab: "model", modelTab: "roles" },
  { key: "subscription", label: "订阅账号授权", description: "ChatGPT Codex、WorkBuddy、Antigravity 登录与连接", location: "模型与连接 / 订阅账号", tab: "model", modelTab: "subscription" },
  { key: "thinking", label: "思考深度菜单与固定思考用量", description: "推理等级、思考 token 预算", location: "模型与连接 / 模型配置", tab: "model", modelTab: "roles" },
  { key: "transfer", label: "导入与导出配置", description: "备份、迁移模型配置", location: "模型与连接 / 模型配置", tab: "model", modelTab: "roles" },
  { key: "jev", label: "实验性 Jev", description: "决策提供商与任务辅助功能", location: "模型与连接 / 模型配置", tab: "model", modelTab: "roles" },
  { key: "workbook", label: "表格与对话切换", description: "发送后返回对话，根据使用习惯自动调整，本机偏好", location: "偏好与运行 / 对话偏好", tab: "runtime", category: "conversation" },
  { key: "login", label: "登录保护与管理员凭据", description: "管理员账号、密码、退出登录", location: "访问与安全", tab: "access" },
  { key: "mcp", label: "外部工具与搜索引擎", description: "MCP 服务、联网搜索、Exa、Tavily、Brave、搜索密钥", location: "扩展与记忆 / MCP", tab: "mcp" },
  { key: "rules", label: "行为规则", description: "自定义指令、规则、助手如何处理任务", location: "扩展与记忆 / 规则", tab: "rules" },
  { key: "skills", label: "安装和管理技能", description: "导入技能包、GitHub、专业工作流程", location: "扩展与记忆 / 技能", tab: "skills" },
  { key: "memory", label: "查看和清理记忆", description: "记忆库、用户偏好、文件结构、删除记忆条目", location: "扩展与记忆 / 记忆", tab: "memory" },
  { key: "version", label: "版本与更新", description: "检查更新、备份、回滚、应用安装、项目资源", location: "关于与更新", tab: "version" },
];

export function searchSettings(query: string): SearchEntry[] {
  const words = query.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
  if (!words.length) return [];
  return SETTINGS_SEARCH_ENTRIES.filter((entry) => words.every((word) => `${entry.label} ${entry.description} ${entry.location} ${entry.key}`.toLocaleLowerCase().includes(word)))
    .sort((a, b) => Number(b.label.includes(query.trim())) - Number(a.label.includes(query.trim())));
}

export function SettingsSearch() {
  const [query, setQuery] = useState("");
  const id = useId();
  const results = searchSettings(query);
  function navigate(entry: SearchEntry) {
    useSettingsNavigationStore.setState((state) => ({ runtimeCategory: entry.category ?? "conversation", targetKey: entry.field ? entry.key : null, targetVersion: state.targetVersion + 1 }));
    if (entry.modelTab) requestModelSubTab(entry.modelTab);
    useUIStore.getState().openSettings(entry.tab);
    setQuery("");
  }

  return (
    <div className="em-settings-search">
      <div className="relative">
        <Search aria-hidden className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" />
        <Input type="search" aria-label="搜索全部设置" aria-controls={query.trim() ? id : undefined} placeholder="搜索全部设置…" className="h-9 pl-9 pr-9 text-sm" value={query} onChange={(event) => setQuery(event.target.value)} onKeyDown={(event) => {
          if (event.key === "Escape" && query) { event.stopPropagation(); setQuery(""); }
          if (event.key === "Enter" && results.length) { event.preventDefault(); navigate(results[0]); }
        }} />
        {query && <button type="button" aria-label="清除设置搜索" className="absolute right-2 top-2 rounded p-0.5 text-muted-foreground" onClick={() => setQuery("")}><X className="h-4 w-4" /></button>}
      </div>
      {query.trim() && <section id={id} aria-label="设置搜索结果" className="em-settings-search-results">
        <p role="status" className="px-3 py-2 text-xs text-muted-foreground">{results.length ? `找到 ${results.length} 项设置 · 点击跳转，或按 Enter 打开第一项` : "没有匹配的设置，试试“模型”“费用”或“记忆”。"}</p>
        {results.map((entry) => <button key={entry.key} type="button" onClick={() => navigate(entry)} className="flex w-full items-start gap-3 rounded-lg px-3 py-3 text-left hover:bg-muted/60 focus-visible:bg-muted/60">
          <div className="min-w-0 flex-1"><span className="block text-sm font-medium">{entry.label}</span><span className="mt-1 block text-[11px] text-muted-foreground">{entry.location}</span><span className="mt-1 block text-xs leading-relaxed text-muted-foreground">{entry.description}</span></div><ArrowUpRight aria-hidden className="mt-1 h-4 w-4 shrink-0 text-muted-foreground" />
        </button>)}
      </section>}
    </div>
  );
}
