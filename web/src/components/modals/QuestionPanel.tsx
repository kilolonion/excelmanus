"use client";

import { MessageCircleQuestion, X, Check, SkipForward } from "lucide-react";
import { useChatStore } from "@/stores/chat-store";
import { useSessionStore } from "@/stores/session-store";
import { abortChat, answerQuestion } from "@/lib/api";
import { resumeAfterInteraction } from "@/lib/chat-actions";
import { motion } from "framer-motion";
import type { Question } from "@/lib/types";
import { useEffect, useRef } from "react";
import { openWorkbookQuestion } from "@/lib/workbook-interaction";
import { useWorkbookInteractionStore } from "@/stores/workbook-interaction-store";
import { useExcelStore } from "@/stores/excel-store";

/** Options with these labels are treated as "free-text fallback" and hidden from chips. */
const OTHER_LABELS = new Set(["Other", "其他", "other"]);

interface InlineQuestionBannerProps {
  question: Question;
  selected: Set<string>;
  onToggle: (label: string) => void;
}

/**
 * Inline question banner rendered inside ChatInput.
 * Shows question header/text + horizontal option chips.
 * "Other/其他" options are filtered out — the user types directly in the textarea.
 */
export function InlineQuestionBanner({ question, selected, onToggle }: InlineQuestionBannerProps) {
  const setPendingQuestion = useChatStore((s) => s.setPendingQuestion);
  const sessionId = useSessionStore((s) => s.activeSessionId);
  const lastOpened = useRef("");
  useEffect(() => {
    if (question.selection && question.autoOpen !== false && sessionId
      && useExcelStore.getState().autoOpenSuppressedSessionId !== sessionId
      && (!question.sessionId || question.sessionId === sessionId)) {
      const key = `${sessionId}:${question.id}`;
      if (lastOpened.current === key) return;
      const current = useWorkbookInteractionStore.getState().request;
      if (current?.questionId !== question.id || current.sessionId !== sessionId) {
        if (openWorkbookQuestion(question.id, question.selection, sessionId)) lastOpened.current = key;
      } else {
        lastOpened.current = key;
      }
    }
  }, [question.id, question.selection, question.sessionId, question.autoOpen, sessionId]);

  const visibleOptions = question.options.filter((o) => !OTHER_LABELS.has(o.label) && !(question.selection && o.label === "补充说明"));

  return (
    <motion.div
      initial={{ opacity: 0, height: 0 }}
      animate={{ opacity: 1, height: "auto" }}
      exit={{ opacity: 0, height: 0 }}
      transition={{ duration: 0.2, ease: "easeOut" }}
      className="overflow-hidden"
    >
      <div className="mx-3 mt-2.5 mb-1">
        {/* Header row */}
        <div className="flex items-start gap-2.5 mb-2">
          <div className="flex items-center justify-center w-6 h-6 rounded-full shrink-0 mt-0.5"
            style={{ backgroundColor: "color-mix(in srgb, var(--em-primary) 12%, transparent)" }}
          >
            <MessageCircleQuestion className="h-3.5 w-3.5" style={{ color: "var(--em-primary)" }} />
          </div>
          <div className="flex-1 min-w-0">
            <p
              className="text-[13px] font-semibold text-foreground leading-snug truncate"
              title={question.header || "请回答问题"}
            >
              {question.header || "请回答问题"}
            </p>
            {question.text && (
              <p className="text-xs text-muted-foreground mt-0.5 leading-relaxed max-h-24 overflow-y-auto">
                {question.text}
              </p>
            )}
          </div>
          <div className="flex items-center gap-0.5 shrink-0">
            {!question.selection && <button
              onClick={() => {
                const sid = useSessionStore.getState().activeSessionId;
                const qid = question.id;
                setPendingQuestion(null);
                if (sid && qid) {
                  answerQuestion(sid, qid, "[用户选择跳过此问题，请自行判断并继续执行]")
                    .then((response) => resumeAfterInteraction(sid, response)).catch(() => {});
                }
              }}
              className="text-muted-foreground/50 hover:text-foreground transition-colors px-1.5 py-1 rounded-lg hover:bg-muted/60 text-[11px] font-medium"
              title="跳过此问题"
            >
              <SkipForward className="h-3.5 w-3.5" />
            </button>}
            <button
              onClick={() => {
                const sid = useSessionStore.getState().activeSessionId;
                setPendingQuestion(null);
                if (sid) abortChat(sid).catch(() => {});
              }}
              className="text-muted-foreground/50 hover:text-foreground transition-colors p-1 rounded-lg hover:bg-muted/60"
              title="取消并终止任务"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
        </div>

        {/* Option chips */}
        {question.selection && <button type="button" onClick={() => sessionId && openWorkbookQuestion(question.id, question.selection!, sessionId)}
          className="mb-2 text-xs underline underline-offset-4" style={{ color: "var(--em-primary)" }}>
          打开表格选择区域 · {question.selection.sheet}
        </button>}
        {visibleOptions.length > 0 && (
          <div className="flex flex-wrap gap-1.5 mb-1">
            {visibleOptions.map((opt) => {
              const isSelected = selected.has(opt.label);
              return (
                <button
                  key={opt.label}
                  onClick={() => onToggle(opt.label)}
                  title={opt.description ? `${opt.label}：${opt.description}` : opt.label}
                  className={`inline-flex max-w-full items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium transition-all duration-150 border ${
                    isSelected
                      ? "border-[var(--em-primary)] text-[var(--em-primary)] shadow-sm"
                      : "border-border/60 text-muted-foreground hover:border-border hover:text-foreground hover:bg-muted/40"
                  }`}
                  style={isSelected ? {
                    backgroundColor: "color-mix(in srgb, var(--em-primary) 8%, transparent)",
                  } : undefined}
                >
                  {isSelected && <Check className="h-3 w-3 shrink-0" />}
                  <span className="min-w-0 truncate">{opt.label}</span>
                  {opt.description && (
                    <span className={`min-w-0 max-w-40 truncate text-[11px] hidden sm:inline ${
                      isSelected ? "text-[var(--em-primary)]/60" : "text-muted-foreground/60"
                    }`}>
                      {opt.description}
                    </span>
                  )}
                </button>
              );
            })}
          </div>
        )}
      </div>

      {/* Subtle separator */}
      <div className="h-px mx-3" style={{ backgroundColor: "color-mix(in srgb, var(--em-primary) 10%, transparent)" }} />
    </motion.div>
  );
}
