"use client";

import { lazy, Suspense, type ReactNode } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Brain, Loader2, Package, Plug, ScrollText } from "lucide-react";

const RulesTab = lazy(() => import("./RulesTab").then((m) => ({ default: m.RulesTab })));
const SkillsTab = lazy(() => import("./SkillsTab").then((m) => ({ default: m.SkillsTab })));
const MCPTab = lazy(() => import("./MCPTab").then((m) => ({ default: m.MCPTab })));
const MemoryTab = lazy(() => import("./MemoryTab").then((m) => ({ default: m.MemoryTab })));

export type PluginSettingsTab = "rules" | "skills" | "mcp" | "memory";

const PLUGIN_TABS: {
  key: PluginSettingsTab;
  label: string;
  title: string;
  description: string;
  icon: ReactNode;
  capabilities: string[];
}[] = [
  {
    key: "rules",
    label: "规则",
    title: "行为规则",
    description: "用可启停的规则约束 Agent 的处理方式，全局规则会应用到每个新任务。",
    icon: <ScrollText className="h-4 w-4" />,
    capabilities: ["全局生效", "单独启停", "会话覆盖"],
  },
  {
    key: "skills",
    label: "技能",
    title: "技能包",
    description: "为 Agent 装载可复用的专业流程、指令和配套资源。",
    icon: <Package className="h-4 w-4" />,
    capabilities: ["本地导入", "GitHub 导入", "在线编辑"],
  },
  {
    key: "mcp",
    label: "MCP",
    title: "MCP 服务",
    description: "连接外部工具与数据源，并在启用前测试连接和工具发现结果。",
    icon: <Plug className="h-4 w-4" />,
    capabilities: ["stdio", "SSE", "HTTP", "连接诊断"],
  },
  {
    key: "memory",
    label: "记忆",
    title: "长期记忆",
    description: "查看 Agent 跨任务保留的信息，并按类型筛选或清理不再需要的内容。",
    icon: <Brain className="h-4 w-4" />,
    capabilities: ["自动沉淀", "分类浏览", "单条清理"],
  },
];

function TabSpinner() {
  return (
    <div className="flex items-center justify-center py-12 text-muted-foreground">
      <Loader2 className="h-5 w-5 animate-spin" />
    </div>
  );
}

export function PluginsTab({
  activeTab,
  onTabChange,
}: {
  activeTab: PluginSettingsTab;
  onTabChange: (tab: PluginSettingsTab) => void;
}) {
  const activeMeta = PLUGIN_TABS.find((tab) => tab.key === activeTab) ?? PLUGIN_TABS[1];

  return (
    <div className="em-plugin-workspace flex flex-col gap-3">
      <nav
        className="flex items-center gap-1 overflow-x-auto scrollbar-none"
        role="tablist"
        aria-label="插件配置"
        data-coach-id="coach-settings-plugin-tabs"
      >
        {PLUGIN_TABS.map((tab) => {
          const isActive = activeTab === tab.key;
          return (
            <button
              key={tab.key}
              type="button"
              role="tab"
              aria-selected={isActive}
              data-coach-id={`coach-settings-tab-${tab.key}`}
              className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium transition-colors whitespace-nowrap border shrink-0 ${
                isActive
                  ? "text-white border-transparent"
                  : "border-border text-muted-foreground hover:bg-muted/60 hover:text-foreground"
              }`}
              style={isActive ? { backgroundColor: "var(--em-primary)" } : undefined}
              onClick={() => onTabChange(tab.key)}
            >
              {tab.icon}
              {tab.label}
            </button>
          );
        })}
      </nav>

      <div className="em-plugin-overview flex items-start gap-3 rounded-xl border px-3.5 py-3">
        <div className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-[var(--em-primary-alpha-10)] text-[var(--em-primary)]">
          {activeMeta.icon}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 flex-wrap">
            <h3 className="text-sm font-semibold">{activeMeta.title}</h3>
            <span className="rounded-full bg-background/70 px-2 py-0.5 text-[10px] font-medium text-muted-foreground">
              插件配置
            </span>
          </div>
          <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
            {activeMeta.description}
          </p>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {activeMeta.capabilities.map((capability) => (
              <span
                key={capability}
                className="rounded-md border border-[var(--em-primary-alpha-10)] bg-background/60 px-1.5 py-0.5 text-[10px] text-muted-foreground"
              >
                {capability}
              </span>
            ))}
          </div>
        </div>
      </div>

      <AnimatePresence mode="wait">
        <motion.div
          key={activeTab}
          initial={{ opacity: 0, y: 4 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -4 }}
          transition={{ duration: 0.12 }}
        >
          <Suspense fallback={<TabSpinner />}>
            {activeTab === "rules" && <RulesTab />}
            {activeTab === "skills" && <SkillsTab />}
            {activeTab === "mcp" && <MCPTab />}
            {activeTab === "memory" && <MemoryTab />}
          </Suspense>
        </motion.div>
      </AnimatePresence>
    </div>
  );
}
