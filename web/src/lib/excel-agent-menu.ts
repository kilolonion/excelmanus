import { type GridAskKind, type RibbonAskContext, SELECTION_AGENT_ACTIONS } from "@/lib/excel-ribbon-actions";

export interface AgentMenuSelection extends RibbonAskContext {
  sheet: string;
  range: string;
  formula?: string;
}

export interface AgentMenuHost {
  /** 返回当前可用于 Agent 的选区；文件未就绪/无选区时返回 null，菜单动作静默不做。 */
  readSelection: () => AgentMenuSelection | null;
  onReference: (sel: AgentMenuSelection) => void;
  onAsk: (kind: GridAskKind, sel: AgentMenuSelection) => void;
}

/** 与 Facade 对齐的最小接口，方便测试注入假实现。 */
export interface AgentMenuApi {
  createMenu(item: { id: string; title: string; tooltip?: string; order?: number; action: () => void }): unknown;
  createSubmenu(item: { id: string; title: string; order?: number }): {
    addSubmenu(menu: unknown): unknown;
    addSeparator(): unknown;
    appendTo(path: string | string[]): void;
  };
}

export const AGENT_MENU_ID = "excelmanus.agent";

/**
 * 在 Univer 原生右键菜单的 others 段追加「交给 Agent」子菜单。
 * 菜单项标题直接进 localeService.t()，不能含英文句点。
 */
export function registerAgentContextMenu(api: AgentMenuApi, host: AgentMenuHost): void {
  const submenu = api.createSubmenu({ id: AGENT_MENU_ID, title: "交给 Agent", order: 100 });

  submenu.addSubmenu(api.createMenu({
    id: `${AGENT_MENU_ID}.reference`,
    title: "添加到对话框",
    tooltip: "以 @选区 提及的形式加入当前对话",
    action: () => {
      const sel = host.readSelection();
      if (!sel) return;
      host.onReference(sel);
    },
  }));

  submenu.addSeparator();

  for (const action of SELECTION_AGENT_ACTIONS) {
    submenu.addSubmenu(api.createMenu({
      id: `${AGENT_MENU_ID}.${action.kind}`,
      title: action.label,
      tooltip: action.title,
      action: () => {
        const sel = host.readSelection();
        if (!sel) return;
        const kind: GridAskKind = action.kind === "explain-selection" && sel.formula
          ? "explain-formula"
          : action.kind;
        host.onAsk(kind, sel);
      },
    }));
  }

  submenu.addSeparator();

  submenu.addSubmenu(api.createMenu({
    id: `${AGENT_MENU_ID}.trace-formula`,
    title: "追踪公式引用",
    tooltip: "仅对公式单元格有意义：说明先例、依赖与影响范围",
    action: () => {
      const sel = host.readSelection();
      if (!sel) return;
      // 原生菜单项无法按选区动态隐藏，非公式格降级为选区解释。
      host.onAsk(sel.formula ? "trace-formula" : "explain-selection", sel);
    },
  }));

  submenu.appendTo("contextMenu.others");
}
