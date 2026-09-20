"use client";

import dynamic from "next/dynamic";
import { Sidebar, SidebarToggle } from "@/components/sidebar/Sidebar";
import { TopModelSelector } from "@/components/chat/TopModelSelector";
import { ChatSessionHeader } from "@/components/chat/ChatSessionHeader";
import { ChatWorkspaceTabs } from "@/components/chat/ChatWorkspaceTabs";
import { BackgroundTasks } from "@/components/chat/BackgroundTasks";
import { WorkbookPanelButton } from "@/components/excel/WorkbookPanelButton";
import { JevTimelineButton, JevTimelineDrawer } from "@/components/chat/JevTimeline";
import { SessionSync } from "@/components/providers/SessionSync";
import { ExcelDataRecovery } from "@/components/providers/ExcelDataRecovery";
import { PlaceholderAlert } from "@/components/modals/PlaceholderAlert";
import { useOnboardingStore } from "@/stores/onboarding-store";
import { shouldShowCoachMarks, shouldShowOnboardingWizard } from "@/stores/onboarding-state";
import { FilePreviewHost } from "@/components/files/FilePreviewHost";
import { useUIStore } from "@/stores/ui-store";
import { useExcelStore } from "@/stores/excel-store";
import { useWordStore } from "@/stores/word-store";
import { useChatStore } from "@/stores/chat-store";

const SettingsDialog = dynamic(() => import("@/components/settings/SettingsDialog").then((m) => m.SettingsDialog), { ssr: false });
const ExcelSidePanel = dynamic(() => import("@/components/excel/ExcelSidePanel").then((m) => m.ExcelSidePanel), { ssr: false });
const WordSidePanel = dynamic(() => import("@/components/word/WordSidePanel").then((m) => m.WordSidePanel), { ssr: false });

const AdminPanel = dynamic(
  () => import("@/components/admin/AdminPanel").then((m) => ({ default: m.AdminPanel })),
  { ssr: false }
);

// Univer is warmed by workbook hover/focus and opening, not by loading chat.
// requestIdleCallback cannot keep a large module's parse/evaluation work idle.

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
  const userSynced = useOnboardingStore((s) => s._userSynced);
  const guideGeneration = useOnboardingStore((s) => s._resetGeneration);
  const showWizard = shouldShowOnboardingWizard(userSynced, wizardCompleted);
  const showCoachMarks = shouldShowCoachMarks(
    userSynced,
    wizardCompleted,
    coachMarksCompleted,
    advancedGuideCompleted,
    settingsGuideCompleted,
  );

  return (
    <>
      {/* First-run setup can be skipped even before a model is configured. */}
      {showWizard && <OnboardingWizard key={guideGeneration} />}

      {/* Interactive chapters share progress across responsive layouts. */}
      {showCoachMarks && <CoachMarks key={guideGeneration} />}

      <div className="em-app-shell flex h-viewport overflow-hidden">
        <Sidebar />
        <main className="em-main flex-1 flex flex-col overflow-hidden min-w-0">
          {/* 顶栏与对话区共用同一列，右侧面板打开时一起缩窄 */}
          <div className="em-topbar flex flex-col shrink-0 topbar-glass">
            <div className="em-topbar-toolbar relative flex items-center overflow-hidden">
              <div className="em-topbar-leading flex min-w-0 flex-1 items-center">
                <SidebarToggle />
                <ChatSessionHeader />
              </div>

              <div className="em-workspace-tabs">
                <ChatWorkspaceTabs />
              </div>

              <div className="em-topbar-actions ml-auto flex items-center gap-1 sm:gap-1.5 md:gap-2 flex-shrink-0 min-w-0">
                <TopModelSelector />
                <BackgroundTasks />
                <JevTimelineButton />
                <WorkbookPanelButton />
              </div>
            </div>
          </div>
          <div className="flex-1 min-h-0 overflow-hidden">
            {children}
          </div>
        </main>
        <JevTimelineDrawer />
        <WorkspaceOverlays />
        <FilePreviewHost />
        <SessionSync />
        <ExcelDataRecovery />
        <PlaceholderAlert />
      </div>
    </>
  );
}

function WorkspaceOverlays() {
  const settingsOpen = useUIStore((s) => s.settingsOpen);
  const adminOpen = useUIStore((s) => s.adminOpen);
  const hasApproval = useChatStore((s) => !!s.pendingApproval);
  // Retain the initialized workbook when closed, preserving the existing
  // editor lifecycle, but do not import its UI before the first open.
  const hasExcelPanel = useExcelStore((s) => s.panelOpen || !!s.activeFilePath);
  const wordPanelOpen = useWordStore((s) => s.panelOpen);
  return <>
    {hasExcelPanel && <ExcelSidePanel />}
    {wordPanelOpen && <WordSidePanel />}
    {hasApproval && <ApprovalModal />}
    {settingsOpen && <SettingsDialog />}
    {adminOpen && <AdminPanel />}
  </>;
}
