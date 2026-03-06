"use client";

import { useEffect } from "react";
import { useSessionStore } from "@/stores/session-store";
import {
  refreshSessionMessagesFromBackend,
  useChatStore,
} from "@/stores/chat-store";
import { subscribeToSession } from "@/lib/chat-actions";
import { useUIStore } from "@/stores/ui-store";
import { fetchSessionDetail, fetchSessions, apiGet } from "@/lib/api";
import { buildDefaultSessionTitle } from "@/lib/session-title";
import type { Session, AssistantBlock } from "@/lib/types";
import { DEMO_SESSION_PREFIX } from "@/components/onboarding/CoachMarks";

/** 灏嗗悗绔?route_mode 鏄犲皠涓虹敤鎴峰弸濂界殑涓枃鏍囩锛堜笌 chat-actions.ts 淇濇寔涓€鑷达級 */
const _ROUTE_MODE_LABELS: Record<string, string> = {
  all_tools: "Smart Route",
  control_command: "Control Command",
  slash_direct: "Slash Command",
  slash_not_found: "Skill Not Found",
  slash_not_user_invocable: "Skill Not Invocable",
  no_skillpack: "Base Mode",
  fallback: "Fallback Mode",
  hidden: "Route",
};

function _friendlyRouteMode(mode: string): string {
  return _ROUTE_MODE_LABELS[mode] || mode;
}

/**
 * 鍒锋柊鍚庢仮澶嶈矾鐢辩姸鎬?block锛氬湪鏈€鍚庝竴涓?assistant 娑堟伅鐨?blocks 寮€澶存敞鍏ヨ矾鐢变俊鎭紝
 * 浠呭綋璇ユ秷鎭皻鏈寘鍚?route variant 鐨?status block 鏃舵墽琛屻€?
 */
function _injectRouteBlock(
  chat: ReturnType<typeof useChatStore.getState>,
  route: { routeMode: string; skillsUsed: string[] },
) {
  const msgs = chat.messages;
  for (let i = msgs.length - 1; i >= 0; i--) {
    const m = msgs[i];
    if (m.role !== "assistant") continue;
    // 宸叉湁 route status block 鍒欒烦杩?
    if (m.blocks.some((b) => b.type === "status" && b.variant === "route")) return;
    const routeBlock: AssistantBlock = {
      type: "status",
      label: _friendlyRouteMode(route.routeMode),
      detail: route.skillsUsed.length > 0 ? route.skillsUsed.join(",") : undefined,
      variant: "route",
    };
    chat.updateAssistantMessage(m.id, (message) => ({
      ...message,
      blocks: [routeBlock, ...message.blocks],
    }));
    return;
  }
}

/**
 * 灏嗘渶鍚庝竴涓?assistant 娑堟伅涓渶鍚庝竴涓?running/success 鐘舵€佺殑 tool_call 鏍囪涓?pending锛?
 * 鐢ㄤ簬鍒锋柊鍚庢仮澶嶅鎵瑰脊绐楁椂鍚屾宸ュ叿璋冪敤鍗＄墖鐨勮瑙夌姸鎬併€?
 */
