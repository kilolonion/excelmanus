"use client";

import { Brain, MessageSquare } from "lucide-react";
import { Switch } from "@/components/ui/switch";
import { useWorkbookChatPreferencesStore } from "@/stores/workbook-chat-preferences-store";

export function WorkbookChatSettings() {
  const autoReturnToChat = useWorkbookChatPreferencesStore((s) => s.autoReturnToChat);
  const learnFromNavigation = useWorkbookChatPreferencesStore((s) => s.learnFromNavigation);
  const setAutoReturnToChat = useWorkbookChatPreferencesStore((s) => s.setAutoReturnToChat);
  const setLearnFromNavigation = useWorkbookChatPreferencesStore((s) => s.setLearnFromNavigation);

  return (
    <section className="space-y-3" aria-label="表格与对话">
      <h3 className="text-xs font-semibold text-muted-foreground tracking-wider">表格与对话</h3>
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-start gap-2.5 min-w-0">
          <MessageSquare className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
          <div>
            <label htmlFor="workbook-auto-return" className="text-sm font-medium">发送后返回对话</label>
            <p id="workbook-auto-return-description" className="text-[11px] sm:text-xs text-muted-foreground">
              在表格面板发送消息后，自动切换到对话面板。并排对话时保留当前布局。
            </p>
          </div>
        </div>
        <Switch id="workbook-auto-return" checked={autoReturnToChat} onCheckedChange={setAutoReturnToChat}
          aria-describedby="workbook-auto-return-description" className="shrink-0" />
      </div>
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-start gap-2.5 min-w-0">
          <Brain className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
          <div>
            <label htmlFor="workbook-learn-navigation" className="text-sm font-medium">根据使用习惯自动调整</label>
            <p id="workbook-learn-navigation-description" className="text-[11px] sm:text-xs text-muted-foreground">
              最近 5 次中有 4 次在发送后 10 秒内切回表格，就关闭自动返回；关闭后若经常立即切到对话，则重新开启。关闭习惯学习可固定当前选择。
            </p>
          </div>
        </div>
        <Switch id="workbook-learn-navigation" checked={learnFromNavigation} onCheckedChange={setLearnFromNavigation}
          aria-describedby="workbook-learn-navigation-description" className="shrink-0" />
      </div>
      <p className="text-[11px] text-muted-foreground">更改立即生效，并保存在本机。</p>
    </section>
  );
}
