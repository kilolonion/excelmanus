"use client";

import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { CheckCircle2, Loader2, Save } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Separator } from "@/components/ui/separator";
import { Switch } from "@/components/ui/switch";
import { apiGet, apiPut } from "@/lib/api";
import { settingsCache } from "@/lib/settings-cache";
import { useConnectionStore } from "@/stores/connection-store";
import { SettingsFoldSection } from "./SettingsFoldSection";

export type RuntimeSettingValue = boolean | number | string;
export type RuntimeSettings = Record<string, RuntimeSettingValue>;

interface SelectOption {
  value: string;
  label: string;
}

export interface RuntimeSettingItem {
  key: string;
  label: string;
  desc: string;
  icon: ReactNode;
  type: "bool" | "int" | "float" | "select" | "string";
  options?: SelectOption[];
  min?: number;
  max?: number;
  disabledWhen?: (settings: RuntimeSettings) => boolean;
  disabledDesc?: string;
}

export interface RuntimeSettingGroup {
  title: string;
  description?: string;
  icon: ReactNode;
  items: RuntimeSettingItem[];
  defaultOpen?: boolean;
}

export function RuntimeSettingsPanel({
  groups,
  className = "",
  onSaved,
}: {
  groups: RuntimeSettingGroup[];
  className?: string;
  onSaved?: (settings: RuntimeSettings) => void;
}) {
  const [config, setConfig] = useState<RuntimeSettings | null>(null);
  const [draft, setDraft] = useState<RuntimeSettings>({});
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState("");
  const triggerRestart = useConnectionStore((state) => state.triggerRestart);

  const fetchConfig = useCallback(async (force = false) => {
    if (!force) {
      const cached = settingsCache.get<RuntimeSettings>("/config/runtime");
      if (cached) {
        setConfig(cached);
        setDraft({});
        return;
      }
    }
    setLoading(true);
    setError("");
    try {
      const data = await apiGet<RuntimeSettings>("/config/runtime");
      settingsCache.set("/config/runtime", data);
      setConfig(data);
      setDraft({});
    } catch (err) {
      setError(err instanceof Error ? err.message : "无法读取配置");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchConfig();
  }, [fetchConfig]);

  const merged = useMemo(
    () => (config ? { ...config, ...draft } : null),
    [config, draft],
  );
  const hasChanges = Object.keys(draft).length > 0;

  const update = (key: string, value: RuntimeSettingValue) => {
    setSaved(false);
    setError("");
    setDraft((previous) => ({ ...previous, [key]: value }));
  };

  const handleSave = async () => {
    if (!hasChanges || saving || !config) return;
    setSaving(true);
    setError("");
    try {
      const response = await apiPut<{ restarting?: boolean; restart_reason?: string }>(
        "/config/runtime",
        draft,
      );
      if (response?.restarting) {
        triggerRestart(response.restart_reason || "配置已更新");
        return;
      }
      onSaved?.({ ...config, ...draft });
      setSaved(true);
      window.setTimeout(() => setSaved(false), 2000);
      await fetchConfig(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存失败，请重试");
    } finally {
      setSaving(false);
    }
  };

  if (loading && !config) {
    return (
      <div className="flex items-center justify-center rounded-xl border border-dashed py-8 text-xs text-muted-foreground">
        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
        正在读取配置…
      </div>
    );
  }

  if (!merged) {
    return (
      <div className="rounded-xl border border-dashed px-4 py-6 text-center text-xs text-muted-foreground">
        {error || "无法读取配置"}
      </div>
    );
  }

  return (
    <section className={`em-settings-panel em-settings-runtime-panel ${className}`.trim()}>
      {groups.map((group) => (
        <SettingsFoldSection
          embedded
          key={group.title}
          title={group.title}
          description={group.description ?? ""}
          icon={<span className="text-[var(--em-primary)]">{group.icon}</span>}
          defaultOpen={group.defaultOpen ?? true}
        >
          <div className="space-y-3 border-t border-border/60 p-3">
            {group.items.map((item, index) => {
              const value = merged[item.key];
              const disabled = item.disabledWhen?.(merged) ?? false;
              return (
                <div key={item.key} data-runtime-setting-key={item.key}>
                  {item.type === "bool" ? (
                    <div className="flex items-center justify-between gap-3">
                      <div className="flex min-w-0 flex-1 items-start gap-2.5">
                        <span className="mt-0.5 shrink-0 text-muted-foreground">{item.icon}</span>
                        <div className="min-w-0">
                          <div className="text-sm font-medium">{item.label}</div>
                          <div className="text-[11px] text-muted-foreground sm:text-xs">
                            {disabled && item.disabledDesc ? item.disabledDesc : item.desc}
                          </div>
                        </div>
                      </div>
                      <Switch
                        aria-label={item.label}
                        checked={Boolean(value)}
                        disabled={disabled}
                        onCheckedChange={(checked) => update(item.key, checked)}
                        className="shrink-0"
                      />
                    </div>
                  ) : (
                    <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between sm:gap-4">
                      <div className="flex min-w-0 flex-1 items-start gap-2.5">
                        <span className="mt-0.5 shrink-0 text-muted-foreground">{item.icon}</span>
                        <div className="min-w-0">
                          <div className="text-sm font-medium">{item.label}</div>
                          <div className="text-[11px] text-muted-foreground sm:text-xs">
                            {disabled && item.disabledDesc ? item.disabledDesc : item.desc}
                          </div>
                        </div>
                      </div>
                      {item.type === "select" && item.options ? (
                        <select
                          aria-label={item.label}
                          className="h-9 w-full shrink-0 rounded-md border border-input bg-background px-2 text-sm disabled:cursor-not-allowed disabled:opacity-50 sm:h-8 sm:w-32"
                          value={String(value ?? "")}
                          disabled={disabled}
                          onChange={(event) => update(item.key, event.target.value)}
                        >
                          {item.options.map((option) => (
                            <option key={option.value} value={option.value}>{option.label}</option>
                          ))}
                        </select>
                      ) : item.type === "string" ? (
                        <Input
                          aria-label={item.label}
                          type="text"
                          className="h-9 w-full shrink-0 text-xs font-mono sm:h-8 sm:w-40"
                          value={String(value ?? "")}
                          disabled={disabled}
                          onChange={(event) => update(item.key, event.target.value)}
                        />
                      ) : (
                        <Input
                          aria-label={item.label}
                          type="number"
                          className="h-9 w-full shrink-0 text-right text-sm sm:h-8 sm:w-24"
                          step={item.type === "float" ? 0.05 : 1}
                          min={item.min ?? (item.type === "float" ? 0 : 1)}
                          max={item.max ?? (item.type === "float" ? 1 : 500)}
                          value={typeof value === "number" ? value : ""}
                          disabled={disabled}
                          onChange={(event) => {
                            const next = item.type === "float"
                              ? Number.parseFloat(event.target.value)
                              : Number.parseInt(event.target.value, 10);
                            if (!Number.isNaN(next)) update(item.key, next);
                          }}
                        />
                      )}
                    </div>
                  )}
                  {index < group.items.length - 1 && <Separator className="mt-3" />}
                </div>
              );
            })}
          </div>
        </SettingsFoldSection>
      ))}

      {error && <p role="alert" className="px-3 pt-3 text-xs text-destructive">{error}</p>}
      <div className="em-settings-card-footer">
        <Button
          type="button"
          size="sm"
          disabled={!hasChanges || saving}
          onClick={handleSave}
          className="gap-1.5"
        >
          {saving ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : saved ? (
            <CheckCircle2 className="h-3.5 w-3.5" />
          ) : (
            <Save className="h-3.5 w-3.5" />
          )}
          {saved ? "已保存" : "保存配置"}
        </Button>
      </div>
    </section>
  );
}
