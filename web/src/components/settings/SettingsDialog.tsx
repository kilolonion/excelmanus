"use client";

import { Settings, Server, Package, Plug, SlidersHorizontal, ScrollText, Brain } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { ModelTab } from "./ModelTab";
import { RulesTab } from "./RulesTab";
import { SkillsTab } from "./SkillsTab";
import { MCPTab } from "./MCPTab";
import { MemoryTab } from "./MemoryTab";
import { RuntimeTab } from "./RuntimeTab";
import { useUIStore } from "@/stores/ui-store";

const TAB_META = [
  { value: "model", label: "模型", icon: <Server className="h-3.5 w-3.5" /> },
  { value: "rules", label: "规则", icon: <ScrollText className="h-3.5 w-3.5" /> },
  { value: "skills", label: "技能", icon: <Package className="h-3.5 w-3.5" /> },
  { value: "mcp", label: "MCP", icon: <Plug className="h-3.5 w-3.5" /> },
  { value: "memory", label: "记忆", icon: <Brain className="h-3.5 w-3.5" /> },
  { value: "runtime", label: "运行时", icon: <SlidersHorizontal className="h-3.5 w-3.5" /> },
];

export function SettingsDialog() {
  const { settingsOpen, settingsTab, openSettings, closeSettings } = useUIStore();

  return (
    <Dialog open={settingsOpen} onOpenChange={(v) => (v ? openSettings(settingsTab) : closeSettings())}>
      <DialogTrigger asChild>
        <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => openSettings("model")}>
          <Settings className="h-4 w-4" />
        </Button>
      </DialogTrigger>
      <DialogContent className="!grid-none !flex !flex-col max-w-2xl max-h-[85vh] p-0 overflow-hidden">
        <DialogHeader className="px-6 pt-6 pb-0 flex-shrink-0">
          <DialogTitle className="flex items-center gap-2">
            <Settings className="h-5 w-5" />
            设置
          </DialogTitle>
        </DialogHeader>

        <Tabs
          value={settingsTab}
          onValueChange={(v) => openSettings(v)}
          className="px-6 pb-6 flex flex-col overflow-hidden min-h-0 flex-1"
        >
          <TabsList className="w-full justify-start mb-4 flex-shrink-0">
            {TAB_META.map((tab) => (
              <TabsTrigger key={tab.value} value={tab.value} className="gap-1.5 text-xs">
                {tab.icon}
                {tab.label}
              </TabsTrigger>
            ))}
          </TabsList>

          <TabsContent value="model" className="overflow-y-auto min-h-0 flex-1 mt-0">
            <ModelTab />
          </TabsContent>

          <TabsContent value="rules" className="overflow-y-auto min-h-0 flex-1 mt-0">
            <RulesTab />
          </TabsContent>

          <TabsContent value="skills" className="overflow-y-auto min-h-0 flex-1 mt-0">
            <SkillsTab />
          </TabsContent>

          <TabsContent value="mcp" className="overflow-y-auto min-h-0 flex-1 mt-0">
            <MCPTab />
          </TabsContent>

          <TabsContent value="memory" className="overflow-y-auto min-h-0 flex-1 mt-0">
            <MemoryTab />
          </TabsContent>

          <TabsContent value="runtime" className="overflow-y-auto min-h-0 flex-1 mt-0">
            <RuntimeTab />
          </TabsContent>
        </Tabs>
      </DialogContent>
    </Dialog>
  );
}
