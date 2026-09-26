"use client";

import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { CheckCircle2, Loader2, Save } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { MiniCheckbox } from "@/components/ui/MiniCheckbox";
import { Switch } from "@/components/ui/switch";
import { apiGet, apiPut } from "@/lib/api";
import { settingsCache } from "@/lib/settings-cache";
import { SETTING_EFFECT_LABELS, settingError, settingPayload, type RuntimeSettings, type RuntimeSettingGroup } from "@/lib/runtime-settings-form";
import { useSettingsNavigationStore } from "@/stores/settings-navigation-store";
import { useSettingsDraftStore } from "@/stores/settings-draft-store";
import { useConnectionStore } from "@/stores/connection-store";
import { useUIStore } from "@/stores/ui-store";
import { SettingsFoldSection } from "./SettingsFoldSection";
import { GlassSelect } from "./model/form-widgets";

export type { RuntimeSettingValue, RuntimeSettings, RuntimeSettingItem, RuntimeSettingGroup } from "@/lib/runtime-settings-form";

export function RuntimeSettingsPanel({ groups, className = "", onSaved, category, query = "" }: {
  groups: RuntimeSettingGroup[];
  className?: string;
  onSaved?: (settings: RuntimeSettings) => void;
  category?: string;
  query?: string;
}) {
  const [config, setConfig] = useState<RuntimeSettings | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState("");
  const pending = useRef(false);
  const id = useId();
  const targetKey = useSettingsNavigationStore((state) => state.targetKey);
  const targetVersion = useSettingsNavigationStore((state) => state.targetVersion);
  const allDrafts = useSettingsDraftStore((state) => state.drafts);
  const items = useMemo(() => groups.flatMap((group) => group.items), [groups]);
  const draft = useMemo(() => Object.fromEntries(items.filter((item) => Object.hasOwn(allDrafts, item.key)).map((item) => [item.key, allDrafts[item.key]])), [items, allDrafts]);

  const fetchConfig = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const data = await apiGet<RuntimeSettings>("/config/runtime");
      settingsCache.set("/config/runtime", data);
      setConfig(data);
      if (data.message_dispatch_default) useUIStore.getState().setMessageDispatchDefault(data.message_dispatch_default as "steer" | "interrupt" | "queue");
    } catch (err) {
      setError(err instanceof Error ? err.message : "无法读取配置，请重试");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void fetchConfig(); }, [fetchConfig]);
  useEffect(() => {
    if (!config || !targetKey || !items.some((item) => item.key === targetKey)) return;
    const frame = requestAnimationFrame(() => {
      const input = document.getElementById(`${id}-${targetKey}`);
      input?.scrollIntoView?.({ block: "center", behavior: "instant" });
      input?.focus({ preventScroll: true });
    });
    return () => cancelAnimationFrame(frame);
  }, [config, id, items, targetKey, targetVersion]);
  const merged = config ? { ...config, ...draft } : null;
  const changedItems = items.filter((item) => Object.hasOwn(draft, item.key));
  const errors = merged ? Object.fromEntries(changedItems.flatMap((item) => {
    const error = settingError(item, merged[item.key], merged);
    return error ? [[item.key, error]] : [];
  })) : {};
  const invalid = Object.keys(errors).length > 0;
  const restartItems = changedItems.filter((item) => item.effect === "restart");
  const search = query.trim().toLocaleLowerCase();
  const visibleGroups = groups.map((group) => ({
    ...group,
    items: search ? group.items.filter((item) => `${group.title} ${item.label} ${item.desc} ${item.key}`.toLocaleLowerCase().includes(search)) : group.items,
  })).filter((group) => group.items.length && (search || !category || category === group.category));

  async function handleSave() {
    if (!config || !merged || !changedItems.length || invalid || pending.current) return;
    pending.current = true;
    setSaving(true);
    setError("");
    setSaved(false);
    const payload = settingPayload(items, draft);
    try {
      const response = await apiPut<{ restarting?: boolean; restart_reason?: string }>("/config/runtime", payload);
      const next = { ...config, ...payload };
      setConfig(next);
      settingsCache.set("/config/runtime", next);
      // Only acknowledge the submitted values; another mounted panel may have edited a field.
      const latest = useSettingsDraftStore.getState();
      latest.discard(Object.keys(draft).filter((key) => latest.drafts[key] === draft[key]));
      if (next.message_dispatch_default) useUIStore.getState().setMessageDispatchDefault(next.message_dispatch_default as "steer" | "interrupt" | "queue");
      onSaved?.(next);
      setSaved(true);
      if (response?.restarting) {
        settingsCache.delete("/config/runtime");
        useConnectionStore.getState().triggerRestart(response.restart_reason || "配置已更新");
      } else {
        await fetchConfig();
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存失败，修改仍保留，请重试");
    } finally {
      pending.current = false;
      setSaving(false);
    }
  }

  if (!merged) return (
    <div className="rounded-xl border border-dashed px-4 py-8 text-center text-sm text-muted-foreground">
      {loading ? <span role="status"><Loader2 className="mr-2 inline h-4 w-4 animate-spin" />正在读取配置…</span> : <><p role="alert">{error}</p><Button variant="outline" size="sm" className="mt-3" onClick={() => void fetchConfig()}>重新加载</Button></>}
    </div>
  );

  return (
    <section className={`em-settings-panel em-settings-runtime-panel ${className}`.trim()} aria-busy={saving}>
      <p className="border-b border-border/60 px-4 py-3 text-xs text-muted-foreground">以下设置由此服务共用。修改后需保存；切换设置页面会保留草稿。</p>
      {!visibleGroups.length && <p role="status" className="p-6 text-center text-sm text-muted-foreground">没有匹配的设置，试试“费用”“重试”或“上下文”。</p>}
      {visibleGroups.map((group) => (
        <SettingsFoldSection embedded key={`${group.title}-${targetVersion}`} title={group.title} description={group.description ?? ""} icon={<span className="text-[var(--em-primary)]">{group.icon}</span>} defaultOpen={group.items.some((item) => item.key === targetKey) || (group.defaultOpen ?? true)} open={search ? true : undefined}>
          <div className="divide-y divide-border/60 border-t border-border/60 px-4">
            {group.items.map((item) => {
              const value = merged[item.key];
              const available = Object.hasOwn(config!, item.key);
              const disabled = (item.disabledWhen?.(merged) ?? false) || saving || !available;
              const inputId = `${id}-${item.key}`;
              const auto = Boolean(item.automatic && Number(value) === 0);
              const update = (next: string | boolean | number) => {
                setSaved(false);
                setError("");
                useSettingsDraftStore.getState().update(item.key, next, config![item.key]);
              };
              return (
                <div key={item.key} data-runtime-setting-key={item.key} data-coach-id={item.coachId} className={`py-4 ${targetKey === item.key ? "bg-[var(--em-primary-alpha-04)]" : ""}`}>
                  <div className="flex flex-col justify-between gap-3 sm:flex-row sm:items-start sm:gap-6">
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                        <label htmlFor={inputId} className="text-sm font-medium">{item.label}</label>
                        <span className={`text-[10px] ${item.effect === "restart" ? "text-amber-700 dark:text-amber-400" : "text-muted-foreground"}`}>{SETTING_EFFECT_LABELS[item.effect ?? "new-session"]}</span>
                      </div>
                      <p id={`${inputId}-desc`} className="mt-1 text-xs leading-relaxed text-muted-foreground">{!available ? "当前服务尚未提供此设置，重启服务加载更新后可使用。" : item.disabledWhen?.(merged) && item.disabledDesc ? item.disabledDesc : item.desc}</p>
                    </div>
                    <div className={`shrink-0 ${item.type === "bool" ? "self-end sm:self-start" : "w-full sm:w-44"}`}>
                      {item.type === "bool" ? (
                        <Switch id={inputId} aria-describedby={`${inputId}-desc`} checked={Boolean(value)} disabled={disabled} onCheckedChange={update} />
                      ) : item.type === "select" ? (
                        <GlassSelect
                          id={inputId}
                          ariaLabel={item.label}
                          ariaDescribedBy={`${inputId}-desc`}
                          className="w-full"
                          value={String(value ?? "")}
                          disabled={disabled}
                          options={(item.options ?? []).map((option) => ({ value: option.value, label: option.label }))}
                          onChange={(next) => update(next)}
                        />
                      ) : (
                        <>
                          {item.automatic && (
                            <MiniCheckbox
                              className="mb-2"
                              checked={auto}
                              disabled={disabled}
                              label={item.automatic.label}
                              onChange={(checked) => update(checked ? 0 : Number(merged[item.automatic!.effectiveKey]) || 256000)}
                            />
                          )}
                          <div className="flex items-center gap-2">
                            <Input id={inputId} aria-describedby={`${inputId}-desc${errors[item.key] ? ` ${inputId}-error` : ""}`} aria-invalid={Boolean(errors[item.key])} type={item.type === "string" ? "text" : "number"} className="h-9 min-w-0 text-sm" step={item.step ?? (item.type === "int" ? 1 : "any")} min={item.min} max={item.max} value={auto ? String(merged[item.automatic!.effectiveKey] ?? "") : String(value ?? "")} placeholder={item.placeholder} disabled={disabled || auto} onChange={(event) => update(event.target.value)} />
                            {item.unit && <span className="whitespace-nowrap text-xs text-muted-foreground">{item.unit}</span>}
                          </div>
                        </>
                      )}
                      {errors[item.key] && <p id={`${inputId}-error`} role="alert" className="mt-1 text-xs text-destructive">{errors[item.key]}</p>}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </SettingsFoldSection>
      ))}
      <div className="em-settings-savebar">
        <div className="min-w-0 flex-1 text-xs" role="status" aria-live="polite">
          {error ? <p role="alert" className="text-destructive">{error}</p> : saved ? <span className="text-emerald-700 dark:text-emerald-400">已保存</span> : changedItems.length ? <span>有 {changedItems.length} 项未保存{invalid ? "，请修正输入" : ""}</span> : <span className="text-muted-foreground">所有更改已保存</span>}
          {invalid && <p className="mt-1 text-destructive">需要修正：{changedItems.filter((item) => errors[item.key]).map((item) => item.label).join("、")}</p>}
          {restartItems.length > 0 && <p className="mt-1 text-amber-700 dark:text-amber-400">保存将重启服务并中断进行中的任务：{restartItems.map((item) => item.label).join("、")}。</p>}
          {changedItems.some((item) => (item.effect ?? "new-session") === "new-session") && <p className="mt-1 text-muted-foreground">部分修改需要新建对话后生效。</p>}
        </div>
        {changedItems.length > 0 && <Button type="button" variant="ghost" size="sm" disabled={saving} onClick={() => { useSettingsDraftStore.getState().discard(items.map((item) => item.key)); setError(""); setSaved(false); }}>撤销修改</Button>}
        <Button type="button" size="sm" disabled={!changedItems.length || saving || invalid} onClick={() => void handleSave()} className="gap-1.5">
          {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : saved ? <CheckCircle2 className="h-3.5 w-3.5" /> : <Save className="h-3.5 w-3.5" />}
          {saving ? "正在保存…" : restartItems.length ? "保存并重启" : "保存配置"}
        </Button>
      </div>
    </section>
  );
}
