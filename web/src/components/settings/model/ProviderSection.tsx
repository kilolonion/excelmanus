"use client";

import { useMemo, useState } from "react";
import {
  Plus, Trash2, Pencil, Loader2, Zap, AlertTriangle, X, Bot, Database,
  ExternalLink, Crown, Lock, Check, ChevronRight,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { formatModelIdForDisplay } from "@/lib/model-display";
import { useAdminModel } from "./admin-model-context";
import { PROVIDER_PRESETS } from "./constants";
import { ProviderLogo, ProviderAvatar } from "./ProviderLogo";
import { CapabilityBadges } from "./capability-widgets";
import {
  isModelUnhealthy, getHealthError, inferProfileProvider, getProviderBrandColor,
  withAlpha, EMPTY_PROFILE_DRAFT, groupProfilesByProvider, getProfileProviderId,
  isCodexProfile, isProfileConnected, pickDefaultProfile, profileToDraft,
} from "./helpers";
import type { ProviderGroup } from "./helpers";
import type { ModelCapabilities, ProfileEntry } from "./types";
import { SettingsFoldSection } from "../SettingsFoldSection";
import { ProfileEditorForm } from "./ProfileEditorForm";

function providerStatusText(group: ProviderGroup, unhealthy: boolean): string {
  const connectedCount = group.profiles.filter(isProfileConnected).length;
  if (unhealthy) return "连接失败";
  if (connectedCount === 0) return "需要填写 API Key";
  return `已连接 · ${group.profiles.length} 个可用模型`;
}

export function ProviderSection() {
  const {
    config,
    newProfile,
    setNewProfile,
    editingProfile,
    setEditingProfile,
    profileError,
    setProfileError,
    highlightProfile,
    profileDraft,
    setProfileDraft,
    capsMap,
    probingKey,
    activatingProfile,
    applyPresetToProfileDraft,
    handleProbeOne,
    handleActivateProfile,
    handleDeleteProfile,
    profileCardRefs,
    setRemoteModelError,
    setRemoteModelHint,
    setTestResult,
    siblingSourceName,
    setSiblingSourceName,
    beginAddSiblingProfile,
    setRemoteModels,
    setModelDropdownTarget,
  } = useAdminModel();

  const groups = useMemo(
    () => groupProfilesByProvider(config?.profiles || []),
    [config?.profiles],
  );

  const activeName = config?.active || null;
  const defaultGroupId = useMemo(() => {
    const active = (config?.profiles || []).find((p) => p.name === activeName);
    return active ? getProfileProviderId(active) : null;
  }, [config?.profiles, activeName]);

  const [selectedProviderId, setSelectedProviderId] = useState<string | null>(null);
  const [expandedProviderId, setExpandedProviderId] = useState<string | null>(null);
  const [sectionOpen, setSectionOpen] = useState(true);

  const selectedId = selectedProviderId || defaultGroupId;

  const beginAdd = () => {
    setSectionOpen(true);
    setNewProfile(true);
    setEditingProfile(null);
    setSiblingSourceName(null);
    setProfileDraft({ ...EMPTY_PROFILE_DRAFT });
    setProfileError(null);
    setRemoteModelError(null);
    setRemoteModelHint(null);
    setRemoteModels([]);
    setModelDropdownTarget(null);
    setTestResult((prev) => ({ ...prev, _profile_form: null }));
  };

  const beginEdit = (profile: ProfileEntry) => {
    if (isCodexProfile(profile)) return;
    setEditingProfile(profile.name);
    setNewProfile(false);
    setSiblingSourceName(null);
    setProfileDraft(profileToDraft(profile));
    setRemoteModelError(null);
    setRemoteModelHint(null);
    setRemoteModels([]);
    setModelDropdownTarget(null);
    setTestResult((prev) => ({ ...prev, _profile_form: null }));
  };

  const handleSelectProvider = (group: ProviderGroup) => {
    const wasSelected = selectedId === group.id && expandedProviderId === group.id;
    setSelectedProviderId(group.id);
    setExpandedProviderId(wasSelected ? null : group.id);
    const alreadyDefault = group.profiles.some((p) => p.name === activeName);
    if (alreadyDefault) return;
    const next = pickDefaultProfile(group, activeName);
    if (next) void handleActivateProfile(next);
  };

  return (
    <SettingsFoldSection
      title="模型提供商"
      description="添加并管理模型服务，然后选择默认提供商"
      icon={<Database className="h-4 w-4" style={{ color: "var(--em-primary)" }} />}
      coachId="coach-settings-profiles"
      open={sectionOpen}
      onOpenChange={setSectionOpen}
      actions={
        <Button
          size="sm"
          variant="outline"
          className="h-7 text-[11px] gap-1 shrink-0"
          style={{ color: "var(--em-primary)", borderColor: "color-mix(in srgb, var(--em-primary) 35%, transparent)" }}
          onClick={beginAdd}
        >
          <Plus className="h-3 w-3" />
          添加提供商
        </Button>
      }
    >
      <div className="px-3 pb-3 space-y-2">
        {profileError && (
          <div className="rounded-xl border border-destructive/30 bg-destructive/[0.06] px-3 py-2.5 flex items-start gap-2">
            <AlertTriangle className="h-3.5 w-3.5 text-destructive shrink-0 mt-0.5" />
            <p className="text-xs text-destructive flex-1 leading-relaxed">{profileError}</p>
            <button onClick={() => setProfileError(null)} className="text-destructive/60 hover:text-destructive shrink-0 mt-0.5">
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
        )}

        {(newProfile || editingProfile) && !editingProfile && !siblingSourceName && (
          <div className="rounded-xl border border-border/70 bg-muted/20 p-2.5 space-y-2">
            <p className="text-[11px] font-medium text-foreground/80">从预设开始</p>
            <p className="text-[11px] text-muted-foreground -mt-1">选择常见提供商预填，或点「自定义」填写自己的服务</p>
            <div className="grid grid-cols-2 min-[360px]:grid-cols-3 sm:grid-cols-5 gap-1.5">
              {PROVIDER_PRESETS.map((preset) => (
                <button
                  key={preset.id}
                  type="button"
                  className={`text-left rounded-xl border px-2 sm:px-2.5 py-2 transition-all ${
                    newProfile && profileDraft.model === preset.model && profileDraft.base_url === preset.base_url
                      ? "border-[var(--em-primary)]/60 bg-[var(--em-primary)]/5 shadow-[0_0_0_1px_var(--em-primary-alpha-15)]"
                      : "border-border bg-background hover:border-[var(--em-primary)]/40 hover:bg-[var(--em-primary)]/5"
                  }`}
                  onClick={() => applyPresetToProfileDraft(preset)}
                >
                  <div className="flex items-center gap-1 sm:gap-1.5">
                    <ProviderLogo id={preset.id} />
                    <span className="text-[11px] sm:text-xs font-medium truncate">{preset.label}</span>
                  </div>
                  <p className="text-[10px] font-mono text-muted-foreground truncate mt-0.5 hidden sm:block">{preset.model}</p>
                  <a
                    href={preset.purchaseUrl}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="items-center gap-0.5 text-[10px] mt-1 hover:underline hidden sm:inline-flex"
                    style={{ color: "var(--em-primary)" }}
                    onClick={(e) => e.stopPropagation()}
                  >
                    获取 Key <ExternalLink className="h-2.5 w-2.5" />
                  </a>
                </button>
              ))}
              <button
                type="button"
                className={`text-left rounded-xl border px-2 sm:px-2.5 py-2 transition-all ${
                  newProfile && !PROVIDER_PRESETS.some((preset) =>
                    profileDraft.model === preset.model && profileDraft.base_url === preset.base_url,
                  )
                    ? "border-[var(--em-primary)]/60 bg-[var(--em-primary)]/5 shadow-[0_0_0_1px_var(--em-primary-alpha-15)]"
                    : "border-dashed border-border bg-background hover:border-[var(--em-primary)]/40 hover:bg-[var(--em-primary)]/5"
                }`}
                onClick={() => {
                  setProfileDraft({ ...EMPTY_PROFILE_DRAFT });
                  setProfileError(null);
                }}
              >
                <div className="flex items-center gap-1 sm:gap-1.5">
                  <span className="inline-flex h-4 w-4 items-center justify-center rounded-md border border-dashed text-[9px] text-muted-foreground">+</span>
                  <span className="text-[11px] sm:text-xs font-medium truncate">自定义</span>
                </div>
                <p className="text-[10px] text-muted-foreground truncate mt-0.5 hidden sm:block">填写自己的服务</p>
              </button>
            </div>
          </div>
        )}

        {(newProfile || editingProfile) && <ProfileEditorForm />}

        <div className="space-y-2">
            {groups.map((group) => {
              const isDefault = group.id === defaultGroupId;
              const isSelected = selectedId === group.id;
              const isExpanded = expandedProviderId === group.id;
              const unhealthy = group.profiles.some((p) => isModelUnhealthy(capsMap[p.name]));
              const highlighted = group.profiles.some((p) => highlightProfile === p.name);
              const first = group.profiles[0];
              const siblingSource = group.profiles.find((profile) => !isCodexProfile(profile));

              return (
                <div
                  key={group.id}
                  className={`rounded-xl border text-sm transition-colors ${
                    isDefault
                      ? "border-[var(--em-primary)] bg-[var(--em-primary)]/6"
                      : highlighted
                        ? "border-[var(--em-primary)]/50 bg-[var(--em-primary)]/5"
                        : unhealthy
                          ? "border-destructive/30 bg-destructive/3"
                          : "border-border/70 bg-card hover:border-border hover:bg-muted/20"
                  }`}
                >
                  <div
                    role="button"
                    tabIndex={0}
                    className="flex items-center gap-3 px-3 py-2.5 cursor-pointer"
                    onClick={() => handleSelectProvider(group)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        handleSelectProvider(group);
                      }
                    }}
                  >
                    <ProviderAvatar
                      id={group.id.startsWith("custom:") ? "custom" : group.id}
                      label={group.label}
                      color={group.color}
                      className="h-8 w-8"
                    />
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-1.5 flex-wrap">
                        <span className="font-semibold text-sm">{group.label}</span>
                        {group.profiles.some(isCodexProfile) && (
                          <span className="inline-flex items-center gap-0.5 rounded-full px-1.5 py-0.5 text-[9px] font-semibold bg-[var(--em-primary)]/15 text-[var(--em-primary)]">
                            <Lock className="h-2 w-2" />
                            OAuth
                          </span>
                        )}
                      </div>
                      <p className={`text-[11px] mt-0.5 ${unhealthy ? "text-destructive" : "text-muted-foreground"}`}>
                        <span className={`inline-block h-1.5 w-1.5 rounded-full mr-1.5 align-middle ${
                          unhealthy ? "bg-destructive" : isProfileConnected(first) ? "bg-emerald-500" : "bg-muted-foreground/40"
                        }`} />
                        {providerStatusText(group, unhealthy)}
                      </p>
                    </div>
                    <div className="flex items-center gap-1 shrink-0">
                      {isDefault && (
                        <span className="inline-flex items-center gap-0.5 rounded-full px-1.5 py-0.5 text-[10px] font-medium bg-[var(--em-primary)]/12 text-[var(--em-primary)]">
                          默认
                        </span>
                      )}
                      {isSelected && <Check className="h-3.5 w-3.5" style={{ color: "var(--em-primary)" }} />}
                      {siblingSource && (
                          <button
                            type="button"
                            title="添加模型"
                            aria-label="添加模型"
                            className="h-7 w-7 flex items-center justify-center rounded-md text-muted-foreground hover:text-foreground hover:bg-muted/60"
                            onClick={(e) => {
                              e.stopPropagation();
                              setExpandedProviderId(group.id);
                              setSelectedProviderId(group.id);
                              beginAddSiblingProfile(siblingSource);
                            }}
                          >
                            <Plus className="h-3.5 w-3.5" />
                          </button>
                      )}
                      {first && !isCodexProfile(first) && (
                        <button
                          title="编辑"
                          className="h-7 w-7 flex items-center justify-center rounded-md text-muted-foreground hover:text-foreground hover:bg-muted/60"
                          onClick={(e) => {
                            e.stopPropagation();
                            setExpandedProviderId(group.id);
                            setSelectedProviderId(group.id);
                            beginEdit(group.profiles.length === 1 ? first : group.profiles[0]);
                          }}
                        >
                          <Pencil className="h-3.5 w-3.5" />
                        </button>
                      )}
                      <ChevronRight
                        className={`h-3.5 w-3.5 text-muted-foreground transition-transform ${isExpanded ? "rotate-90" : ""}`}
                      />
                    </div>
                  </div>

                  {isExpanded && (
                    <div className="px-3 pb-3 space-y-2 border-t border-border/50 pt-2">
                      {group.profiles.map((p) => (
                        <ProviderMemberRow
                          key={p.name}
                          profile={p}
                          isActive={p.name === activeName}
                          isHighlighted={highlightProfile === p.name}
                          onEdit={() => beginEdit(p)}
                          rowRef={(el) => { profileCardRefs.current[p.name] = el; }}
                          caps={capsMap[p.name]}
                          probing={probingKey === p.name}
                          activating={activatingProfile === p.name}
                          onActivate={() => handleActivateProfile(p)}
                          onProbe={() => handleProbeOne(p.name)}
                          onDelete={() => handleDeleteProfile(p.name)}
                        />
                      ))}
                      {siblingSource && (
                        <button
                          type="button"
                          className="w-full rounded-lg border border-dashed border-border/80 px-2.5 py-2 text-xs text-muted-foreground hover:text-foreground hover:border-[var(--em-primary)]/40 hover:bg-[var(--em-primary)]/5 transition-colors flex items-center gap-2"
                          onClick={() => beginAddSiblingProfile(siblingSource)}
                        >
                          <Plus className="h-3.5 w-3.5 shrink-0" style={{ color: "var(--em-primary)" }} />
                          <span className="font-medium text-foreground/80">添加模型</span>
                          <span className="text-[10px] truncate">沿用此提供商的连接</span>
                        </button>
                      )}
                    </div>
                  )}
                </div>
              );
            })}

            {groups.length === 0 && !newProfile && (
              <div className="flex flex-col items-center gap-2 py-8 text-center">
                <div className="w-10 h-10 rounded-xl bg-muted/50 flex items-center justify-center">
                  <Bot className="h-5 w-5 text-muted-foreground/50" />
                </div>
                <p className="text-xs text-muted-foreground">还没有添加提供商</p>
                <p className="text-[10px] text-muted-foreground/60">点击右上角「添加提供商」开始配置</p>
              </div>
            )}
        </div>
      </div>
    </SettingsFoldSection>
  );
}

