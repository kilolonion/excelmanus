"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Brain, Check, ChevronDown, Loader2, MessageSquare, Settings2 } from "lucide-react";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { apiGet, apiPut } from "@/lib/api";
import { settingsCache } from "@/lib/settings-cache";
import { formatModelIdForDisplay } from "@/lib/model-display";
import { SettingsFoldSection } from "../SettingsFoldSection";
import { useAdminModel } from "./admin-model-context";
import { ProviderLogo } from "./ProviderLogo";
import {
  findProfileByModelId,
  formatProviderModelLabel,
  inferProfileProvider,
} from "./helpers";
import type { ProfileEntry } from "./types";

type RuntimeSnippet = {
  memory_maintenance_model?: string;
};

const PICKER_TRIGGER_CLASS =
  "inline-flex items-center gap-2 h-9 w-[13.75rem] shrink-0 rounded-lg border border-input bg-background px-2.5 text-left text-xs hover:bg-muted/40 disabled:opacity-50 disabled:cursor-not-allowed";

function ModelPicker({
  valueLabel,
  providerId,
  disabled,
  emptyText,
  onSelect,
  profiles,
  selectedName,
  extraOption,
}: {
  valueLabel: string;
  providerId: string | null;
  disabled: boolean;
  emptyText: string;
  onSelect: (profile: ProfileEntry) => void;
  profiles: ProfileEntry[];
  selectedName?: string | null;
  extraOption?: { label: string; selected: boolean; onSelect: () => void };
}) {
  return (
    <DropdownMenu modal={false}>
      <DropdownMenuTrigger asChild disabled={disabled}>
        <button type="button" className={PICKER_TRIGGER_CLASS}>
          {providerId ? <ProviderLogo id={providerId} /> : <span className="w-4 shrink-0" />}
          <span className="flex-1 truncate">{disabled ? emptyText : valueLabel}</span>
          <ChevronDown className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-[13.75rem] max-w-[calc(100vw-2rem)]">
        {extraOption && (
          <DropdownMenuItem className="gap-2 text-xs" onClick={extraOption.onSelect}>
            <span className="w-4 shrink-0" />
            <span className="flex-1">{extraOption.label}</span>
            {extraOption.selected && <Check className="h-3.5 w-3.5" style={{ color: "var(--em-primary)" }} />}
          </DropdownMenuItem>
        )}
        {profiles.map((profile) => {
          const id = inferProfileProvider(profile);
          const selected = profile.name === selectedName;
          return (
            <DropdownMenuItem
              key={profile.name}
              className="gap-2 text-xs"
              onClick={() => onSelect(profile)}
            >
              {id ? <ProviderLogo id={id} /> : <span className="w-4" />}
              <span className="flex-1 min-w-0">
                <span className="block truncate">{formatProviderModelLabel(profile)}</span>
                <span className="block text-[10px] text-muted-foreground truncate">{profile.name}</span>
              </span>
              {selected && <Check className="h-3.5 w-3.5" style={{ color: "var(--em-primary)" }} />}
            </DropdownMenuItem>
          );
        })}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

export function RoleModelSection() {
  const { config, handleActivateProfile, activatingProfile } = useAdminModel();
  const profiles = config?.profiles || [];
  const active = profiles.find((p) => p.name === config?.active) || profiles[0] || null;

  const [memoryModel, setMemoryModel] = useState("");
  const [memorySaving, setMemorySaving] = useState(false);

  const loadRuntime = useCallback(async () => {
    const cached = settingsCache.get<RuntimeSnippet>("/config/runtime");
    if (cached) {
      setMemoryModel(cached.memory_maintenance_model || "");
      return;
    }
    try {
      const data = await apiGet<RuntimeSnippet>("/config/runtime", { direct: true });
      settingsCache.set("/config/runtime", data);
      setMemoryModel(data.memory_maintenance_model || "");
    } catch {
      // 后端未就绪时保持跟随聊天模型
    }
  }, []);

  useEffect(() => {
    void loadRuntime();
  }, [loadRuntime]);

  const saveMemoryModel = async (modelId: string) => {
    setMemorySaving(true);
    const previous = memoryModel;
    setMemoryModel(modelId);
    try {
      await apiPut("/config/runtime", { memory_maintenance_model: modelId }, { direct: true });
      const cached = settingsCache.get<RuntimeSnippet>("/config/runtime");
      settingsCache.set("/config/runtime", { ...(cached || {}), memory_maintenance_model: modelId });
    } catch {
      setMemoryModel(previous);
    } finally {
      setMemorySaving(false);
    }
  };

  const memoryProfile = useMemo(
    () => (memoryModel ? findProfileByModelId(profiles, memoryModel) : undefined),
    [memoryModel, profiles],
  );

  const empty = profiles.length === 0;
  const busy = Boolean(activatingProfile) || memorySaving;

  return (
    <SettingsFoldSection
      title="模型配置"
      description="从已添加的提供商中，为不同任务选择模型"
      icon={<Settings2 className="h-4 w-4" style={{ color: "var(--em-primary)" }} />}
      coachId="coach-settings-model-roles"
    >
      <div className="px-3 pb-3">
        <div className="rounded-lg border border-border/70 divide-y divide-border/70 overflow-hidden">
          <div className="flex flex-col sm:flex-row sm:items-center gap-2 px-3 py-3">
            <div className="flex items-start gap-2 flex-1 min-w-0">
              <MessageSquare className="h-4 w-4 mt-0.5 shrink-0 text-muted-foreground" />
              <div className="min-w-0">
                <p className="text-sm font-medium">聊天模型</p>
                <p className="text-[11px] text-muted-foreground">处理日常对话、子代理与上下文压缩</p>
              </div>
            </div>
            <div className="flex items-center justify-end gap-2">
              {busy && activatingProfile && <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />}
              <ModelPicker
                valueLabel={active ? formatModelIdForDisplay(active.model) : "未选择"}
                providerId={active ? inferProfileProvider(active) : null}
                disabled={empty}
                emptyText="请先添加提供商"
                profiles={profiles}
                selectedName={active?.name}
                onSelect={(profile) => { void handleActivateProfile(profile); }}
              />
            </div>
          </div>

          <div className="flex flex-col sm:flex-row sm:items-center gap-2 px-3 py-3">
            <div className="flex items-start gap-2 flex-1 min-w-0">
              <Brain className="h-4 w-4 mt-0.5 shrink-0 text-muted-foreground" />
              <div className="min-w-0">
                <p className="text-sm font-medium">记忆模型</p>
                <p className="text-[11px] text-muted-foreground">用于记忆维护；可跟随聊天模型</p>
              </div>
            </div>
            <div className="flex items-center justify-end gap-2">
              {memorySaving && <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />}
              <ModelPicker
                valueLabel={
                  memoryModel
                    ? (memoryProfile ? formatModelIdForDisplay(memoryProfile.model) : memoryModel)
                    : "跟随聊天模型"
                }
                providerId={memoryProfile ? inferProfileProvider(memoryProfile) : null}
                disabled={empty}
                emptyText="请先添加提供商"
                profiles={profiles}
                selectedName={memoryProfile?.name}
                extraOption={{
                  label: "跟随聊天模型",
                  selected: !memoryModel,
                  onSelect: () => { void saveMemoryModel(""); },
                }}
                onSelect={(profile) => { void saveMemoryModel(profile.model); }}
              />
            </div>
          </div>
        </div>
        <p className="text-[11px] text-muted-foreground mt-2">
          模型列表来自「供应商」中已添加的提供商。对话、子代理和压缩使用聊天模型。
        </p>
      </div>
    </SettingsFoldSection>
  );
}
