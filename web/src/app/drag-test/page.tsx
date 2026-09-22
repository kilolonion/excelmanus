"use client";

import { useEffect } from "react";
import { SessionList } from "@/components/sidebar/SessionList";
import { useSessionStore } from "@/stores/session-store";

const fake = Array.from({ length: 12 }, (_, i) => ({
  id: `t${i + 1}`,
  title: `Test Chat ${i + 1}`,
  messageCount: 0,
  inFlight: false,
  updatedAt: `2026-09-${String(20 - i).padStart(2, "0")}T00:00:00Z`,
}));

export default function DragTestPage() {
  useEffect(() => {
    useSessionStore.setState({ sessions: fake, sidebarSessionOrder: {}, activeSessionId: null });
  }, []);
  return (
    <div className="flex h-screen">
      <div className="w-72 border-r h-full">
        <SessionList />
      </div>
      <div className="flex-1 p-8 text-sm text-muted-foreground">
        Drag test page — reorder items in the left list twice.
      </div>
    </div>
  );
}
