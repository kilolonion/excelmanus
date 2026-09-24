"use client";

import { ArrowUp, CornerDownRight, ListOrdered, MessageCircleQuestion } from "lucide-react";
import { DropdownMenu, DropdownMenuContent, DropdownMenuRadioGroup, DropdownMenuRadioItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { Button } from "@/components/ui/button";
import { DISPATCH_LABELS, DISPATCH_DESCRIPTIONS } from "@/stores/dispatch-store";
import type { MessageDispatchMode } from "@/lib/types";

export function DispatchModeMenu({ mode, onChange, disabled, answering, onAnswer }: { mode: MessageDispatchMode; onChange: (mode: MessageDispatchMode) => void; disabled?: boolean; answering?: boolean; onAnswer?: () => void }) {
  const Icon = answering ? MessageCircleQuestion : mode === "queue" ? ListOrdered : mode === "interrupt" ? ArrowUp : CornerDownRight;
  const label = answering ? "回答问题" : DISPATCH_LABELS[mode];
  return <DropdownMenu>
    <DropdownMenuTrigger asChild>
      <Button type="button" variant="ghost" size="icon-sm" disabled={disabled} aria-label={`发送方式：${label}`} title={`本条消息：${label}`}><Icon className="size-4" /></Button>
    </DropdownMenuTrigger>
    <DropdownMenuContent align="end" className="w-40">
      <DropdownMenuRadioGroup value={answering ? "answer" : mode} onValueChange={(value) => value === "answer" ? onAnswer?.() : onChange(value as MessageDispatchMode)}>
        {onAnswer && <DropdownMenuRadioItem value="answer">回答问题</DropdownMenuRadioItem>}
        {(["steer", "interrupt", "queue"] as const).map((value) => <DropdownMenuRadioItem key={value} value={value} title={DISPATCH_DESCRIPTIONS[value]}>{DISPATCH_LABELS[value]}</DropdownMenuRadioItem>)}
      </DropdownMenuRadioGroup>
    </DropdownMenuContent>
  </DropdownMenu>;
}
