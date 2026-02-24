"use client";

import { useEffect } from "react";
import { useSessionStore } from "@/stores/session-store";
import {
  refreshSessionMessagesFromBackend,
  useChatStore,
} from "@/stores/chat-store";
import { useUIStore } from "@/stores/ui-store";
import { fetchSessionDetail, fetchSessions } from "@/lib/api";
import { buildDefaultSessionTitle } from "@/lib/session-title";
import type { Session, AssistantBlock } from "@/lib/types";

/**
 * 刷新后恢复路由状态 block：在最后一个 assistant 消息的 blocks 开头注入路由信息，
 * 仅当该消息尚未包含 route variant 的 status block 时执行。
 */
function _injectRouteBlock(
  chat: ReturnType<typeof useChatStore.getState>,
  route: { routeMode: string; skillsUsed: string[] },
) {
  const msgs = chat.messages;
  for (let i = msgs.length - 1; i >= 0; i--) {
    const m = msgs[i];
    if (m.role !== "assistant") continue;
    // 已有 route status block 则跳过
    if (m.blocks.some((b) => b.type === "status" && b.variant === "route")) return;
    const routeBlock: AssistantBlock = {
      type: "status",
      label: `路由: ${route.routeMode}`,
      detail: route.skillsUsed.length > 0 ? `技能: ${route.skillsUsed.join(", ")}` : undefined,
      variant: "route",
    };
    const updatedBlocks = [routeBlock, ...m.blocks];
    const updatedMsgs = [...msgs];
    updatedMsgs[i] = { ...m, blocks: updatedBlocks };
    chat.setMessages(updatedMsgs);
    return;
  }
}

/**
 * 将最后一个 assistant 消息中最后一个 running/success 状态的 tool_call 标记为 pending，
 * 用于刷新后恢复审批弹窗时同步工具调用卡片的视觉状态。
 */
function _markLastToolCallPending(chat: ReturnType<typeof useChatStore.getState>) {
  const msgs = chat.messages;
  for (let i = msgs.length - 1; i >= 0; i--) {
    const m = msgs[i];
    if (m.role !== "assistant") continue;
    for (let j = m.blocks.length - 1; j >= 0; j--) {
      const b = m.blocks[j];
      if (b.type === "tool_call" && (b.status === "running" || b.status === "success")) {
        const updatedBlocks = [...m.blocks];
        updatedBlocks[j] = { ...b, status: "pending" as const };
        const updatedMsgs = [...msgs];
        updatedMsgs[i] = { ...m, blocks: updatedBlocks };
        chat.setMessages(updatedMsgs);
        return;
      }
    }
    break; // only check the last assistant message
  }
}

