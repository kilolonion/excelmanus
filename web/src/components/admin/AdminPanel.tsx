"use client";

import { Settings2 } from "lucide-react";
import { useUIStore } from "@/stores/ui-store";
import { SlidePanel } from "@/components/ui/slide-panel";
import AdminPage from "@/app/admin/page";

export function AdminPanel() {
  const adminOpen = useUIStore((s) => s.adminOpen);
  const closeAdmin = useUIStore((s) => s.closeAdmin);

  return (
    <SlidePanel
      open={adminOpen}
      onClose={closeAdmin}
      title="管理中心"
      icon={<Settings2 className="h-4 w-4" style={{ color: "var(--em-primary)" }} />}
      width={580}
    >
      {adminOpen && <AdminPage />}
    </SlidePanel>
  );
}
