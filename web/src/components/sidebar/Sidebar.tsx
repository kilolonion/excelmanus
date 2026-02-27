"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { useIsMobile } from "@/hooks/use-mobile";
import {
  PanelLeftClose,
  PanelLeft,
  MessageSquare,
  FolderOpen,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { useUIStore } from "@/stores/ui-store";
import { useSessionStore } from "@/stores/session-store";
import { sidebarTransition, sidebarContentVariants, useMotionSafe } from "@/lib/sidebar-motion";
import { SessionList } from "./SessionList";
import { ExcelFilesBar } from "./ExcelFilesBar";
import { StatusFooter } from "./StatusFooter";

type SidebarTab = "chats" | "files";

const tabs: { key: SidebarTab; label: string; icon: typeof MessageSquare }[] = [
  { key: "chats", label: "对话", icon: MessageSquare },
  { key: "files", label: "文件", icon: FolderOpen },
];

/** Hook: swipe-left to close sidebar on mobile */
function useSwipeToClose(enabled: boolean, onClose: () => void) {
  const touchRef = useRef<{ startX: number; startY: number; startTime: number } | null>(null);

  const onTouchStart = useCallback((e: React.TouchEvent) => {
    if (!enabled) return;
    const t = e.touches[0];
    touchRef.current = { startX: t.clientX, startY: t.clientY, startTime: Date.now() };
  }, [enabled]);

  const onTouchEnd = useCallback((e: React.TouchEvent) => {
    if (!enabled || !touchRef.current) return;
    const t = e.changedTouches[0];
    const dx = t.clientX - touchRef.current.startX;
    const dy = Math.abs(t.clientY - touchRef.current.startY);
    const dt = Date.now() - touchRef.current.startTime;
    touchRef.current = null;
    // 左滑：dx < -60px，基本水平，400ms 内
    if (dx < -60 && dy < 80 && dt < 400) {
      onClose();
    }
  }, [enabled, onClose]);

  return { onTouchStart, onTouchEnd };
}

export function Sidebar() {
  const sidebarOpen = useUIStore((s) => s.sidebarOpen);
  const toggleSidebar = useUIStore((s) => s.toggleSidebar);
  const isMobile = useIsMobile();
  
  const activeSessionId = useSessionStore((s) => s.activeSessionId);
  const { safeTransition } = useMotionSafe();
  const [activeTab, setActiveTab] = useState<SidebarTab>("chats");

  // 首次渲染跳过动画，避免侧栏闪烁
  const isFirstRender = useRef(true);
  useEffect(() => { isFirstRender.current = false; }, []);

  // 移动端左滑关闭侧栏
  const swipe = useSwipeToClose(isMobile && sidebarOpen, toggleSidebar);

  // 移动端自动收起侧栏（首次挂载及会话变化时）
  useEffect(() => {
    if (isMobile && sidebarOpen) {
      toggleSidebar();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeSessionId, isMobile]);

  return (
    <>
      {/* Mobile backdrop */}
      <AnimatePresence>
        {isMobile && sidebarOpen && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.2 }}
            className="fixed inset-0 z-40 bg-black/50"
            onClick={toggleSidebar}
          />
        )}
      </AnimatePresence>
      <motion.aside
        animate={{ width: isMobile ? (sidebarOpen ? "min(85vw, 320px)" : 0) : (sidebarOpen ? 260 : 0) }}
        transition={isFirstRender.current ? { duration: 0 } : (safeTransition ?? sidebarTransition)}
        className={`flex flex-col border-r border-border ${
          isMobile ? "fixed inset-y-0 left-0 z-50" : ""
        }`}
        style={{ 
          backgroundColor: "var(--em-sidebar-bg)",
          overflow: sidebarOpen ? "hidden" : "hidden"
        }}
        onTouchStart={swipe.onTouchStart}
        onTouchEnd={swipe.onTouchEnd}
      >
        {/* Inner content container with fixed width to prevent layout shifts */}
        <motion.div 
          className="flex flex-col h-full"
          style={{ 
            width: isMobile ? "min(85vw, 320px)" : "260px",
            minWidth: isMobile ? "min(85vw, 320px)" : "260px"
          }}
          variants={sidebarContentVariants}
          animate={sidebarOpen ? "open" : "closed"}
          transition={isFirstRender.current ? { duration: 0 } : undefined}
        >
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-3 flex-shrink-0">
        <div className="flex items-center min-w-0">
          <img
            src="/logo.svg"
            alt="ExcelManus"
            className="h-7 flex-shrink-0"
            style={{ width: "auto", minWidth: "120px" }}
          />
        </div>
        <Button
          variant="ghost"
          size="icon"
          onClick={toggleSidebar}
          className="h-7 w-7 min-h-8 min-w-8 flex-shrink-0"
        >
          <PanelLeftClose className="h-4 w-4" />
        </Button>
      </div>

      <div
        className="h-px"
        style={{
          background:
            "linear-gradient(to right, transparent, var(--border), transparent)",
        }}
      />

      {/* Tab Navigation */}
      <div className="px-3 flex gap-1 flex-shrink-0">
        {tabs.map(({ key, label, icon: Icon }) => (
          <button
            key={key}
            className="relative flex-1 flex items-center justify-center gap-1.5 py-1.5 text-xs font-medium rounded-md cursor-pointer transition-colors duration-150 select-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--em-primary)]"
            style={activeTab === key ? { color: "var(--em-primary)" } : { color: "var(--muted-foreground)" }}
            onClick={() => setActiveTab(key)}
          >
            {activeTab === key && (
              <motion.div
                layoutId="sidebar-tab-indicator"
                className="absolute inset-0 rounded-md"
                style={{ backgroundColor: "var(--em-primary-alpha-10)" }}
                transition={{ type: "spring", stiffness: 400, damping: 30 }}
              />
            )}
            <Icon className="h-3.5 w-3.5 relative z-10" />
            <span className="relative z-10">{label}</span>
          </button>
        ))}
      </div>

      {/* Divider */}
      <div
        className="mx-3 mt-2 mb-1 h-px flex-shrink-0"
        style={{ background: "linear-gradient(to right, transparent, var(--border), transparent)" }}
      />

      {/* Tab Content — full scroll area */}
      <div className="flex-1 min-h-0 overflow-hidden">
        <ScrollArea className="h-full px-2">
          {activeTab === "chats" ? (
            <SessionList />
          ) : (
            <ExcelFilesBar embedded />
          )}
        </ScrollArea>
      </div>

        {/* Footer */}
        <div style={{ paddingBottom: isMobile ? "env(safe-area-inset-bottom)" : undefined }}>
          <StatusFooter />
        </div>
        </motion.div>
      </motion.aside>
    </>
  );
}

export function SidebarToggle() {
  const sidebarOpen = useUIStore((s) => s.sidebarOpen);
  const toggleSidebar = useUIStore((s) => s.toggleSidebar);

  if (sidebarOpen) return null;

  return (
    <Button
      variant="ghost"
      size="icon"
      onClick={toggleSidebar}
      className="h-8 w-8 mr-1"
    >
      <PanelLeft className="h-4 w-4" />
    </Button>
  );
}