export function SessionSync() {
  const mergeSessions = useSessionStore((s) => s.mergeSessions);
  const activeSessionId = useSessionStore((s) => s.activeSessionId);
  const currentSessionId = useChatStore((s) => s.currentSessionId);
  const abortController = useChatStore((s) => s.abortController);
  const switchSession = useChatStore((s) => s.switchSession);
  const setStreaming = useChatStore((s) => s.setStreaming);
  const setFullAccessEnabled = useUIStore((s) => s.setFullAccessEnabled);
  const setPlanModeEnabled = useUIStore((s) => s.setPlanModeEnabled);
  const setCurrentModel = useUIStore((s) => s.setCurrentModel);

  const setActiveSession = useSessionStore((s) => s.setActiveSession);

  useEffect(() => {
    let cancelled = false;
    let firstLoad = true;

    const syncSessions = async () => {
      try {
        const raw = await fetchSessions({ includeArchived: true });
        if (cancelled) return;
        const mapped: Session[] = (raw as Record<string, unknown>[]).map((s) => ({
          id: s.id as string,
          title:
            (typeof s.title === "string" ? s.title.trim() : "")
            || buildDefaultSessionTitle(s.id as string),
          messageCount: (s.message_count as number) ?? 0,
          inFlight: (s.in_flight as boolean) ?? false,
          updatedAt: s.updated_at as string | undefined,
          status: s.status === "archived" ? "archived" : "active",
        }));
        mergeSessions(mapped);

        // On first load, if activeSessionId (restored from localStorage)
        // doesn't match any known backend session, clear it to avoid
        // showing stale toolbar data for a non-existent session.
        if (firstLoad) {
          firstLoad = false;
          const currentActive = useSessionStore.getState().activeSessionId;
          if (currentActive && !mapped.some((s) => s.id === currentActive)) {
            setActiveSession(null);
          }
        }
      } catch {
        // ignore
      }
    };

    void syncSessions();
    const timer = window.setInterval(() => {
      void syncSessions();
    }, 5000);

    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [mergeSessions, setActiveSession]);

  useEffect(() => {
    // Never auto-switch while a local SSE stream is active; let the stream
    // settle first to avoid wiping optimistic in-flight messages.
    if (abortController) return;

    if (!activeSessionId) {
      if (currentSessionId !== null) {
        switchSession(null);
      }
      return;
    }
    if (currentSessionId !== activeSessionId) {
      switchSession(activeSessionId);
    }
  }, [activeSessionId, currentSessionId, abortController, switchSession]);

  useEffect(() => {
    if (!activeSessionId) {
      setFullAccessEnabled(false);
      setPlanModeEnabled(false);
      // Don't reset currentModel here — TopModelSelector independently
      // manages the global active model name via /models API.  Clearing
      // it causes a visual flash where the toolbar briefly shows "模型"
      // before TopModelSelector re-fetches the value.
      // Only kill streaming state if there is no active SSE connection.
      // Otherwise the stream callback will keep writing to a "stopped" store.
      if (!useChatStore.getState().abortController) {
        setStreaming(false);
      }
      return;
    }

    let cancelled = false;
    const prevInFlightRef = { current: false };
    let initialLoadDone = false;
    let notFoundCount = 0;
    const NOT_FOUND_THRESHOLD = 3;
    const pollDetail = async () => {
      try {
        const detail = await fetchSessionDetail(activeSessionId);
        if (cancelled) {
          return;
        }

        if (!detail) {
          // Session not yet known to backend (created locally, first message
          // hasn't reached the server). Silently skip — do NOT remove the
          // session or it will break the optimistic-create flow.
          // 但如果连续多次 404，说明会话确实不存在（如后端重启），清理 activeSessionId。
          // 如果存在活跃的 SSE 流（abortController !== null），说明消息正在发送中，
          // 后端可能还未来得及注册该会话，不要重置。
          notFoundCount++;
          const hasActiveStream = useChatStore.getState().abortController !== null;
          if (notFoundCount >= NOT_FOUND_THRESHOLD && !hasActiveStream) {
            setActiveSession(null);
          }
          return;
        }

        // 收到有效响应，重置计数器
        notFoundCount = 0;

        setFullAccessEnabled(detail.fullAccessEnabled);
        setPlanModeEnabled(detail.planModeEnabled);
        const modelName = detail.currentModelName || detail.currentModel;
        setCurrentModel(modelName ?? "");

        // IMPORTANT:
        // pollDetail is async and can race with optimistic local sendMessage().
        // Always re-read latest chat state before any destructive refresh to
        // avoid wiping freshly appended local user/assistant bubbles.
        const chat = useChatStore.getState();
        const hasLocalLiveStream = chat.abortController !== null;

        // 页面刷新后没有本地 stream 连接时，用后端 in_flight 状态接管。
        if (!hasLocalLiveStream) {
          if (detail.inFlight !== chat.isStreaming) {
            chat.setStreaming(detail.inFlight);
          }

          // 仅在以下情况刷新消息（避免轮询期间替换消息数组导致编辑状态丢失）：
          // 1) 首次加载且本地消息为空（页面刷新恢复）
          // 2) 后端正在处理中（inFlight），持续同步
          // 3) inFlight 刚从 true -> false（后端处理完毕），做最终同步
          const wasInFlight = prevInFlightRef.current;
          const shouldRefresh =
            (!initialLoadDone && chat.messages.length === 0)
            || detail.inFlight
            || (wasInFlight && !detail.inFlight);
          prevInFlightRef.current = detail.inFlight;
          initialLoadDone = true;

          if (shouldRefresh) {
            const latestChat = useChatStore.getState();
            // If local streaming starts while this poll is in-flight, skip
            // backend replacement to preserve optimistic local messages.
            if (latestChat.abortController === null && !latestChat.isStreaming) {
              await refreshSessionMessagesFromBackend(activeSessionId);
            }
          }

          // 注意：refreshSessionMessagesFromBackend 已更新 store，需重新获取最新状态
          const freshChat = useChatStore.getState();

          // 恢复路由状态 block（刷新后丢失的 SSE route_end 产物）
          if (detail.lastRoute && detail.lastRoute.routeMode) {
            _injectRouteBlock(useChatStore.getState(), detail.lastRoute);
          }

          // 恢复待处理审批弹窗（刷新后丢失的瞬态状态）
          if (detail.pendingApproval && !freshChat.pendingApproval) {
            freshChat.setPendingApproval(detail.pendingApproval);
            // 同时将最后一个匹配的 tool_call block 标记为 pending
            _markLastToolCallPending(useChatStore.getState());
          } else if (!detail.pendingApproval && freshChat.pendingApproval) {
            freshChat.setPendingApproval(null);
          }

          // 恢复待处理问题弹窗
          if (detail.pendingQuestion && !freshChat.pendingQuestion) {
            freshChat.setPendingQuestion(detail.pendingQuestion);
          } else if (!detail.pendingQuestion && freshChat.pendingQuestion) {
            freshChat.setPendingQuestion(null);
          }
        }
      } catch {
        if (cancelled) {
          return;
        }
        // Network error or non-404 server error — reset UI toggles but
        // keep the session entry intact.  Don't reset currentModel —
        // it is managed independently by TopModelSelector.
        setFullAccessEnabled(false);
        setPlanModeEnabled(false);
      }
    };

    void pollDetail();
    const timer = window.setInterval(() => {
      void pollDetail();
    }, 2000);

    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [
    activeSessionId,
    setActiveSession,
    setStreaming,
    setCurrentModel,
    setFullAccessEnabled,
    setPlanModeEnabled,
  ]);

  return null;
}
