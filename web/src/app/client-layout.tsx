"use client";

import dynamic from "next/dynamic";
import { Sidebar, SidebarToggle } from "@/components/sidebar/Sidebar";
import { TopModelSelector } from "@/components/chat/TopModelSelector";
import { ModeBadges } from "@/components/chat/ModeBadges";
import { SessionStatusBar } from "@/components/chat/SessionStatusBar";
import { BackupApplyBadge } from "@/components/chat/BackupApplyBadge";
import { SessionSync } from "@/components/providers/SessionSync";
import { ExcelDataRecovery } from "@/components/providers/ExcelDataRecovery";
import { useIsDesktop, useIsMediumScreen } from "@/hooks/use-mobile";

const ExcelSidePanel = dynamic(
  () => import("@/components/excel/ExcelSidePanel").then((m) => ({ default: m.ExcelSidePanel })),
  { ssr: false }
);

const ApprovalModal = dynamic(
  () => import("@/components/modals/ApprovalModal").then((m) => ({ default: m.ApprovalModal })),
  { ssr: false }
);

export function ClientLayout({ children }: { children: React.ReactNode }) {
  const isDesktop = useIsDesktop();
  const isMediumScreen = useIsMediumScreen();

  // 只有在桌面端（>=1280px）才使用完整的三栏布局
  // 中等屏幕（1024-1279px）使用两栏布局，Excel面板以浮层形式显示
  const useThreeColumnLayout = isDesktop;

  return (
    <div className="flex h-viewport overflow-hidden">
      <Sidebar />
      <main className="flex-1 flex flex-col overflow-hidden">
        {/* Top bar — model selector (ChatGPT style) */}
        <div className="flex items-center h-12 px-3 flex-shrink-0 border-b border-border/60">
          {/* Left group: navigation + model */}
          <SidebarToggle />
          <TopModelSelector />
          <div className="hidden sm:flex"><ModeBadges /></div>

          <div className="flex-1" />

          {/* Right group: status indicators */}
          <div className="flex items-center gap-2 min-w-0">
            <BackupApplyBadge />
            <SessionStatusBar />
          </div>
        </div>
        <div className="flex-1 min-h-0 overflow-hidden flex">
          <div className="flex-1 min-w-0 overflow-hidden">
            {children}
          </div>
          {/* 只有在桌面端才显示固定的右侧Excel面板 */}
          {useThreeColumnLayout && <ExcelSidePanel />}
          {/* 中等屏幕和移动端使用浮层模式的Excel面板 */}
          {!useThreeColumnLayout && <ExcelSidePanel />}
        </div>
      </main>
      <ApprovalModal />
      <SessionSync />
      <ExcelDataRecovery />
    </div>
  );
}
