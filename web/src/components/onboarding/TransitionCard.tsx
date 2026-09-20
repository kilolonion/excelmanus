"use client";

import { Dialog } from "radix-ui";
import { Compass, ArrowRight } from "lucide-react";
import { useGuideViewport } from "./useTargetRect";

export function TransitionCard({ variant, onContinue, onDecline, onSkip }: {
  variant: "basic-to-advanced" | "advanced-to-settings";
  onContinue: () => void; onDecline: () => void; onSkip: () => void;
}) {
  const viewport = useGuideViewport();
  const width = Math.min(440, viewport.width - 24);
  const nextIsFiles = variant === "basic-to-advanced";
  return (
    <Dialog.Root open onOpenChange={(open) => { if (!open) onSkip(); }}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-[10001] bg-black/30" />
        <Dialog.Content className="em-tour-transition" style={{ left: viewport.left + (viewport.width - width) / 2, top: viewport.top + viewport.height / 2, transform: "translateY(-50%)", width, maxHeight: viewport.height - 24 }}>
          <span className="em-onboarding-completion-icon"><Compass aria-hidden="true" /></span>
          <Dialog.Title>{nextIsFiles ? "接下来，试试文件与表格" : "最后，认识模型与插件"}</Dialog.Title>
          <Dialog.Description>已走完这一节。可以继续体验、跳到下一节，或现在进入工作区。</Dialog.Description>
          <button type="button" className="em-tour-next" onClick={onContinue}>{nextIsFiles ? "体验文件与表格" : "体验模型与插件"}<ArrowRight aria-hidden="true" /></button>
          <button type="button" onClick={onDecline}>{nextIsFiles ? "跳过本节，前往设置引导" : "跳过本节，进入工作区"}</button>
          <button type="button" onClick={onSkip}>结束全部引导</button>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
