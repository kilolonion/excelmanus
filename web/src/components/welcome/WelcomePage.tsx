"use client";

import { useCallback } from "react";
import { motion } from "framer-motion";
import {
  Code2,
  ScanLine,
  TrendingUp,
  TableProperties,
  Paperclip,
  type LucideIcon,
} from "lucide-react";
import { duration } from "@/lib/sidebar-motion";
import { uploadFile } from "@/lib/api";
import type { AttachedFile } from "@/lib/types";

const smoothEase: [number, number, number, number] = [0.4, 0, 0.2, 1];

interface Suggestion {
  text: string;
  icon: LucideIcon;
  sampleFile?: string;
  sampleFileName?: string;
}

const SUGGESTIONS: Suggestion[] = [
  { text: "读取数据并用 Python 做回归分析，结果写回 Excel", icon: Code2, sampleFile: "/samples/广告与销售数据.csv", sampleFileName: "广告与销售数据.csv" },
  { text: "识别截图中的表格，还原数据和样式到 Excel", icon: ScanLine, sampleFile: "/samples/收款收据.jpg", sampleFileName: "收款收据.jpg" },
  { text: "按月份汇总销售额，生成趋势折线图和同比分析", icon: TrendingUp, sampleFile: "/samples/月度销售报表.csv", sampleFileName: "月度销售报表.csv" },
  { text: "跨 Sheet 用 VLOOKUP 关联数据，自动补全缺失列", icon: TableProperties, sampleFile: "/samples/订单数据.csv", sampleFileName: "订单数据.csv" },
];

interface WelcomePageProps {
  onSuggestionClick: (text: string, files?: AttachedFile[]) => void;
}

const containerVariants = {
  hidden: {},
  show: {
    transition: { staggerChildren: 0.08, delayChildren: 0.1 },
  },
};

const fadeUp = {
  hidden: { opacity: 0, y: 16 },
  show: { opacity: 1, y: 0, transition: { duration: duration.normal, ease: smoothEase } },
};

const logoVariant = {
  hidden: { opacity: 0, scale: 0.85 },
  show: { opacity: 1, scale: 1, transition: { duration: duration.slow, ease: smoothEase } },
};

const cardVariants = {
  hidden: { opacity: 0, y: 20, scale: 0.95 },
  show: { opacity: 1, y: 0, scale: 1, transition: { duration: duration.normal, ease: smoothEase } },
};

async function fetchAndUploadSample(
  sampleFile: string,
  sampleFileName: string,
): Promise<AttachedFile | null> {
  try {
    const res = await fetch(sampleFile);
    if (!res.ok) return null;
    const blob = await res.blob();
    const file = new File([blob], sampleFileName, { type: blob.type });
    const id = `sample-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    const result = await uploadFile(file);
    return { id, file, status: "success", uploadResult: result };
  } catch {
    return null;
  }
}

export function WelcomePage({ onSuggestionClick }: WelcomePageProps) {
  const handleClick = useCallback(
    async (suggestion: Suggestion) => {
      if (suggestion.sampleFile && suggestion.sampleFileName) {
        const attached = await fetchAndUploadSample(
          suggestion.sampleFile,
          suggestion.sampleFileName,
        );
        onSuggestionClick(
          suggestion.text,
          attached ? [attached] : undefined,
        );
      } else {
        onSuggestionClick(suggestion.text);
      }
    },
    [onSuggestionClick],
  );

  return (
    <motion.div
      className="flex-1 min-h-0 flex flex-col items-center justify-center px-4 overflow-y-auto"
      variants={containerVariants}
      initial="hidden"
      animate="show"
    >
      {/* Logo */}
      <motion.div className="flex items-center gap-3 mb-3" variants={logoVariant}>
        <img
          src="/logo.svg"
          alt="ExcelManus"
          className="h-12 w-auto"
        />
      </motion.div>

      {/* Greeting */}
      <motion.h1 className="text-xl font-semibold mb-1" variants={fadeUp}>你好！我是你的 Excel 智能助手</motion.h1>
      <motion.p className="text-sm text-muted-foreground mb-8" variants={fadeUp}>上传文件或输入任务，我来帮你处理</motion.p>

      {/* Suggestion cards */}
      <motion.div
        className="grid grid-cols-1 sm:grid-cols-2 gap-3 max-w-lg w-full"
        variants={{ hidden: {}, show: { transition: { staggerChildren: 0.06 } } }}
      >
        {SUGGESTIONS.map((suggestion) => {
          const { text, icon: Icon, sampleFileName } = suggestion;
          return (
            <motion.button
              key={text}
              variants={cardVariants}
              whileHover={{ y: -2, transition: { duration: 0.15 } }}
              whileTap={{ scale: 0.97 }}
              onClick={() => handleClick(suggestion)}
              className="group flex items-center gap-3 rounded-xl border border-border/60 bg-card p-4 text-left text-sm
                hover:border-[var(--em-primary-alpha-20)] hover:bg-[var(--em-primary-alpha-06)]
                hover:shadow-[0_0_12px_var(--em-primary-alpha-10)]
                active:bg-[var(--em-primary-alpha-10)] transition-[border-color,background-color,box-shadow,color] duration-200 cursor-pointer min-h-[44px]"
            >
              <span className="flex-shrink-0 h-8 w-8 rounded-lg bg-[var(--em-primary-alpha-06)] flex items-center justify-center group-hover:bg-[var(--em-primary-alpha-15)] transition-colors">
                <Icon className="h-4 w-4 text-muted-foreground group-hover:text-[var(--em-primary)] transition-colors" />
              </span>
              <span className="flex-1 group-hover:text-foreground transition-colors truncate">{text}</span>
              {sampleFileName && (
                <span className="flex items-center gap-1 text-[10px] text-muted-foreground/60 group-hover:text-muted-foreground transition-colors flex-shrink-0">
                  <Paperclip className="h-3 w-3" />
                </span>
              )}
            </motion.button>
          );
        })}
      </motion.div>
    </motion.div>
  );
}
