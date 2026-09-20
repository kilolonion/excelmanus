"use client";

import Image from "next/image";
import { useEffect, useRef, useCallback, useState } from "react";
import dynamic from "next/dynamic";
import { motion, AnimatePresence } from "framer-motion";
import { useIsMobile } from "@/hooks/use-mobile";
import {
  PanelLeftClose,
  PanelLeft,
  MessageSquare,
  FolderOpen,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { useUIStore } from "@/stores/ui-store";
import { useSessionStore } from "@/stores/session-store";
import { sidebarTransition, sidebarContentVariants, useMotionSafe } from "@/lib/sidebar-motion";
import { SessionList } from "./SessionList";
import { StatusFooter } from "./StatusFooter";

const ExcelFilesBar = dynamic(() => import("./ExcelFilesBar").then((m) => m.ExcelFilesBar), {
  loading: () => <div role="status" className="p-4 text-xs text-muted-foreground">正在准备文件列表…</div>,
});

const tabs: { key: "chats" | "files"; label: string; icon: typeof MessageSquare }[] = [
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
  const activeTab = useUIStore((s) => s.sidebarTab);
  const setActiveTab = useUIStore((s) => s.setSidebarTab);
  const [contentMounted, setContentMounted] = useState(sidebarOpen);

  // 首次渲染跳过动画，避免侧栏闪烁
  const isFirstRender = useRef(true);
  useEffect(() => { isFirstRender.current = false; }, []);

  // 移动端左滑关闭侧栏
  const swipe = useSwipeToClose(isMobile && sidebarOpen, toggleSidebar);

  // 移动端抽屉保持固定宽度，只动画 transform。动画 width 会在每一帧重排
  // ScrollArea 里的整份会话历史，低端移动设备上尤其明显。
  const mobileSidebarWidth = "min(88vw, 360px)";
  const sidebarAnimate = isMobile
    // Keep width out of the mobile animation entirely; only x changes.
    ? { x: sidebarOpen ? 0 : "-100%" }
    : { width: sidebarOpen ? 320 : 0, x: 0 };
  const sidebarOpenTransition = isFirstRender.current
    ? { duration: 0 }
    : (safeTransition ?? (isMobile ? { duration: 0.22, ease: "easeOut" as const } : sidebarTransition));

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
        data-coach-id="coach-sidebar"
        aria-label="侧栏"
        aria-hidden={!sidebarOpen}
        inert={!sidebarOpen ? true : undefined}
        animate={sidebarAnimate}
        transition={sidebarOpenTransition}
        onAnimationStart={() => {
          if (useUIStore.getState().sidebarOpen) setContentMounted(true);
        }}
        onAnimationComplete={() => setContentMounted(useUIStore.getState().sidebarOpen)}
        className={`em-sidebar flex flex-col ${
          isMobile ? "fixed inset-y-0 left-0 z-50" : ""
        }`}
        style={{ 
          width: isMobile ? mobileSidebarWidth : undefined,
          minWidth: isMobile ? mobileSidebarWidth : undefined,
          overflow: "hidden",
          pointerEvents: sidebarOpen ? "auto" : "none",
          willChange: isMobile && (sidebarOpen || contentMounted) ? "transform" : undefined,
          boxShadow: sidebarOpen ? undefined : "none",
        }}
        onTouchStart={swipe.onTouchStart}
        onTouchEnd={swipe.onTouchEnd}
      >
        {/* Inner content container with fixed width to prevent layout shifts */}
        <motion.div
          className="em-sidebar-inner flex flex-col h-full"
          style={{
            width: isMobile ? mobileSidebarWidth : "320px",
            minWidth: isMobile ? mobileSidebarWidth : "320px",
          }}
          variants={isMobile ? undefined : sidebarContentVariants}
          animate={isMobile ? undefined : sidebarOpen ? "open" : "closed"}
          transition={isMobile ? undefined : isFirstRender.current ? { duration: 0 } : undefined}
        >
          {/* Header */}
          <div className="em-sidebar-header flex items-center justify-between px-4 pt-4 pb-3 flex-shrink-0">
            <div className="em-brand-lockup">
              <Image
                src="/logo.svg"
                alt="ExcelManus"
                width={176}
                height={28}
                priority
                unoptimized
                className="em-brand-logo"
              />
            </div>
            <Button
              variant="ghost"
              size="icon"
              onClick={toggleSidebar}
              aria-label="收起侧栏"
              className="h-7 w-7 min-h-8 min-w-8 flex-shrink-0"
            >
              <PanelLeftClose className="h-4 w-4" />
            </Button>
          </div>

          {/* Tab Navigation */}
          <div className="em-sidebar-tabs mx-3 flex gap-1 flex-shrink-0" data-coach-id="coach-sidebar-tabs">
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

          {/* Unmount inactive content and release the drawer after its exit
              animation, so hidden lists have no subscriptions or scan effects. */}
          <div className="flex-1 min-h-0 overflow-hidden relative">
            {(sidebarOpen || contentMounted) && (activeTab === "chats" ? (
              <div className="h-full px-2">
                <SessionList />
              </div>
            ) : (
              <div className="h-full min-h-0">
                <ExcelFilesBar embedded />
              </div>
            ))}
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
      aria-label="展开侧栏"
      className="h-8 w-8 mr-1"
    >
      <PanelLeft className="h-4 w-4" />
    </Button>
  );
}
