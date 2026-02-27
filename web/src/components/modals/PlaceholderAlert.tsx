"use client";

import { useEffect, useState } from "react";
import { AlertTriangle, Settings, KeyRound } from "lucide-react";
import { checkModelPlaceholder } from "@/lib/api";
import type { PlaceholderCheckResult } from "@/lib/api";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { useUIStore } from "@/stores/ui-store";

const DISMISS_KEY = "excelmanus_placeholder_alert_dismissed";

export function PlaceholderAlert() {
  const [data, setData] = useState<PlaceholderCheckResult | null>(null);
  const [open, setOpen] = useState(false);
  const openSettings = useUIStore((s) => s.openSettings);
  const setConfigReady = useUIStore((s) => s.setConfigReady);
  const setConfigPlaceholderItems = useUIStore((s) => s.setConfigPlaceholderItems);

  useEffect(() => {
    checkModelPlaceholder()
      .then((result) => {
        if (result?.has_placeholder) {
          setData(result);
          setConfigReady(false);
          setConfigPlaceholderItems(result.items);
          if (typeof window !== "undefined" && sessionStorage.getItem(DISMISS_KEY) !== "1") {
            setOpen(true);
          }
        } else {
          setConfigReady(true);
          setConfigPlaceholderItems([]);
        }
      })
      .catch(() => {
        setConfigReady(true);
        setConfigPlaceholderItems([]);
      });
  }, [setConfigReady, setConfigPlaceholderItems]);

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
    <Dialog open={open} onOpenChange={(v) => !v && handleDismiss()}>
      <DialogContent className="max-w-md" showCloseButton={false}>
        <DialogHeader>
          <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-amber-100 dark:bg-amber-900/40">
            <AlertTriangle className="h-6 w-6 text-amber-600 dark:text-amber-400" />
          </div>
          <DialogTitle className="text-center pt-2">
            模型配置未完成
          </DialogTitle>
          <DialogDescription className="text-center">
            以下模型的 API Key 为空或疑似占位符，可能导致对话失败
          </DialogDescription>
        </DialogHeader>

        <div className="rounded-lg border bg-muted/50 divide-y">
          {items.map((item, i) => (
            <div key={i} className="flex items-center gap-3 px-3 py-2.5">
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
        </div>

        <DialogFooter className="sm:justify-center gap-2 pt-2">
          <Button variant="outline" onClick={handleDismiss}>
            稍后配置
          </Button>
          <Button onClick={handleGoSettings} className="gap-1.5">
            <Settings className="h-4 w-4" />
            前往设置
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
