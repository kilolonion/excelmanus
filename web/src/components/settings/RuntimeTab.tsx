"use client";

import { useState, useCallback } from "react";
import { BookOpen, ChevronDown, Sparkles, Cpu, ArrowRight, SlidersHorizontal } from "lucide-react";
import { useSettingsNavigationStore } from "@/stores/settings-navigation-store";
import { useOnboardingStore } from "@/stores/onboarding-store";
import { useUIStore } from "@/stores/ui-store";
import { WorkbookChatSettings } from "./WorkbookChatSettings";
import { SettingsPageLayout, SettingsPagePanel, SettingsPageSubnav } from "./SettingsPageLayout";
import { RuntimeSettingsPanel } from "./RuntimeSettingsPanel";
import { RUNTIME_CATEGORIES, RUNTIME_SETTING_GROUPS } from "./runtime-setting-groups";

interface GuideSection {
  key: "wizard" | "basic" | "advanced" | "settings";
  category: string;
  categoryColor: string;
  categoryBg: string;
  title: string;
  description: string;
  icon: React.ReactNode;
}

const GUIDE_SECTIONS: GuideSection[] = [
  {
    key: "wizard",
    category: "模型添加",
    categoryColor: "text-blue-600 dark:text-blue-400",
    categoryBg: "bg-blue-50 dark:bg-blue-950/40",
    title: "模型配置向导",
    description: "重新配置模型连接；模型参数统一在模型与连接 → 模型配置中管理",
    icon: <Cpu className="h-4 w-4" />,
  },
  {
    key: "basic",
    category: "基础",
    categoryColor: "text-emerald-600 dark:text-emerald-400",
    categoryBg: "bg-emerald-50 dark:bg-emerald-950/40",
    title: "对话与任务",
    description: "练习工作区切换、文件引用、发送与暂停、对话模式和模型选择",
    icon: <BookOpen className="h-4 w-4" />,
  },
  {
    key: "advanced",
    category: "进阶",
    categoryColor: "text-amber-600 dark:text-amber-400",
    categoryBg: "bg-amber-50 dark:bg-amber-950/40",
    title: "文件与表格",
    description: "练习快捷命令、工作表切换、单元格引用、修改对比和文件类型选择",
    icon: <Sparkles className="h-4 w-4" />,
  },
  {
    key: "settings",
    category: "设置",
    categoryColor: "text-violet-600 dark:text-violet-400",
    categoryBg: "bg-violet-50 dark:bg-violet-950/40",
    title: "模型与扩展",
    description: "了解模型连接、模型配置、订阅授权、规则、技能、MCP、记忆和运行偏好",
    icon: <SlidersHorizontal className="h-4 w-4" />,
  },
];

function OnboardingReplayCard() {
  const { wizardCompleted, coachMarksCompleted, resetToPhase } =
    useOnboardingStore();
  const closeSettings = useUIStore((s) => s.closeSettings);
  const [expanded, setExpanded] = useState(false);

  const handleReplayFrom = useCallback(
    (target: "wizard" | "basic" | "advanced" | "settings") => {
      resetToPhase(target);
      closeSettings();
    },
    [resetToPhase, closeSettings]
  );

  return (
    <div className="rounded-lg border border-border overflow-hidden">
      {/* Header */}
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="w-full flex items-center justify-between gap-3 p-4 hover:bg-muted/40 transition-colors"
      >
        <div className="flex items-start gap-2.5 min-w-0 text-left">
          <span
            className="mt-0.5 flex-shrink-0 w-7 h-7 rounded-md flex items-center justify-center"
            style={{ backgroundColor: "var(--em-primary-alpha-10)", color: "var(--em-primary)" }}
          >
            <BookOpen className="h-3.5 w-3.5" />
          </span>
          <div className="min-w-0">
            <div className="text-sm font-medium">新手引导</div>
            <div className="text-[11px] sm:text-xs text-muted-foreground">
              {wizardCompleted && coachMarksCompleted
                ? "已完成引导。展开选择章节重新播放。"
                : wizardCompleted
                  ? "配置向导已完成，界面指引进行中。"
                  : "尚未完成引导。"}
            </div>
          </div>
        </div>
        <div className="flex items-center gap-1.5 flex-shrink-0">
          <span className="text-[11px] text-muted-foreground hidden sm:inline">
            重新引导
          </span>
          <ChevronDown
            className={`h-4 w-4 text-muted-foreground transition-transform duration-200 ${expanded ? "rotate-180" : ""}`}
          />
        </div>
      </button>

      {/* Expandable drawer */}
      {expanded && (
        <div className="border-t border-border bg-muted/20 px-3 pb-3 pt-2 space-y-2">
          <p className="text-[11px] text-muted-foreground px-1 mb-1">
            选择要进入的章节
          </p>
          {GUIDE_SECTIONS.map((section) => (
            <button
              key={section.key}
              type="button"
              onClick={() => handleReplayFrom(section.key)}
              className="w-full flex items-center gap-3 rounded-lg border border-border bg-background p-3 text-left hover:border-[var(--em-primary)] hover:shadow-sm transition-all group"
            >
              <span
                className={`flex-shrink-0 w-9 h-9 rounded-lg flex items-center justify-center ${section.categoryBg} ${section.categoryColor}`}
              >
                {section.icon}
              </span>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 mb-0.5">
                  <span className="text-sm font-medium group-hover:text-[var(--em-primary)] transition-colors">
                    {section.title}
                  </span>
                  <span
                    className={`text-[10px] font-semibold px-1.5 py-px rounded-full ${section.categoryBg} ${section.categoryColor}`}
                  >
                    {section.category}
                  </span>
                </div>
                <p className="text-[11px] text-muted-foreground leading-relaxed break-words">
                  {section.description}
                </p>
              </div>
              <ArrowRight className="h-3.5 w-3.5 flex-shrink-0 text-muted-foreground opacity-0 -translate-x-1 group-hover:opacity-100 group-hover:translate-x-0 transition-all" />
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export function RuntimeTab() {
  const category = useSettingsNavigationStore((state) => state.runtimeCategory);
  return (
    <SettingsPageLayout className="space-y-4">
      <SettingsPageSubnav label="偏好与运行分类" showHeader={false} items={RUNTIME_CATEGORIES} activeKey={category} onChange={(key) => useSettingsNavigationStore.setState({ runtimeCategory: key, targetKey: null })} />
      {category === "conversation" && <SettingsPagePanel><WorkbookChatSettings /></SettingsPagePanel>}
      <RuntimeSettingsPanel groups={RUNTIME_SETTING_GROUPS} category={category} />
      {category === "conversation" && <OnboardingReplayCard />}
    </SettingsPageLayout>
  );
}
