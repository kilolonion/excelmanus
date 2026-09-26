"use client";

import { useEffect, useMemo, useState } from "react";
import { Crown, Loader2, Plus, Settings2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { glassMenuItemClass, glassMenuPanelClass } from "@/components/ui/menu-panel";
import { SettingsFoldSection } from "../SettingsFoldSection";
import { GlassSelect } from "./form-widgets";
import { useAdminModel } from "./admin-model-context";
import { ProviderAvatar } from "./ProviderLogo";
import { formatProviderModelLabel, getProviderBrandColor, inferProfileProvider } from "./helpers";
import { ModelConfigForm } from "./ModelConfigForm";
import { requestModelSubTab, takePendingModelProfile, takePendingNewModelSource } from "./model-subtab";
import { AdvancedDiagnosticsPanel } from "./AdvancedDiagnosticsPanel";
import { JevRoleSection } from "./JevRoleSection";

/**
 * 模型配置：一个模型 = 一张配置卡 = 一份表单。
 * 模型切换、默认模型、添加模型都在卡片顶部完成，
 * 模型参数、能力探测与思考等级在下方同一份表单里一次保存。
 */
export function RoleModelSection() {
  const {
    config,
    profileBusy,
    beginEditProfile,
    beginAddSiblingProfile,
    editingProfile,
    newProfile,
    siblingSourceName,
    handleActivateProfile,
    activatingProfile,
  } = useAdminModel();

  const profiles = useMemo(() => config?.profiles || [], [config?.profiles]);
  const active = profiles.find((profile) => profile.name === config?.active) || profiles[0] || null;
  const [pendingNewSource] = useState<string | null>(() => takePendingNewModelSource());
  const [selectedName, setSelectedName] = useState<string | null>(() => takePendingModelProfile() ?? pendingNewSource);

  // 供应商页「添加模型」会带着来源连接跳进来；只消费一次，避免重渲染后重复打开。
  useEffect(() => {
    if (!pendingNewSource) return;
    const source = profiles.find((profile) => profile.name === pendingNewSource);
    if (source) beginAddSiblingProfile(source);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingNewSource]);

  // 选中项与表单保持同一个目标：保存重命名后跟随新名字，删除后回退到下一个模型。
  useEffect(() => {
    if (newProfile || profiles.length === 0) return;
    const known = (name: string | null) => Boolean(name && profiles.some((profile) => profile.name === name));
    if (editingProfile && known(editingProfile)) {
      if (editingProfile !== selectedName) setSelectedName(editingProfile);
      return;
    }
    const next = known(selectedName) ? selectedName : profiles[0].name;
    if (next !== selectedName) setSelectedName(next);
    const profile = profiles.find((entry) => entry.name === next);
    if (profile && editingProfile !== next) beginEditProfile(profile);
  }, [profiles, selectedName, editingProfile, newProfile, beginEditProfile]);

  const selected = profiles.find((profile) => profile.name === selectedName) || null;
  const empty = profiles.length === 0;
  const showForm = Boolean(editingProfile || newProfile);

  return (
    <SettingsFoldSection
      title="模型配置"
      description="选择模型，在同一张表单里完成身份、能力与输入、思考和请求配置，一次保存。"
      icon={<Settings2 className="h-4 w-4" style={{ color: "var(--em-primary)" }} />}
      coachId="coach-settings-model-roles"
    >
      <div className="p-3">
        <div className="overflow-hidden rounded-xl border border-border/70 bg-card" aria-label="配置模型参数">
          <div className="flex flex-wrap items-center gap-2 border-b border-border/70 bg-muted/15 px-3 py-2.5">
            <div className="flex min-w-0 flex-1 items-center gap-2">
              <GlassSelect
                ariaLabel="选择要配置的模型"
                value={selectedName || ""}
                disabled={empty || profileBusy}
                placeholder="还没有模型"
                align="start"
                className="w-full min-w-0 sm:w-[16rem] sm:flex-none"
                onChange={(name) => {
                  const profile = profiles.find((entry) => entry.name === name);
                  setSelectedName(name);
                  if (profile) beginEditProfile(profile);
                }}
                options={profiles.map((profile) => {
                  const providerId = inferProfileProvider(profile);
                  return {
                    value: profile.name,
                    label: `${formatProviderModelLabel(profile)}${profile.name === active?.name ? "（默认）" : ""}`,
                    icon: (
                      <ProviderAvatar
                        id={providerId || "unknown"}
                        label={profile.name}
                        color={getProviderBrandColor(providerId)}
                        className="h-5 w-5 rounded-md"
                        iconClassName="h-3.5 w-3.5"
                      />
                    ),
                  };
                })}
              />
              {selected && (
                selected.name === active?.name ? (
                  <span className="inline-flex shrink-0 items-center gap-1 rounded-full bg-[var(--em-primary-alpha-12)] px-2 py-1 text-[10px] font-medium text-[var(--em-primary)]">
                    <Crown className="h-2.5 w-2.5" />
                    默认模型
                  </span>
                ) : (
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    className="h-9 shrink-0 gap-1 text-[11px]"
                    disabled={profileBusy || activatingProfile === selected.name}
                    onClick={() => void handleActivateProfile(selected)}
                  >
                    {activatingProfile === selected.name
                      ? <Loader2 className="h-3 w-3 animate-spin" />
                      : <Crown className="h-3 w-3" />}
                    设为默认
                  </Button>
                )
              )}
            </div>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  className="h-9 shrink-0 gap-1 text-xs"
                  disabled={empty || profileBusy}
                  title="沿用现有连接的凭证，添加另一个模型"
                >
                  <Plus className="h-3.5 w-3.5" />
                  添加模型
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent
                align="end"
                sideOffset={6}
                collisionPadding={12}
                className={`${glassMenuPanelClass} z-[80] max-h-72 min-w-[16rem] max-w-[calc(100vw-1.5rem)] overflow-y-auto`}
              >
                {profiles.map((profile) => {
                  const providerId = inferProfileProvider(profile);
                  return (
                    <DropdownMenuItem
                      key={profile.name}
                      className={glassMenuItemClass}
                      onSelect={() => beginAddSiblingProfile(profile)}
                    >
                      <ProviderAvatar
                        id={providerId || "unknown"}
                        label={profile.name}
                        color={getProviderBrandColor(providerId)}
                        className="h-5 w-5 rounded-md"
                        iconClassName="h-3.5 w-3.5"
                      />
                      <span className="min-w-0 flex-1">
                        <span className="block truncate">{formatProviderModelLabel(profile)}</span>
                        <span className="block truncate text-[10px] text-muted-foreground">沿用此连接的地址与凭证</span>
                      </span>
                    </DropdownMenuItem>
                  );
                })}
              </DropdownMenuContent>
            </DropdownMenu>
          </div>

          {empty ? (
            <div className="flex flex-col items-center gap-2 px-3 py-8 text-center">
              <p className="text-xs text-muted-foreground">还没有模型档案</p>
              <p className="text-[10px] text-muted-foreground/70">先在「模型连接」添加提供商和模型，再回到这里配置参数。</p>
              <Button type="button" size="sm" variant="outline" className="mt-1 h-8 text-xs" onClick={() => requestModelSubTab("providers")}>
                前往模型连接
              </Button>
            </div>
          ) : newProfile && !siblingSourceName ? (
            <div className="flex flex-col items-center gap-2 px-3 py-8 text-center">
              <p className="text-xs text-muted-foreground">正在新建提供商连接</p>
              <p className="text-[10px] text-muted-foreground/70">连接创建完成后会自动回到模型配置表单。</p>
              <Button type="button" size="sm" variant="outline" className="mt-1 h-8 text-xs" onClick={() => requestModelSubTab("providers")}>
                前往模型连接
              </Button>
            </div>
          ) : showForm ? (
            <ModelConfigForm />
          ) : (
            <div className="flex items-center justify-center gap-2 px-3 py-8 text-xs text-muted-foreground">
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
              正在载入模型配置…
            </div>
          )}
        </div>

        <AdvancedDiagnosticsPanel />
        <JevRoleSection />
      </div>
    </SettingsFoldSection>
  );
}
