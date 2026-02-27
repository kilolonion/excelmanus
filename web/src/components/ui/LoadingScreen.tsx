"use client";

/**
 * 品牌加载屏 — 用于 AppShell 初始化、AuthProvider 身份验证等全屏等待场景。
 *
 * 设计：居中 logo + 呼吸脉冲 + 底部品牌进度条 + 可选状态文字。
 */

interface LoadingScreenProps {
  message?: string;
}

export function LoadingScreen({ message }: LoadingScreenProps) {
  return (
    <div className="h-screen flex flex-col items-center justify-center bg-background relative overflow-hidden">
      {/* 背景微光 */}
      <div className="absolute inset-0 pointer-events-none">
        <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[480px] h-[480px] rounded-full bg-[var(--em-primary)] opacity-[0.03] blur-[100px]" />
      </div>

      {/* Logo + 文字 */}
      <div className="relative flex flex-col items-center gap-5">
        {/* Logo 容器：呼吸动画 */}
        <div className="loading-logo-pulse relative">
          <div className="w-14 h-14 rounded-2xl overflow-hidden shadow-lg shadow-[var(--em-primary-alpha-20)]">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src="/icon-512.png"
              alt="ExcelManus"
              width={56}
              height={56}
              className="w-full h-full object-cover"
            />
          </div>
          {/* 外圈涟漪 */}
          <div className="absolute inset-0 rounded-2xl loading-ring-pulse" />
        </div>

        {/* 品牌名 */}
        <span className="text-sm font-medium tracking-wide text-foreground/70">
          ExcelManus
        </span>

        {/* 可选状态提示 */}
        {message && (
          <span className="text-xs text-muted-foreground animate-pulse">
            {message}
          </span>
        )}
      </div>

      {/* 底部进度条 */}
      <div className="absolute bottom-0 left-0 right-0 h-[2px] bg-[var(--em-primary-alpha-06)]">
        <div className="h-full w-1/3 bg-[var(--em-primary)] loading-progress-bar rounded-full" />
      </div>
    </div>
  );
}
