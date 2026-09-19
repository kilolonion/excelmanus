"use client";

import { useRef, useState, useCallback, useEffect, useMemo } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  ArrowUp,
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
import { buildApiUrl, apiGet, apiPut, getAuthHeaders, fetchWorkspaceFiles } from "@/lib/api";
import { extractTypedFileMentions, findMissingFileMentions, shouldBlockMissingFileMentions } from "@/lib/mention-existence";
import { formatModelIdForDisplay } from "@/lib/model-display";
import { applyVisionFromModel } from "@/lib/vision-capability";
import { UndoPanel } from "@/components/modals/UndoPanel";
import type { ModelInfo, AttachedFile } from "@/lib/types";
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
import { useChatUpload } from "./use-chat-upload";
import { shouldCancelComposerNativeDrop } from "./chat-drop";
import { ComposerRecoveryBar } from "./ComposerRecoveryBar";
import { useExcelStore } from "@/stores/excel-store";
import { findLastRetryableFailure } from "@/lib/failure-recovery";

interface ChatInputProps {
  onSend: (text: string, files?: AttachedFile[]) => void;
  onCommandResult?: (command: string, result: string, format: "markdown" | "text") => void;
  disabled?: boolean;
  isStreaming?: boolean;
  onStop?: () => void;
  composerDraft?: { seq: number; text: string; files: File[] } | null;
}

