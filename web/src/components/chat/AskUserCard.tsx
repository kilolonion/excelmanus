"use client";

import React from "react";
import {
  MessageCircleQuestion,
  CheckCircle2,
  Loader2,
  User,
} from "lucide-react";

interface AskUserCardProps {
  args: Record<string, unknown>;
  status: "running" | "success" | "error" | "pending" | "streaming";
  result?: string;
}

/**
 * 将 ask_user 工具调用的「提问」和「用户回答」合并为一张卡片。
 *
 * - 等待中：显示问题内容 + 旋转加载指示
 * - 已回答：显示问题 + 用户回答，紧凑排列
 */
export const AskUserCard = React.memo(function AskUserCard({
  args,
  status,
  result,
}: AskUserCardProps) {
  // 解析问题内容 —— 兼容 questions 数组和 question 单对象两种格式
  const questions: { header?: string; text?: string; options?: { label: string; description?: string }[] }[] = [];
  const rawQuestions = args.questions;
  const rawQuestion = args.question;
  if (Array.isArray(rawQuestions)) {
    for (const q of rawQuestions) {
      if (q && typeof q === "object") questions.push(q as typeof questions[0]);
    }
  } else if (rawQuestion && typeof rawQuestion === "object") {
    questions.push(rawQuestion as typeof questions[0]);
  }

  // 如果无法解析出问题，回退显示原始 args
  if (questions.length === 0) {
    // 尝试从扁平参数提取
    const header = args.header as string | undefined;
    const text = args.text as string | undefined;
    if (header || text) {
      questions.push({
        header: header || undefined,
        text: text || undefined,
        options: Array.isArray(args.options) ? args.options as { label: string; description?: string }[] : undefined,
      });
    }
  }

  // suggest_mode_switch 特殊格式：{ target_mode, reason }
  if (questions.length === 0 && args.target_mode) {
    const modeLabels: Record<string, string> = { write: "写入", read: "读取", plan: "计划" };
    const targetLabel = modeLabels[args.target_mode as string] || (args.target_mode as string);
    questions.push({
      header: "建议切换模式",
      text: (args.reason as string) || `是否切换到「${targetLabel}」模式？`,
    });
  }

  const isWaiting = status === "running" || (status as string) === "streaming";
  const isDone = status === "success";

  return (
    <div className="my-1.5 rounded-lg border border-[var(--em-primary-alpha-15)] overflow-hidden">
      {/* 问题区域 */}
      <div
        className="flex items-start gap-2.5 px-3 py-2.5"
        style={{ backgroundColor: "color-mix(in srgb, var(--em-primary) 4%, transparent)" }}
      >
        <div
          className="flex items-center justify-center w-5 h-5 rounded-full shrink-0 mt-0.5"
          style={{ backgroundColor: "color-mix(in srgb, var(--em-primary) 12%, transparent)" }}
        >
          <MessageCircleQuestion className="h-3 w-3" style={{ color: "var(--em-primary)" }} />
        </div>
        <div className="flex-1 min-w-0">
          {questions.length > 0 ? (
            questions.map((q, i) => (
              <div key={i} className={i > 0 ? "mt-2 pt-2 border-t border-border/20" : ""}>
                {q.header && (
                  <p className="text-[12px] font-semibold text-foreground leading-snug">
                    {q.header}
                  </p>
                )}
                {q.text && (
                  <p className="text-[12px] text-muted-foreground leading-relaxed mt-0.5 whitespace-pre-wrap">
                    {q.text}
                  </p>
                )}
                {q.options && q.options.length > 0 && (
                  <div className="flex flex-wrap gap-1 mt-1.5">
                    {q.options.map((opt) => (
                      <span
                        key={opt.label}
                        className="inline-flex items-center rounded-full px-2 py-0.5 text-[11px] border border-border/50 text-muted-foreground bg-background/60"
                      >
                        {opt.label}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            ))
          ) : (
            <p className="text-[12px] text-muted-foreground">
              {(args.text as string) || "等待用户回答…"}
            </p>
          )}
        </div>
        {/* 状态指示 */}
        <div className="shrink-0 mt-0.5">
          {isWaiting && (
            <Loader2 className="h-3.5 w-3.5 animate-spin" style={{ color: "var(--em-cyan)" }} />
          )}
          {isDone && (
            <CheckCircle2 className="h-3.5 w-3.5" style={{ color: "var(--em-primary)" }} />
          )}
        </div>
      </div>

      {/* 用户回答区域 —— 仅在有结果时显示 */}
      {result && (
        <div className="flex items-start gap-2.5 px-3 py-2 border-t border-[var(--em-primary-alpha-15)] bg-background">
          <div className="flex items-center justify-center w-5 h-5 rounded-full shrink-0 mt-0.5 bg-muted/60">
            <User className="h-3 w-3 text-muted-foreground" />
          </div>
          <p className="flex-1 min-w-0 text-[12px] text-foreground leading-relaxed whitespace-pre-wrap break-words">
            {result}
          </p>
        </div>
      )}
    </div>
  );
});
