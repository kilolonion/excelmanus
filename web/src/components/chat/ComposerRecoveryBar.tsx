"use client";

import { Play, XCircle } from "lucide-react";
import { RetryModelPicker } from "@/components/chat/RetryModelPicker";

const HINT_MAX_LEN = 10;

const ACTION =
  "touch-compact inline-flex h-7 w-7 sm:w-auto shrink-0 items-center justify-center gap-1 rounded-full px-0 sm:px-2.5 text-[12px] font-medium";

interface ComposerRecoveryBarProps {
  hint: string;
  onContinue: () => void;
  onRetryWithModel?: (modelName: string) => void;
}

function clipHint(text: string): string {
  const t = text.replace(/\s+/g, " ").trim();
  return t.length > HINT_MAX_LEN ? `${t.slice(0, HINT_MAX_LEN)}…` : t;
}

export function ComposerRecoveryBar({
  hint,
  onContinue,
  onRetryWithModel,
}: ComposerRecoveryBarProps) {
  const fullHint = hint.replace(/\s+/g, " ").trim() || "回复未完成";
  const shortHint = clipHint(fullHint);

  return (
    <div className="mb-2 flex min-w-0 items-center gap-1.5">
      <span
        className="inline-flex h-7 min-w-0 items-center gap-1.5 rounded-full border border-[var(--em-hairline)] bg-background px-2.5 text-[12px] font-medium text-muted-foreground"
        title={fullHint}
      >
        <XCircle className="h-3.5 w-3.5 shrink-0 text-red-500" />
        <span className="truncate">{shortHint}</span>
      </span>
      <button
        type="button"
        onClick={onContinue}
        aria-label="继续执行"
        title="继续执行"
        className={`${ACTION} text-white bg-[var(--em-primary)] hover:opacity-90`}
      >
        <Play className="h-3 w-3 fill-current" />
        <span className="hidden sm:inline">继续</span>
      </button>
      {onRetryWithModel && (
        <RetryModelPicker
          onSelect={onRetryWithModel}
          labelClassName="hidden sm:inline"
          triggerClassName={`${ACTION} border border-[var(--em-hairline)] bg-background text-muted-foreground hover:text-foreground hover:bg-muted/50`}
        />
      )}
    </div>
  );
}
