"use client";

import { useSyncExternalStore } from "react";
import {
  ArrowLeftRight,
  ArrowRight,
  BarChart3,
  ClipboardCheck,
  FileSpreadsheet,
  Lightbulb,
  MessageSquare,
  Search,
  Sparkles,
  Table2,
  X,
  type LucideIcon,
} from "lucide-react";
import { useSessionStore } from "@/stores/session-store";
import { useExcelStore } from "@/stores/excel-store";
import { useWorkbookConversationStore } from "@/stores/workbook-conversation-store";
import { hasPendingWorkbookEdits, isWorkbookEditPaused, subscribeWorkbookEdits } from "@/lib/excel-cell-edit";
import { fileBaseName } from "@/lib/revision-display";
import { recordWorkbookChatNavigation } from "@/lib/workbook-chat-navigation";
import { currentWorkbookSendContext } from "@/lib/workbook-context";
import { useShallow } from "zustand/react/shallow";
import { useWordStore } from "@/stores/word-store";
import styles from "./WorkbookConversation.module.css";

export function useWorkbookConversation() {
  const sessionId = useSessionStore((s) => s.activeSessionId);
  useSessionStore((s) => s.sessions);
  useWordStore((s) => s.fullViewPath);
  useWorkbookConversationStore(useShallow((s) => [s.targets, s.views]));
  useExcelStore(useShallow((s) => [s.panelOpen, s.activeFilePath, s.activeSheet, s.activeWorkspaceKey, s.fullViewPath, s.fullViewSheet, s.fullViewLayout, s.compareMode]));
  const context = currentWorkbookSendContext(sessionId);
  return { sessionId, target: context?.target, view: context?.view, isPreview: context?.isPreview };
}

export function WorkbookContextChip() {
  const { sessionId, target, view, isPreview } = useWorkbookConversation();
  const editState = useSyncExternalStore(subscribeWorkbookEdits,
    () => !target ? "" : isWorkbookEditPaused(target.file) ? "保存需要处理" : hasPendingWorkbookEdits(target.file) ? "正在保存…" : "",
    () => "");
  if (!target || !sessionId) return null;
  const label = `${fileBaseName(target.file.relative)}${target.sheet ? ` · ${target.sheet}` : ""}`;
  const contextState = editState
    ? "saving"
    : view?.status === "error"
      ? "error"
      : view?.status === "ready"
        ? isPreview ? "preview" : "ready"
        : "loading";
  const contextStatus = editState || (view?.status === "ready" ? isPreview ? "引用预览" : "当前工作簿" : view?.status === "error" ? "打开失败" : "正在打开");

  return <div
    className="em-composer-tab em-composer-tab--workbook text-xs"
    data-workbook-context={target.file.relative}
    data-context-state={contextState}
    aria-label={`当前引用：${label}`}
  >
    <span className="em-composer-tab-icon" aria-hidden="true"><FileSpreadsheet className="h-3.5 w-3.5" /></span>
    <span className="em-composer-tab-copy min-w-0">
      <span className="em-composer-tab-kind">表格引用</span>
      <button type="button" className="em-composer-tab-label truncate text-left min-w-0" title={`本次提问默认引用：${target.file.relative}`} onClick={() => {
        recordWorkbookChatNavigation("sheet", target.file.relative);
        useExcelStore.getState().openFullView(target.file.relative, target.sheet, target.layout);
      }}>{label}</button>
    </span>
    <button type="button" aria-label={isPreview ? "设为主对话文件" : "更换主对话文件"} title={isPreview ? "关闭预览后继续讨论此表格" : "更换主对话文件"}
      className="em-composer-tab-action inline-flex shrink-0 items-center gap-1"
      onClick={() => {
        const store = useWorkbookConversationStore.getState();
        if (isPreview) { useExcelStore.getState().setPrimaryWorkbook(target.file.relative); }
        else store.openSwitchPicker(sessionId);
      }}>
      <ArrowLeftRight className="h-3 w-3" /><span className="em-composer-tab-action-label">{isPreview ? "设为主对话" : "更换"}</span>
    </button>
    <span className="em-composer-tab-status shrink-0"><span className="em-composer-tab-status-dot" aria-hidden="true" />{contextStatus}</span>
    <button type="button" aria-label={isPreview ? "关闭当前预览" : "移除当前表格关联"} className="em-composer-tab-dismiss shrink-0" onClick={() => {
      useExcelStore.getState().closePanel();
      useExcelStore.getState().closeFullView();
      if (!isPreview) useWorkbookConversationStore.getState().detach(sessionId);
    }}><X className="h-3 w-3" /></button>
  </div>;
}

