"use client";

import { useEffect, useState } from "react";
import { onDesktopMenuAction, type DesktopMenuAction } from "@/lib/desktop-menu";
import { newChatInPreferredWorkspace } from "@/lib/session-actions";
import { activateChatWorkspaceTab } from "@/lib/chat-workspace-tabs";
import { uploadChatFiles } from "@/components/chat/chat-upload-bridge";
import { useUIStore } from "@/stores/ui-store";

async function handleUploadFile(notify: (message: string) => void) {
  const picker = window.excelManusDesktop?.pickChatFiles;
  if (!picker) return;
  try {
    // Compare view unmounts the composer. Restore it before selecting files.
    activateChatWorkspaceTab("chat");
    const { files, skipped } = await picker();
    if (skipped.length > 0) {
      notify(`以下文件不可读、读取时发生变化或超过本次 256 MB 上传上限，已跳过：${skipped.join("、")}`);
    }
    if (files.length && !uploadChatFiles(
      files.map((f) => new File([f.data instanceof ArrayBuffer ? f.data : f.data.slice().buffer], f.name, { type: f.type })),
    )) notify("输入框尚未就绪，请返回对话后重新上传文件。");
  } catch (err) {
    notify(err instanceof Error ? err.message : "打开文件选择器失败，请重试。");
  }
}

function handleMenuAction(action: DesktopMenuAction, notify: (message: string) => void) {
  switch (action) {
    case "new-chat":
      void newChatInPreferredWorkspace()
        .then(() => activateChatWorkspaceTab("chat"))
        .catch((err) => notify(err instanceof Error ? err.message : "新建对话失败，请重试。"));
      return;
    case "upload-file":
      void handleUploadFile(notify);
      return;
    case "show-chat":
      activateChatWorkspaceTab("chat");
      return;
    case "show-sheet":
      activateChatWorkspaceTab("sheet");
      return;
    case "toggle-sidebar":
      useUIStore.getState().toggleSidebar();
      return;
    case "open-settings":
      useUIStore.getState().openSettings();
      return;
  }
}

/** 桌面端顶部菜单动作 → 应用内行为。仅在 Electron preload 存在时有事件来源。 */
export function DesktopMenuBridge() {
  const [notice, setNotice] = useState<string | null>(null);
  useEffect(() => onDesktopMenuAction((action) => handleMenuAction(action, setNotice)), []);
  if (!notice) return null;
  return <div role="status" className="fixed bottom-5 right-5 z-[200] flex max-w-md items-start gap-3 rounded-xl border bg-background p-4 text-sm shadow-lg">
    <p className="min-w-0 break-words">{notice}</p>
    <button type="button" className="shrink-0 text-muted-foreground" onClick={() => setNotice(null)}>关闭</button>
  </div>;
}