function ProviderMemberRow({
  profile,
  isActive,
  isHighlighted,
  caps,
  probing,
  activating,
  onEdit,
  onActivate,
  onProbe,
  onDelete,
  rowRef,
}: {
  profile: ProfileEntry;
  isActive: boolean;
  isHighlighted: boolean;
  caps?: ModelCapabilities;
  probing: boolean;
  activating: boolean;
  onEdit: () => void;
  onActivate: () => void;
  onProbe: () => void;
  onDelete: () => void;
  rowRef: (el: HTMLDivElement | null) => void;
}) {
  const isUnhealthy = isModelUnhealthy(caps);
  const codex = isCodexProfile(profile);
  const providerId = inferProfileProvider(profile);
  const providerColor = getProviderBrandColor(providerId);

  return (
    <div
      ref={rowRef}
      className={`rounded-lg border px-2.5 py-2 ${
        isActive
          ? "border-[var(--em-primary)]/40 bg-[var(--em-primary)]/5"
          : isHighlighted
            ? "border-[var(--em-primary)]/30 bg-[var(--em-primary)]/5"
            : isUnhealthy
              ? "border-destructive/30 bg-destructive/3"
              : "border-border/60"
      }`}
    >
      <div className="flex items-center gap-2">
        <div
          className="h-6 w-6 rounded-md flex items-center justify-center shrink-0"
          style={{ backgroundColor: withAlpha(providerColor, "1A"), color: providerColor }}
        >
          {codex ? <Lock className="h-3 w-3" /> : providerId ? <ProviderLogo id={providerId} color={providerColor} /> : <span className="text-[9px] font-bold">{profile.name.slice(0, 2).toUpperCase()}</span>}
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-1.5">
            <span className="text-xs font-medium truncate">{profile.name}</span>
            {isActive && (
              <span className="inline-flex items-center gap-0.5 rounded-full px-1.5 py-0.5 text-[9px] font-semibold bg-amber-500/15 text-amber-600 dark:text-amber-400">
                <Crown className="h-2.5 w-2.5" />
                当前
              </span>
            )}
          </div>
          <p className="text-[10px] font-mono text-muted-foreground truncate">
            {formatModelIdForDisplay(profile.model)}
          </p>
          {profile.description && (
            <p className="text-[10px] text-muted-foreground truncate">{profile.description}</p>
          )}
        </div>
        <div className="hidden sm:block">
          {!isUnhealthy && <CapabilityBadges caps={caps ?? null} />}
        </div>
        <div className="flex items-center gap-0.5 shrink-0">
          {!isActive && (
            <button
              title="设为默认"
              className="h-7 w-7 flex items-center justify-center rounded-md text-muted-foreground hover:text-foreground hover:bg-muted/60 disabled:opacity-40"
              onClick={onActivate}
              disabled={activating}
            >
              {activating ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Crown className="h-3.5 w-3.5" />}
            </button>
          )}
          <button
            title="探测能力"
            className="h-7 w-7 flex items-center justify-center rounded-md text-muted-foreground hover:text-foreground hover:bg-muted/60 disabled:opacity-40"
            onClick={onProbe}
            disabled={probing}
          >
            {probing ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Zap className="h-3.5 w-3.5" />}
          </button>
          {!codex && (
            <button
              title="编辑"
              className="h-7 w-7 flex items-center justify-center rounded-md text-muted-foreground hover:text-foreground hover:bg-muted/60"
              onClick={onEdit}
            >
              <Pencil className="h-3.5 w-3.5" />
            </button>
          )}
          <button
            title="删除"
            className="h-7 w-7 flex items-center justify-center rounded-md text-muted-foreground hover:text-destructive hover:bg-destructive/10"
            onClick={onDelete}
          >
            <Trash2 className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>
      {isUnhealthy && !codex && (
        <p className="text-[10px] text-destructive mt-1 truncate" title={getHealthError(caps)}>
          {getHealthError(caps) || "连接失败"}
        </p>
      )}
    </div>
  );
}