export function WorkbookConversationWelcome({ onSuggestion }: { onSuggestion: (text: string) => void }) {
  const { sessionId, target } = useWorkbookConversation();
  const workbookName = target ? fileBaseName(target.file.relative) : "当前工作簿";

  const suggestions: WorkbookSuggestion[] = [
    {
      label: "先了解这张表",
      prompt: "这张表主要记录什么？",
      description: "快速梳理字段、结构和数据含义",
      icon: Table2,
    },
    {
      label: "检查数据质量",
      prompt: "检查这张表是否有重复或缺失的数据",
      description: "找出重复、缺失和格式异常",
      icon: ClipboardCheck,
    },
    {
      label: "汇总关键数据",
      prompt: "帮我汇总这份表格的关键数据",
      description: "提炼指标，给出清晰的结论",
      icon: BarChart3,
    },
    {
      label: "找出值得关注的地方",
      prompt: "找出这份表格中值得关注的异常和趋势",
      description: "发现异常值、变化和潜在趋势",
      icon: Search,
    },
  ];

  return <main className={styles.page}>
    <section className={styles.hero} aria-labelledby="workbook-welcome-title">
      <div className={styles.heroGlow} aria-hidden="true" />
      <div className={styles.heroContent}>
        <div className={styles.eyebrow}><Sparkles /> 工作簿助手</div>
        <h1 id="workbook-welcome-title">和这份表格一起，<span>把问题变成结果。</span></h1>
        <p className={styles.heroDescription}>从理解数据开始，快速完成检查、汇总、分析和整理。</p>
        {target && <div className={styles.workbookCard}>
          <div className={styles.workbookIcon}><FileSpreadsheet /></div>
          <div className={styles.workbookMeta}>
            <strong title={target.file.relative}>{workbookName}</strong>
            <span>{target.sheet ? `当前工作表：${target.sheet}` : "当前工作簿"}</span>
          </div>
          <ArrowRight className={styles.workbookArrow} aria-hidden="true" />
        </div>}
      </div>
      <div className={styles.heroPattern} aria-hidden="true"><span /><span /><span /></div>
    </section>

    <section className={styles.tasksSection} aria-labelledby="workbook-task-title">
      <div className={styles.sectionHeading}>
        <div>
          <p className={styles.sectionEyebrow}>快速开始</p>
          <h2 id="workbook-task-title">你想先做什么？</h2>
        </div>
        <span>点击即可把任务带入输入框</span>
      </div>
      <div className={styles.taskGrid}>
        {suggestions.map(({ label, prompt, description, icon: Icon }) => (
          <button type="button" key={prompt} onClick={() => onSuggestion(prompt)} className={styles.taskCard}>
            <span className={styles.taskIcon}><Icon /></span>
            <span className={styles.taskCopy}>
              <strong>{label}</strong>
              <span>{description}</span>
            </span>
            <ArrowRight className={styles.taskArrow} aria-hidden="true" />
          </button>
        ))}
      </div>
    </section>

    <section className={styles.tipStrip} aria-label="使用提示">
      <div className={styles.tipIcon}><Lightbulb /></div>
      <div><strong>让结果更准确</strong><span>可以先选中表格区域，再告诉我你想比较或修改的内容。</span></div>
    </section>

    {target && <p className={styles.discussion}><MessageSquare /><span>当前讨论</span><strong title={target.file.relative}>{workbookName}</strong>{sessionId && <button type="button" onClick={() => useWorkbookConversationStore.getState().openSwitchPicker(sessionId)}><ArrowLeftRight /> 更换</button>}</p>}
  </main>;
}

interface WorkbookSuggestion {
  label: string;
  prompt: string;
  description: string;
  icon: LucideIcon;
}
