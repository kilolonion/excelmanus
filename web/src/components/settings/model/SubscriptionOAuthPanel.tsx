"use client";

import { useUIStore } from "@/stores/ui-store";
import { useCallback } from "react";
import { ArrowRight, KeyRound } from "lucide-react";
import { requestModelSubTab } from "./model-subtab";
import { useAdminModel } from "./admin-model-context";
import { CodexOAuthCard } from "./CodexOAuthCard";
import { WorkBuddyOAuthCard } from "./WorkBuddyOAuthCard";
import { AntigravityOAuthCard } from "./AntigravityOAuthCard";

export function SubscriptionOAuthPanel() {
  const { config } = useAdminModel();
  const profileNames = (config?.profiles || []).map((p) => p.name);
  const notify = useCallback(() => useUIStore.getState().bumpModelProfiles(), []);

  return (
    <div className="space-y-4 pt-2">
      <div className="space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="text-base font-semibold tracking-tight">连接你的订阅账号</h3>
          <span className="rounded-md bg-primary/8 px-2 py-0.5 text-[11px] font-medium text-primary">无需 API Key</span>
        </div>
        <p className="text-xs leading-relaxed text-muted-foreground">使用已有订阅登录，连接后自动添加模型。选择下方服务开始，或展开已连接账号进行管理。</p>
      </div>
      <ol aria-label="订阅连接流程" className="flex flex-wrap items-center gap-x-4 gap-y-2 rounded-lg bg-muted/40 px-3 py-2.5 text-xs text-muted-foreground">
        {["选择服务", "登录并授权", "配置模型"].map((step, index) => <li key={step} className="flex items-center gap-2">
          <span className="flex size-5 items-center justify-center rounded-full border border-border bg-background text-[10px] font-medium">{index + 1}</span>
          {step}{index < 2 && <ArrowRight className="ml-1 size-3 text-muted-foreground/50" />}
        </li>)}
      </ol>
      <div className="space-y-2">
      <CodexOAuthCard
        onProfileCreated={notify}
        existingProfileNames={profileNames}
      />
      <WorkBuddyOAuthCard
        realm="cn"
        onProfileCreated={notify}
        existingProfileNames={profileNames}
      />
      <WorkBuddyOAuthCard
        realm="global"
        onProfileCreated={notify}
        existingProfileNames={profileNames}
      />
      <AntigravityOAuthCard
        onProfileCreated={notify}
        existingProfileNames={profileNames}
      />
      </div>
      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border/60 pt-3 text-xs text-muted-foreground">
        <span>有 API Key？也可以通过供应商配置连接。</span>
        <button type="button" className="inline-flex min-h-8 items-center gap-1.5 font-medium text-primary hover:underline" onClick={() => requestModelSubTab("providers")}>
          <KeyRound className="size-3.5" /> 配置 API Key <ArrowRight className="size-3" />
        </button>
      </div>
    </div>
  );
}
