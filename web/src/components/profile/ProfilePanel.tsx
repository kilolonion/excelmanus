"use client";

import { UserCircle } from "lucide-react";
import { useUIStore } from "@/stores/ui-store";
import { SlidePanel } from "@/components/ui/slide-panel";
import { ProfilePage } from "./ProfilePage";

export function ProfilePanel() {
  const profileOpen = useUIStore((s) => s.profileOpen);
  const closeProfile = useUIStore((s) => s.closeProfile);

  return (
    <SlidePanel
      open={profileOpen}
      onClose={closeProfile}
      title="个人中心"
      icon={<UserCircle className="h-4 w-4" style={{ color: "var(--em-primary)" }} />}
      width={480}
    >
      {profileOpen && <ProfilePage />}
    </SlidePanel>
  );
}
