"use client";

import React, { useState, useCallback, useMemo } from "react";
import {
  MessageCircleQuestion,
  CheckCircle2,
  Loader2,
  User,
  ChevronLeft,
  ChevronRight,
} from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";

interface AskUserCardProps {
  args: Record<string, unknown>;
  status: "running" | "success" | "error" | "pending" | "streaming";
  result?: string;
}

type ParsedQuestion = { header?: string; text?: string; options?: { label: string; description?: string }[] };

/**
 * 将 ask_user 工具调用的「提问」和「用户回答」合并为一张卡片。
 *
 * - 多问题时：分页展示，底部导航栏带前后切换 + 圆点指示器
 * - 单问题时：与原来完全一致，无导航栏
 * - 已回答：显示问题 + 用户回答，紧凑排列
 */
export const AskUserCard = React.memo(function AskUserCard({
  args,
  status,
  result,
}: AskUserCardProps) {
  // 解析问题内容 —— 兼容 questions 数组和 question 单对象两种格式
  const questions = useMemo(() => {
    const qs: ParsedQuestion[] = [];
    const rawQuestions = args.questions;
    const rawQuestion = args.question;
    if (Array.isArray(rawQuestions)) {
      for (const q of rawQuestions) {
        if (q && typeof q === "object") qs.push(q as ParsedQuestion);
      }
    } else if (rawQuestion && typeof rawQuestion === "object") {
      qs.push(rawQuestion as ParsedQuestion);
    }

    // 如果无法解析出问题，回退显示原始 args
    if (qs.length === 0) {
      const header = args.header as string | undefined;
      const text = args.text as string | undefined;
      if (header || text) {
        qs.push({
          header: header || undefined,
          text: text || undefined,
          options: Array.isArray(args.options) ? args.options as { label: string; description?: string }[] : undefined,
        });
      }
    }

    // suggest_mode_switch 特殊格式：{ target_mode, reason }
    if (qs.length === 0 && args.target_mode) {
      const modeLabels: Record<string, string> = { write: "写入", read: "读取", plan: "计划" };
      const targetLabel = modeLabels[args.target_mode as string] || (args.target_mode as string);
      qs.push({
        header: "建议切换模式",
        text: (args.reason as string) || `是否切换到「${targetLabel}」模式？`,
      });
    }
    return qs;
  }, [args]);

  const [page, setPage] = useState(0);
  const [direction, setDirection] = useState(0); // -1 = left, 1 = right

  const hasPagination = questions.length > 1;
  const currentPage = Math.min(page, questions.length - 1);

  const goTo = useCallback((target: number) => {
    setDirection(target > page ? 1 : -1);
    setPage(target);
  }, [page]);

  const goPrev = useCallback(() => {
    if (currentPage > 0) goTo(currentPage - 1);
  }, [currentPage, goTo]);

  const goNext = useCallback(() => {
    if (currentPage < questions.length - 1) goTo(currentPage + 1);
  }, [currentPage, questions.length, goTo]);

  const isWaiting = status === "running" || (status as string) === "streaming";
  const isDone = status === "success";

  const slideVariants = {
    enter: (dir: number) => ({ x: dir > 0 ? 60 : -60, opacity: 0 }),
    center: { x: 0, opacity: 1 },
    exit: (dir: number) => ({ x: dir > 0 ? -60 : 60, opacity: 0 }),
  };

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
            hasPagination ? (
              /* ── 分页模式：单卡片 + 滑动动画 ── */
              <div className="overflow-hidden">
                <AnimatePresence mode="wait" custom={direction} initial={false}>
                  <motion.div
                    key={currentPage}
                    custom={direction}
                    variants={slideVariants}
                    initial="enter"
                    animate="center"
                    exit="exit"
                    transition={{ duration: 0.2, ease: [0.25, 0.1, 0.25, 1] }}
                  >
                    <QuestionContent question={questions[currentPage]} />
                  </motion.div>
                </AnimatePresence>
              </div>
            ) : (
              /* ── 单问题：直接渲染，无动画 ── */
              <QuestionContent question={questions[0]} />
            )
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

      {/* 分页导航栏 */}
      {hasPagination && (
        <div
          className="flex items-center justify-center gap-2.5 px-3 py-1.5 border-t border-[var(--em-primary-alpha-15)]"
          style={{ backgroundColor: "color-mix(in srgb, var(--em-primary) 2%, transparent)" }}
        >
          {/* 上一页按钮 */}
          <button
            type="button"
            onClick={goPrev}
            disabled={currentPage === 0}
            className="flex items-center justify-center w-5 h-5 rounded-full transition-all duration-150 cursor-pointer disabled:cursor-default disabled:opacity-30 hover:enabled:bg-[var(--em-primary-alpha-15)]"
            aria-label="上一个问题"
          >
            <ChevronLeft className="h-3 w-3" style={{ color: "var(--em-primary)" }} />
          </button>

          {/* 圆点指示器 */}
          <div className="flex items-center gap-1.5">
            {questions.map((_, i) => (
              <button
                key={i}
                type="button"
                onClick={() => goTo(i)}
                className="p-0 border-0 bg-transparent cursor-pointer transition-all duration-200"
                aria-label={`第 ${i + 1} 个问题`}
              >
                <span
                  className="block rounded-full transition-all duration-200"
                  style={{
                    width: i === currentPage ? 14 : 6,
                    height: 6,
                    backgroundColor: i === currentPage
                      ? "var(--em-primary)"
                      : "color-mix(in srgb, var(--em-primary) 20%, transparent)",
                    borderRadius: 3,
                  }}
                />
              </button>
            ))}
          </div>

          {/* 页码文字 */}
          <span className="text-[10px] text-muted-foreground tabular-nums select-none min-w-[28px] text-center">
            {currentPage + 1} / {questions.length}
          </span>

          {/* 下一页按钮 */}
          <button
            type="button"
            onClick={goNext}
            disabled={currentPage === questions.length - 1}
            className="flex items-center justify-center w-5 h-5 rounded-full transition-all duration-150 cursor-pointer disabled:cursor-default disabled:opacity-30 hover:enabled:bg-[var(--em-primary-alpha-15)]"
            aria-label="下一个问题"
          >
            <ChevronRight className="h-3 w-3" style={{ color: "var(--em-primary)" }} />
          </button>
        </div>
      )}

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

/** 单个问题的内容渲染（标题 + 描述 + 选项 chips） */
function QuestionContent({ question: q }: { question: ParsedQuestion }) {
  return (
    <div>
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
  );
}
