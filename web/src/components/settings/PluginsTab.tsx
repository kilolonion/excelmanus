"use client";

import { lazy, Suspense, type ReactNode } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Brain, Loader2, Package, Plug, ScrollText } from "lucide-react";
import { SettingsPageLayout, SettingsPageSubnav } from "./SettingsPageLayout";

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
}[] = [
  {
    key: "rules",
    label: "规则",
    title: "行为规则",
    description: "用可启停的规则约束 Agent 的处理方式，全局规则会应用到每个新任务。",
    icon: <ScrollText className="h-4 w-4" />,
  },
  {
    key: "skills",
    label: "技能",
    title: "技能包",
    description: "为 Agent 装载可复用的专业流程、指令和配套资源。",
    icon: <Package className="h-4 w-4" />,
  },
  {
    key: "mcp",
    label: "MCP",
    title: "MCP 服务",
    description: "连接外部工具与数据源，并在启用前测试连接和工具发现结果。",
    icon: <Plug className="h-4 w-4" />,
  },
  {
    key: "memory",
    label: "记忆",
    title: "长期记忆",
    description: "配置 Agent 的跨任务记忆、自动维护与保留期限，并清理不再需要的内容。",
    icon: <Brain className="h-4 w-4" />,
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
  return (
    <SettingsPageLayout className="em-plugin-page em-settings-page-stack">
      <SettingsPageSubnav
        label="扩展工作区"
        showHeader={false}
        activeKey={activeTab}
        items={PLUGIN_TABS.map((tab) => ({ key: tab.key, label: tab.label, icon: tab.icon, description: tab.description, coachId: `coach-settings-tab-${tab.key}` }))}
        onChange={(key) => onTabChange(key as PluginSettingsTab)}
        className="em-plugin-subnav"
        coachId="coach-settings-plugin-tabs"
      />

      <AnimatePresence mode="wait">
        <motion.div
          className="em-plugin-content"
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
    </SettingsPageLayout>
  );
}
