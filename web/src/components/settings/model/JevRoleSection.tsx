"use client";

import { Check, ChevronDown, FlaskConical, Info, Loader2 } from "lucide-react";
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
  { value: "enforce", title: "全面启用", description: "开启全部环节，评估建议直接接入任务" },
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

  if (!runtime?.jev_experimental_enabled) return null;

  const status = jevEntryStatus({
    configured,
    enabled: draft.jev_enabled,
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
      icon={<FlaskConical className="h-4 w-4" style={{ color: "var(--em-gold)" }} />}
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
                <p className="mt-1 text-xs leading-5 text-muted-foreground">关闭总开关会停用所有 Jev 环节；启用后默认全面接入，也可以在下方单独关闭某个环节。</p>
                <div role="group" aria-label="Jev 介入方式" className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-2">
                  {MODES.map((mode) => <button key={mode.value} type="button" aria-pressed={draft.jev_enabled === mode.value} onClick={() => handleMasterGateChange(mode.value)} className={cn("rounded-xl border p-3 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring", draft.jev_enabled === mode.value ? "border-[var(--em-primary)] bg-[var(--em-primary-alpha-06)]" : "border-border hover:bg-muted/40")}><span className="flex items-center justify-between gap-2 text-xs font-semibold">{mode.title}{draft.jev_enabled === mode.value && <Check className="size-3.5 text-[var(--em-primary)]" />}</span><span className="mt-1.5 block text-[11px] leading-5 text-muted-foreground">{mode.description}</span></button>)}
                </div>
                {draft.jev_enabled === "enforce" && <p className="mt-3 flex items-start gap-2 rounded-lg bg-muted/50 p-2.5 text-xs leading-5 text-muted-foreground"><Info className="mt-0.5 size-3.5 shrink-0" />上下文、工具、审批、结果整理、恢复和界面建议都会按下方环节开关直接接入。高风险自动放行还需要 approval pack 完成签名标定。</p>}
                {draft.jev_enabled === "off" && <p className="mt-3 text-xs text-muted-foreground">总开关已关闭，下方配置将在重新开启后使用。</p>}
              </div>

              <div className="px-3 pt-3 pb-1">
                <p className="text-[11px] font-medium text-muted-foreground">功能开关</p>
                <p className="text-[11px] text-muted-foreground mt-0.5">
                  总开关关闭或某个环节关闭后，该环节不会评估；开启的环节会直接接入任务。
                </p>
              </div>

              <div className="divide-y divide-border/70">
                <JevFieldRow label="本轮工具与上下文" desc="向主模型建议工作区、表格位置和需澄清的信息；工具预加载和技能置顶一并应用。">
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
                <JevFieldRow label="写入验证" desc="写入回合结束后检查用户要求是否覆盖，并提供必要的后续动作。">
                  <JevGateSelect
                    label="写入验证的模式"
                    value={draft.jev_verification}
                    onChange={(value) => setDraft((prev) => ({ ...prev, jev_verification: value }))}
                  />
                </JevFieldRow>
                <JevFieldRow label="失败恢复" desc="熔断后提供检查、询问或停止建议。">
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
                <JevFieldRow label="模型名智能匹配" desc="添加或保存模型档案时，自动把 Model ID 匹配到已知模型名（如 gpt-5-6-sol → gpt-5.6-sol），并继承其上下文窗口与能力配置。不会改写发给上游的 Model ID；开启时会为已有档案补绑。">
                  <Switch
                    aria-label="模型名智能匹配"
                    checked={draft.model_canonical_match_enabled}
                    onCheckedChange={(checked) =>
                      setDraft((prev) => ({ ...prev, model_canonical_match_enabled: checked }))
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
