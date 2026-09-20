"use client";

import { useEffect } from "react";
import { Button } from "@/components/ui/button";

interface AppErrorFallbackProps {
  error: Error & { digest?: string };
  onReset?: () => void;
  title?: string;
}

export function AppErrorFallback({ error, onReset, title = "界面出现异常" }: AppErrorFallbackProps) {
  useEffect(() => {
    console.error("[excelmanus] 界面异常", error);
  }, [error]);

  const copyErrorInfo = () => {
    const text = [
      `message: ${error.message}`,
      error.digest ? `digest: ${error.digest}` : null,
      error.stack ? `stack:\n${error.stack}` : null,
    ].filter(Boolean).join("\n");
    void navigator.clipboard?.writeText(text).catch(() => {});
  };

  return (
    <div className="flex min-h-screen flex-col items-center justify-center bg-background px-6 text-foreground">
      <div className="w-full max-w-2xl rounded-xl border border-border bg-card p-6 shadow-sm">
        <h1 className="text-lg font-semibold">{title}</h1>
        <p className="mt-2 text-sm text-muted-foreground">
          已阻止界面崩溃扩散，请重试或重新加载。若反复出现，请把下方错误信息反馈给我们。
        </p>
        <pre className="mt-4 max-h-40 overflow-auto whitespace-pre-wrap break-all rounded-md border border-border bg-muted/40 p-3 font-mono text-xs">
          {error.message}
        </pre>
        {error.digest && (
          <p className="mt-2 font-mono text-xs text-muted-foreground">错误编号: {error.digest}</p>
        )}
        {error.stack && (
          <details className="mt-3 rounded-md border border-border p-3 text-xs text-muted-foreground">
            <summary className="cursor-pointer select-none">错误堆栈</summary>
            <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap break-all font-mono">
              {error.stack}
            </pre>
          </details>
        )}
        <div className="mt-5 flex flex-wrap gap-2">
          {onReset && <Button onClick={onReset}>重试</Button>}
          <Button variant="outline" onClick={() => window.location.reload()}>
            重新加载
          </Button>
          <Button variant="ghost" onClick={copyErrorInfo}>
            复制错误信息
          </Button>
        </div>
      </div>
    </div>
  );
}
