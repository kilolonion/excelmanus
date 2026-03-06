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
import { uploadFile } from "@/lib/api";
import type { AttachedFile } from "@/lib/types";

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
  { text: "读取数据并用 Python 做回归分析，结果写回 Excel", icon: Code2, samples: [{ path: "/samples/广告与销售数据.csv", name: "广告与销售数据.csv" }] },
  { text: "识别截图中的表格，还原数据和样式到 Excel", icon: ScanLine, samples: [{ path: "/samples/收款收据.jpg", name: "收款收据.jpg" }] },
  { text: "按月份汇总销售额，生成趋势折线图和同比分析", icon: TrendingUp, samples: [{ path: "/samples/月度销售报表.csv", name: "月度销售报表.csv" }] },
  { text: "跨 Sheet 用 VLOOKUP 关联数据，自动补全缺失列", icon: TableProperties, samples: [
    { path: "/samples/订单数据.csv", name: "订单数据.csv" },
    { path: "/samples/产品目录.csv", name: "产品目录.csv" },
  ] },
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

const _IMAGE_EXTS = new Set([".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"]);
function _isImageName(name: string): boolean {
  const dot = name.lastIndexOf(".");
  return dot >= 0 && _IMAGE_EXTS.has(name.slice(dot).toLowerCase());
}

function _blobToBase64(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = reader.result as string;
      const idx = result.indexOf(",");
      resolve(idx >= 0 ? result.slice(idx + 1) : result);
    };
    reader.onerror = reject;
    reader.readAsDataURL(blob);
  });
}

