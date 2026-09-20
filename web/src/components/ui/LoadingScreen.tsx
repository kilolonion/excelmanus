"use client";

import { Lightbulb } from "lucide-react";
import { useEffect, useState } from "react";
import { getSplashFeedback } from "@/components/ui/splash-feedback";
import {
  BrandWordmark,
  LoadingBrandMark,
  LoadingProgressBar,
  LoadingStatusSpinner,
} from "@/components/ui/loading-visual";

interface LoadingScreenProps {
  message?: string;
  error?: string | null;
  desktop?: boolean;
}

export function LoadingScreen({ message, error, desktop = false }: LoadingScreenProps) {
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  useEffect(() => {
    if (error) return;
    const startedAt = Date.now();
    const timer = window.setInterval(() => {
      setElapsedSeconds(Math.floor((Date.now() - startedAt) / 1000));
    }, 1000);
    return () => window.clearInterval(timer);
  }, [error]);
  const status = error ?? message ?? "正在初始化...";
  const feedback = getSplashFeedback(elapsedSeconds, desktop);

  return (
    <div
      className="em-splash"
      data-error={error ? "true" : undefined}
    >
      <div className="em-splash-glow" aria-hidden="true" />

      <header className="em-splash-header">
        <BrandWordmark />
        <span className="em-splash-tag">智能表格助手</span>
      </header>

      <main className="em-splash-main">
        <LoadingBrandMark />

        <h1 className="em-splash-title">
          {error ? "暂时无法进入工作空间" : "正在准备你的工作空间"}
        </h1>
        <p className="em-splash-subtitle">
          让繁琐的表格工作，变得简单。
        </p>

        <div className="em-splash-status" role={error ? "alert" : "status"} aria-live="polite">
          {!error && <LoadingStatusSpinner />}
          <span className="em-splash-status-text">{status}</span>
        </div>
        {!error && <LoadingProgressBar />}
        {!error && <p className="em-splash-elapsed" aria-live="off">{feedback.elapsed}</p>}
        <p className="em-splash-hint" aria-live="polite">
          {error ? "请重新加载页面后再试。" : feedback.hint}
        </p>
        {!desktop && (
          <div className="em-splash-recovery">
            <a href="" onClick={(event) => { event.preventDefault(); window.location.reload(); }}>
              重新加载
            </a>
          </div>
        )}
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
