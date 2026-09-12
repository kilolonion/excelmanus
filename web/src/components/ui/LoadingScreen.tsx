"use client";

import { Lightbulb } from "lucide-react";
import {
  BrandWordmark,
  LoadingBrandMark,
  LoadingProgressBar,
  LoadingStatusSpinner,
} from "@/components/ui/loading-visual";

interface LoadingScreenProps {
  message?: string;
}

export function LoadingScreen({ message }: LoadingScreenProps) {
  const status = message ?? "正在初始化...";

  return (
    <div
      role="status"
      aria-live="polite"
      aria-busy="true"
      aria-label={status}
      className="relative flex h-dvh min-h-[100dvh] flex-col overflow-hidden bg-background select-none"
    >
      <div className="pointer-events-none absolute inset-0">
        <div className="absolute left-1/2 top-[42%] h-[min(72vw,520px)] w-[min(72vw,520px)] -translate-x-1/2 -translate-y-1/2 rounded-full bg-[var(--em-primary)] opacity-[0.045] blur-[90px] md:top-1/2 md:h-[560px] md:w-[560px]" />
      </div>

      <header className="relative z-10 flex flex-nowrap items-center justify-between gap-3 px-4 pt-[max(1.25rem,env(safe-area-inset-top))] md:px-10 md:pt-8">
        <BrandWordmark />
        <span className="hidden whitespace-nowrap text-[13px] tracking-wide text-[var(--em-text-secondary)] md:inline">
          智能表格助手
        </span>
      </header>

      <main className="relative z-10 flex flex-1 flex-col items-center justify-center px-5 text-center md:px-16 lg:px-24">
        <LoadingBrandMark />

        <h1 className="mt-7 whitespace-nowrap text-[1.5rem] font-semibold leading-none tracking-tight text-[var(--em-text)] md:mt-9 md:text-[2rem]">
          正在准备你的工作空间
        </h1>
        <p className="mt-2.5 whitespace-nowrap text-[13px] leading-none text-[var(--em-text-secondary)] md:text-[15px]">
          让繁琐的表格工作，变得简单。
        </p>

        <div className="mt-8 flex flex-nowrap items-center gap-2 whitespace-nowrap text-[13px] text-[var(--em-text-secondary)] md:mt-9">
          <LoadingStatusSpinner />
          <span>{status}</span>
        </div>
        <LoadingProgressBar className="mt-3.5 w-[min(70vw,220px)] md:w-[168px]" />
      </main>

      <footer className="relative z-10 flex justify-center overflow-hidden px-3 pb-[max(1.75rem,env(safe-area-inset-bottom))] md:px-10 md:pb-10">
        <p className="flex max-w-full items-center gap-1.5 text-[11px] leading-none text-[var(--em-text-secondary)] md:gap-2 md:text-[13px]">
          <Lightbulb
            className="size-3.5 shrink-0 text-[var(--em-primary)] md:size-4"
            strokeWidth={1.75}
            aria-hidden="true"
          />
          <span className="whitespace-nowrap">小提示：用一句话描述需求，即可开始处理表格。</span>
        </p>
      </footer>
    </div>
  );
}