async function fetchImageSampleFast(
  sampleFile: string,
  sampleFileName: string,
): Promise<AttachedFile | null> {
  try {
    const res = await fetch(sampleFile);
    if (!res.ok) return null;
    const blob = await res.blob();
    const file = new File([blob], sampleFileName, { type: blob.type || "image/jpeg" });
    const id = `sample-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    const cachedBase64 = await _blobToBase64(blob);
    // 图片卡片走“极速发送”路径：不阻塞等待 uploadFile，
    // sendMessage 会直接使用 cachedBase64 发起多模态请求。
    return { id, file, status: "success", cachedBase64 };
  } catch {
    return null;
  }
}

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
  const [loadingKey, setLoadingKey] = useState<string | null>(null);
  // sampleFile → Promise<AttachedFile | null>
  const prefetchCache = useRef<Map<string, Promise<AttachedFile | null>>>(new Map());
  // 防止移动端快速连点导致重复触发（state 更新前的竞态窗口）
  const clickLockRef = useRef(false);

  const ensureSingleTask = useCallback((ref: SampleFileRef) => {
    const existing = prefetchCache.current.get(ref.path);
    if (existing) return existing;
    const task = _isImageName(ref.name)
      ? fetchImageSampleFast(ref.path, ref.name)
      : fetchAndUploadSample(ref.path, ref.name);
    prefetchCache.current.set(ref.path, task);
    return task;
  }, []);

  const prefetchSample = useCallback((suggestion: Suggestion) => {
    if (!suggestion.samples?.length) return;
    for (const s of suggestion.samples) void ensureSingleTask(s);
  }, [ensureSingleTask]);

  const handleClick = useCallback(
    async (suggestion: Suggestion) => {
      if (clickLockRef.current) return;
      clickLockRef.current = true;
      setLoadingKey(suggestion.text);

      try {
        if (suggestion.samples?.length) {
          let files = (await Promise.all(suggestion.samples.map(s => ensureSingleTask(s)))).filter((r): r is AttachedFile => r !== null);

          // 预热失败时点击重试一次，避免因缓存了 null 导致一直不带附件。
          if (files.length === 0) {
            for (const s of suggestion.samples) prefetchCache.current.delete(s.path);
            files = (await Promise.all(suggestion.samples.map(s => ensureSingleTask(s)))).filter((r): r is AttachedFile => r !== null);
          }

          onSuggestionClick(suggestion.text, files.length ? files : undefined);
        } else {
          onSuggestionClick(suggestion.text);
        }
      } finally {
        setLoadingKey(null);
        clickLockRef.current = false;
      }
    },
    [onSuggestionClick, ensureSingleTask],
  );

  return (
    <motion.div
      className="relative flex-1 min-h-0 flex flex-col items-center px-4 py-6 overflow-y-auto before:content-[''] before:flex-[1_0_0px] after:content-[''] after:flex-[1_0_0px]"
      variants={containerVariants}
      initial="hidden"
      animate="show"
    >
      {/* Decorative background */}
      <div className="absolute inset-0 welcome-bg-grid pointer-events-none" />
      <div className="welcome-orb welcome-orb-1" />
      <div className="welcome-orb welcome-orb-2" />

      {/* Logo */}
      <motion.div className="relative flex items-center gap-3 mb-4" variants={logoVariant}>
        <div className="absolute inset-0 -m-4 rounded-full bg-[var(--em-primary-alpha-06)] blur-xl" />
        <img
          src="/logo.svg"
          alt="ExcelManus"
          className="relative h-12 w-auto drop-shadow-sm"
        />
      </motion.div>

      {/* Greeting */}
      <motion.h1 className="relative text-xl font-semibold mb-1" variants={fadeUp}>你好！我是你的 Excel 智能助手</motion.h1>
      <motion.p className="relative text-sm text-muted-foreground mb-8" variants={fadeUp}>上传文件或输入任务，我来帮你处理</motion.p>

      {/* Suggestion cards */}
      <motion.div
        className="grid grid-cols-1 sm:grid-cols-2 gap-3 max-w-lg w-full"
        variants={{ hidden: {}, show: { transition: { staggerChildren: 0.06 } } }}
      >
        {SUGGESTIONS.map((suggestion) => {
          const { text, icon: Icon, samples } = suggestion;
          const isThis = loadingKey === text;
          const isBusy = !!loadingKey;
          return (
            <motion.button
              key={text}
              variants={cardVariants}
              whileHover={isBusy ? {} : { y: -2, transition: { duration: 0.15 } }}
              whileTap={isBusy ? {} : { scale: 0.97 }}
              onPointerEnter={() => prefetchSample(suggestion)}
              onPointerDown={() => prefetchSample(suggestion)}
              onClick={() => handleClick(suggestion)}
              disabled={isBusy}
              className={`group flex flex-col gap-2 rounded-xl welcome-card-glass p-4 text-left text-sm
                transition-[border-color,background-color,box-shadow,color,opacity] duration-200 min-h-[44px]
                ${isThis ? "opacity-60 cursor-wait" : isBusy ? "opacity-80 cursor-default" : "hover:bg-[var(--em-primary-alpha-06)] active:bg-[var(--em-primary-alpha-10)] cursor-pointer"}`}
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
                <span className="relative overflow-hidden -mx-1">
                  <span
                    className="flex items-center gap-1.5 overflow-x-auto scrollbar-none whitespace-nowrap pl-1 sm:pl-11 pr-6"
                    style={{ WebkitOverflowScrolling: "touch", touchAction: "pan-x" }}
                    onClick={(e) => e.stopPropagation()}
                    onPointerDown={(e) => e.stopPropagation()}
                  >
                    {samples.map((s) => (
                      <span
                        key={s.name}
                        className="inline-flex items-center gap-1 rounded-md bg-muted/50 px-1.5 py-0.5 text-[10px] text-muted-foreground group-hover:bg-muted group-hover:text-muted-foreground/80 transition-colors flex-shrink-0"
                      >
                        <Paperclip className="h-2.5 w-2.5" />
                        {s.name}
                      </span>
                    ))}
                  </span>
                  <span className="pointer-events-none absolute inset-y-0 right-0 w-6 bg-gradient-to-l from-[var(--welcome-card-bg,var(--card))] to-transparent" />
                </span>
              )}
            </motion.button>
          );
        })}
      </motion.div>
    </motion.div>
  );
}
