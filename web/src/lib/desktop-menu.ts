/**
 * 桌面端（Electron）顶部应用菜单与渲染进程的桥接。
 * preload 把主进程的 "excelmanus:menu-action" IPC 转发为同名 DOM CustomEvent，
 * detail 为动作名；这里统一定义动作集合与订阅入口。
 */
export type DesktopMenuAction =
  | "new-chat"
  | "upload-file"
  | "show-chat"
  | "show-sheet"
  | "toggle-sidebar"
  | "open-settings";

export const DESKTOP_MENU_EVENT = "excelmanus:menu-action";

const ACTIONS = new Set<string>([
  "new-chat",
  "upload-file",
  "show-chat",
  "show-sheet",
  "toggle-sidebar",
  "open-settings",
]);

export function onDesktopMenuAction(
  handler: (action: DesktopMenuAction) => void,
): () => void {
  const listener = (event: Event) => {
    const action = (event as CustomEvent<string>).detail;
    if (typeof action === "string" && ACTIONS.has(action)) {
      handler(action as DesktopMenuAction);
    }
  };
  window.addEventListener(DESKTOP_MENU_EVENT, listener);
  return () => window.removeEventListener(DESKTOP_MENU_EVENT, listener);
}
