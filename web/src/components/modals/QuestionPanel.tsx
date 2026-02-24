"use client";

import { useState } from "react";
import { HelpCircle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useChatStore } from "@/stores/chat-store";
import { sendContinuation } from "@/lib/chat-actions";
import { motion, AnimatePresence } from "framer-motion";

export function QuestionPanel() {
  const pendingQuestion = useChatStore((s) => s.pendingQuestion);
  const setPendingQuestion = useChatStore((s) => s.setPendingQuestion);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [freeText, setFreeText] = useState("");

  if (!pendingQuestion) return null;

  const hasOptions = pendingQuestion.options.length > 0;

  const handleSubmit = () => {
    let answer: string;
    if (hasOptions) {
      answer = Array.from(selected).join(", ");
    } else {
      answer = freeText;
    }
    if (!answer.trim()) return;
    setPendingQuestion(null);
    sendContinuation(answer);
    setSelected(new Set());
    setFreeText("");
  };

  const toggleOption = (label: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (pendingQuestion.multiSelect) {
        if (next.has(label)) next.delete(label);
        else next.add(label);
      } else {
        next.clear();
        next.add(label);
      }
      return next;
    });
  };

  return (
    <AnimatePresence>
      <motion.div
        initial={{ y: 20, opacity: 0 }}
        animate={{ y: 0, opacity: 1 }}
        exit={{ y: 20, opacity: 0 }}
        className="bg-card border border-border rounded-xl shadow-sm p-4 mb-3"
      >
        <div className="flex items-center gap-2 mb-2">
          <HelpCircle className="h-4 w-4" style={{ color: "var(--em-cyan)" }} />
          <span className="font-semibold text-sm">
            {pendingQuestion.header || "请回答问题"}
          </span>
        </div>

        {pendingQuestion.text && (
          <p className="text-sm text-muted-foreground mb-3">{pendingQuestion.text}</p>
        )}

        {hasOptions ? (
          <div className="space-y-1.5 mb-3">
            {pendingQuestion.options.map((opt) => (
              <button
                key={opt.label}
                onClick={() => toggleOption(opt.label)}
                className={`w-full text-left px-3 py-3 rounded-lg border text-sm transition-colors min-h-[44px] ${
                  selected.has(opt.label)
                    ? "border-[var(--em-primary)] bg-[var(--em-primary)]/5"
                    : "border-border hover:bg-muted/30 active:bg-muted/50"
                }`}
              >
                <span className="font-medium">{opt.label}</span>
                {opt.description && (
                  <span className="text-muted-foreground ml-2 text-xs">
                    {opt.description}
                  </span>
                )}
              </button>
            ))}
          </div>
        ) : (
          <Input
            value={freeText}
            onChange={(e) => setFreeText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") handleSubmit();
            }}
            placeholder="输入回答..."
            className="mb-3"
          />
        )}

        <Button
          size="sm"
          className="w-full text-white"
          style={{ backgroundColor: "var(--em-primary)" }}
          onClick={handleSubmit}
          disabled={hasOptions ? selected.size === 0 : !freeText.trim()}
        >
          提交回答
        </Button>
      </motion.div>
    </AnimatePresence>
  );
}
