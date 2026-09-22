import { formatFileMention } from "@/components/chat/chat-input-insert";

export type NativeRibbonTab = "home" | "formula" | "data";

export type RibbonAskKind =
  | "explain-formula"
  | "trace-formula"
  | "generate-formula"
  | "data-quality"
  | "filter-analyze"
  | "dedupe"
  | "sort"
  | "chart"
  | "pivot";

export interface RibbonAskContext {
  path: string;
  sheet?: string;
  range?: string;
  version?: string | null;
}

export const FORMULA_ASK_ACTIONS: { kind: RibbonAskKind; label: string; title: string }[] = [
  { kind: "explain-formula", label: "解释公式", title: "把当前公式的含义和引用填进对话，不改表" },
  { kind: "trace-formula", label: "追踪引用", title: "查看先例和依赖，说明改这个格子会影响谁" },
  { kind: "generate-formula", label: "生成公式", title: "按表头和邻近数据生成公式，写入前会先说明" },
];

export const DATA_ASK_ACTIONS: { kind: RibbonAskKind; label: string; title: string }[] = [
  { kind: "data-quality", label: "数据质量", title: "检查空值、重复、类型异常；先报告，不改表" },
  { kind: "filter-analyze", label: "筛选方案", title: "携带当前选区和筛选条件，生成可确认的筛选方案" },
  { kind: "chart", label: "创建图表", title: "选择图表类型和数据列，交给 Agent 生成方案" },
  { kind: "pivot", label: "透视汇总", title: "指定行、列和汇总字段，交给 Agent 生成方案" },
  { kind: "dedupe", label: "去重", title: "先报告重复行和判定键，再问是否删除" },
  { kind: "sort", label: "排序", title: "先确认排序列和升降序，再写入" },
];

export function ribbonAskActions(tab: NativeRibbonTab) {
  if (tab === "formula") return FORMULA_ASK_ACTIONS;
  if (tab === "data") return DATA_ASK_ACTIONS;
  return [];
}

export function buildRibbonAskPrompt(kind: RibbonAskKind, ctx: RibbonAskContext): string {
  const mention = formatFileMention({
    path: ctx.path,
    sheet: ctx.sheet,
    range: ctx.range,
    version: ctx.version,
  });

  switch (kind) {
    case "chart":
      return `请为 ${mention} 制定图表方案，确认类型、数据系列和放置位置后再执行。`;
    case "pivot":
      return `请为 ${mention} 制定透视汇总方案，确认行列字段、汇总方式和输出位置后再执行。`;
    case "explain-formula":
      return `请解释 ${mention} 的公式含义、引用和可能错误。只说明，不要改表。公式缓存值不是已经重算。`;
    case "trace-formula":
      return `请追踪 ${mention} 的公式先例和依赖，并说明改这个格子会影响哪些单元格。只分析，不要改表。`;
    case "generate-formula":
      return `请根据 ${mention} 的表头和邻近数据，为当前选区生成合适的公式。先写出公式并说明，得到确认后再写入。`;
    case "data-quality":
      return `请对 ${mention} 做数据质量检查：空值、重复、类型异常和可疑离群。先报告，不要改表。`;
    case "filter-analyze":
      return `请分析 ${mention} 可以怎么筛选，列出关键维度和建议条件。这不是表格上的自动筛选；需要改表时再问我。`;
    case "dedupe":
      return `请检查 ${mention} 是否有重复行，说明判定键和重复数量。先不要删除。`;
    case "sort":
      return `请把 ${mention} 按当前选区所在列排序。先确认排序列和升降序，再写入。`;
  }
}

/**
 * Actions exposed from the grid itself.  These intentionally use the same
 * mention/version contract as the ribbon actions so a right-click never
 * creates a second, UI-only way of talking about a workbook.
 */
export type SelectionAgentKind =
  | "analyze-selection"
  | "explain-selection"
  | "data-quality-selection"
  | "clean-selection";

export const SELECTION_AGENT_ACTIONS: {
  kind: SelectionAgentKind;
  label: string;
  title: string;
}[] = [
  { kind: "analyze-selection", label: "分析此选区", title: "把当前选区交给 Agent 分析" },
  { kind: "explain-selection", label: "解释公式/内容", title: "解释当前选区的公式、字段和内容" },
  { kind: "data-quality-selection", label: "检查数据质量", title: "检查当前选区的空值、重复和类型异常" },
  { kind: "clean-selection", label: "提出清洗方案", title: "先提出清洗方案，确认后再修改表格" },
];

export function buildSelectionAgentPrompt(kind: SelectionAgentKind, ctx: RibbonAskContext): string {
  const mention = formatFileMention({
    path: ctx.path,
    sheet: ctx.sheet,
    range: ctx.range,
    version: ctx.version,
  });

  switch (kind) {
    case "analyze-selection":
      return `请分析 ${mention}：概括数据结构、关键模式、异常和可以继续追问的问题。先分析，不要修改表格。`;
    case "explain-selection":
      return `请解释 ${mention} 的字段含义、公式逻辑和可疑内容。对公式说明引用关系；只说明，不要修改表格。`;
    case "data-quality-selection":
      return `请检查 ${mention} 的数据质量：空值、重复、类型不一致、格式异常和可疑离群。给出具体位置和数量，先报告，不要修改表格。`;
    case "clean-selection":
      return `请为 ${mention} 制定清洗方案，明确每类问题、拟采用的规则和预计影响。先提出方案，等我确认后再修改表格。`;
  }
}

