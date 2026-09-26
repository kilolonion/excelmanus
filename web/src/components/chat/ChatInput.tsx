"use client";

import { activateModelProfile } from "@/lib/model-config-api";

import { useRef, useState, useCallback, useEffect, useMemo } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  ArrowUp,
  Play,
  Square,
  Loader2,
  Check,
  Cpu,
  AlertTriangle,
  ShieldX,
  Settings,
  KeyRound,
  Sparkles,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { useChatStore } from "@/stores/chat-store";
import { useSessionStore } from "@/stores/session-store";
import { useUIStore } from "@/stores/ui-store";
import { apiFetch, buildApiUrl, apiGet, getAuthHeaders } from "@/lib/api";
import { cleanModelDescription, formatModelIdForDisplay } from "@/lib/model-display";
import { applyVisionFromModel } from "@/lib/vision-capability";
import { UndoPanel } from "@/components/modals/UndoPanel";
import type { ExampleContext, ModelInfo, AttachedFile, MessageDispatchMode } from "@/lib/types";
import {
  SLASH_COMMANDS,
  DISPLAY_COMMANDS,
  FRONTEND_ACTIONS,
  isStreamedSlashCommand,
  AUTO_EXEC_ARGS,
  type PopoverMode,
} from "./chat-input-constants";
import { ChatModeTabs } from "./ChatModeTabs";
import { ModeBadges } from "./ModeBadges";
import { ThinkingLevelSelector } from "./ThinkingLevelSelector";
import { ContextUsageButton } from "./ContextUsageButton";
import { FileAttachmentChips } from "./FileAttachmentChips";
import { CommandPopover } from "./CommandPopover";
import { InlineQuestionBanner } from "@/components/modals/QuestionPanel";
import { answerQuestion } from "@/lib/api";
import { resumeAfterInteraction } from "@/lib/chat-actions";
import { applyDisplayReplacements, useInsertMentionTokens } from "./chat-input-insert";
import {
  ChatMentionList,
  applyMentionSelection,
  buildMentionPopoverItems,
  maybeOpenMentionPopover,
  useChatMentions,
} from "./ChatMentionList";
import { ChatDropzone, ChatUploadButton } from "./ChatUploadButton";
import { ChatSelectionChip } from "./ChatSelectionChip";
import { WorkbookContextChip } from "@/components/excel/WorkbookConversation";
import { ChatLiveSelectionChip } from "./ChatLiveSelectionChip";
import { workspaceKeyForSessionId } from "@/lib/workspace-file-ref";
import { useChatUpload } from "./use-chat-upload";
import { registerChatFileUpload } from "./chat-upload-bridge";
import { shouldCancelComposerNativeDrop } from "./chat-drop";
import { ComposerRecoveryBar } from "./ComposerRecoveryBar";
import { useExcelStore } from "@/stores/excel-store";
import { useWordStore } from "@/stores/word-store";
import { resolveWorkspaceSurface } from "@/lib/workspace-surface";
import { findLastRetryableFailure, needsContinuationOffer } from "@/lib/failure-recovery";
import { DispatchQueue } from "./DispatchQueue";

interface ChatInputProps {
  onSend: (text: string, files?: AttachedFile[], sessionId?: string | null, dispatchMode?: MessageDispatchMode, exampleContext?: ExampleContext) => void | boolean | Promise<void | boolean>;
  onCommandResult?: (command: string, result: string, format: "markdown" | "text") => void;
  disabled?: boolean;
  isStreaming?: boolean;
  onStop?: () => void;
  composerDraft?: { seq: number; text: string; files: File[]; example?: ExampleContext } | null;
}

