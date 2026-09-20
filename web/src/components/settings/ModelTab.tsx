"use client";

import { useEffect, useState, type ReactNode } from "react";
import { Loader2, CheckCircle2, AlertTriangle, X, Database, Settings2, Crown, SlidersHorizontal } from "lucide-react";
import { AdminModelContext } from "./model/admin-model-context";
import { useAdminModelSettings } from "./model/useAdminModelSettings";
import { ProviderSection } from "./model/ProviderSection";
import { JevProviderSection } from "./model/JevProviderSection";
import { RoleModelSection } from "./model/RoleModelSection";
import { JevRoleSection } from "./model/JevRoleSection";
import { SubscriptionOAuthPanel } from "./model/SubscriptionOAuthPanel";
import { AdvancedDiagnosticsPanel } from "./model/AdvancedDiagnosticsPanel";
import {
  subscribeModelSubTab,
  takePendingModelSubTab,
  type ModelSubTab,
} from "./model/model-subtab";

const SUB_TABS: { key: ModelSubTab; label: string; icon: ReactNode; coachId: string }[] = [
  { key: "providers", label: "供应商", icon: <Database className="h-3 w-3" />, coachId: "coach-settings-subtab-providers" },
  { key: "roles", label: "模型配置", icon: <Settings2 className="h-3 w-3" />, coachId: "coach-settings-subtab-roles" },
  { key: "subscription", label: "订阅与 OAuth", icon: <Crown className="h-3 w-3" />, coachId: "coach-settings-subtab-subscription" },
  { key: "diagnostics", label: "高级设置", icon: <SlidersHorizontal className="h-3 w-3" />, coachId: "coach-settings-subtab-diagnostics" },
];

export function ModelTab() {
  const ctx = useAdminModelSettings();
  const [subTab, setSubTab] = useState<ModelSubTab>(() => takePendingModelSubTab() ?? "providers");

  useEffect(() => subscribeModelSubTab(setSubTab), []);

  if (ctx.loading) {
    return (
      <div className="flex items-center justify-center py-12">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <AdminModelContext.Provider value={ctx}>
      <div className="flex flex-col gap-2">
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

        <div className="flex items-center gap-1 mb-1 overflow-x-auto scrollbar-none">
          {SUB_TABS.map((tab) => {
            const isActive = subTab === tab.key;
            return (
              <button
                key={tab.key}
                type="button"
                data-coach-id={tab.coachId}
                className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium transition-colors whitespace-nowrap border ${
                  isActive
                    ? "text-white border-transparent"
                    : "border-border text-muted-foreground hover:bg-muted/60 hover:text-foreground"
                }`}
                style={isActive ? { backgroundColor: "var(--em-primary)" } : undefined}
                onClick={() => setSubTab(tab.key)}
              >
                {tab.icon}
                {tab.label}
              </button>
            );
          })}
        </div>

        {subTab === "providers" && (
          <div className="flex flex-col gap-3">
            <ProviderSection />
            <JevProviderSection />
          </div>
        )}
        {subTab === "roles" && (
          <div className="flex flex-col gap-3">
            <RoleModelSection />
            <JevRoleSection />
          </div>
        )}
        {subTab === "subscription" && <SubscriptionOAuthPanel />}
        {subTab === "diagnostics" && <AdvancedDiagnosticsPanel />}
      </div>
    </AdminModelContext.Provider>
  );
}
