"use client";

import { MessageCircleQuestion, X, Check } from "lucide-react";
import { useChatStore } from "@/stores/chat-store";
import { useSessionStore } from "@/stores/session-store";
import { abortChat } from "@/lib/api";
import { motion } from "framer-motion";
import type { Question } from "@/lib/types";

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

  const visibleOptions = question.options.filter((o) => !OTHER_LABELS.has(o.label));

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
            <p className="text-[13px] font-semibold text-foreground leading-snug">
              {question.header || "请回答问题"}
            </p>
            {question.text && (
              <p className="text-xs text-muted-foreground mt-0.5 leading-relaxed">
                {question.text}
              </p>
            )}
          </div>
          <button
            onClick={() => {
              const sid = useSessionStore.getState().activeSessionId;
              setPendingQuestion(null);
              if (sid) abortChat(sid).catch(() => {});
            }}
            className="shrink-0 text-muted-foreground/50 hover:text-foreground transition-colors p-1 rounded-lg hover:bg-muted/60"
            title="取消并终止任务"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </div>

        {/* Option chips */}
        {visibleOptions.length > 0 && (
          <div className="flex flex-wrap gap-1.5 mb-1">
            {visibleOptions.map((opt) => {
              const isSelected = selected.has(opt.label);
              return (
                <button
                  key={opt.label}
                  onClick={() => onToggle(opt.label)}
                  className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium transition-all duration-150 border ${
                    isSelected
                      ? "border-[var(--em-primary)] text-[var(--em-primary)] shadow-sm"
                      : "border-border/60 text-muted-foreground hover:border-border hover:text-foreground hover:bg-muted/40"
                  }`}
                  style={isSelected ? {
                    backgroundColor: "color-mix(in srgb, var(--em-primary) 8%, transparent)",
                  } : undefined}
                >
                  {isSelected && <Check className="h-3 w-3 shrink-0" />}
                  <span>{opt.label}</span>
                  {opt.description && (
                    <span className={`text-[11px] hidden sm:inline ${
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
