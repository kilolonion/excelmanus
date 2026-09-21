import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  AGENT_MENU_ID,
  registerAgentContextMenu,
  type AgentMenuApi,
  type AgentMenuSelection,
} from "@/lib/excel-agent-menu";
import { SELECTION_AGENT_ACTIONS } from "@/lib/excel-ribbon-actions";

interface FakeMenuItem {
  id: string;
  title: string;
  tooltip?: string;
  order?: number;
  action: () => void;
}

function fakeApi() {
  const menus: FakeMenuItem[] = [];
  const submenuCalls: { id: string; title: string; order?: number }[] = [];
  const appendedTo: (string | string[])[] = [];
  const separators: number[] = [];
  const added: FakeMenuItem[] = [];
  const api: AgentMenuApi = {
    createMenu: (item) => {
      menus.push(item);
      return item;
    },
    createSubmenu: (item) => {
      submenuCalls.push(item);
      return {
        addSubmenu: (menu) => { added.push(menu as FakeMenuItem); return menu; },
        addSeparator: () => { separators.push(added.length); return null; },
        appendTo: (path) => { appendedTo.push(path); },
      };
    },
  };
  return { api, menus, submenuCalls, appendedTo, separators, added };
}

const selection: AgentMenuSelection = {
  path: "sales.xlsx", sheet: "明细", range: "C2:C9", version: "v1",
};

function makeHost(sel: AgentMenuSelection | null = selection) {
  return {
    readSelection: vi.fn(() => sel),
    onReference: vi.fn(),
    onAsk: vi.fn(),
  };
}

function actionOf(api: ReturnType<typeof fakeApi>, id: string) {
  const item = api.menus.find((m) => m.id === id);
  if (!item) throw new Error(`menu ${id} not registered`);
  return item.action;
}

beforeEach(() => vi.restoreAllMocks());

describe("registerAgentContextMenu", () => {
  it("registers a submenu appended to the others section in order", () => {
    const f = fakeApi();
    registerAgentContextMenu(f.api, makeHost());
    expect(f.submenuCalls).toEqual([{ id: AGENT_MENU_ID, title: "交给 Agent", order: 100 }]);
    expect(f.added.map((m) => m.id)).toEqual([
      `${AGENT_MENU_ID}.reference`,
      ...SELECTION_AGENT_ACTIONS.map((a) => `${AGENT_MENU_ID}.${a.kind}`),
      `${AGENT_MENU_ID}.trace-formula`,
    ]);
    expect(f.separators).toEqual([1, 1 + SELECTION_AGENT_ACTIONS.length]);
    expect(f.appendedTo).toEqual(["contextMenu.others"]);
  });

  it("uses no English periods in titles (localeService.t splits on dots)", () => {
    const f = fakeApi();
    registerAgentContextMenu(f.api, makeHost());
    for (const item of [...f.submenuCalls, ...f.menus]) {
      expect(item.title).not.toContain(".");
    }
  });

  it("routes the reference action through the host", () => {
    const f = fakeApi();
    const host = makeHost();
    registerAgentContextMenu(f.api, host);
    actionOf(f, `${AGENT_MENU_ID}.reference`)();
    expect(host.readSelection).toHaveBeenCalled();
    expect(host.onReference).toHaveBeenCalledWith(selection);
  });

  it("does nothing when there is no usable selection", () => {
    const f = fakeApi();
    const host = makeHost(null);
    registerAgentContextMenu(f.api, host);
    for (const item of f.menus) item.action();
    expect(host.onReference).not.toHaveBeenCalled();
    expect(host.onAsk).not.toHaveBeenCalled();
  });

  it("maps explain-selection to explain-formula on formula cells", () => {
    const f = fakeApi();
    const host = makeHost({ ...selection, formula: "=SUM(A1:A9)" });
    registerAgentContextMenu(f.api, host);
    actionOf(f, `${AGENT_MENU_ID}.explain-selection`)();
    expect(host.onAsk).toHaveBeenCalledWith("explain-formula", expect.objectContaining({ range: "C2:C9" }));
  });

  it("keeps explain-selection on plain cells and degrades trace-formula", () => {
    const f = fakeApi();
    const host = makeHost();
    registerAgentContextMenu(f.api, host);
    actionOf(f, `${AGENT_MENU_ID}.explain-selection`)();
    expect(host.onAsk).toHaveBeenLastCalledWith("explain-selection", selection);
    actionOf(f, `${AGENT_MENU_ID}.trace-formula`)();
    expect(host.onAsk).toHaveBeenLastCalledWith("explain-selection", selection);
  });

  it("passes trace-formula through on formula cells", () => {
    const f = fakeApi();
    const host = makeHost({ ...selection, formula: "=B2*2" });
    registerAgentContextMenu(f.api, host);
    actionOf(f, `${AGENT_MENU_ID}.trace-formula`)();
    expect(host.onAsk).toHaveBeenCalledWith("trace-formula", expect.objectContaining({ formula: "=B2*2" }));
  });
});
