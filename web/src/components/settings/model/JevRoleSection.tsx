"use client";

import { Check, ChevronDown, Gauge, Info, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Switch } from "@/components/ui/switch";
import {
  jevEntryStatus,
  jevModelsForProvider,
  jevRoleDetail,
  resolveJevCatalogModel,
  type JevGate,
} from "@/lib/jev-settings";
import { SettingsFoldSection } from "../SettingsFoldSection";
import { JevAvatar, JevFieldRow, JevGateSelect, JevSaveBar, JevStatusChip } from "./jev-widgets";
import { useJevSettings } from "./useJevSettings";
import { requestModelSubTab } from "./model-subtab";
import { cn } from "@/lib/utils";

const PICKER_TRIGGER_CLASS =
  "inline-flex items-center gap-2 h-10 w-full sm:w-[13.75rem] shrink-0 rounded-lg border border-input bg-background px-2.5 text-left text-xs hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50 disabled:cursor-not-allowed";

const MODES: { value: JevGate; title: string; description: string }[] = [
  { value: "off", title: "关闭", description: "暂停全部评估，保留各项设置" },
  { value: "shadow", title: "仅观察", description: "开启全部评估，只记录建议" },
  { value: "enforce", title: "辅助执行", description: "开启全部功能，符合条件时应用建议" },
];

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
    reload,
    resetDraft,
  } = useJevSettings();

  const status = jevEntryStatus({
    configured,
    enabled: draft.jev_enabled,
    enforceReady: runtime?.jev_enforce_ready,
  });
  const current = resolveJevCatalogModel(draft.jev_model, providers);
  const options = providers.flatMap((provider) => {
    const models = [...jevModelsForProvider(provider)];
    if (provider.id === activeProvider?.id && !models.some((model) => model.id === draft.jev_model)) {
      models.unshift({ id: draft.jev_model, label: draft.jev_model, hint: "当前自定义版本" });
    }
    return models.map((model) => ({ ...model, provider }));
  });
  const detail = jevRoleDetail({
    configured,
    model: draft.jev_model,
    providerName: activeProvider?.name,
  });
  const empty = providers.length === 0;

  const handleMasterGateChange = (value: JevGate) => {
    setDraft((prev) => {
      if (value === "off") return { ...prev, jev_enabled: value };

      return {
        ...prev,
        jev_enabled: value,
        jev_exposure: value,
        jev_observation: value,
        jev_verification: value,
        jev_recovery: value,
        jev_ui_hint: true,
        jev_mode_hint: true,
      };
    });
  };

  const handleSelectModel = (providerId: string, modelId: string) => {
    setDraft((prev) => ({
      ...prev,
      jev_model: modelId,
      jev_active_provider: providerId,
    }));
  };

  return (
    <SettingsFoldSection
      title="Jev 任务辅助"
      description="理解任务上下文、整理工具结果，让每次介入都有据可查"
      icon={<Gauge className="h-4 w-4" style={{ color: "var(--em-primary)" }} />}
      coachId="coach-settings-jev-model"
    >
      <div className="px-3 pb-3">
        {loading && !runtime ? (
          <div className="flex items-center justify-center gap-2 py-8 text-xs text-muted-foreground">
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
            加载配置…
          </div>
        ) : !runtime ? (
          <div className="rounded-xl bg-muted/40 p-5 text-center"><p role="alert" className="text-xs text-destructive">{error || "暂时无法读取配置"}</p><Button className="mt-3" size="sm" variant="outline" onClick={() => void reload()}>重新加载</Button></div>
        ) : (
          <fieldset disabled={saving} className="min-w-0">
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
                      <JevStatusChip tone={hasRoleChanges ? "idle" : status.tone} chip={hasRoleChanges ? "待保存" : status.chip} />
                    </div>
                    <p className="text-[11px] text-muted-foreground mt-0.5">{detail}</p>
                  </div>
                </div>
                <DropdownMenu modal={false}>
                  <DropdownMenuTrigger asChild disabled={empty}>
                    <button type="button" aria-label="选择 Jev 提供商与模型" className={PICKER_TRIGGER_CLASS}>
                      <JevAvatar className="h-5 w-5 text-[10px] rounded-md" />
                      <span className="flex-1 truncate">{empty ? "请先添加提供商" : current.label}</span>
                      <ChevronDown className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
                    </button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="end" className="w-[13.75rem] max-w-[calc(100vw-2rem)]">
                    {options.map((option) => {
                      const selected = option.id === current.id && option.provider.id === activeProvider?.id;
                      const owner = option.provider;
                      return (
                        <DropdownMenuItem
                          key={`${owner?.id || "any"}:${option.id}`}
                          className="gap-2 text-xs"
                          onClick={() => handleSelectModel(owner.id, option.id)}
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

            {!configured && <div className="mt-3 flex flex-wrap items-center gap-2 rounded-xl bg-muted/50 p-3 text-xs"><p className="mr-auto text-muted-foreground">先添加提供商密钥，即可开始使用 Jev。</p><Button size="sm" variant="outline" onClick={() => requestModelSubTab("providers")}>前往配置提供商</Button></div>}

            <div className="mt-3 rounded-lg border border-border/70 overflow-hidden">
              <div className="p-3">
                <p className="text-sm font-medium">介入方式</p>
                <p className="mt-1 text-xs leading-5 text-muted-foreground">初次使用可选「仅观察」，先了解 Jev 的判断。切换后可在下方逐项调整。</p>
                <div role="group" aria-label="Jev 介入方式" className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-3">
                  {MODES.map((mode) => <button key={mode.value} type="button" aria-pressed={draft.jev_enabled === mode.value} onClick={() => handleMasterGateChange(mode.value)} className={cn("rounded-xl border p-3 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring", draft.jev_enabled === mode.value ? "border-[var(--em-primary)] bg-[var(--em-primary-alpha-06)]" : "border-border hover:bg-muted/40")}><span className="flex items-center justify-between gap-2 text-xs font-semibold">{mode.title}{draft.jev_enabled === mode.value && <Check className="size-3.5 text-[var(--em-primary)]" />}</span><span className="mt-1.5 block text-[11px] leading-5 text-muted-foreground">{mode.description}</span></button>)}
                </div>
                {draft.jev_enabled === "enforce" && <p className="mt-3 flex items-start gap-2 rounded-lg bg-muted/50 p-2.5 text-xs leading-5 text-muted-foreground"><Info className="mt-0.5 size-3.5 shrink-0" />{runtime.jev_enforce_ready ? "上下文建议会直接提供给主模型；工具、审批与界面调整按下方各项设置应用。" : "上下文建议会直接提供给主模型；其余介入仍在验证中，暂只记录建议、不改变任务。"}</p>}
                {draft.jev_enabled === "off" && <p className="mt-3 text-xs text-muted-foreground">总开关已关闭，下方配置将在重新开启后使用。</p>}
              </div>

              <div className="px-3 pt-3 pb-1">
                <p className="text-[11px] font-medium text-muted-foreground">功能开关</p>
                <p className="text-[11px] text-muted-foreground mt-0.5">
                  总开关为「仅观察」时，各项都只记录建议。实际影响以对话中的 Jev 时间线为准。
                </p>
              </div>

              <div className="divide-y divide-border/70">
                <JevFieldRow label="本轮工具与上下文" desc={runtime.jev_enforce_ready ? "向主模型建议工作区、表格位置和需澄清的信息；工具预加载和技能置顶一并应用。" : "向主模型建议工作区、表格位置和需澄清的信息；工具预加载和技能置顶仍在验证中，暂只记录。"}>
                  <JevGateSelect
                    label="本轮工具与上下文的模式"
                    value={draft.jev_exposure}
                    onChange={(value) => setDraft((prev) => ({ ...prev, jev_exposure: value }))}
                  />
                </JevFieldRow>
                <JevFieldRow label="结果整理" desc="过长的工具结果会被收起，需要时再取回。">
                  <JevGateSelect
                    label="结果整理的模式"
                    value={draft.jev_observation}
                    onChange={(value) => setDraft((prev) => ({ ...prev, jev_observation: value }))}
                  />
                </JevFieldRow>
                <JevFieldRow label="写入验证" desc="写入回合结束后，观察 Jev 是否认为用户要求已完成；当前只记录，不改变执行。">
                  <JevGateSelect
                    label="写入验证的模式"
                    value={draft.jev_verification}
                    onChange={(value) => setDraft((prev) => ({ ...prev, jev_verification: value }))}
                  />
                </JevFieldRow>
                <JevFieldRow label="失败恢复" desc="熔断后记录恢复建议；当前不会自动重试或停止循环。">
                  <JevGateSelect
                    label="失败恢复的模式"
                    value={draft.jev_recovery}
                    onChange={(value) => setDraft((prev) => ({ ...prev, jev_recovery: value }))}
                  />
                </JevFieldRow>
                <JevFieldRow label="界面建议" desc="完成后是否打开表格或侧栏。">
                  <Switch
                    aria-label="界面建议"
                    checked={draft.jev_ui_hint}
                    onCheckedChange={(checked) =>
                      setDraft((prev) => ({ ...prev, jev_ui_hint: checked }))
                    }
                  />
                </JevFieldRow>
                <JevFieldRow label="模式建议" desc="需要换模式时先询问。">
                  <Switch
                    aria-label="模式建议"
                    checked={draft.jev_mode_hint}
                    onCheckedChange={(checked) =>
                      setDraft((prev) => ({ ...prev, jev_mode_hint: checked }))
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
              onReset={resetDraft}
            />
          </fieldset>
        )}
      </div>
    </SettingsFoldSection>
  );
}
