"use client";

import "./globals.css";
import { AppErrorFallback } from "@/components/ui/AppErrorFallback";

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <html lang="zh-CN" suppressHydrationWarning>
      <body suppressHydrationWarning>
        <AppErrorFallback error={error} onReset={reset} />
      </body>
    </html>
  );
}
