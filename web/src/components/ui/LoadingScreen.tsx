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
      className="em-splash"
    >
      <div className="em-splash-glow" aria-hidden="true" />

      <header className="em-splash-header">
        <BrandWordmark />
        <span className="em-splash-tag">智能表格助手</span>
      </header>

      <main className="em-splash-main">
        <LoadingBrandMark />

        <h1 className="em-splash-title">
          正在准备你的工作空间
        </h1>
        <p className="em-splash-subtitle">
          让繁琐的表格工作，变得简单。
        </p>

        <div className="em-splash-status">
          <LoadingStatusSpinner />
          <span>{status}</span>
        </div>
        <LoadingProgressBar />
      </main>

      <footer className="em-splash-footer">
        <p className="em-splash-tip">
          <Lightbulb strokeWidth={1.75} aria-hidden="true" />
          <span>小提示：用一句话描述需求，即可开始处理表格。</span>
        </p>
      </footer>
    </div>
  );
}
