"use client";

import { useUIStore } from "@/stores/ui-store";
import { useAdminModel } from "./admin-model-context";
import { CodexOAuthCard } from "./CodexOAuthCard";
import { WorkBuddyOAuthCard } from "./WorkBuddyOAuthCard";
import { AntigravityOAuthCard } from "./AntigravityOAuthCard";

export function SubscriptionOAuthPanel() {
  const { config } = useAdminModel();
  const profileNames = (config?.profiles || []).map((p) => p.name);
  const notify = () => useUIStore.getState().bumpModelProfiles();

  return (
    <div className="space-y-2.5">
      <p className="text-xs text-muted-foreground">订阅登录（无需 API Key）</p>
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
  );
}