/**
 * 右键网格菜单的 ask 动作：公式单元格额外提供公式专用入口。
 */
export type GridAskKind = SelectionAgentKind | "explain-formula" | "trace-formula";

export function buildGridAskPrompt(kind: GridAskKind, ctx: RibbonAskContext): string {
  if (kind === "explain-formula" || kind === "trace-formula") return buildRibbonAskPrompt(kind, ctx);
  return buildSelectionAgentPrompt(kind, ctx);
}

/** 与 Univer classic 页签同一套 class，避免「历史」看起来像外挂。 */
export const NATIVE_RIBBON_TAB_CLASS =
  "univer-focus:outline-none univer-focus:ring-2 univer-focus:ring-primary-500 dark:!univer-focus:ring-primary-300 univer-flex univer-cursor-pointer univer-appearance-none univer-items-center univer-gap-1 univer-rounded-sm univer-border-none univer-px-2 univer-py-1 univer-text-sm univer-transition-colors";

export const NATIVE_RIBBON_TAB_SELECTED_CLASS =
  "univer-bg-primary-50 univer-font-semibold univer-text-primary-600 univer-shadow-sm dark:!univer-bg-primary-900 dark:!univer-text-primary-300";

export const NATIVE_RIBBON_TAB_IDLE_CLASS =
  "univer-hover:bg-gray-100 dark:!univer-hover:bg-gray-700 univer-bg-transparent univer-text-gray-700 dark:!univer-text-gray-200";

const HISTORY_RIBBON_STYLE_ID = "em-history-ribbon-style";

export function nativeRibbonTabFromLabel(
  label: string,
  historySelected = false,
): NativeRibbonTab | null {
  if (historySelected) return null;
  const title = label.trim();
  if (title.includes("公式")) return "formula";
  if (title.includes("数据")) return "data";
  if (title.includes("开始")) return "home";
  return null;
}

export function readNativeRibbonTab(
  tablist: HTMLElement | null,
  historyActive = false,
): NativeRibbonTab | null {
  if (!tablist || historyActive) return null;
  const historySelected = Boolean(
    tablist.querySelector('[data-em-ribbon="history"][aria-selected="true"]'),
  );
  const selected = tablist.querySelector<HTMLElement>(
    '[role="tab"][aria-selected="true"]:not([data-em-ribbon])',
  );
  const title = selected?.getAttribute("title") || selected?.textContent || "";
  return nativeRibbonTabFromLabel(title, historySelected);
}

export function ensureHistoryRibbonStyle(): void {
  if (typeof document === "undefined") return;
  if (document.getElementById(HISTORY_RIBBON_STYLE_ID)) return;
  const style = document.createElement("style");
  style.id = HISTORY_RIBBON_STYLE_ID;
  style.textContent = `
[data-em-ribbon-mode="history"] [role="tab"]:not([data-em-ribbon]) {
  background-color: transparent !important;
  box-shadow: none !important;
  font-weight: 400 !important;
  color: #374151 !important;
}
.dark [data-em-ribbon-mode="history"] [role="tab"]:not([data-em-ribbon]) {
  color: #e5e7eb !important;
}
[data-em-ribbon-toolbar-hidden] {
  display: none !important;
}
`;
  document.head.appendChild(style);
}

export function setHistoryRibbonMode(tablist: HTMLElement | null, historyActive: boolean): void {
  if (!tablist) return;
  if (historyActive) {
    tablist.setAttribute("data-em-ribbon-mode", "history");
  } else {
    tablist.removeAttribute("data-em-ribbon-mode");
  }
}

export function setRibbonToolbarHidden(toolbar: HTMLElement | null, hidden: boolean): void {
  if (!toolbar) return;
  if (hidden) {
    toolbar.setAttribute("data-em-ribbon-toolbar-hidden", "");
    toolbar.hidden = true;
    removeRibbonCommandHost(toolbar);
  } else {
    toolbar.removeAttribute("data-em-ribbon-toolbar-hidden");
    toolbar.hidden = false;
  }
}

export function findRibbonToolbar(root: HTMLElement | null): HTMLElement | null {
  const header = root?.querySelector<HTMLElement>('header[data-u-comp="headerbar"]');
  if (!header) return null;
  for (const child of Array.from(header.children)) {
    if (!(child instanceof HTMLElement)) continue;
    if (child.getAttribute("data-u-comp") === "ribbon-header-menu") continue;
    if (child.classList.contains("univer-invisible") || child.classList.contains("univer-hidden")) {
      continue;
    }
    if (child.className.includes("univer-h-10")) return child;
  }
  return null;
}

export function ensureRibbonCommandHost(toolbar: HTMLElement | null): HTMLElement | null {
  if (!toolbar) return null;
  let host = toolbar.querySelector<HTMLElement>("[data-em-ribbon-commands]");
  if (!host) {
    host = document.createElement("div");
    host.setAttribute("data-em-ribbon-commands", "");
    host.className =
      "flex items-center gap-0.5 shrink-0 ml-2 pl-2 border-l border-gray-200 dark:border-gray-600";
    toolbar.appendChild(host);
  }
  return host;
}

export function removeRibbonCommandHost(toolbar: HTMLElement | null): void {
  toolbar?.querySelector("[data-em-ribbon-commands]")?.remove();
}
