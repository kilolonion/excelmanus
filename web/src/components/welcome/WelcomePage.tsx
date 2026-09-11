"use client";

import { useCallback, useRef, useState } from "react";
import { motion } from "framer-motion";
import {
  Code2,
  ScanLine,
  TrendingUp,
  TableProperties,
  Paperclip,
  Loader2,
  type LucideIcon,
} from "lucide-react";
import { duration } from "@/lib/sidebar-motion";
import { isImageFile } from "@/components/chat/chat-input-constants";

const smoothEase: [number, number, number, number] = [0.4, 0, 0.2, 1];

interface SampleFileRef {
  path: string;
  name: string;
}

interface Suggestion {
  text: string;
  icon: LucideIcon;
  samples?: SampleFileRef[];
}

const SUGGESTIONS: Suggestion[] = [
  {
    text: "读取数据并用 Python 做回归分析，结果写回 Excel",
    icon: Code2,
    samples: [{ path: "/samples/广告与销售数据.csv", name: "广告与销售数据.csv" }],
  },
  {
    text: "识别截图中的表格，还原数据和样式到 Excel",
    icon: ScanLine,
    samples: [{ path: "/samples/收款收据.jpg", name: "收款收据.jpg" }],
  },
  {
    text: "按区域汇总月度销售额，生成趋势折线图和同比分析",
    icon: TrendingUp,
    samples: [{ path: "/samples/月度销售报表.csv", name: "月度销售报表.csv" }],
  },
  {
    text: "跨 Sheet 用 VLOOKUP 关联订单和产品，补全单价和金额",
    icon: TableProperties,
    samples: [{ path: "/samples/订单与产品.xlsx", name: "订单与产品.xlsx" }],
  },
];

