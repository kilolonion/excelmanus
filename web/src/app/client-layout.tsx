"use client";

import { Sidebar, SidebarToggle } from "@/components/sidebar/Sidebar";
import { TopModelSelector } from "@/components/chat/TopModelSelector";
import { ModeBadges } from "@/components/chat/ModeBadges";
import { SessionStatusBar } from "@/components/chat/SessionStatusBar";
import { BackupApplyBadge } from "@/components/chat/BackupApplyBadge";
import { ApprovalModal } from "@/components/modals/ApprovalModal";
import { SessionSync } from "@/components/providers/SessionSync";
import { ExcelSidePanel } from "@/components/excel/ExcelSidePanel";

export function ClientLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex h-screen overflow-hidden">
      <Sidebar />
      <main className="flex-1 flex flex-col overflow-hidden">
        {/* Top bar — model selector (ChatGPT style) */}
        <div className="flex items-center h-12 px-3 flex-shrink-0 border-b border-border">
          <SidebarToggle />
          <TopModelSelector />
          <ModeBadges />
          <div className="flex-1" />
          <BackupApplyBadge />
          <SessionStatusBar />
        </div>
        <div className="flex-1 min-h-0 overflow-hidden flex">
          <div className="flex-1 min-w-0 overflow-hidden">
            {children}
          </div>
          <ExcelSidePanel />
        </div>
      </main>
      <ApprovalModal />
      <SessionSync />
    </div>
  );
}
