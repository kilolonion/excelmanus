"use client";

import { useEffect, useState } from "react";
import { AlertTriangle, Settings, KeyRound } from "lucide-react";
import { checkModelPlaceholder } from "@/lib/api";
import type { PlaceholderCheckResult } from "@/lib/api";
import {
  OverlayCard,
  OverlayCardAction,
  OverlayCardBody,
  OverlayCardFooter,
  OverlayCardHeader,
  OverlayCardInset,
} from "@/components/ui/overlay-card";
import { useUIStore } from "@/stores/ui-store";
import { useOnboardingStore } from "@/stores/onboarding-store";

const DISMISS_KEY = "excelmanus_placeholder_alert_dismissed";

export function PlaceholderAlert() {
  const [data, setData] = useState<PlaceholderCheckResult | null>(null);
  const [open, setOpen] = useState(false);
  const openSettings = useUIStore((s) => s.openSettings);
  const setConfigReady = useUIStore((s) => s.setConfigReady);
  const setConfigPlaceholderItems = useUIStore((s) => s.setConfigPlaceholderItems);
  const wizardCompleted = useOnboardingStore((s) => s.wizardCompleted);
  const guideCompleted = useOnboardingStore((s) => s.coachMarksCompleted && s.advancedGuideCompleted && s.settingsGuideCompleted);
  const skippedAt = useOnboardingStore((s) => s.skippedAt);
  const backendConfigured = useOnboardingStore((s) => s.backendConfigured);
  // A deliberate skip must not be replaced by another blocking config dialog.
  // Keep configReady=false so a real task still asks the user to connect a model.
  const canPrompt = wizardCompleted && guideCompleted && !skippedAt;

  useEffect(() => {
    let cancelled = false;
    checkModelPlaceholder()
      .then((result) => {
        if (cancelled) return;
        setData(result);
        if (result?.has_placeholder) {
          setConfigReady(false);
          setConfigPlaceholderItems(result.items);
          if (
            canPrompt &&
            typeof window !== "undefined" &&
            sessionStorage.getItem(DISMISS_KEY) !== "1"
          ) {
            setOpen(true);
          }
        } else {
          setOpen(false);
          setConfigReady(true);
          setConfigPlaceholderItems([]);
        }
      })
      .catch(() => {
        if (cancelled) return;
        setConfigReady(true);
        setConfigPlaceholderItems([]);
      });
    return () => { cancelled = true; };
  }, [setConfigReady, setConfigPlaceholderItems, canPrompt, backendConfigured]);

  const handleDismiss = () => {
    setOpen(false);
    sessionStorage.setItem(DISMISS_KEY, "1");
  };

  const handleGoSettings = () => {
    setOpen(false);
    sessionStorage.setItem(DISMISS_KEY, "1");
    openSettings("model");
  };

  if (!data?.has_placeholder) return null;

  const items = data.items;

  return (
    <OverlayCard open={open && canPrompt} onOpenChange={(v) => !v && handleDismiss()} size="sm" tone="warning">
      <OverlayCardHeader
        icon={<AlertTriangle className="h-5 w-5" />}
        title="模型配置未完成"
        description="以下模型的 API Key 为空或疑似占位符，可能导致对话失败"
        onClose={handleDismiss}
      />

      <OverlayCardBody>
        <OverlayCardInset padded={false}>
          {items.map((item, i) => (
            <div key={i} className="flex items-center gap-3 px-3 py-2.5 border-b border-border/40 last:border-b-0">
              <KeyRound className="h-4 w-4 text-muted-foreground shrink-0" />
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium truncate">{item.name}</p>
                <p className="text-xs text-muted-foreground truncate">
                  {item.model || "未设置模型"}
                  {" · "}
                  {item.field === "api_key" ? "API Key 缺失" : `${item.field} 缺失`}
                </p>
              </div>
            </div>
          ))}
        </OverlayCardInset>
      </OverlayCardBody>

      <OverlayCardFooter>
        <OverlayCardAction action="ghost" onClick={handleDismiss}>
          稍后配置
        </OverlayCardAction>
        <OverlayCardAction action="primary" onClick={handleGoSettings}>
          <Settings className="h-4 w-4" />
          前往设置
        </OverlayCardAction>
      </OverlayCardFooter>
    </OverlayCard>
  );
}
