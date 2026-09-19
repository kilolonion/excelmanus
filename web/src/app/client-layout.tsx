"use client";

import dynamic from "next/dynamic";
import { Sidebar, SidebarToggle } from "@/components/sidebar/Sidebar";
import { TopModelSelector } from "@/components/chat/TopModelSelector";
import { ChatSessionHeader } from "@/components/chat/ChatSessionHeader";
import { ChatWorkspaceTabs } from "@/components/chat/ChatWorkspaceTabs";
import { WorkbookPanelButton } from "@/components/excel/WorkbookPanelButton";
import { JevTimelineButton, JevTimelineDrawer } from "@/components/chat/JevTimeline";
import { SessionSync } from "@/components/providers/SessionSync";
import { ExcelDataRecovery } from "@/components/providers/ExcelDataRecovery";
import { PlaceholderAlert } from "@/components/modals/PlaceholderAlert";
import { SettingsDialog } from "@/components/settings/SettingsDialog";
import { useOnboardingStore } from "@/stores/onboarding-store";
import { shouldShowCoachMarks, shouldShowOnboardingWizard } from "@/stores/onboarding-state";
import { ExcelSidePanel } from "@/components/excel/ExcelSidePanel";
import { prefetchUniverModules } from "@/lib/univer-modules";
import { WordSidePanel } from "@/components/word/WordSidePanel";
import { FilePreviewHost } from "@/components/files/FilePreviewHost";

const AdminPanel = dynamic(
  () => import("@/components/admin/AdminPanel").then((m) => ({ default: m.AdminPanel })),
  { ssr: false }
);

// 首屏空闲后再预加载 Univer，避免和会话/模型请求抢带宽
prefetchUniverModules();

const ApprovalModal = dynamic(
  () => import("@/components/modals/ApprovalModal").then((m) => ({ default: m.ApprovalModal })),
  { ssr: false }
);

const OnboardingWizard = dynamic(
  () => import("@/components/onboarding/OnboardingWizard").then((m) => ({ default: m.OnboardingWizard })),
  { ssr: false }
);

const CoachMarks = dynamic(
  () => import("@/components/onboarding/CoachMarks").then((m) => ({ default: m.CoachMarks })),
  { ssr: false }
);

export function ClientLayout({ children }: { children: React.ReactNode }) {
  const wizardCompleted = useOnboardingStore((s) => s.wizardCompleted);
  const coachMarksCompleted = useOnboardingStore((s) => s.coachMarksCompleted);
  const advancedGuideCompleted = useOnboardingStore((s) => s.advancedGuideCompleted);
  const settingsGuideCompleted = useOnboardingStore((s) => s.settingsGuideCompleted);
  const backendConfigured = useOnboardingStore((s) => s.backendConfigured);
  const userSynced = useOnboardingStore((s) => s._userSynced);
  const showWizard = shouldShowOnboardingWizard(userSynced, wizardCompleted, backendConfigured);
  const showCoachMarks = shouldShowCoachMarks(
    userSynced,
    wizardCompleted,
    backendConfigured,
    coachMarksCompleted,
    advancedGuideCompleted,
    settingsGuideCompleted,
  );

  return (
    <>
      {/* Onboarding Wizard — full-screen overlay for first-time setup or missing backend config */}
      {showWizard && <OnboardingWizard />}

      {/* Coach Marks — two-phase guide (basic + advanced explore) */}
      {showCoachMarks && <CoachMarks />}

      <div className="flex h-viewport overflow-hidden">
        <Sidebar />
        <main className="flex-1 flex flex-col overflow-hidden min-w-0">
          {/* 顶栏只占对话列；表格/文档侧栏与左侧栏一样通顶挤压 */}
          <div className="flex flex-col shrink-0 topbar-glass">
            <div className="flex items-center h-12 px-2 sm:px-3 overflow-hidden">
              <SidebarToggle />
              <ChatSessionHeader />

              <div className="ml-auto flex items-center gap-1 sm:gap-1.5 md:gap-2 flex-shrink-0 min-w-0">
                <TopModelSelector />
                <JevTimelineButton />
                <WorkbookPanelButton />
              </div>
            </div>
            <ChatWorkspaceTabs />
          </div>
          <div className="flex-1 min-h-0 overflow-hidden">
            {children}
          </div>
        </main>
        <JevTimelineDrawer />
        <ExcelSidePanel />
        <WordSidePanel />
        <FilePreviewHost />
        <ApprovalModal />
        <SessionSync />
        <ExcelDataRecovery />
        <PlaceholderAlert />
        <SettingsDialog />
        <AdminPanel />
      </div>
    </>
  );
}
