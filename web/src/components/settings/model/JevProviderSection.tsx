"use client";

import { useState } from "react";
import { Check, ChevronRight, FlaskConical, Gauge, Loader2, Minus, Pencil, Plus, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  EMPTY_JEV_PROVIDER_DRAFT,
  draftFromJevPreset,
  draftFromJevProvider,
  jevEntryStatus,
  jevPresetById,
  jevProviderDetail,
  newCustomJevDraft,
  validateJevProvider,
  type JevProviderDraft,
  type JevProviderPublic,
} from "@/lib/jev-settings";
import { SettingsFoldSection } from "../SettingsFoldSection";
import { cn } from "@/lib/utils";
import { JevFieldRow, JevSaveBar, JevStatusChip } from "./jev-widgets";
import { JevPresetPicker, JevProviderForm } from "./JevProviderForm";
import { useJevSettings } from "./useJevSettings";
import { requestModelSubTab } from "./model-subtab";

function ProviderMark({ id, name }: { id: string; name: string }) {
  const letter = id === "typesafe" ? "T" : id === "vercel" ? "V" : (name.trim().slice(0, 1) || "J").toUpperCase();
  return (
    <span
      className="inline-flex h-8 w-8 items-center justify-center rounded-xl shrink-0 text-[13px] font-semibold border"
      style={{
        backgroundColor: "var(--em-primary-alpha-10)",
        color: "var(--em-primary)",
        borderColor: "color-mix(in srgb, var(--em-gold) 45%, transparent)",
      }}
      aria-hidden
    >
      {letter}
    </span>
  );
}

