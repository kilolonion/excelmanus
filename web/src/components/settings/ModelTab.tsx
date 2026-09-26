"use client";

import { useCallback, useEffect, useState, type ReactNode } from "react";
import { Loader2, CheckCircle2, AlertTriangle, X, Database, Settings2, Crown } from "lucide-react";
import { AdminModelContext } from "./model/admin-model-context";
import { useAdminModelSettings } from "./model/useAdminModelSettings";
import { ProviderSection } from "./model/ProviderSection";
import { JevProviderSection } from "./model/JevProviderSection";
import { RoleModelSection } from "./model/RoleModelSection";
import { SubscriptionOAuthPanel } from "./model/SubscriptionOAuthPanel";
import { SettingsPageLayout, SettingsPageSubnav } from "./SettingsPageLayout";
import {
  subscribeModelSubTab,
  takePendingModelSubTab,
  type ModelSubTab,
} from "./model/model-subtab";

const SUB_TABS: { key: ModelSubTab; label: string; icon: ReactNode; coachId: string }[] = [
  { key: "providers", label: "模型连接", icon: <Database className="h-3 w-3" />, coachId: "coach-settings-subtab-providers" },
  { key: "roles", label: "模型配置", icon: <Settings2 className="h-3 w-3" />, coachId: "coach-settings-subtab-roles" },
  { key: "subscription", label: "订阅账号", icon: <Crown className="h-3 w-3" />, coachId: "coach-settings-subtab-subscription" },
];

const SUB_TAB_DESCRIPTIONS: Record<ModelSubTab, string> = {
  providers: "添加 API 或订阅连接，管理可用模型。",
  roles: "选择默认模型，并集中调整能力、推理和请求行为。",
  subscription: "连接已有订阅，免去重复填写 API Key。",
  diagnostics: "模型配置已统一到模型配置页。",
};

function normalizeModelSubTab(tab: ModelSubTab): Exclude<ModelSubTab, "diagnostics"> {
  return tab === "diagnostics" ? "roles" : tab;
}

export function ModelTab() {
  const ctx = useAdminModelSettings();
  const [subTab, setSubTab] = useState<Exclude<ModelSubTab, "diagnostics">>(() => normalizeModelSubTab(takePendingModelSubTab() ?? "providers"));
  const [subscriptionVisited, setSubscriptionVisited] = useState(subTab === "subscription");
  const navigate = useCallback((tab: ModelSubTab) => {
    const next = normalizeModelSubTab(tab);
    setSubTab(next);
    if (next === "subscription") setSubscriptionVisited(true);
  }, []);

  useEffect(() => subscribeModelSubTab(navigate), [navigate]);

  if (ctx.loading && !ctx.config) {
    return (
      <div className="flex items-center justify-center py-12">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <AdminModelContext.Provider value={ctx}>
      <SettingsPageLayout className="em-model-page em-settings-page-stack">
        <SettingsPageSubnav
          label="模型工作区"
          showHeader={false}
          activeKey={subTab}
          items={SUB_TABS.map((tab) => ({ ...tab, description: SUB_TAB_DESCRIPTIONS[tab.key] }))}
          onChange={(key) => navigate(key as ModelSubTab)}
        />
        {ctx.loadError && <div role="alert" className="flex items-center gap-3 rounded-lg border border-destructive/20 p-3 text-xs text-destructive">
          <span className="flex-1">{ctx.config ? "配置刷新失败，当前显示上次读取的内容。" : "无法读取模型配置。"}{ctx.loadError}</span>
          <button type="button" className="shrink-0 underline" onClick={() => void ctx.fetchConfig(true)}>重新加载</button>
        </div>}
        {ctx.saveToast && (
          <div
            className={`flex items-center gap-2 px-3 py-2 rounded-lg text-xs font-medium transition-opacity backdrop-blur-xl shadow-sm ${
              ctx.saveToast.type === "success"
                ? "bg-emerald-50/80 dark:bg-emerald-950/70 text-emerald-600 dark:text-emerald-400 border border-emerald-500/20"
                : "bg-red-50/80 dark:bg-red-950/70 text-destructive border border-destructive/20"
            }`}
          >
            {ctx.saveToast.type === "success" ? <CheckCircle2 className="h-3.5 w-3.5 shrink-0" /> : <AlertTriangle className="h-3.5 w-3.5 shrink-0" />}
            <span className="flex-1">{ctx.saveToast.msg}</span>
            <button className="shrink-0 hover:opacity-70" onClick={() => ctx.setSaveToast(null)}>
              <X className="h-3 w-3" />
            </button>
          </div>
        )}

        <main className="em-model-content" aria-live="polite">
            {subTab === "providers" && (
              <div className="flex flex-col gap-3">
                <ProviderSection />
                <JevProviderSection />
              </div>
            )}
            {subTab === "roles" && (
              <div className="flex flex-col gap-3">
                <RoleModelSection />
              </div>
            )}
            {subscriptionVisited && <div hidden={subTab !== "subscription"}><SubscriptionOAuthPanel /></div>}
        </main>
      </SettingsPageLayout>
    </AdminModelContext.Provider>
  );
}
