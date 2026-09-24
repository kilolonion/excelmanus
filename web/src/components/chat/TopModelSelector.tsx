"use client";

import { useEffect, useRef, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { ChevronDown, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Popover } from "radix-ui";
import { useUIStore } from "@/stores/ui-store";
import { apiGet } from "@/lib/api";
import { displayModelLabel } from "@/lib/model-display";
import { getProviderColor, inferModelBrand } from "@/lib/provider-brand";
import { useIsMobile } from "@/hooks/use-mobile";
import { ModelListBottomSheet } from "@/components/chat/ModelListBottomSheet";
import { hasProviderLogo, ProviderLogo, providerFallbackInitial } from "@/components/settings/model/ProviderLogo";
import { useModelSelection } from "@/hooks/use-model-selection";
import { ModelPickerContent } from "./ModelPickerContent";
import styles from "./ModelPickerContent.module.css";

interface ModelCapabilitySummary {
  name: string;
  model: string;
  base_url: string;
  capabilities: { healthy: boolean | null; health_error: string } | null;
}

function ModelBrandMark({
  provider,
  label,
  unhealthy,
}: {
  provider: string;
  label: string;
  unhealthy: boolean;
}) {
  const color = getProviderColor(provider);
  const initial = providerFallbackInitial(label);

  return (
    <span
      className="relative inline-flex h-4 w-4 shrink-0 items-center justify-center"
      aria-label={`${label} 品牌`}
    >
      {hasProviderLogo(provider) ? (
        <ProviderLogo id={provider} color={color} className="h-4 w-4" />
      ) : (
        <span
          className="inline-flex h-4 w-4 items-center justify-center rounded-full text-[9px] font-bold leading-none"
          style={{
            color,
            backgroundColor: `${color}18`,
          }}
        >
          {initial}
        </span>
      )}
      {unhealthy && (
        <span
          className="absolute -bottom-0.5 -right-0.5 h-1.5 w-1.5 rounded-full border border-card bg-[var(--em-error)]"
          aria-label="模型不可用"
        />
      )}
    </span>
  );
}

export function TopModelSelector() {
  const { currentModel, models, switching, loading, loadError, switchError, reload, selectModel } = useModelSelection();
  const modelProfileVersion = useUIStore((s) => s.modelProfileVersion);
  const [capsMap, setCapsMap] = useState<Record<string, { healthy: boolean | null; health_error: string }>>({});
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const capabilitiesLoadedRef = useRef(false);
  const isMobile = useIsMobile();

  const fetchCapabilities = () => {
    if (capabilitiesLoadedRef.current) return;
    capabilitiesLoadedRef.current = true;
    const version = useUIStore.getState().modelProfileVersion;
    apiGet<{ items: ModelCapabilitySummary[] }>("/config/models/capabilities/all")
      .then((data) => {
        if (version !== useUIStore.getState().modelProfileVersion) return;
        const map: Record<string, { healthy: boolean | null; health_error: string }> = {};
        for (const item of data.items) {
          if (item.capabilities) {
            map[item.name] = {
              healthy: item.capabilities.healthy,
              health_error: item.capabilities.health_error,
            };
          }
        }
        setCapsMap(map);
      })
      .catch(() => {
        capabilitiesLoadedRef.current = false;
      });
  };

  // Health details are only needed while choosing a model.  Keeping this out
  // of the initial workspace request fan-out makes the chat surface usable
  // before the (potentially large) profile capability list is read.
  useEffect(() => {
    if (open) { fetchCapabilities(); void reload(); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  // 当 Settings 页 profile 变更时自动刷新模型列表
  useEffect(() => {
    if (modelProfileVersion > 0) {
      capabilitiesLoadedRef.current = false;
      setCapsMap({});
    }
  }, [modelProfileVersion]);

  const handleSwitch = async (name: string) => {
    if (await selectModel(name)) setOpen(false);
  };
  const activeModel = models.find((model) => model.name === currentModel);
  const displayName = activeModel ? displayModelLabel(activeModel) : currentModel || "选择模型";
  const currentModelUnhealthy = Boolean(currentModel && capsMap[currentModel]?.healthy === false);
  const activeProvider = activeModel ? inferModelBrand(activeModel) : "unknown";
  const pickerProps = {
    models, currentModel, capsMap, switching, loading, loadError, switchError,
    onSelect: handleSwitch,
    onClose: () => setOpen(false),
    onReload: () => { void reload(); fetchCapabilities(); },
  };
  const trigger = (
    <Button ref={triggerRef} variant="ghost"
      className="em-model-selector-trigger gap-1.5 px-2.5 h-8 rounded-full border border-[var(--em-line)] bg-card/80 text-[12px] font-medium text-muted-foreground group shrink-0 shadow-sm hover:border-[var(--em-line-strong)] data-[state=open]:bg-[var(--em-primary-alpha-06)] data-[state=open]:border-[var(--em-primary-alpha-25)]"
      data-coach-id="coach-model-selector" aria-label={`选择模型：${displayName}`}
      aria-haspopup={isMobile ? "dialog" : undefined} aria-expanded={isMobile ? open : undefined}
      onClick={isMobile ? () => setOpen(true) : undefined}>
      <ModelBrandMark provider={activeProvider} label={displayName} unhealthy={currentModelUnhealthy} />
      <AnimatePresence mode="wait" initial={false}>
        <motion.span key={currentModel || "_none"}
          initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -4 }}
          transition={{ duration: 0.15, ease: [0.4, 0, 0.2, 1] }}
          className={`em-model-selector-label truncate ${currentModelUnhealthy ? "text-destructive" : ""}`}>
          {displayName}
        </motion.span>
      </AnimatePresence>
      {switching ? <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />
        : <ChevronDown className="h-3.5 w-3.5 text-muted-foreground transition-transform duration-200 group-data-[state=open]:rotate-180" />}
    </Button>
  );

  if (isMobile) {
    return <>{trigger}<ModelListBottomSheet {...pickerProps} open={open} onOpenChange={setOpen} mode="switch"
      onCloseAutoFocus={(event) => {
        event.preventDefault();
        if (!useUIStore.getState().settingsOpen) triggerRef.current?.focus();
      }} /></>;
  }

  return (
    <Popover.Root open={open} onOpenChange={setOpen}>
      <Popover.Trigger asChild>{trigger}</Popover.Trigger>
      <Popover.Portal>
        <Popover.Content align="start" sideOffset={8} collisionPadding={12} className={styles.popover}
          aria-label="选择模型"
          onCloseAutoFocus={(event) => { if (useUIStore.getState().settingsOpen) event.preventDefault(); }}
          onOpenAutoFocus={(event) => {
            event.preventDefault();
            (event.currentTarget as HTMLElement).querySelector<HTMLInputElement>("input")?.focus();
          }}>
          <ModelPickerContent {...pickerProps} />
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}
