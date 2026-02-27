"use client";

/**
 * 品牌加载屏 — 用于 AppShell 初始化、AuthProvider 身份验证等全屏等待场景。
 *
 * 设计：居中 logo + 多层光环 + 浮动光点 + 渐变进度条 + 可选状态文字。
 */

interface LoadingScreenProps {
  message?: string;
}

export function LoadingScreen({ message }: LoadingScreenProps) {
  return (
    <div className="h-screen flex flex-col items-center justify-center bg-background relative overflow-hidden select-none">
      {/* ── 背景装饰层 ── */}
      <div className="absolute inset-0 pointer-events-none">
        {/* 主光晕 */}
        <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[600px] h-[600px] rounded-full bg-[var(--em-primary)] opacity-[0.04] blur-[120px] loading-bg-breathe" />
        {/* 次光晕 - 偏移 */}
        <div className="absolute top-[40%] left-[55%] -translate-x-1/2 -translate-y-1/2 w-[300px] h-[300px] rounded-full bg-[var(--em-primary-light)] opacity-[0.03] blur-[80px] loading-bg-breathe-delayed" />
        {/* 浮动光点 */}
        <div className="loading-float-particle absolute top-[30%] left-[20%] w-1 h-1 rounded-full bg-[var(--em-primary)] opacity-20" />
        <div className="loading-float-particle-delayed absolute top-[60%] left-[75%] w-1.5 h-1.5 rounded-full bg-[var(--em-primary-light)] opacity-15" />
        <div className="loading-float-particle-slow absolute top-[45%] left-[85%] w-1 h-1 rounded-full bg-[var(--em-primary)] opacity-10" />
        <div className="loading-float-particle-delayed absolute top-[70%] left-[15%] w-1 h-1 rounded-full bg-[var(--em-primary-light)] opacity-15" />
      </div>

      {/* ── 中心内容 ── */}
      <div className="relative flex flex-col items-center gap-6">
        {/* Logo 容器 + 多层光环 */}
        <div className="relative flex items-center justify-center">
          {/* 最外层光环 */}
          <div className="absolute w-28 h-28 rounded-full border border-[var(--em-primary-alpha-06)] loading-ring-outer" />
          {/* 中层虚线旋转环 */}
          <div className="absolute w-24 h-24 rounded-full border border-dashed border-[var(--em-primary-alpha-15)] loading-ring-spin" />
          {/* 内层光环 */}
          <div className="absolute w-20 h-20 rounded-full border border-[var(--em-primary-alpha-10)] loading-ring-inner" />

          {/* Logo 本体 */}
          <div className="loading-logo-pulse relative z-10">
            <div className="w-14 h-14 rounded-2xl overflow-hidden shadow-lg shadow-[var(--em-primary-alpha-20)] ring-1 ring-[var(--em-primary-alpha-10)]">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src="/icon.png"
                alt="ExcelManus"
                width={56}
                height={56}
                className="w-full h-full object-cover"
              />
            </div>
            {/* 涟漪 */}
            <div className="absolute inset-0 rounded-2xl loading-ring-pulse" />
          </div>
        </div>

        {/* 品牌名 + 渐变闪光 */}
        <div className="flex flex-col items-center gap-2">
          <span className="text-base font-semibold tracking-widest text-foreground/80 loading-text-shimmer">
            ExcelManus
          </span>

          {/* 三点加载指示器 */}
          <div className="flex items-center gap-1.5 mt-1">
            <div className="w-1.5 h-1.5 rounded-full bg-[var(--em-primary)] loading-dot-bounce" style={{ animationDelay: "0ms" }} />
            <div className="w-1.5 h-1.5 rounded-full bg-[var(--em-primary)] loading-dot-bounce" style={{ animationDelay: "160ms" }} />
            <div className="w-1.5 h-1.5 rounded-full bg-[var(--em-primary)] loading-dot-bounce" style={{ animationDelay: "320ms" }} />
          </div>
        </div>

        {/* 可选状态提示 */}
        {message && (
          <span className="text-xs text-muted-foreground/80 loading-fade-in tracking-wide">
            {message}
          </span>
        )}
      </div>

      {/* ── 底部渐变进度条 ── */}
      <div className="absolute bottom-0 left-0 right-0 h-[3px] bg-[var(--em-primary-alpha-06)] overflow-hidden">
        <div className="h-full w-1/4 loading-progress-bar rounded-full bg-gradient-to-r from-transparent via-[var(--em-primary)] to-transparent opacity-80" />
      </div>
    </div>
  );
}
