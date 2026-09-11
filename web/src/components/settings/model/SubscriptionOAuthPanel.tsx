"use client";

import { useUIStore } from "@/stores/ui-store";
import { useAdminModel } from "./admin-model-context";
import { CodexOAuthCard } from "./CodexOAuthCard";

export function SubscriptionOAuthPanel() {
  const { config, fetchConfig } = useAdminModel();

  return (
    <div className="space-y-2.5">
      <p className="text-xs text-muted-foreground">ChatGPT 订阅登录（无需 API Key）</p>
      <CodexOAuthCard
        onProfileCreated={() => { fetchConfig(true); useUIStore.getState().bumpModelProfiles(); }}
        existingProfileNames={(config?.profiles || []).map((p) => p.name)}
      />
    </div>
  );
}