interface WelcomePageProps {
  onSuggestionClick: (text: string, files?: File[]) => void;
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

function mimeForName(name: string): string {
  const ext = name.slice(name.lastIndexOf(".")).toLowerCase();
  switch (ext) {
    case ".csv":
      return "text/csv";
    case ".xlsx":
      return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
    case ".xls":
      return "application/vnd.ms-excel";
    case ".jpg":
    case ".jpeg":
      return "image/jpeg";
    case ".png":
      return "image/png";
    case ".webp":
      return "image/webp";
    default:
      return "application/octet-stream";
  }
}

async function fetchSampleFile(ref: SampleFileRef): Promise<File | null> {
  try {
    const res = await fetch(ref.path);
    if (!res.ok) return null;
    const blob = await res.blob();
    return new File([blob], ref.name, { type: blob.type || mimeForName(ref.name) });
  } catch {
    return null;
  }
}

export function WelcomePage({ onSuggestionClick }: WelcomePageProps) {
  const [loadingKey, setLoadingKey] = useState<string | null>(null);
  const [errorKey, setErrorKey] = useState<string | null>(null);
  const prefetchCache = useRef<Map<string, Promise<File | null>>>(new Map());
  const clickLockRef = useRef(false);

  const ensureSample = useCallback((ref: SampleFileRef) => {
    const existing = prefetchCache.current.get(ref.path);
    if (existing) return existing;
    const task = fetchSampleFile(ref);
    prefetchCache.current.set(ref.path, task);
    return task;
  }, []);

  const prefetchSample = useCallback((suggestion: Suggestion) => {
    if (!suggestion.samples?.length) return;
    for (const s of suggestion.samples) void ensureSample(s);
  }, [ensureSample]);

  const handleClick = useCallback(
    async (suggestion: Suggestion) => {
      if (clickLockRef.current) return;
      clickLockRef.current = true;
      setLoadingKey(suggestion.text);
      setErrorKey(null);

      try {
        if (!suggestion.samples?.length) {
          onSuggestionClick(suggestion.text);
          return;
        }

        let files = (await Promise.all(suggestion.samples.map((s) => ensureSample(s)))).filter(
          (f): f is File => f !== null,
        );

        if (files.length !== suggestion.samples.length) {
          for (const s of suggestion.samples) prefetchCache.current.delete(s.path);
          files = (await Promise.all(suggestion.samples.map((s) => ensureSample(s)))).filter(
            (f): f is File => f !== null,
          );
        }

        if (files.length !== suggestion.samples.length) {
          setErrorKey(suggestion.text);
          return;
        }

        onSuggestionClick(suggestion.text, files);
      } finally {
        setLoadingKey(null);
        clickLockRef.current = false;
      }
    },
    [onSuggestionClick, ensureSample],
  );

  return (
    <motion.div
      className="relative flex-1 min-h-0 flex flex-col items-center px-4 py-6 overflow-y-auto before:content-[''] before:flex-[1_0_0px] after:content-[''] after:flex-[1_0_0px]"
      variants={containerVariants}
      initial="hidden"
      animate="show"
    >
      <div className="absolute inset-0 welcome-bg-grid pointer-events-none" />
      <div className="welcome-orb welcome-orb-1" />
      <div className="welcome-orb welcome-orb-2" />

      <motion.div className="relative flex items-center gap-3 mb-4" variants={logoVariant}>
        <div className="absolute inset-0 -m-4 rounded-full bg-[var(--em-primary-alpha-06)] blur-xl" />
        <img
          src="/logo.svg"
          alt="ExcelManus"
          className="relative h-12 w-auto drop-shadow-sm"
        />
      </motion.div>

      <motion.h1 className="relative text-xl font-semibold mb-1" variants={fadeUp}>你好！我是你的 Excel 智能助手</motion.h1>
      <motion.p className="relative text-sm text-muted-foreground mb-8" variants={fadeUp}>上传文件或输入任务，我来帮你处理</motion.p>

      <motion.div
        className="grid grid-cols-1 sm:grid-cols-2 gap-3 max-w-lg w-full"
        variants={{ hidden: {}, show: { transition: { staggerChildren: 0.06 } } }}
      >
        {SUGGESTIONS.map((suggestion) => {
          const { text, icon: Icon, samples } = suggestion;
          const isThis = loadingKey === text;
          const isBusy = !!loadingKey;
          const hasError = errorKey === text;
          return (
            <motion.button
              key={text}
              type="button"
              variants={cardVariants}
              whileHover={isBusy ? {} : { y: -2, transition: { duration: 0.15 } }}
              whileTap={isBusy ? {} : { scale: 0.97 }}
              onPointerEnter={() => prefetchSample(suggestion)}
              onPointerDown={() => prefetchSample(suggestion)}
              onClick={() => handleClick(suggestion)}
              disabled={isBusy}
              aria-label={`试用示例：${text}`}
              className={`group flex flex-col gap-2 rounded-xl welcome-card-glass p-4 text-left text-sm
                transition-[border-color,background-color,box-shadow,color,opacity] duration-200 min-h-[44px]
                ${isThis ? "opacity-60 cursor-wait" : isBusy ? "opacity-80 cursor-default" : "hover:bg-[var(--em-primary-alpha-06)] active:bg-[var(--em-primary-alpha-10)] cursor-pointer"}
                ${hasError ? "border-[color:var(--destructive)]/40" : ""}`}
            >
              <span className="flex items-center gap-3">
                <span className="flex-shrink-0 h-8 w-8 rounded-lg bg-[var(--em-primary-alpha-06)] flex items-center justify-center group-hover:bg-[var(--em-primary-alpha-15)] transition-colors">
                  {isThis ? (
                    <Loader2 className="h-4 w-4 text-muted-foreground animate-spin" />
                  ) : (
                    <Icon className="h-4 w-4 text-muted-foreground group-hover:text-[var(--em-primary)] transition-colors" />
                  )}
                </span>
                <span className="flex-1 group-hover:text-foreground transition-colors line-clamp-2">{text}</span>
              </span>
              {!!samples?.length && (
                <span className="flex flex-wrap items-center gap-1.5 pl-11">
                  {samples.map((s) =>
                    isImageFile(s.name) ? (
                      <span
                        key={s.name}
                        className="inline-flex items-center gap-1.5 rounded-md bg-muted/50 px-1 py-0.5 pr-1.5 text-[10px] text-muted-foreground group-hover:bg-muted transition-colors"
                      >
                        <img
                          src={s.path}
                          alt=""
                          className="h-7 w-9 rounded object-cover border border-black/5 dark:border-white/10"
                        />
                        <span className="max-w-[9rem] truncate">{s.name}</span>
                      </span>
                    ) : (
                      <span
                        key={s.name}
                        className="inline-flex items-center gap-1 rounded-md bg-muted/50 px-1.5 py-0.5 text-[10px] text-muted-foreground group-hover:bg-muted transition-colors"
                      >
                        <Paperclip className="h-2.5 w-2.5" />
                        {s.name}
                      </span>
                    ),
                  )}
                </span>
              )}
              {hasError && (
                <span className="pl-11 text-[11px] text-destructive">示例文件加载失败，请再试一次</span>
              )}
            </motion.button>
          );
        })}
      </motion.div>
    </motion.div>
  );
}