function _markLastToolCallPending(chat: ReturnType<typeof useChatStore.getState>) {
  const msgs = chat.messages;
  for (let i = msgs.length - 1; i >= 0; i--) {
    const m = msgs[i];
    if (m.role !== "assistant") continue;
    for (let j = m.blocks.length - 1; j >= 0; j--) {
      const b = m.blocks[j];
      if (b.type === "tool_call" && (b.status === "running" || b.status === "success")) {
        chat.updateAssistantMessage(m.id, (message) => {
          const updatedBlocks = [...message.blocks];
          const block = updatedBlocks[j];
          if (block?.type === "tool_call") {
            updatedBlocks[j] = { ...block, status: "pending" as const };
          }
          return { ...message, blocks: updatedBlocks };
        });
        return;
      }
    }
    break; // 鍙鏌ユ渶鍚庝竴鏉?assistant 娑堟伅
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
  const setVisionCapable = useUIStore((s) => s.setVisionCapable);
  const setChatMode = useUIStore((s) => s.setChatMode);
  const setCurrentModel = useUIStore((s) => s.setCurrentModel);
  const setThinkingEffort = useUIStore((s) => s.setThinkingEffort);

  const setActiveSession = useSessionStore((s) => s.setActiveSession);

  // 鍚姩鏃舵媺鍙?thinking config 鍚屾鍒?store
  useEffect(() => {
    apiGet<{ effort: string }>("/thinking")
      .then((data) => {
        if (data.effort) setThinkingEffort(data.effort);
      })
      .catch(() => {});
  }, [setThinkingEffort]);

  useEffect(() => {
    let cancelled = false;

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

        // 鑻?activeSessionId锛堜粠 localStorage 鎭㈠锛変笌鍚庣宸茬煡浼氳瘽閮戒笉鍖归厤锛屽垯娓呯┖浠ラ伩鍏嶉檲鏃?404 杞椋庢毚銆?
        // 鑻ユ湁娲昏穬 SSE 娴侊紙涔愯鍒涘缓灏氭湭鍒拌揪鏈嶅姟绔級鍒欒烦杩囥€?
        // Demo sessions are local-only 鈥?never prune them via backend sync.
        const currentActive = useSessionStore.getState().activeSessionId;
        if (currentActive && !currentActive.startsWith(DEMO_SESSION_PREFIX) && !mapped.some((s) => s.id === currentActive)) {
          {
            const hasActiveStream = useChatStore.getState().abortController !== null;
            if (!hasActiveStream) {
              // 淇濇姢鏈湴鏂板缓浣嗗皻鏈彂閫侀鏉℃秷鎭殑浼氳瘽锛氭鏌?session-store 涓槸鍚?
              // 瀛樺湪璇ヤ細璇濅笖 messageCount === 0 涓斿垱寤轰笉瓒呰繃 60 绉掋€?
              const localSession = useSessionStore.getState().sessions.find((s) => s.id === currentActive);
              const GRACE_MS = 60_000;
              const isLocalUnsent = localSession
                && localSession.messageCount === 0
                && localSession.createdAt
                && (Date.now() - localSession.createdAt) < GRACE_MS;
              if (!isLocalUnsent) {
                setActiveSession(null);
              }
            }
          }
        }
      } catch {
        // 蹇界暐
      }
    };

    void syncSessions();
    const timer = window.setInterval(() => {
      void syncSessions();
    }, 15_000);

    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [mergeSessions, setActiveSession]);

  useEffect(() => {
    // 鏈湴 SSE 娴佹椿璺冩椂涓嶈嚜鍔ㄥ垏鎹細璇濓紱绛夋祦缁撴潫鍐嶅垏鎹紝閬垮厤娓呮帀涔愯杩涜涓殑娑堟伅銆?
    if (abortController) return;

    // Demo sessions are fully managed by CoachMarks 鈥?skip async switchSession
    // which would wipe mock messages by trying to load from IDB/backend.
    if (activeSessionId?.startsWith(DEMO_SESSION_PREFIX)) return;

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
      // 姝ゅ涓嶉噸缃?chatMode锛屽叾涓虹敤鎴烽┍鍔ㄧ姸鎬侊紙ChatModeTabs锛夛紱閲嶇疆浼氬鑷寸敤鎴峰垏鍒?read/plan 鍚庡嚑绉掑張寮瑰洖 write銆?
      // 姝ゅ涓嶉噸缃?currentModel锛汿opModelSelector 閫氳繃 /models API 鐙珛绠＄悊鍏ㄥ眬妯″瀷鍚嶏紝娓呯┖浼氬鑷村伐鍏锋爮鐭殏鏄剧ず銆屾ā鍨嬨€嶅啀琚噸鏂版媺鍙栥€?
      // 浠呭湪娌℃湁娲昏穬 SSE 杩炴帴鏃舵竻闄ゆ祦寮忕姸鎬侊紝鍚﹀垯娴佸洖璋冧細缁х画鍐欏叆宸层€屽仠姝€嶇殑 store銆?
      if (!useChatStore.getState().abortController) {
        setStreaming(false);
      }
      return;
    }

    // Demo sessions are local-only 鈥?skip backend polling entirely.
    if (activeSessionId.startsWith(DEMO_SESSION_PREFIX)) return;

    let cancelled = false;
    const prevInFlightRef = { current: false };
    let snapshotValidated = false;
    let notFoundCount = 0;
    const NOT_FOUND_THRESHOLD = 2;
    const pollDetail = async () => {
      try {
        const detail = await fetchSessionDetail(activeSessionId);
        if (cancelled) {
          return;
        }

        if (!detail) {
          // 浼氳瘽灏氭湭琚悗绔煡鏅擄紙鏈湴鍒涘缓锛岄鏉℃秷鎭湭鍒版湇鍔＄锛夈€傞潤榛樿烦杩囷紝涓嶈绉婚櫎浼氳瘽锛屽惁鍒欎細鐮村潖涔愯鍒涘缓娴佺▼銆?
          // 浣嗗鏋滆繛缁娆?404锛岃鏄庝細璇濈‘瀹炰笉瀛樺湪锛堝鍚庣閲嶅惎锛夛紝娓呯悊 activeSessionId銆?
          // 濡傛灉瀛樺湪娲昏穬鐨?SSE 娴侊紙abortController !== null锛夛紝璇存槑娑堟伅姝ｅ湪鍙戦€佷腑锛?
          // 鍚庣鍙兘杩樻湭鏉ュ緱鍙婃敞鍐岃浼氳瘽锛屼笉瑕侀噸缃€?
          notFoundCount++;
          const hasActiveStream = useChatStore.getState().abortController !== null;
          if (notFoundCount >= NOT_FOUND_THRESHOLD && !hasActiveStream) {
            // 淇濇姢鏈湴鏂板缓浣嗗皻鏈彂閫侀鏉℃秷鎭殑浼氳瘽锛岄伩鍏嶈疆璇㈣娓呫€?
            const localSession = useSessionStore.getState().sessions.find((s) => s.id === activeSessionId);
            const GRACE_MS = 60_000;
            const isLocalUnsent = localSession
              && localSession.messageCount === 0
              && localSession.createdAt
              && (Date.now() - localSession.createdAt) < GRACE_MS;
            if (!isLocalUnsent) {
              setActiveSession(null);
            }
          }
          return;
        }

        // 鏀跺埌鏈夋晥鍝嶅簲锛岄噸缃鏁板櫒
        notFoundCount = 0;
        consecutiveErrors = 0;

        setFullAccessEnabled(detail.fullAccessEnabled);
        setVisionCapable(detail.visionCapable);
        // 娉ㄦ剰锛氫笉瑕佸湪杞涓敤鍚庣 chatMode 瑕嗙洊鍓嶇鐘舵€併€?
        // chatMode 鐨勬潈濞佹潵婧愭槸鍓嶇鐢ㄦ埛鎿嶄綔锛圕hatModeTabs 鐐瑰嚮锛夛紝
        // 鍚庣 _current_chat_mode 鍙湪 engine.chat() 璋冪敤鏃舵洿鏂帮紝
        // 杞瑕嗙洊浼氬鑷寸敤鎴峰垏鎹㈡ā寮忓悗鍑犵琚噸缃洖鏃у€笺€?
        // 鍚庣涓诲姩鎺ㄩ€佺殑妯″紡鍙樻洿锛圫SE mode_changed 浜嬩欢锛変粛鐒剁敓鏁堛€?
        const modelName = detail.currentModelName || detail.currentModel;
        if (modelName) setCurrentModel(modelName);

        // 閲嶈锛歱ollDetail 涓哄紓姝ワ紝鍙兘涓庝箰瑙傛湰鍦?sendMessage() 绔炴€併€?
        // 鍦ㄤ换浣曚細瑕嗙洊娑堟伅鐨勫埛鏂板墠锛屽姟蹇呴噸鏂拌鍙栨渶鏂?chat 鐘舵€侊紝閬垮厤鎿﹂櫎鍒氳拷鍔犵殑鏈湴 user/assistant 姘旀场銆?
        const chat = useChatStore.getState();
        const detailStreamId = detail.activeStreamId ?? null;
        const detailLatestSeq = Math.max(0, detail.latestSeq ?? 0);
        if (!chat.abortController) {
          if (detail.inFlight && detailStreamId) {
            chat.setStreamState(detailStreamId, Math.max(chat.latestSeq, detailLatestSeq));
          } else if (!detail.inFlight) {
            chat.setStreamState(null, 0);
            chat.clearResumeFailed();
          }
        }
        const hasLocalLiveStream = chat.abortController !== null;
        prevInFlightRef.current = detail.inFlight;

        // 椤甸潰鍒锋柊鍚庢病鏈夋湰鍦?stream 杩炴帴鏃讹紝鐢ㄥ悗绔?in_flight 鐘舵€佹帴绠°€?
        if (!hasLocalLiveStream) {
          // 仅在快照校验失败时回源，避免常态全量刷新。
          if (detail.inFlight) {
            snapshotValidated = false;
          } else if (!snapshotValidated) {
            snapshotValidated = true;
            const latestChat = useChatStore.getState();
            if (latestChat.abortController === null && !latestChat.isStreaming) {
              const remoteCount = Math.max(0, detail.messageCount ?? 0);
              const localCount = latestChat.messageOrder.length;
              if (remoteCount !== localCount) {
                await refreshSessionMessagesFromBackend(activeSessionId);
              }
            }
          }

          // SSE 閲嶈繛锛氭娴嬪埌鍚庣浠嶅湪澶勭悊涓斿墠绔棤娲昏穬 SSE 杩炴帴鏃讹紝
          // 鑷姩璋冪敤 subscribeToSession 閲嶆柊鎺ュ叆浜嬩欢娴併€?
          if (detail.inFlight) {
            const latest = useChatStore.getState();
            if (!latest.abortController && !latest.isStreaming) {
              // 鍏堣缃?streaming 閬垮厤涓嬩竴杞?poll 閲嶅瑙﹀彂
              latest.setStreaming(true);
              subscribeToSession(activeSessionId).catch(() => {
                // 璁㈤槄澶辫触鏃跺洖閫€鍒拌疆璇㈡ā寮?
                const s = useChatStore.getState();
                if (!s.abortController) s.setStreaming(false);
              });
            }
          } else if (detail.inFlight !== chat.isStreaming) {
            chat.setStreaming(detail.inFlight);
          }

          // 娉ㄦ剰锛歳efreshSessionMessagesFromBackend 宸叉洿鏂?store锛岄渶閲嶆柊鑾峰彇鏈€鏂扮姸鎬?
          const freshChat = useChatStore.getState();

          // 鎭㈠璺敱鐘舵€?block锛堝埛鏂板悗涓㈠け鐨?SSE route_end 浜х墿锛?
          if (detail.lastRoute && detail.lastRoute.routeMode) {
            _injectRouteBlock(useChatStore.getState(), detail.lastRoute);
          }

          // 鎭㈠寰呭鐞嗗鎵瑰脊绐楋紙鍒锋柊鍚庝涪澶辩殑鐬€佺姸鎬侊級
          // 娉ㄦ剰锛氱敤鎴风偣鍑诲厑璁?鎷掔粷鍚庝細璁板綍 _lastDismissedApprovalId锛?
          // 闃叉 SessionSync 杞鍦ㄥ悗绔皻鏈鐞嗗畬瀹℃壒鏃舵妸寮圭獥閲嶆柊鎷夊洖鏉?
          if (detail.pendingApproval && !freshChat.pendingApproval) {
            const dismissed = freshChat._lastDismissedApprovalId;
            const incomingId = (detail.pendingApproval as { id?: string })?.id
              ?? (detail.pendingApproval as { approval_id?: string })?.approval_id;
            if (!dismissed || dismissed !== incomingId) {
              freshChat.setPendingApproval(detail.pendingApproval);
              // 鍚屾椂灏嗘渶鍚庝竴涓尮閰嶇殑 tool_call block 鏍囪涓?pending
              _markLastToolCallPending(useChatStore.getState());
            }
          } else if (!detail.pendingApproval && freshChat.pendingApproval) {
            freshChat.setPendingApproval(null);
          }

          // 鎭㈠寰呭鐞嗛棶棰樺脊绐?
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
        // 缃戠粶閿欒锛堝鏂綉/瓒呮椂锛夛細涓嶉噸缃?UI 寮€鍏筹紝閬垮厤鐬椂缃戠粶娉㈠姩瀵艰嚧鐢ㄦ埛涓㈠け Full Access 鐘舵€併€?
        // fullAccessEnabled 绛?UI 鐘舵€佷細鍦ㄤ笅涓€娆℃垚鍔熻疆璇㈡椂鑷劧鎭㈠銆?
        consecutiveErrors++;
      }
    };

    // 鑷€傚簲杞锛歩nFlight 鏃?2s 楂橀鍚屾锛岀┖闂叉椂 5s 浣庨妫€鏌?
    // 杩炵画澶辫触鏃舵寚鏁伴€€閬匡紙鏈€澶?30s锛夛紝鎴愬姛鏃堕噸缃?
    // 鎬ц兘浼樺寲锛氶娆″欢杩?800ms 鍐嶈Е鍙戯紝閬垮厤涓?switchSession 鐨勬秷鎭姞杞界珵鎬?
    const POLL_FAST = 2000;
    const POLL_IDLE = 5000;
    const POLL_MAX_BACKOFF = 30_000;
    const POLL_INITIAL_DELAY = 800;
    let currentInterval = POLL_FAST;
    let consecutiveErrors = 0;
    let timer = window.setTimeout(function schedule() {
      void pollDetail().then(() => {
        if (cancelled) return;
        const isActive = prevInFlightRef.current;
        let nextInterval = isActive ? POLL_FAST : POLL_IDLE;
        // 杩炵画澶辫触鏃舵寚鏁伴€€閬?
        if (consecutiveErrors > 0) {
          nextInterval = Math.min(nextInterval * Math.pow(2, consecutiveErrors), POLL_MAX_BACKOFF);
        }
        currentInterval = nextInterval;
        timer = window.setTimeout(schedule, currentInterval);
      });
    }, POLL_INITIAL_DELAY); // 寤惰繜棣栨瑙﹀彂锛岄伩鍏嶄笌 switchSession 绔炴€?

    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [
    activeSessionId,
    setActiveSession,
    setStreaming,
    setCurrentModel,
    setFullAccessEnabled,
    setVisionCapable,
    setChatMode,
  ]);

  return null;
}

