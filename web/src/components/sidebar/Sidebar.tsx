"use client";

import { useEffect, useState, useRef, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { useIsMobile, useIsDesktop, useIsMediumScreen } from "@/hooks/use-mobile";
import { uuid } from "@/lib/utils";
import {
  MessageSquarePlus,
  PanelLeftClose,
  PanelLeft,
  ChevronDown,
  Trash2,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { useUIStore } from "@/stores/ui-store";
import { useSessionStore } from "@/stores/session-store";
import { useChatStore } from "@/stores/chat-store";
import { useExcelStore } from "@/stores/excel-store";
import { sidebarTransition, sidebarContentVariants, useMotionSafe } from "@/lib/sidebar-motion";
import { SessionList } from "./SessionList";
import { ExcelFilesBar } from "./ExcelFilesBar";
import { StatusFooter } from "./StatusFooter";

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
    // Swipe left: dx < -60px, mostly horizontal, within 400ms
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
  const isDesktop = useIsDesktop();
  const isMediumScreen = useIsMediumScreen();
  
  const activeSessionId = useSessionStore((s) => s.activeSessionId);
  const sessions = useSessionStore((s) => s.sessions);
  const addSession = useSessionStore((s) => s.addSession);
  const setActiveSession = useSessionStore((s) => s.setActiveSession);
  const switchSession = useChatStore((s) => s.switchSession);
  const clearAllHistory = useChatStore((s) => s.clearAllHistory);
  const recentFiles = useExcelStore((s) => s.recentFiles);
  const { safeTransition } = useMotionSafe();
  const [newChatHover, setNewChatHover] = useState(false);
  const [chatOpen, setChatOpen] = useState(true);
  const [excelOpen, setExcelOpen] = useState(true);
  const [clearing, setClearing] = useState(false);

  // Skip animation on first render to prevent sidebar flash
  const isFirstRender = useRef(true);
  useEffect(() => { isFirstRender.current = false; }, []);

  const handleNewChat = () => {
    const id = uuid();
    addSession({
      id,
      title: "新对话",
      messageCount: 0,
      inFlight: false,
    });
    setActiveSession(id);
    switchSession(id);
  };

  const hasExcelFiles = recentFiles.length > 0;

  // Swipe-left to close sidebar on mobile
  const swipe = useSwipeToClose(isMobile && sidebarOpen, toggleSidebar);

  // Auto-close sidebar on mobile (initial mount + session changes)
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
        animate={{ width: isMobile ? (sidebarOpen ? 280 : 0) : (sidebarOpen ? 260 : 0) }}
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
            width: isMobile ? "280px" : "260px",
            minWidth: isMobile ? "280px" : "260px"
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

      {/* New Chat Button */}
      <div className="p-3">
        <Button
          className="w-full justify-start gap-2 text-white hover:scale-[1.02] transition-transform duration-150 ease-out"
          size="sm"
          style={{
            backgroundColor: newChatHover
              ? "var(--em-primary-light)"
              : "var(--em-primary)",
          }}
          onPointerEnter={() => setNewChatHover(true)}
          onPointerLeave={() => setNewChatHover(false)}
          onClick={handleNewChat}
        >
          <MessageSquarePlus className="h-4 w-4" />
          新建对话
        </Button>
      </div>

      {/* Two-section scrollable area */}
      <div className="flex-1 flex flex-col min-h-0 overflow-hidden">
        {/* Section 1: Chat History */}
        <Collapsible open={chatOpen} onOpenChange={setChatOpen} className={`flex flex-col min-h-0 ${chatOpen ? "flex-1" : "flex-shrink-0"}`}>
          <CollapsibleTrigger asChild>
            <div className="flex items-center justify-between px-3 py-1.5 text-xs font-semibold text-muted-foreground uppercase tracking-wider hover:text-foreground transition-colors cursor-pointer select-none group/collapse">
              <span>对话历史</span>
              <div className="flex items-center gap-0.5">
                {sessions.length > 0 && (
                  <button
                    type="button"
                    className="p-0.5 rounded hover:bg-destructive/20 hover:text-destructive transition-colors md:opacity-0 md:group-hover/collapse:opacity-100 focus:opacity-100"
                    onClick={async (e) => {
                      e.preventDefault();
                      e.stopPropagation();
                      if (clearing) return;
                      if (!window.confirm("确定清空所有会话历史？此操作不可恢复。")) return;
                      setClearing(true);
                      try {
                        await clearAllHistory();
                      } finally {
                        setClearing(false);
                      }
                    }}
                    title="清空全部历史"
                  >
                    <Trash2 className="h-3 w-3" />
                  </button>
                )}
                <ChevronDown
                  className={`h-3 w-3 transition-transform duration-200 ${
                    chatOpen ? "" : "-rotate-90"
                  }`}
                />
              </div>
            </div>
          </CollapsibleTrigger>
          <CollapsibleContent className="flex-1 min-h-0">
            <ScrollArea className="h-full px-2">
              <SessionList />
            </ScrollArea>
          </CollapsibleContent>
        </Collapsible>

        {/* Divider between sections */}
        <div className="mx-3 my-2 flex-shrink-0 flex items-center gap-2">
          <div className="flex-1 h-px opacity-25" style={{ background: "linear-gradient(to right, transparent, var(--em-primary))" }} />
          <div className="h-1 w-1 rounded-full opacity-40" style={{ backgroundColor: "var(--em-primary)" }} />
          <div className="flex-1 h-px opacity-25" style={{ background: "linear-gradient(to left, transparent, var(--em-primary))" }} />
        </div>

        {/* Section 2: Workspace Files — expands when chat is collapsed */}
        <Collapsible open={excelOpen} onOpenChange={setExcelOpen} className={`flex flex-col min-h-0 ${chatOpen ? "flex-shrink-0" : "flex-1"}`} style={chatOpen ? { maxHeight: "40%" } : undefined}>
          <CollapsibleTrigger className="flex items-center justify-between px-3 py-1.5 text-xs font-semibold text-muted-foreground uppercase tracking-wider hover:text-foreground transition-colors cursor-pointer select-none">
            <span>工作区文件</span>
            <ChevronDown
              className={`h-3 w-3 transition-transform duration-200 ${
                excelOpen ? "" : "-rotate-90"
              }`}
            />
          </CollapsibleTrigger>
          <CollapsibleContent className="flex-1 min-h-0 overflow-hidden">
            <ScrollArea className="h-full">
              <ExcelFilesBar embedded />
            </ScrollArea>
          </CollapsibleContent>
        </Collapsible>
      </div>

        {/* Footer */}
        <StatusFooter />
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
