"use client";

import { ArrowRight, Crown } from "lucide-react";
import { useUIStore } from "@/stores/ui-store";
import { requestModelSubTab } from "./model-subtab";

export function SubscriptionSettingsEntry({ onNavigate }: { onNavigate?: () => void }) {
  return <button type="button" className="flex min-h-11 w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-xs text-primary transition-colors hover:bg-primary/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" onClick={() => {
    onNavigate?.();
    requestModelSubTab("subscription");
    useUIStore.getState().openSettings("model");
  }}>
    <Crown className="size-4 shrink-0" />
    <span className="flex-1">连接订阅账号<span className="ml-2 text-[10px] text-muted-foreground">无需 API Key</span></span>
    <ArrowRight className="size-3.5" />
  </button>;
}
