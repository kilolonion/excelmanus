import { useExcelStore } from "@/stores/excel-store";
import { useSessionStore } from "@/stores/session-store";
import { useWordStore } from "@/stores/word-store";
import {
  useWorkbookChatPreferencesStore,
  WORKBOOK_CHAT_LEARNING_WINDOW_MS,
} from "@/stores/workbook-chat-preferences-store";
import { normalizeRelativePath, workspaceKeyForSessionId } from "@/lib/workspace-file-ref";

type Observation = {
  path: string;
  returnToChat: boolean;
  startedAt: number;
  timer: ReturnType<typeof setTimeout>;
  unsubscribe: (() => void)[];
};

let pending: Observation | null = null;

function cancelObservation() {
  const observation = pending;
  pending = null;
  if (!observation) return;
  clearTimeout(observation.timer);
  observation.unsubscribe.forEach((unsubscribe) => unsubscribe());
}

function finishObservation(returnToChat: boolean) {
  cancelObservation();
  useWorkbookChatPreferencesStore.getState().recordChoice(returnToChat);
}

/** Call after accepting a send and capturing its workbook context, before waiting for the reply. */
export function handleWorkbookMessageSent(sessionId: string | null | undefined) {
  cancelObservation();
  const excel = useExcelStore.getState();
  const path = excel.fullViewPath;
  if (!sessionId || useSessionStore.getState().activeSessionId !== sessionId
    || !path || excel.fullViewLayout !== "embedded" || excel.compareMode
    || useWordStore.getState().fullViewPath
    || excel.activeWorkspaceKey !== workspaceKeyForSessionId(sessionId)) return;

  const preferences = useWorkbookChatPreferencesStore.getState();
  const returnToChat = preferences.autoReturnToChat;
  if (returnToChat) excel.closeFullView();
  if (!preferences.learnFromNavigation) return;

  pending = {
    path,
    returnToChat,
    startedAt: Date.now(),
    timer: setTimeout(() => finishObservation(returnToChat), WORKBOOK_CHAT_LEARNING_WINDOW_MS),
    unsubscribe: [
      useSessionStore.subscribe((state, previous) => {
        if (state.activeSessionId !== previous.activeSessionId) cancelObservation();
      }),
      useExcelStore.subscribe((state, previous) => {
        // Explicit user actions report before navigation. Other surface changes
        // (recovery, agent previews, another file) discard this observation.
        if (state.fullViewPath !== previous.fullViewPath
          || state.fullViewLayout !== previous.fullViewLayout
          || state.activeWorkspaceKey !== previous.activeWorkspaceKey
          || state.activeFilePath !== previous.activeFilePath
          || state.panelOpen !== previous.panelOpen
          || state.compareMode !== previous.compareMode) cancelObservation();
      }),
      useWordStore.subscribe((state, previous) => {
        if (state.fullViewPath !== previous.fullViewPath || state.panelOpen !== previous.panelOpen) cancelObservation();
      }),
      useWorkbookChatPreferencesStore.subscribe((state) => {
        if (state.preferenceVersion !== preferences.preferenceVersion) cancelObservation();
      }),
    ],
  };
}

/** Explicit user navigation only; automatic store updates must never call this. */
export function recordWorkbookChatNavigation(tab: "chat" | "sheet", path?: string) {
  const observation = pending;
  if (!observation) return;
  if (path && normalizeRelativePath(path) !== normalizeRelativePath(observation.path)) {
    cancelObservation();
    return;
  }
  const returnToChat = tab === "chat";
  if (returnToChat === observation.returnToChat) return;
  const elapsed = Date.now() - observation.startedAt;
  finishObservation(elapsed >= 0 && elapsed <= WORKBOOK_CHAT_LEARNING_WINDOW_MS
    ? returnToChat : observation.returnToChat);
}
