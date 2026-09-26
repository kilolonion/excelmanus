"use client";

import { FlaskConical } from "lucide-react";
import { MODEL_RUNTIME_SETTING_GROUPS } from "../settings-catalog";
import { RuntimeSettingsPanel } from "../RuntimeSettingsPanel";
import { useUIStore } from "@/stores/ui-store";
import { useJevStore } from "@/stores/jev-store";
import { jevChatEnabledFromRuntime } from "@/lib/jev-settings";
import { ConfigTransferPanel } from "./ConfigTransferPanel";

/** Remaining model-wide controls. Per-model capability and thinking controls live in ProfileEditorForm. */
export function AdvancedDiagnosticsPanel() {
  return (
    <div className="em-diagnostics-stack mt-3 space-y-5" aria-label="模型请求与迁移设置">
      <div className="em-diagnostics-card">
        <ConfigTransferPanel onImported={() => { useUIStore.getState().bumpModelProfiles(); }} />
      </div>
      <RuntimeSettingsPanel groups={MODEL_RUNTIME_SETTING_GROUPS} />
      <RuntimeSettingsPanel onSaved={(settings) => {
        useJevStore.getState().setChatEnabled(jevChatEnabledFromRuntime(settings as Parameters<typeof jevChatEnabledFromRuntime>[0]));
      }} groups={[{
        title: "实验性功能",
        description: "默认关闭仍在验证中的能力；开启后会在模型连接和模型配置中显示 Jev，并允许后台评估。",
        defaultOpen: false,
        icon: <FlaskConical className="h-4 w-4" />,
        items: [{
          key: "jev_experimental_enabled",
          label: "启用实验性 Jev",
          desc: "显示 Jev 决策提供商、模型选择和时间线等组件。关闭后停止后端评估并隐藏相关界面，但不删除已有配置。",
          icon: <FlaskConical className="h-4 w-4" />,
          type: "bool",
        }],
      }]} />
    </div>
  );
}