export function ChatInput({ onSend, onCommandResult, disabled, isStreaming, onStop, composerDraft }: ChatInputProps) {
  const [text, setText] = useState("");
  const [isAnswerSubmitting, setIsAnswerSubmitting] = useState(false);
  const [answerSubmitError, setAnswerSubmitError] = useState<string | null>(null);
  const [inputHint, setInputHint] = useState<string | null>(null);
  const [popover, setPopover] = useState<PopoverMode>(null);
  const [popoverFilter, setPopoverFilter] = useState("");
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [activeSlashCmd, setActiveSlashCmd] = useState<string | null>(null);
  const [modelList, setModelList] = useState<ModelInfo[]>([]);
  const currentModel = useUIStore((s) => s.currentModel);
  const setCurrentModel = useUIStore((s) => s.setCurrentModel);
  const visionCapable = useUIStore((s) => s.visionCapable);
  const configReady = useUIStore((s) => s.configReady);
  const configError = useUIStore((s) => s.configError);
  const configPlaceholderItems = useUIStore((s) => s.configPlaceholderItems);
  const openSettings = useUIStore((s) => s.openSettings);
  const setConfigError = useUIStore((s) => s.setConfigError);
  const [confirmedTokens, setConfirmedTokens] = useState<Set<string>>(new Set());
  const [undoPanelOpen, setUndoPanelOpen] = useState(false);

  const tokenMapRef = useRef<Map<string, string>>(new Map());
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);
  const backdropRef = useRef<HTMLDivElement>(null);
  const inputHintTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const isComposingRef = useRef(false);
  const pendingQuestion = useChatStore((s) => s.pendingQuestion);
  const hasMessages = useChatStore((s) => s.messageOrder.length > 0);
  const messages = useChatStore((s) => s.messages);
  const setPendingQuestion = useChatStore((s) => s.setPendingQuestion);
  const lastFailure = useMemo(
    () => (isStreaming ? null : findLastRetryableFailure(messages)),
    [isStreaming, messages],
  );

  const handleRetryLastFailed = useCallback(() => {
    if (!lastFailure) return;
    const sessionId = useSessionStore.getState().activeSessionId;
    void import("@/lib/chat-actions").then(({ retryAssistantMessage }) => {
      retryAssistantMessage(lastFailure.messageId, sessionId);
    });
  }, [lastFailure]);

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
            description: m.description || displayModel,
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
      await apiPut("/models/active", { name });
      setCurrentModel(name);
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
        fetch(buildApiUrl(`/sessions/${sessionId}/clear`), { method: "POST", headers: { ...getAuthHeaders() } }).catch(() => {});
      }
      if (onCommandResult) onCommandResult("/clear", "对话历史已清除", "text");
      return;
    }
    if (onCommandResult && trimmed.startsWith("/") && !isStreamedSlashCommand(trimmed)) {
      const sessionId = useSessionStore.getState().activeSessionId;
      try {
        const res = await fetch(buildApiUrl("/command"), {
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
    onSend(trimmed);
  };

  const configBlocked = configReady !== true || !!configError;

  const getConfigBlockedHint = useCallback(() => {
    if (configError) return "模型服务异常，请先在设置中修复后再发送";
    if (configReady === null) return "正在检查模型配置，请稍候再发送";
    if (configReady === false) return "模型未配置，请先完成配置后再发送";
    return "当前无法发送，请检查模型配置后重试";
  }, [configError, configReady]);

  const handleSend = async () => {
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
          fetch(buildApiUrl(`/sessions/${sessionId}/clear`), { method: "POST", headers: { ...getAuthHeaders() } }).catch(() => {});
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
          const res = await fetch(buildApiUrl("/command"), {
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

    if (pendingQuestion) {
      const selectedLabels = Array.from(questionSelected);
      let answer: string;
      if (selectedLabels.length > 0 && trimmed) {
        answer = `${selectedLabels.join(", ")}\n${trimmed}`;
      } else if (selectedLabels.length > 0) {
        answer = selectedLabels.join(", ");
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
        await answerQuestion(sessionId, questionId, answer);
        if (!hasMoreQuestions) {
          setPendingQuestion(null);
        }
        setQuestionSelected(new Set());
        setText("");
        requestAnimationFrame(autoResize);
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
    const finalText = applyDisplayReplacements(trimmed, tokenMapRef.current);
    try {
      if (extractTypedFileMentions(finalText).length > 0) {
        const sessionId = useSessionStore.getState().activeSessionId;
        const workspaceFiles = await fetchWorkspaceFiles(sessionId);
        const knownPaths = workspaceFiles.map((f) => f.path);
        const missing = findMissingFileMentions(finalText, knownPaths);
        if (shouldBlockMissingFileMentions(knownPaths, missing)) {
          nudgeInput(`找不到引用的文件：${missing[0]}，请检查后重试`);
          return;
        }
      }
    } catch {
      /* fail-open：工作区列表不可用时跳过校验放行 */
    }
    const validFiles = files.filter((af) => af.status === "success");
    onSend(finalText, validFiles.length > 0 ? validFiles : undefined);
    setText("");
    setFiles([]);
    setConfirmedTokens(new Set());
    tokenMapRef.current.clear();
    requestAnimationFrame(autoResize);
  };

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
      if (isStreaming) {
        nudgeInput("助手正在回复，请稍候");
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
      {lastFailure && !pendingQuestion && (
        <ComposerRecoveryBar
          hint={lastFailure.title}
          onRetry={handleRetryLastFailed}
          onRetryWithModel={handleRetryLastFailedWithModel}
        />
      )}
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
              <div className="flex flex-col sm:flex-row sm:items-center gap-2 sm:gap-2.5">
                <div className="flex items-center gap-2 sm:gap-2.5 min-w-0">
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
                      onClick={() => setConfigError(null)}
                      className="text-xs text-red-500/70 hover:text-red-600 dark:text-red-400/60 dark:hover:text-red-400 transition-colors"
                    >
                      忽略并重试
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
        <div className="flex items-center justify-between">
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

      <div className="flex items-end gap-1 px-1.5 py-1.5">
        <ChatUploadButton onPick={insertFileMentions} />

        <div className="relative flex-1 min-w-0">
          <div
            ref={backdropRef}
            aria-hidden="true"
            className="absolute inset-0 pointer-events-none overflow-hidden whitespace-pre-wrap break-words font-sans"
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
            placeholder={pendingQuestion ? "输入自定义回答，或选择上方选项后发送" : (hasMessages ? "继续分析，或告诉我下一步…" : "有问题，尽管问")}
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
          <AnimatePresence mode="wait" initial={false}>
            {isStreaming && !pendingQuestion ? (
              <motion.div
                key="stop"
                initial={{ scale: 0, rotate: -90 }}
                animate={{ scale: 1, rotate: 0 }}
                exit={{ scale: 0, rotate: 90 }}
                transition={{ duration: 0.15, ease: "easeOut" }}
              >
                <Button
                  data-coach-id="coach-stop-btn"
                  size="icon"
                  className="touch-compact h-9 w-9 sm:h-8 sm:w-8 rounded-full bg-foreground hover:bg-foreground/80"
                  onClick={onStop}
                >
                  <Square className="h-3 w-3 fill-background text-background" />
                </Button>
              </motion.div>
            ) : (
              <motion.div
                key="send"
                initial={{ scale: 0, rotate: 90 }}
                animate={{ scale: 1, rotate: 0 }}
                exit={{ scale: 0, rotate: -90 }}
                transition={{ duration: 0.15, ease: "easeOut" }}
              >
                <Button
                  data-coach-id="coach-send-btn"
                  size="icon"
                  className="touch-compact h-9 w-9 sm:h-8 sm:w-8 rounded-full text-white transition-opacity send-btn-glow"
                  style={{ backgroundColor: "var(--em-primary)" }}
                  onClick={handleSend}
                  disabled={disabled || isAnswerSubmitting || configBlocked || (!text.trim() && files.length === 0 && questionSelected.size === 0) || hasUploadingFiles || hasFailedFiles}
                >
                  {isAnswerSubmitting ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <ArrowUp className="h-3.5 w-3.5" />
                  )}
                </Button>
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      </div>
      <UndoPanel open={undoPanelOpen} onClose={() => setUndoPanelOpen(false)} />
    </ChatDropzone>
    </>
  );
}
