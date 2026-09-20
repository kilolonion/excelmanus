"use client";

import { AppErrorFallback } from "@/components/ui/AppErrorFallback";

export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return <AppErrorFallback error={error} onReset={reset} />;
}
