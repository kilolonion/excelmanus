"use client";

import dynamic from "next/dynamic";
import { Sidebar, SidebarToggle } from "@/components/sidebar/Sidebar";
import { TopModelSelector } from "@/components/chat/TopModelSelector";
import { ModeBadges } from "@/components/chat/ModeBadges";
import { SessionStatusBar } from "@/components/chat/SessionStatusBar";
import { BackupApplyBadge } from "@/components/chat/BackupApplyBadge";
import { SessionSync } from "@/components/providers/SessionSync";
import { ExcelDataRecovery } from "@/components/providers/ExcelDataRecovery";
import { PlaceholderAlert } from "@/components/modals/PlaceholderAlert";
import { SettingsDialog } from "@/components/settings/SettingsDialog";
import { ProfilePanel } from "@/components/profile/ProfilePanel";
import { AdminPanel } from "@/components/admin/AdminPanel";
import { useOnboardingStore } from "@/stores/onboarding-store";
import { ExcelSidePanel } from "@/components/excel/ExcelSidePanel";
import { prefetchUniverModules } from "@/components/excel/UniverSheet";

// 应用启动时后台预加载 Univer 库，避免首次打开面板时等待
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
  const showCoachMarks = wizardCompleted && backendConfigured !== false && (!coachMarksCompleted || !advancedGuideCompleted || !settingsGuideCompleted);
  const showWizard = !wizardCompleted || backendConfigured === false;

  return (
    <>
      {/* Onboarding Wizard — full-screen overlay for first-time setup or missing backend config */}
      {showWizard && <OnboardingWizard />}

      {/* Coach Marks — two-phase guide (basic + advanced explore) */}
      {showCoachMarks && <CoachMarks />}

      <div className="flex h-viewport overflow-hidden">
        <Sidebar />
        <main className="flex-1 flex flex-col overflow-hidden">
          {/* 顶栏 — 模型选择器 */}
          <div className="flex items-center h-12 px-3 flex-shrink-0 topbar-glass">
            {/* 左侧：导航 + 模型 */}
            <SidebarToggle />
            <TopModelSelector />
            <ModeBadges />

            <div className="flex-1" />

            {/* 右侧：状态指示 */}
            <div className="flex items-center gap-2 min-w-0">
              <BackupApplyBadge />
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
        <ExcelDataRecovery />
        <PlaceholderAlert />
        <SettingsDialog />
        <ProfilePanel />
        <AdminPanel />
      </div>
    </>
  );
}
