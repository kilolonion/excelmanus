"use client";

import { Check, ChevronDown, Gauge, Loader2 } from "lucide-react";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Switch } from "@/components/ui/switch";
import {
  jevCatalogOptions,
  jevEntryStatus,
  jevModelsForProvider,
  jevRoleDetail,
  resolveJevCatalogModel,
} from "@/lib/jev-settings";
import { SettingsFoldSection } from "../SettingsFoldSection";
import { JevAvatar, JevFieldRow, JevGateSelect, JevSaveBar, JevStatusChip } from "./jev-widgets";
import { useJevSettings } from "./useJevSettings";

const PICKER_TRIGGER_CLASS =
  "inline-flex items-center gap-2 h-9 w-[13.75rem] shrink-0 rounded-lg border border-input bg-background px-2.5 text-left text-xs hover:bg-muted/40 disabled:opacity-50 disabled:cursor-not-allowed";

export function JevRoleSection() {
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
    configured,
    hasRoleChanges,
    saveRole,
  } = useJevSettings();

  const status = jevEntryStatus({
    configured,
    enabled: draft.jev_enabled,
    enforceReady: runtime?.jev_enforce_ready,
  });
  const current = resolveJevCatalogModel(draft.jev_model, providers);
  const options = jevCatalogOptions(draft.jev_model, providers);
  const detail = jevRoleDetail({
    configured,
    model: draft.jev_model,
    providerName: activeProvider?.name,
  });
  const empty = providers.length === 0;

  const handleSelectModel = (modelId: string) => {
    const owner = providers.find((provider) =>
      jevModelsForProvider(provider).some((item) => item.id === modelId),
    );
    setDraft((prev) => ({
      ...prev,
      jev_model: modelId,
      jev_active_provider: owner?.id || prev.jev_active_provider,
    }));
  };

  return (
    <SettingsFoldSection
      title="决策模型"
      description="从已添加的决策提供商中选择 Jev 模型，并配置评估行为"
      icon={<Gauge className="h-4 w-4" style={{ color: "var(--em-primary)" }} />}
      coachId="coach-settings-jev-model"
    >
      <div className="px-3 pb-3">
        {loading && !runtime ? (
          <div className="flex items-center justify-center gap-2 py-8 text-xs text-muted-foreground">
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
            加载配置…
          </div>
        ) : (
          <>
            <div className="rounded-lg border border-border/70 divide-y divide-border/70 overflow-hidden">
              <div className="flex flex-col sm:flex-row sm:items-center gap-2 px-3 py-3">
                <div className="flex items-start gap-2 flex-1 min-w-0">
                  <JevAvatar className="h-7 w-7 text-[12px]" />
                  <div className="min-w-0">
                    <div className="flex items-center gap-1.5 flex-wrap">
                      <p className="text-sm font-medium">决策模型</p>
                      {activeProvider && (
                        <span className="rounded-full border border-border/80 px-1.5 py-px text-[10px] font-medium text-muted-foreground">
                          {activeProvider.name}
                        </span>
                      )}
                      <JevStatusChip tone={status.tone} chip={status.chip} />
                    </div>
                    <p className="text-[11px] text-muted-foreground mt-0.5">{detail}</p>
                  </div>
                </div>
                <DropdownMenu modal={false}>
                  <DropdownMenuTrigger asChild disabled={empty}>
                    <button type="button" className={PICKER_TRIGGER_CLASS}>
                      <JevAvatar className="h-5 w-5 text-[10px] rounded-md" />
                      <span className="flex-1 truncate">{empty ? "请先添加提供商" : current.label}</span>
                      <ChevronDown className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
                    </button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="end" className="w-[13.75rem] max-w-[calc(100vw-2rem)]">
                    {options.map((option) => {
                      const selected = option.id === current.id;
                      const owner = providers.find((provider) =>
                        jevModelsForProvider(provider).some((item) => item.id === option.id),
                      );
                      return (
                        <DropdownMenuItem
                          key={`${owner?.id || "any"}:${option.id}`}
                          className="gap-2 text-xs"
                          onClick={() => handleSelectModel(option.id)}
                        >
                          <JevAvatar className="h-5 w-5 text-[10px] rounded-md" />
                          <span className="flex-1 min-w-0">
                            <span className="block truncate">{option.label}</span>
                            <span className="block text-[10px] text-muted-foreground truncate">
                              {owner ? `${owner.name} · ${option.hint}` : option.hint}
                            </span>
                          </span>
                          {selected && <Check className="h-3.5 w-3.5" style={{ color: "var(--em-primary)" }} />}
                        </DropdownMenuItem>
                      );
                    })}
                  </DropdownMenuContent>
                </DropdownMenu>
              </div>
            </div>

            <div className="mt-3 rounded-lg border border-border/70 overflow-hidden">
              <div className="divide-y divide-border/70">
                <JevFieldRow label="总开关" desc="关闭后不评估。仅观察只记录结果；完成标定后，生效模式才会改变执行面。">
                  <JevGateSelect
                    value={draft.jev_enabled}
                    onChange={(value) => setDraft((prev) => ({ ...prev, jev_enabled: value }))}
                  />
                </JevFieldRow>
              </div>

              <div className="px-3 pt-3 pb-1">
                <p className="text-[11px] font-medium text-muted-foreground">功能开关</p>
                <p className="text-[11px] text-muted-foreground mt-0.5">
                  总开关关闭时，下面的选项不会生效。
                </p>
              </div>

              <div className="divide-y divide-border/70">
                <JevFieldRow label="本轮工具" desc="按任务收窄本轮可用工具，并置顶可能相关的技能。">
                  <JevGateSelect
                    value={draft.jev_exposure}
                    onChange={(value) => setDraft((prev) => ({ ...prev, jev_exposure: value }))}
                  />
                </JevFieldRow>
                <JevFieldRow label="结果整理" desc="过长的工具结果会被收起，需要时再取回。">
                  <JevGateSelect
                    value={draft.jev_observation}
                    onChange={(value) => setDraft((prev) => ({ ...prev, jev_observation: value }))}
                  />
                </JevFieldRow>
                <JevFieldRow label="界面建议" desc="完成后是否打开表格或侧栏。">
                  <Switch
                    checked={draft.jev_ui_hint}
                    onCheckedChange={(checked) =>
                      setDraft((prev) => ({ ...prev, jev_ui_hint: checked }))
                    }
                  />
                </JevFieldRow>
                <JevFieldRow label="模式建议" desc="需要换模式时先询问。">
                  <Switch
                    checked={draft.jev_mode_hint}
                    onCheckedChange={(checked) =>
                      setDraft((prev) => ({ ...prev, jev_mode_hint: checked }))
                    }
                  />
                </JevFieldRow>
                <JevFieldRow label="临时代码模式" desc="本轮可以临时改用代码模式，不会改你的默认设置。">
                  <Switch
                    checked={draft.jev_present_as_auto}
                    onCheckedChange={(checked) =>
                      setDraft((prev) => ({ ...prev, jev_present_as_auto: checked }))
                    }
                  />
                </JevFieldRow>
              </div>
            </div>

            <p className="text-[11px] text-muted-foreground mt-2">
              模型列表来自「供应商」里已添加的 TypeSafe、Vercel 或自定义决策提供商。
            </p>

            <JevSaveBar
              hasChanges={hasRoleChanges}
              saving={saving}
              saved={saved}
              error={error}
              onSave={() => void saveRole()}
            />
          </>
        )}
      </div>
    </SettingsFoldSection>
  );
}