export function JevProviderSection() {
  const {
    runtime,
    draft,
    setDraft,
    providers,
    activeProvider,
    loading,
    saving,
    saved,
    error,
    hasProviderChanges,
    saveProvider,
    saveProviders,
    reload,
    resetDraft,
  } = useJevSettings();
  const [formOpen, setFormOpen] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [formDraft, setFormDraft] = useState<JevProviderDraft>(EMPTY_JEV_PROVIDER_DRAFT);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [deleteId, setDeleteId] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [sectionOpen, setSectionOpen] = useState(true);
  const [experimentalOpen, setExperimentalOpen] = useState(false);

  const beginAdd = () => {
    setSectionOpen(true);
    setFormError(null);
    setFormOpen(true);
    setEditingId(null);
    setFormDraft(draftFromJevPreset(jevPresetById("typesafe")!));
  };

  const beginEdit = (provider: JevProviderPublic) => {
    setFormError(null);
    setFormOpen(true);
    setEditingId(provider.id);
    setExpandedId(provider.id);
    setFormDraft(draftFromJevProvider(provider));
  };

  const handleSelectPreset = (presetId: string) => {
    setFormError(null);
    const preset = jevPresetById(presetId);
    if (!preset) return;
    const existing = providers.find((item) => item.id === preset.id);
    setEditingId(existing?.id ?? null);
    setFormDraft(existing ? draftFromJevProvider(existing) : draftFromJevPreset(preset));
  };

  const handleSaveForm = async () => {
    const validation = validateJevProvider(formDraft);
    setFormError(validation);
    if (validation) return;
    const name = formDraft.name.trim() || jevPresetById(formDraft.id)?.label || "自定义";
    const nextProvider: JevProviderPublic = {
      id: formDraft.id || newCustomJevDraft().id,
      name,
      protocol: formDraft.protocol,
      base_url: formDraft.base_url.trim(),
      model: formDraft.model.trim(),
      configured: Boolean(formDraft.api_key.trim()) || Boolean(providers.find((item) => item.id === formDraft.id)?.configured),
      last4: providers.find((item) => item.id === formDraft.id)?.last4 || "",
    };
    const previous = providers.find((item) => item.id === nextProvider.id);
    const next = previous ? providers.map((item) => item.id === nextProvider.id ? nextProvider : item) : [...providers, nextProvider];
    const activeId = activeProvider?.id || nextProvider.id;
    const success = await saveProviders(next, {
      activeId,
      providerId: nextProvider.id,
      apiKey: formDraft.api_key.trim() || undefined,
      timeout: draft.jev_timeout_seconds,
      model: activeId === nextProvider.id ? nextProvider.model : undefined,
    });
    if (success) {
      setFormOpen(false);
      setEditingId(null);
      setFormDraft(EMPTY_JEV_PROVIDER_DRAFT);
    }
  };

  const handleActivate = async (provider: JevProviderPublic) => {
    const model = provider.model || draft.jev_model;
    await saveProviders(providers, { activeId: provider.id, model, timeout: draft.jev_timeout_seconds });
  };

  const handleDelete = async (providerId: string) => {
    const next = providers.filter((item) => item.id !== providerId);
    const replacement = next.find((provider) => provider.configured) || next[0];
    const deletingActive = activeProvider?.id === providerId;
    const activeId = deletingActive ? (replacement?.id || "") : activeProvider?.id || "";
    const success = await saveProviders(next, { activeId, model: deletingActive ? replacement?.model : undefined, timeout: draft.jev_timeout_seconds });
    if (!success) return;
    setDeleteId(null);
    if (editingId === providerId) {
      setFormOpen(false);
      setEditingId(null);
    }
  };

  const updateTimeout = (value: number) => {
    const next = Math.min(8, Math.max(0.2, Math.round(value * 100) / 100));
    setDraft((prev) => ({ ...prev, jev_timeout_seconds: next }));
  };

  return (
    <SettingsFoldSection
      title="实验性功能"
      description="Jev 决策提供商与任务辅助功能"
      icon={<FlaskConical className="h-4 w-4" style={{ color: "var(--em-gold)" }} />}
      coachId="coach-settings-jev-provider"
      open={experimentalOpen}
      onOpenChange={setExperimentalOpen}
    >
      <div className="border-t border-border/60 p-3">
        {!runtime?.jev_experimental_enabled ? (
          <div className="flex flex-wrap items-center gap-3 rounded-xl border border-dashed border-border/80 bg-muted/20 p-4">
            <FlaskConical className="h-4 w-4 shrink-0 text-muted-foreground" />
            <p className="min-w-0 flex-1 text-xs leading-5 text-muted-foreground">
              Jev 仍处于实验阶段。请先在「模型配置」中开启，之后这里才会显示决策提供商和相关配置。
            </p>
            <Button size="sm" variant="outline" onClick={() => requestModelSubTab("roles")}>
              前往模型配置
            </Button>
          </div>
        ) : (
          <SettingsFoldSection
          title="决策提供商"
          description="添加 TypeSafe、Vercel 或自定义决策服务，然后选择默认提供商"
          icon={<Gauge className="h-4 w-4" style={{ color: "var(--em-primary)" }} />}
          open={sectionOpen}
          onOpenChange={setSectionOpen}
          actions={
            <Button
              size="sm"
              variant="outline"
              className="h-7 text-[11px] gap-1 shrink-0"
              style={{ color: "var(--em-primary)", borderColor: "color-mix(in srgb, var(--em-primary) 35%, transparent)" }}
              onClick={beginAdd}
              disabled={loading || saving}
            >
              <Plus className="h-3 w-3" />
              添加提供商
            </Button>
          }
          >
          <div className="px-3 pb-3 space-y-2">
        {loading && !runtime ? (
          <div className="flex items-center justify-center gap-2 py-8 text-xs text-muted-foreground">
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
            加载配置…
          </div>
        ) : !runtime ? (
          <div className="rounded-xl bg-muted/40 p-5 text-center"><p role="alert" className="text-xs text-destructive">{error || "暂时无法读取配置"}</p><Button className="mt-3" size="sm" variant="outline" onClick={() => void reload()}>重新加载</Button></div>
        ) : (
          <fieldset disabled={saving} className="min-w-0 space-y-3">
            {formOpen && (
              <>
                <JevPresetPicker
                  draft={formDraft}
                  onSelectPreset={handleSelectPreset}
                  onSelectCustom={() => {
                    setEditingId(null);
                    setFormDraft(newCustomJevDraft());
                  }}
                />
                <JevProviderForm
                  key={formDraft.id}
                  draft={formDraft}
                  configured={Boolean(providers.find((item) => item.id === formDraft.id)?.configured)}
                  onChange={(value) => { setFormDraft(value); setFormError(null); }}
                />
                {(formError || error) && <p role="alert" className="text-xs text-destructive">{formError || error}</p>}
                <div className="flex justify-end gap-2">
                  <Button size="sm" variant="outline" onClick={() => { setFormOpen(false); setEditingId(null); setFormDraft(EMPTY_JEV_PROVIDER_DRAFT); setFormError(null); }}>
                    取消
                  </Button>
                  <Button size="sm" disabled={saving} onClick={() => void handleSaveForm()}>
                    {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
                    {editingId ? "保存" : "添加"}
                  </Button>
                </div>
              </>
            )}

            <div className="space-y-2">
              {providers.map((provider) => {
                const isDefault = activeProvider?.id === provider.id;
                const isExpanded = expandedId === provider.id;
                const status = jevEntryStatus({
                  configured: provider.configured,
                  enabled: isDefault ? draft.jev_enabled : "off",
                });
                return (
                  <div
                    key={provider.id}
                    className={cn(
                      "rounded-xl border overflow-hidden transition-colors",
                      isDefault
                        ? "border-[var(--em-primary)] bg-[var(--em-primary)]/6"
                        : "border-border/70 bg-card hover:border-border hover:bg-muted/20",
                    )}
                  >
                    <div className="flex flex-wrap items-center gap-2 px-3 py-2.5">
                      <button type="button" aria-expanded={isExpanded} aria-label={`${provider.name} 连接详情`} onClick={() => setExpandedId(isExpanded ? null : provider.id)} className="flex min-w-0 flex-1 items-center gap-3 rounded-lg text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
                      <ProviderMark id={provider.id} name={provider.name} />
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-1.5 flex-wrap">
                          <span className="font-semibold text-sm">{provider.name}</span>
                          <span className="rounded-full border border-border/80 px-1.5 py-px text-[10px] font-medium text-muted-foreground">
                            {provider.protocol === "gateway" ? "Gateway" : "TypeSafe"}
                          </span>
                          <JevStatusChip tone={provider.configured ? "ready" : status.tone} chip={provider.configured ? "已配置密钥" : status.chip} />
                        </div>
                        <p className="text-[11px] text-muted-foreground mt-0.5 truncate">
                          {jevProviderDetail({ configured: provider.configured, last4: provider.last4 })}
                        </p>
                      </div>
                      <ChevronRight className={cn("h-3.5 w-3.5 shrink-0 text-muted-foreground transition-transform", isExpanded && "rotate-90")} />
                      </button>
                      <div className="flex items-center gap-1 shrink-0">
                        {!isDefault && (
                          <button
                            type="button"
                            disabled={!provider.configured || saving}
                            title={provider.configured ? "设为默认提供商" : "请先编辑并添加密钥"}
                            className="rounded-md px-1.5 py-2 text-[11px] text-muted-foreground hover:bg-muted/60 hover:text-foreground disabled:opacity-40"
                            onClick={(event) => {
                              event.stopPropagation();
                              void handleActivate(provider);
                            }}
                          >
                            设为默认
                          </button>
                        )}
                        {isDefault && (
                          <span className="inline-flex items-center rounded-full px-1.5 py-0.5 text-[10px] font-medium bg-[var(--em-primary)]/12 text-[var(--em-primary)]">
                            默认
                          </span>
                        )}
                        {isDefault && <Check className="h-3.5 w-3.5" style={{ color: "var(--em-primary)" }} />}
                        <button
                          type="button"
                          title="编辑"
                          aria-label={`编辑 ${provider.name}`}
                          className="h-7 w-7 flex items-center justify-center rounded-md text-muted-foreground hover:text-foreground hover:bg-muted/60"
                          onClick={(event) => {
                            event.stopPropagation();
                            beginEdit(provider);
                          }}
                        >
                          <Pencil className="h-3.5 w-3.5" />
                        </button>
                        <button
                          type="button"
                          title="删除"
                          aria-label={`删除 ${provider.name}`}
                          className="h-7 w-7 flex items-center justify-center rounded-md text-muted-foreground hover:text-destructive hover:bg-destructive/10"
                          onClick={(event) => {
                            event.stopPropagation();
                            setDeleteId(provider.id);
                          }}
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </button>
                      </div>
                    </div>
                    {deleteId === provider.id && <div className="border-t border-destructive/20 bg-destructive/5 p-3 text-xs"><p>删除 {provider.name} 及其已保存的密钥？{isDefault ? "这是当前默认提供商，删除后将使用其他已配置的服务。" : ""}</p><div className="mt-3 flex justify-end gap-2"><Button size="sm" variant="outline" onClick={() => setDeleteId(null)}>取消</Button><Button size="sm" variant="destructive" onClick={() => void handleDelete(provider.id)}>确认删除</Button></div></div>}
                    {isExpanded && (
                      <div className="px-3 pb-3 border-t border-border/50 pt-2 text-[11px] text-muted-foreground space-y-1">
                        <p className="break-all font-mono">{provider.model}</p>
                        <p className="break-all font-mono">{provider.base_url}</p>
                      </div>
                    )}
                  </div>
                );
              })}

              {providers.length === 0 && !formOpen && (
                <div className="flex flex-col items-center gap-2 py-8 text-center">
                  <p className="text-xs text-muted-foreground">还没有添加决策提供商</p>
                  <p className="text-xs leading-5 text-muted-foreground">选择预设并粘贴密钥，即可在模型配置中开启 Jev。</p>
                  <Button variant="outline" size="sm" onClick={beginAdd}><Plus className="size-3.5" />添加第一个提供商</Button>
                </div>
              )}
            </div>

            <div className="rounded-lg border border-border/70 overflow-hidden">
              <JevFieldRow label="超时（秒）" desc="单次评估的最长等待时间，对所有决策提供商生效。">
                <div className="flex h-9 w-full flex-shrink-0 overflow-hidden rounded-lg border border-input bg-background shadow-[0_1px_2px_rgba(24,58,40,0.04)] transition-[border-color,box-shadow] focus-within:border-[var(--em-primary)] focus-within:ring-2 focus-within:ring-[var(--em-primary-alpha-15)] sm:h-8 sm:w-36">
                  <button
                    type="button"
                    aria-label="减少超时时间"
                    className="grid h-full w-9 shrink-0 place-items-center border-r border-border/70 text-muted-foreground transition-colors hover:bg-[var(--em-primary-alpha-06)] hover:text-[var(--em-primary)] focus-visible:z-10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[var(--em-primary-alpha-25)] disabled:pointer-events-none disabled:opacity-35"
                    disabled={draft.jev_timeout_seconds <= 0.2}
                    onClick={() => updateTimeout(draft.jev_timeout_seconds - 0.05)}
                  >
                    <Minus className="h-3.5 w-3.5" />
                  </button>
                  <Input
                    type="number"
                    aria-label="超时秒数"
                    inputMode="decimal"
                    className="h-full min-w-0 flex-1 rounded-none border-0 bg-transparent px-1 text-center text-sm shadow-none [appearance:textfield] focus-visible:border-transparent focus-visible:ring-0 [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:appearance-none"
                    step={0.05}
                    min={0.2}
                    max={8}
                    value={draft.jev_timeout_seconds}
                    onChange={(event) => {
                      const next = parseFloat(event.target.value);
                      if (!Number.isNaN(next)) {
                        updateTimeout(next);
                      }
                    }}
                  />
                  <button
                    type="button"
                    aria-label="增加超时时间"
                    className="grid h-full w-9 shrink-0 place-items-center border-l border-border/70 text-muted-foreground transition-colors hover:bg-[var(--em-primary-alpha-06)] hover:text-[var(--em-primary)] focus-visible:z-10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[var(--em-primary-alpha-25)] disabled:pointer-events-none disabled:opacity-35"
                    disabled={draft.jev_timeout_seconds >= 8}
                    onClick={() => updateTimeout(draft.jev_timeout_seconds + 0.05)}
                  >
                    <Plus className="h-3.5 w-3.5" />
                  </button>
                </div>
              </JevFieldRow>
              <JevSaveBar
                hasChanges={hasProviderChanges}
                saving={saving}
                saved={saved}
                error={formOpen ? null : error}
                onSave={() => void saveProvider()}
                onReset={resetDraft}
              />
            </div>
          </fieldset>
        )}
          </div>
        </SettingsFoldSection>
        )}
      </div>
    </SettingsFoldSection>
  );
}