export function ChatInput({ onSend, onCommandResult, disabled, isStreaming: streamingProp, onStop, composerDraft }: ChatInputProps) {
  const liveStream = useChatStore((s) => s.isStreaming || s.abortController !== null);
  const isStreaming = liveStream || Boolean(streamingProp);
  const fullViewPath = useExcelStore((s) => s.fullViewPath);
  const compareMode = useExcelStore((s) => s.compareMode);
  const wordFullViewPath = useWordStore((s) => s.fullViewPath);
  const showWorkbookContext = resolveWorkspaceSurface({ fullViewPath, compareMode, wordFullViewPath }) !== "excel";
  const sendPendingRef = useRef(false);
  const [text, setText] = useState("");
  const latestTextRef = useRef(text);
  latestTextRef.current = text;
  const [isAnswerSubmitting, setIsAnswerSubmitting] = useState(false);
  const [answerSubmitError, setAnswerSubmitError] = useState<string | null>(null);
  const [inputHint, setInputHint] = useState<string | null>(null);
  const [popover, setPopover] = useState<PopoverMode>(null);
  const [popoverFilter, setPopoverFilter] = useState("");
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [activeSlashCmd, setActiveSlashCmd] = useState<string | null>(null);
  const [modelList, setModelList] = useState<ModelInfo[]>([]);
  const currentModel = useUIStore((s) => s.currentModel);
  const visionCapable = useUIStore((s) => s.visionCapable);
  const configReady = useUIStore((s) => s.configReady);
  const configError = useUIStore((s) => s.configError);
  const configPlaceholderItems = useUIStore((s) => s.configPlaceholderItems);
  const messageDispatchDefault = useUIStore((s) => s.messageDispatchDefault);
  const openSettings = useUIStore((s) => s.openSettings);
  const setConfigError = useUIStore((s) => s.setConfigError);
  const [confirmedTokens, setConfirmedTokens] = useState<Set<string>>(new Set());
  const [undoPanelOpen, setUndoPanelOpen] = useState(false);
  const [isSending, setIsSending] = useState(false);
  const [exampleContext, setExampleContext] = useState<ExampleContext | undefined>();
  useEffect(() => {
    const controller = new AbortController();
    const before = useUIStore.getState().messageDispatchDefault;
    void apiGet<{ message_dispatch_default?: MessageDispatchMode }>("/config/runtime", { signal: controller.signal }).then((config) => {
      if (!controller.signal.aborted && config.message_dispatch_default && useUIStore.getState().messageDispatchDefault === before) {
        useUIStore.getState().setMessageDispatchDefault(config.message_dispatch_default);
      }
    }).catch(() => {});
    return () => controller.abort();
  }, []);

  const tokenMapRef = useRef<Map<string, string>>(new Map());
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);
  const backdropRef = useRef<HTMLDivElement>(null);
  const inputHintTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const isComposingRef = useRef(false);
  const pendingQuestion = useChatStore((s) => s.pendingQuestion);
  const answeringQuestion = Boolean(pendingQuestion);
  const pendingApproval = useChatStore((s) => s.pendingApproval);
  const hasMessages = useChatStore((s) => s.messageOrder.length > 0);
  // Streaming deltas do not affect the retry control. Avoid rerendering the
  // entire composer (and its file/mention pickers) for each received token.
  const messages = useChatStore((s) => s.isStreaming ? null : s.messages);
  const setPendingQuestion = useChatStore((s) => s.setPendingQuestion);
  const lastFailure = useMemo(
    () => (isStreaming || !messages ? null : findLastRetryableFailure(messages)),
    [isStreaming, messages],
  );
  // 异常退出/失败尾部（含未产生 failure_guidance 的静默中断）→ 三角形继续按钮。
  const continuationOffered = useMemo(
    () => !isStreaming && !pendingQuestion && !pendingApproval && !!messages
      && needsContinuationOffer(messages),
    [isStreaming, pendingQuestion, pendingApproval, messages],
  );

  const handleRetryLastFailedWithModel = useCallback((modelName: string) => {
    if (!lastFailure) return;
    const sessionId = useSessionStore.getState().activeSessionId;
    void import("@/lib/chat-actions").then(({ retryAssistantMessage }) => {
      retryAssistantMessage(lastFailure.messageId, sessionId, modelName);
    });
  }, [lastFailure]);
  const [questionSelected, setQuestionSelected] = useState<Set<string>>(new Set());

  useEffect(() => {
    setQuestionSelected(new Set());
    setAnswerSubmitError(null);
  }, [pendingQuestion?.id]);

  useEffect(() => {
    const handler = () => {
      setText("");
      requestAnimationFrame(() => textareaRef.current?.focus());
    };
    window.addEventListener("coach-clear-input", handler);
    return () => window.removeEventListener("coach-clear-input", handler);
  }, []);

  const toggleQuestionOption = useCallback((label: string) => {
    setQuestionSelected((prev) => {
      const next = new Set(prev);
      if (pendingQuestion?.multiSelect) {
        if (next.has(label)) next.delete(label);
        else next.add(label);
      } else {
        if (next.has(label)) {
          next.clear();
        } else {
          next.clear();
          next.add(label);
        }
      }
      return next;
    });
  }, [pendingQuestion?.multiSelect]);

  const { mentionData, fetchMentionData, atCategory, setAtCategory } = useChatMentions();
  const insertMentionTokens = useInsertMentionTokens(
    text,
    setText,
    textareaRef,
    tokenMapRef,
    setConfirmedTokens,
  );
  const {
    files,
    setFiles,
    getPreviewUrl,
    retryUpload,
    removeFile,
    insertFileMentions,
    attachWorkspaceFiles,
    applySuggestionDraft,
    handlePaste,
    hasUploadingFiles,
    hasFailedFiles,
  } = useChatUpload({
    text,
    setText,
    textareaRef,
    tokenMapRef,
    setConfirmedTokens,
  });

  // 桌面菜单等输入框外部入口经桥接注册表复用同一上传管线
  useEffect(() => registerChatFileUpload(insertFileMentions), [insertFileMentions]);

  const showInputHint = useCallback((message: string) => {
    setInputHint(message);
    if (inputHintTimerRef.current) {
      clearTimeout(inputHintTimerRef.current);
    }
    inputHintTimerRef.current = setTimeout(() => {
      setInputHint(null);
      inputHintTimerRef.current = null;
    }, 2600);
  }, []);

  const nudgeInput = useCallback((hint?: string) => {
    const el = textareaRef.current?.parentElement?.parentElement;
    if (el) {
      el.classList.add("animate-shake-subtle");
      setTimeout(() => el.classList.remove("animate-shake-subtle"), 400);
    }
    if (hint) showInputHint(hint);
  }, [showInputHint]);

  useEffect(() => {
    return () => {
      if (inputHintTimerRef.current) {
        clearTimeout(inputHintTimerRef.current);
      }
    };
  }, []);

  const slashCommandNames = useMemo(
    () => new Set(SLASH_COMMANDS.map((c) => c.command)),
    []
  );

  const renderHighlightedText = useCallback(
    (raw: string): React.ReactNode => {
      if (!raw) return "\u200B";
      const escaped: string[] = [];
      confirmedTokens.forEach((t) => escaped.push(t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
      slashCommandNames.forEach((c) => escaped.push(c.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
      if (escaped.length === 0) return raw + "\n";
      escaped.sort((a, b) => b.length - a.length);
      const pattern = new RegExp(`(${escaped.join("|")})`, "g");
      const parts = raw.split(pattern);
      const allTokens = new Set([...confirmedTokens, ...slashCommandNames]);
      return (
        <>
          {parts.map((part, i) =>
            allTokens.has(part) ? (
              <span
                key={i}
                style={{
                  backgroundColor: "color-mix(in srgb, var(--em-primary) 14%, transparent)",
                  color: "var(--em-primary)",
                  display: "inline",
                  padding: 0,
                  margin: 0,
                  border: "none",
                  borderRadius: "9999px",
                  lineHeight: "inherit",
                  fontFamily: "inherit",
                  fontSize: "inherit",
                  letterSpacing: "inherit",
                  boxDecorationBreak: "clone",
                  WebkitBoxDecorationBreak: "clone",
                }}
              >
                {part}
              </span>
            ) : (
              <span key={i}>{part}</span>
            )
          )}
          {"\n"}
        </>
      );
    },
    [confirmedTokens, slashCommandNames]
  );

  const syncScroll = useCallback(() => {
    if (textareaRef.current && backdropRef.current) {
      backdropRef.current.scrollTop = textareaRef.current.scrollTop;
      backdropRef.current.scrollLeft = textareaRef.current.scrollLeft;
    }
  }, []);

  const autoResize = useCallback(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    const next = Math.min(el.scrollHeight, 180);
    el.style.height = `${next}px`;
    el.style.overflow = next >= 180 ? "auto" : "hidden";
    syncScroll();
  }, [syncScroll]);

  const [draftHighlight, setDraftHighlight] = useState(false);
  useEffect(() => {
    if (!composerDraft) return;
    applySuggestionDraft(composerDraft.text, composerDraft.files);
    setExampleContext(composerDraft.example);
    setDraftHighlight(true);
    const highlightTimer = window.setTimeout(() => setDraftHighlight(false), 900);
    requestAnimationFrame(() => {
      autoResize();
      const el = textareaRef.current;
      el?.focus();
      el?.scrollIntoView({ behavior: "smooth", block: "nearest" });
    });
    return () => window.clearTimeout(highlightTimer);
  }, [composerDraft, applySuggestionDraft, autoResize]);

  const fetchModelList = useCallback(async () => {
    try {
      const data = await apiGet<{ models: ModelInfo[] }>("/models");
      setModelList(data.models);
      const active = data.models.find((m) => m.active);
      applyVisionFromModel(active);
    } catch {
      // 后端不可用
    }
  }, []);

  const isSlashPopover =
    popover === "slash" || popover === "slash-args" || popover === "slash-skills" || popover === "slash-model";

  const popoverItems = useMemo(() => {
    if (popover === "slash") {
      const filter = popoverFilter.toLowerCase();
      return SLASH_COMMANDS.filter(
        (c) => c.command.toLowerCase().includes(filter) || c.description.includes(filter)
      );
    }
    if (popover === "slash-args" && activeSlashCmd) {
      const cmd = SLASH_COMMANDS.find((c) => c.command === activeSlashCmd);
      if (!cmd?.args) return [];
      const filter = popoverFilter.toLowerCase();
      return cmd.args
        .filter((a) => a.toLowerCase().includes(filter))
        .map((a) => ({ command: `${activeSlashCmd} ${a}`, description: a, icon: cmd.icon }));
    }
    if (popover === "slash-skills") {
      const filter = popoverFilter.toLowerCase();
      const items: { command: string; description: string; icon: React.ReactNode }[] = [];
      if (mentionData) {
        for (const s of mentionData.skills) {
          if (!filter || s.name.toLowerCase().includes(filter) || (s.description || "").toLowerCase().includes(filter)) {
            items.push({ command: `/${s.name}`, description: s.description || "技能包", icon: <Sparkles className="h-3.5 w-3.5" /> });
          }
        }
      }
      if (items.length === 0) {
        items.push({ command: "", description: "暂无可用技能包", icon: <Sparkles className="h-3.5 w-3.5 opacity-30" /> });
      }
      return items;
    }
    if (popover === "slash-model") {
      const filter = popoverFilter.toLowerCase();
      const items: { command: string; description: string; icon: React.ReactNode; isActive?: boolean }[] = [];
      for (const m of modelList) {
        const displayModel = formatModelIdForDisplay(m.model);
        const label = m.name !== displayModel ? `${m.name} → ${displayModel}` : m.name;
        if (!filter || label.toLowerCase().includes(filter) || (m.description || "").toLowerCase().includes(filter)) {
          items.push({
            command: m.name,
            description: cleanModelDescription(m.description, [m.name, displayModel]),
            icon: m.name === currentModel
              ? <Check className="h-3.5 w-3.5" style={{ color: "var(--em-primary)" }} />
              : <Cpu className="h-3.5 w-3.5" />,
            isActive: m.name === currentModel,
          });
        }
      }
      if (items.length === 0) {
        items.push({ command: "", description: "暂无可用模型", icon: <Cpu className="h-3.5 w-3.5 opacity-30" /> });
      }
      return items;
    }
    if (popover === "at" || popover === "at-sub") {
      return buildMentionPopoverItems(popover, popoverFilter, atCategory, mentionData);
    }
    return [];
  }, [popover, popoverFilter, activeSlashCmd, atCategory, mentionData, modelList, currentModel]);

  useEffect(() => {
    setSelectedIndex(0);
  }, [popoverItems.length]);

  const closePopover = () => {
    setPopover(null);
    setPopoverFilter("");
    setActiveSlashCmd(null);
    setAtCategory(null);
  };

  const handleTextChange = (value: string) => {
    setText(value);

    setConfirmedTokens((prev) => {
      if (prev.size === 0) return prev;
      let changed = false;
      const next = new Set<string>();
      prev.forEach((token) => {
        if (value.includes(token)) {
          next.add(token);
        } else {
          changed = true;
          tokenMapRef.current.delete(token);
        }
      });
      return changed ? next : prev;
    });

    requestAnimationFrame(autoResize);

    if (value === "/") {
      setPopover("slash");
      setPopoverFilter("");
      return;
    }

    if (value.startsWith("/") && popover === "slash") {
      setPopoverFilter(value.slice(1));
      return;
    }

    if (popover === "slash" && value.includes(" ")) {
      const cmd = value.split(" ")[0];
      const matched = SLASH_COMMANDS.find((c) => c.command === cmd);
      if (matched?.args) {
        setActiveSlashCmd(cmd);
        setPopover("slash-args");
        setPopoverFilter(value.split(" ").slice(1).join(" "));
        return;
      }
      closePopover();
      return;
    }

    if (popover === "slash-args") {
      const parts = value.split(" ");
      setPopoverFilter(parts.slice(1).join(" "));
      return;
    }

    if (popover === "slash-skills" || popover === "slash-model") {
      setPopoverFilter(value);
      return;
    }

    if (maybeOpenMentionPopover(value, fetchMentionData, setPopover, setPopoverFilter)) {
      return;
    }

    if (popover) closePopover();
  };

  const selectPopoverItem = (item: { command: string; hasChildren?: boolean }) => {
    if (applyMentionSelection({
      item,
      popover,
      text,
      setText,
      tokenMapRef,
      setConfirmedTokens,
      setAtCategory,
      setPopover,
      setPopoverFilter,
      fetchMentionData,
      closePopover,
      textareaRef,
    })) {
      return;
    }

    if (popover === "slash") {
      if (item.command === "/undo") {
        closePopover();
        setText("");
        setUndoPanelOpen(true);
        return;
      }
      if (item.command === "/skills") {
        fetchMentionData();
        setPopover("slash-skills");
        setPopoverFilter("");
        setText("");
        textareaRef.current?.focus();
        return;
      }
      if (item.command === "/model") {
        fetchModelList();
        setPopover("slash-model");
        setPopoverFilter("");
        setText("");
        textareaRef.current?.focus();
        return;
      }
      const cmd = SLASH_COMMANDS.find((c) => c.command === item.command);
      if (cmd?.args) {
        setActiveSlashCmd(item.command);
        setPopover("slash-args");
        setPopoverFilter("");
        setText(item.command + " ");
        textareaRef.current?.focus();
        return;
      }
      setText(item.command + " ");
      closePopover();
    } else if (popover === "slash-args") {
      const argPart = item.command.split(" ").slice(1).join(" ");
      if (AUTO_EXEC_ARGS.has(argPart)) {
        closePopover();
        setText("");
        handleSendCommand(item.command);
        return;
      }
      setText(item.command + " ");
      closePopover();
    } else if (popover === "slash-skills") {
      if (!item.command) return;
      setText(item.command + " ");
      closePopover();
      textareaRef.current?.focus();
      return;
    } else if (popover === "slash-model") {
      if (!item.command) return;
      handleModelSwitch(item.command);
      return;
    }
    textareaRef.current?.focus();
  };

  const handleModelSwitch = async (name: string) => {
    if (name === currentModel) {
      closePopover();
      setText("");
      if (onCommandResult) {
        onCommandResult("/model", `当前已是 **${name}**`, "markdown");
      }
      return;
    }
    try {
      await activateModelProfile(name);
      applyVisionFromModel(modelList.find((m) => m.name === name));
      closePopover();
      setText("");
      if (onCommandResult) {
        onCommandResult("/model", `已切换到 **${name}**`, "markdown");
      }
    } catch {
      closePopover();
      setText("");
      if (onCommandResult) {
        onCommandResult("/model", `切换到 ${name} 失败`, "text");
      }
    }
  };

  const handleSendCommand = async (command: string) => {
    const trimmed = command.trim();
    const action = FRONTEND_ACTIONS[trimmed.split(" ")[0]];
    if (action === "stop") { onStop?.(); return; }
    if (action === "clear") {
      const sessionId = useSessionStore.getState().activeSessionId;
      useChatStore.getState().clearMessages();
      if (sessionId) {
        apiFetch(buildApiUrl(`/sessions/${sessionId}/clear`), { method: "POST", headers: { ...getAuthHeaders() } }).catch(() => {});
      }
      if (onCommandResult) onCommandResult("/clear", "对话历史已清除", "text");
      return;
    }
    if (onCommandResult && trimmed.startsWith("/") && !isStreamedSlashCommand(trimmed)) {
      const sessionId = useSessionStore.getState().activeSessionId;
      try {
        const res = await apiFetch(buildApiUrl("/command"), {
          method: "POST",
          headers: { "Content-Type": "application/json", ...getAuthHeaders() },
          body: JSON.stringify({ command: trimmed, session_id: sessionId || "" }),
        });
        if (res.ok) {
          const data = await res.json();
          if (!data.result?.startsWith("未知命令")) {
            onCommandResult(trimmed, data.result, data.format || "text");
            return;
          }
        }
      } catch { /* 回退到作为聊天发送 */ }
    }
    onSend(trimmed, undefined, undefined, undefined, exampleContext);
  };

  const configBlocked = configReady !== true || !!configError;

  const getConfigBlockedHint = useCallback(() => {
    if (configError) return "模型服务异常，请先在设置中修复后再发送";
    if (configReady === null) return "正在检查模型配置，请稍候再发送";
    if (configReady === false) return "模型未配置，请先完成配置后再发送";
    return "当前无法发送，请检查模型配置后重试";
  }, [configError, configReady]);

  const handleSend = async () => {
    if (sendPendingRef.current) return;
    if (disabled) {
      nudgeInput("正在切换对话，请稍候");
      return;
    }
    if (configBlocked) {
      nudgeInput(getConfigBlockedHint());
      return;
    }
    if (hasUploadingFiles) {
      nudgeInput("附件上传中，请稍候再发送");
      return;
    }
    if (hasFailedFiles) {
      nudgeInput("有文件上传失败，请重试或移除后再发送");
      return;
    }
    if (isAnswerSubmitting) {
      nudgeInput("正在提交回答，请稍候");
      return;
    }
    const trimmed = text.trim();
    if (!trimmed && files.length === 0 && questionSelected.size === 0) return;
    closePopover();

    if (trimmed.startsWith("/")) {
      if (trimmed === "/undo") {
        setText("");
        requestAnimationFrame(autoResize);
        setUndoPanelOpen(true);
        return;
      }

      const action = FRONTEND_ACTIONS[trimmed.split(" ")[0]];
      if (action === "stop") {
        onStop?.();
        setText("");
        requestAnimationFrame(autoResize);
        return;
      }
      if (action === "clear") {
        const sessionId = useSessionStore.getState().activeSessionId;
        useChatStore.getState().clearMessages();
        setText("");
        requestAnimationFrame(autoResize);
        if (sessionId) {
          apiFetch(buildApiUrl(`/sessions/${sessionId}/clear`), { method: "POST", headers: { ...getAuthHeaders() } }).catch(() => {});
        }
        if (onCommandResult) {
          onCommandResult("/clear", "对话历史已清除", "text");
        }
        return;
      }
      if ((action === "accept" || action === "reject") && trimmed.split(" ").length === 1) {
        const state = useChatStore.getState();
        const pending = state.pendingApproval;
        if (pending) {
          state.dismissApproval(pending.id);
          const cmd = `/${action} ${pending.id}`;
          onSend(cmd);
          setText("");
          requestAnimationFrame(autoResize);
        } else {
          if (onCommandResult) {
            onCommandResult(`/${action}`, "当前没有待审批的操作", "text");
          }
          setText("");
        }
        return;
      }

      if (trimmed.startsWith("/model ") && !DISPLAY_COMMANDS.has(trimmed)) {
        const modelName = trimmed.slice("/model ".length).trim();
        if (modelName) {
          setText("");
          handleModelSwitch(modelName);
          return;
        }
      }

      if (onCommandResult && !isStreamedSlashCommand(trimmed)) {
        const sessionId = useSessionStore.getState().activeSessionId;
        try {
          const res = await apiFetch(buildApiUrl("/command"), {
            method: "POST",
            headers: { "Content-Type": "application/json", ...getAuthHeaders() },
            body: JSON.stringify({ command: trimmed, session_id: sessionId || "" }),
          });
          if (res.ok) {
            const data = await res.json();
            if (!data.result?.startsWith("未知命令")) {
              onCommandResult(trimmed, data.result, data.format || "text");
              setText("");
              requestAnimationFrame(autoResize);
              return;
            }
          }
        } catch {
          // 回退到作为聊天发送
        }
      }
    }

    if (pendingQuestion && answeringQuestion) {
      const selectedLabels = Array.from(questionSelected);
      let answer: string;
      if (selectedLabels.length > 0 && trimmed) {
        answer = `${selectedLabels.join("\n")}\n${trimmed}`;
      } else if (selectedLabels.length > 0) {
        answer = selectedLabels.join("\n");
      } else {
        answer = trimmed;
      }
      if (!answer.trim()) return;
      const questionId = pendingQuestion.id;
      const sessionId = useSessionStore.getState().activeSessionId;
      if (!sessionId || !questionId) {
        const errorMessage = !sessionId
          ? "当前会话不可用，无法提交回答，请刷新后重试"
          : "问题信息缺失，无法提交回答，请让助手重新提问";
        setAnswerSubmitError(errorMessage);
        nudgeInput(errorMessage);
        return;
      }
      setAnswerSubmitError(null);
      setIsAnswerSubmitting(true);
      const hasMoreQuestions = (pendingQuestion.queueSize ?? 0) > 1;
      try {
        const response = await answerQuestion(sessionId, questionId, answer);
        if ((!hasMoreQuestions || response.resume_required) && useChatStore.getState().pendingQuestion?.id === questionId) {
          setPendingQuestion(null);
        }
        setQuestionSelected(new Set());
        setText("");
        requestAnimationFrame(autoResize);
        resumeAfterInteraction(sessionId, response);
      } catch (err) {
        console.error("[ChatInput] answerQuestion failed:", err);
        const errorMessage = "回答提交失败，请稍后重试";
        setAnswerSubmitError(errorMessage);
        nudgeInput(errorMessage);
      } finally {
        setIsAnswerSubmitting(false);
      }
      return;
    }
    const sessionId = useSessionStore.getState().activeSessionId;
    const draftText = text;
    const finalText = applyDisplayReplacements(trimmed, tokenMapRef.current);
    sendPendingRef.current = true;
    setIsSending(true);
    try {
      if (files.some((file) => file.workspaceKey && file.workspaceKey !== workspaceKeyForSessionId(sessionId))) {
        throw new Error("附件属于另一个工作区，请移除后在当前工作区重新选择");
      }
      if (useSessionStore.getState().activeSessionId !== sessionId) throw new Error("已切换对话，草稿已保留，请确认后重新发送");
      const validFiles = files.filter((af) => af.status === "success");
      const sent = await onSend(finalText, validFiles.length > 0 ? validFiles : undefined, sessionId, messageDispatchDefault, exampleContext);
      if (sent === false) return;
      if (useSessionStore.getState().activeSessionId !== sessionId) return;
      setText((current) => current === draftText ? "" : current);
      setFiles((current) => current.filter((af) => !validFiles.some((sentFile) => sentFile.id === af.id)));
      if (latestTextRef.current === draftText) {
        setConfirmedTokens(new Set());
        tokenMapRef.current.clear();
      }
      requestAnimationFrame(autoResize);
    } catch (err) {
      nudgeInput(err instanceof Error ? err.message : "暂时无法发送，请重试");
    } finally {
      sendPendingRef.current = false;
      setIsSending(false);
    }
  };

  // 三角形「继续」：后台发送隐藏的 continue 接续异常退出/失败的回合，
  // 不产生前台用户消息气泡。
  const handleContinue = useCallback(() => {
    if (disabled) {
      nudgeInput("正在切换对话，请稍候");
      return;
    }
    if (configBlocked) {
      nudgeInput(getConfigBlockedHint());
      return;
    }
    const sessionId = useSessionStore.getState().activeSessionId;
    void import("@/lib/chat-actions").then(({ sendContinuation }) =>
      sendContinuation("continue", sessionId, { promptKind: "continue" }),
    ).catch((err) =>
      nudgeInput(`继续失败：${err instanceof Error ? err.message : "未知错误"}`)
    );
  }, [disabled, configBlocked, getConfigBlockedHint, nudgeInput]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (isComposingRef.current) return;

    if (popover && popoverItems.length > 0) {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setSelectedIndex((i) => (i + 1) % popoverItems.length);
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setSelectedIndex((i) => (i - 1 + popoverItems.length) % popoverItems.length);
        return;
      }
      if (e.key === "Tab" || (e.key === "Enter" && popover)) {
        e.preventDefault();
        selectPopoverItem(popoverItems[selectedIndex]);
        return;
      }
      if (e.key === "Escape") {
        e.preventDefault();
        closePopover();
        return;
      }
    }

    if (e.key === "Backspace" || e.key === "Delete") {
      const textarea = textareaRef.current;
      if (!textarea) return;
      const { selectionStart, selectionEnd } = textarea;
      if (selectionStart !== selectionEnd) return;
      const cursor = selectionStart;
      for (const token of confirmedTokens) {
        const idx = text.indexOf(token);
        if (idx < 0) continue;
        const tokenEnd = idx + token.length;
        const hit =
          e.key === "Backspace"
            ? cursor > idx && cursor <= tokenEnd
            : cursor >= idx && cursor < tokenEnd;
        if (hit) {
          e.preventDefault();
          const trailSpace = text[tokenEnd] === " " ? 1 : 0;
          const newText = text.slice(0, idx) + text.slice(tokenEnd + trailSpace);
          setText(newText);
          setConfirmedTokens((prev) => {
            const next = new Set(prev);
            next.delete(token);
            return next;
          });
          tokenMapRef.current.delete(token);
          const newCursor = idx;
          requestAnimationFrame(() => {
            textarea.focus();
            textarea.setSelectionRange(newCursor, newCursor);
            autoResize();
          });
          return;
        }
      }
    }

    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (disabled) {
        nudgeInput("正在切换对话，请稍候");
        return;
      }
      if (isAnswerSubmitting) {
        nudgeInput("正在提交回答，请稍候");
        return;
      }
      handleSend();
    }
  };

  return (
    <>
      <DispatchQueue />
      {lastFailure && !pendingQuestion && (
        <ComposerRecoveryBar
          hint={lastFailure.title}
          onContinue={handleContinue}
          onRetryWithModel={handleRetryLastFailedWithModel}
        />
      )}
    <div className="em-composer-tabs" aria-label="当前引用">
      {showWorkbookContext && <WorkbookContextChip />}
      <ChatLiveSelectionChip />
    </div>
    <ChatDropzone
      onNativeFiles={insertFileMentions}
      onExcelFiles={attachWorkspaceFiles}
      highlighted={draftHighlight}
    >
      <ChatSelectionChip insertMentionTokens={insertMentionTokens} />
      <ChatMentionList
        popover={popover}
        popoverItems={popoverItems}
        selectedIndex={selectedIndex}
        setSelectedIndex={setSelectedIndex}
        selectPopoverItem={selectPopoverItem}
        popoverRef={popoverRef}
        atCategory={atCategory}
        onBackToAt={() => { setPopover("at"); setAtCategory(null); setPopoverFilter(""); }}
        setText={setText}
        textareaRef={textareaRef}
        tokenMapRef={tokenMapRef}
        setConfirmedTokens={setConfirmedTokens}
        autoResize={autoResize}
        insertMentionTokens={insertMentionTokens}
      />

      {isSlashPopover && (
        <CommandPopover
          popover={popover}
          popoverItems={popoverItems}
          selectedIndex={selectedIndex}
          setSelectedIndex={setSelectedIndex}
          selectPopoverItem={selectPopoverItem}
          popoverRef={popoverRef}
          activeSlashCmd={activeSlashCmd}
          atCategory={atCategory}
          onBackToSlash={() => { setPopover("slash"); setPopoverFilter(""); setText("/"); }}
          onBackToAt={() => { setPopover("at"); setAtCategory(null); setPopoverFilter(""); }}
        />
      )}

      <AnimatePresence>
        {(configReady === false || !!configError) && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
            transition={{ duration: 0.2, ease: "easeOut" }}
            className="overflow-hidden"
          >
            <div className={`mx-2 mt-2 rounded-xl border px-3.5 py-2.5 ${
              configError
                ? "border-red-200 bg-red-50/80 dark:border-red-900/50 dark:bg-red-950/30"
                : "border-amber-200 bg-amber-50/80 dark:border-amber-900/50 dark:bg-amber-950/30"
            }`}>
              <div className="flex flex-wrap items-center gap-2 sm:gap-2.5">
                <div className="flex items-center gap-2 sm:gap-2.5 min-w-0 flex-1 basis-52">
                  <div className={`flex h-6 w-6 sm:h-7 sm:w-7 shrink-0 items-center justify-center rounded-full ${
                    configError
                      ? "bg-red-100 dark:bg-red-900/40"
                      : "bg-amber-100 dark:bg-amber-900/40"
                  }`}>
                    {configError ? (
                      <ShieldX className="h-3 w-3 sm:h-3.5 sm:w-3.5 text-red-600 dark:text-red-400" />
                    ) : (
                      <AlertTriangle className="h-3 w-3 sm:h-3.5 sm:w-3.5 text-amber-600 dark:text-amber-400" />
                    )}
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-1 flex-wrap">
                      <p className={`text-xs sm:text-sm font-medium ${
                        configError
                          ? "text-red-800 dark:text-red-300"
                          : "text-amber-800 dark:text-amber-300"
                      }`}>
                        {configError ? "模型服务不可用" : "模型未配置"}
                      </p>
                      <span className={`text-[11px] sm:text-xs ${
                        configError
                          ? "text-red-600/80 dark:text-red-400/70"
                          : "text-amber-600/80 dark:text-amber-400/70"
                      }`}>
                        — {configError
                          ? "请检查配置后重试"
                          : "请先完成 API 配置"}
                      </span>
                    </div>
                    {!configError && configPlaceholderItems.length > 0 && (
                      <div className="mt-1 flex flex-wrap gap-1">
                        {configPlaceholderItems.map((item, i) => (
                          <span
                            key={i}
                            className="inline-flex items-center gap-1 rounded-md bg-amber-100/80 dark:bg-amber-900/30 px-1.5 py-0.5 text-[11px] text-amber-700 dark:text-amber-400"
                          >
                            <KeyRound className="h-2.5 w-2.5" />
                            {item.name === "active" ? "当前模型" : item.name}
                            <span className="text-amber-500/60 dark:text-amber-500/40">·</span>
                            {item.field === "api_key" ? "Key 缺失" : `${item.field}`}
                          </span>
                        ))}
                      </div>
                    )}
                    {configError && (
                      <p className="text-[11px] text-red-500/70 dark:text-red-400/50 truncate" title={configError}>
                        {configError}
                      </p>
                    )}
                  </div>
                </div>
                <div className="flex items-center gap-2 shrink-0 pl-8 sm:pl-0 sm:ml-auto">
                  {configError && (
                    <button
                      type="button"
                      onClick={() => { setConfigError(null); useUIStore.getState().bumpModelProfiles(); }}
                      className="text-xs text-red-500/70 hover:text-red-600 dark:text-red-400/60 dark:hover:text-red-400 transition-colors"
                    >
                      重新检查
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={() => openSettings("model")}
                    className={`inline-flex items-center gap-1 rounded-lg px-2.5 py-1 text-xs font-medium transition-colors ${
                      configError
                        ? "bg-red-600 hover:bg-red-700 text-white dark:bg-red-700 dark:hover:bg-red-600"
                        : "bg-amber-600 hover:bg-amber-700 text-white dark:bg-amber-600 dark:hover:bg-amber-500"
                    }`}
                  >
                    <Settings className="h-3 w-3" />
                    前往设置
                  </button>
                </div>
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      <AnimatePresence>
        {pendingQuestion && (
          <InlineQuestionBanner
            question={pendingQuestion}
            selected={questionSelected}
            onToggle={toggleQuestionOption}
          />
        )}
      </AnimatePresence>

      {!pendingQuestion && (
        <div className="flex flex-wrap items-center justify-between gap-y-1 px-1 pt-1">
          <ChatModeTabs />
          <div className="flex items-center gap-0.5 pr-3 pt-1 pb-0">
            <ModeBadges />
            <ThinkingLevelSelector />
            <ContextUsageButton />
          </div>
        </div>
      )}

      <FileAttachmentChips
        files={files}
        visionCapable={visionCapable}
        getPreviewUrl={getPreviewUrl}
        retryUpload={retryUpload}
        removeFile={removeFile}
      />

      <AnimatePresence initial={false}>
        {(isAnswerSubmitting || answerSubmitError || inputHint) && (
          <motion.div
            initial={{ opacity: 0, y: -4 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -4 }}
            transition={{ duration: 0.16, ease: "easeOut" }}
            className="px-3 pb-0.5"
          >
            {isAnswerSubmitting ? (
              <div className="inline-flex items-center gap-1.5 rounded-md bg-muted/60 px-2 py-1 text-[11px] text-muted-foreground">
                <Loader2 className="h-3 w-3 animate-spin" />
                正在提交回答...
              </div>
            ) : answerSubmitError ? (
              <div className="inline-flex items-center gap-1.5 rounded-md border border-red-200/80 bg-red-50/80 px-2 py-1 text-[11px] text-red-600 dark:border-red-900/50 dark:bg-red-950/30 dark:text-red-400">
                <AlertTriangle className="h-3 w-3" />
                {answerSubmitError}
              </div>
            ) : (
              <div className="inline-flex items-center gap-1.5 rounded-md bg-muted/60 px-2 py-1 text-[11px] text-muted-foreground">
                {inputHint}
              </div>
            )}
          </motion.div>
        )}
      </AnimatePresence>

      <div className="em-input-toolbar flex items-end gap-1 px-1.5 py-1.5">
        <ChatUploadButton onPick={insertFileMentions} />

        <div className="relative flex-1 min-w-0">
          <div
            ref={backdropRef}
            aria-hidden="true"
            className="em-composer-backdrop absolute inset-0 pointer-events-none overflow-hidden whitespace-pre-wrap break-words font-sans"
            style={{
              color: "var(--foreground)",
              padding: "8px 8px",
              fontSize: "13px",
              lineHeight: "20px",
              fontFamily: "var(--font-sans, inherit)",
              wordBreak: "break-all",
              WebkitTextSizeAdjust: "100%",
            }}
          >
            {renderHighlightedText(text)}
          </div>
          <Textarea
            ref={textareaRef}
            value={text}
            onChange={(e) => handleTextChange(e.target.value)}
            onKeyDown={handleKeyDown}
            onScroll={syncScroll}
            onPaste={handlePaste}
            onDragOver={(e) => {
              if (shouldCancelComposerNativeDrop(e.dataTransfer.types, useExcelStore.getState().draggingFileCount)) {
                e.preventDefault();
              }
            }}
            onDrop={(e) => {
              if (shouldCancelComposerNativeDrop(e.dataTransfer.types, useExcelStore.getState().draggingFileCount)) {
                e.preventDefault();
              }
            }}
            onCompositionStart={() => { isComposingRef.current = true; }}
            onCompositionEnd={() => { isComposingRef.current = false; }}
            placeholder={answeringQuestion ? "输入自定义回答，或选择上方选项后发送" : (hasMessages ? "继续分析，或告诉我下一步…" : "有问题，尽管问")}
            disabled={disabled || isAnswerSubmitting}
            className="min-h-[36px] max-h-[180px] resize-none border-0 bg-transparent shadow-none
              focus-visible:ring-0 focus-visible:ring-offset-0
              selection:bg-[var(--em-primary)]/20 relative z-10"
            style={{
              padding: "8px 8px",
              fontSize: "13px",
              lineHeight: "20px",
              fontFamily: "var(--font-sans, inherit)",
              wordBreak: "break-all" as const,
              WebkitTextSizeAdjust: "100%",
              color: "transparent",
              caretColor: "var(--foreground)",
            }}
            rows={1}
          />
        </div>

        <div className="flex-shrink-0">
            {isStreaming && !pendingQuestion && !text.trim() && files.length === 0 ? (
                <Button
                  key="stop"
                  data-coach-id="coach-stop-btn"
                  aria-label="停止生成"
                  title="停止生成"
                  size="icon"
                  className="touch-compact h-9 w-9 sm:h-8 sm:w-8 rounded-full bg-foreground hover:bg-foreground/80"
                  onClick={onStop}
                >
                  <Square className="h-3 w-3 fill-background text-background" />
                </Button>
            ) : continuationOffered && !text.trim() && files.length === 0 ? (
                <Button
                  key="continue"
                  data-coach-id="coach-continue-btn"
                  aria-label="继续执行"
                  title="继续执行"
                  size="icon"
                  className="touch-compact h-9 w-9 sm:h-8 sm:w-8 rounded-full text-white transition-opacity send-btn-glow"
                  style={{ backgroundColor: "var(--em-primary)" }}
                  onClick={handleContinue}
                  disabled={disabled || isAnswerSubmitting || configBlocked}
                >
                  {isAnswerSubmitting ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <Play className="h-3.5 w-3.5 fill-current" />
                  )}
                </Button>
            ) : (
              <div className="flex items-center gap-1">
                {isStreaming && <Button type="button" variant="ghost" size="icon-sm" aria-label="停止生成" title="停止生成" onClick={onStop}><Square className="size-3.5" /></Button>}
                <Button
                  key="send"
                  data-coach-id="coach-send-btn"
                  aria-label={answeringQuestion ? "发送回答" : "发送消息"}
                  size="icon"
                  className="touch-compact h-9 w-9 sm:h-8 sm:w-8 rounded-full text-white transition-opacity send-btn-glow"
                  style={{ backgroundColor: "var(--em-primary)" }}
                  onClick={handleSend}
                  disabled={disabled || isSending || isAnswerSubmitting || configBlocked || (!text.trim() && files.length === 0 && questionSelected.size === 0) || hasUploadingFiles || hasFailedFiles}
                >
                  {isAnswerSubmitting || isSending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <ArrowUp className="h-3.5 w-3.5" />}
                </Button>
              </div>
            )}
        </div>
      </div>
      <UndoPanel open={undoPanelOpen} onClose={() => setUndoPanelOpen(false)} />
    </ChatDropzone>
    </>
  );
}
