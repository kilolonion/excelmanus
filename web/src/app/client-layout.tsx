"use client";

import dynamic from "next/dynamic";
import { Sidebar, SidebarToggle } from "@/components/sidebar/Sidebar";
import { TopModelSelector } from "@/components/chat/TopModelSelector";
import { ModeBadges } from "@/components/chat/ModeBadges";
import { SessionStatusBar } from "@/components/chat/SessionStatusBar";
import { BackupApplyBadge } from "@/components/chat/BackupApplyBadge";
import { SessionSync } from "@/components/providers/SessionSync";

const ExcelSidePanel = dynamic(
  () => import("@/components/excel/ExcelSidePanel").then((m) => ({ default: m.ExcelSidePanel })),
  { ssr: false }
);

const ApprovalModal = dynamic(
  () => import("@/components/modals/ApprovalModal").then((m) => ({ default: m.ApprovalModal })),
  { ssr: false }
);

export function ClientLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex h-viewport overflow-hidden">
      <Sidebar />
      <main className="flex-1 flex flex-col overflow-hidden">
        {/* Top bar — model selector (ChatGPT style) */}
        <div className="flex items-center h-12 px-3 flex-shrink-0 border-b border-border">
          <SidebarToggle />
          <TopModelSelector />
          <div className="flex">
            <ModeBadges />
          </div>
          <div className="flex-1" />
          <BackupApplyBadge />
          <div className="flex">
            <SessionStatusBar />
          </div>
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
