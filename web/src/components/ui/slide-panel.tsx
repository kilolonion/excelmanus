"use client";

import { useEffect, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useIsMobile } from "@/hooks/use-mobile";

interface SlidePanelProps {
  open: boolean;
  onClose: () => void;
  title: string;
  icon?: React.ReactNode;
  children: React.ReactNode;
  /** Panel width on desktop, default 520px */
  width?: number;
}

export function SlidePanel({ open, onClose, title, icon, children, width = 520 }: SlidePanelProps) {
  const isMobile = useIsMobile();

  // Escape key to close
  const handleKeyDown = useCallback(
    (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    },
    [onClose],
  );

  useEffect(() => {
    if (open) {
      document.addEventListener("keydown", handleKeyDown);
      return () => document.removeEventListener("keydown", handleKeyDown);
    }
  }, [open, handleKeyDown]);

  return (
    <AnimatePresence>
      {open && (
        <>
          {/* Backdrop */}
          <motion.div
            key="slide-panel-backdrop"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.2 }}
            className="fixed inset-0 z-[60] bg-black/40 backdrop-blur-[2px]"
            onClick={onClose}
          />
          {/* Panel */}
          <motion.div
            key="slide-panel-content"
            initial={isMobile ? { y: "100%" } : { x: "100%" }}
            animate={isMobile ? { y: 0 } : { x: 0 }}
            exit={isMobile ? { y: "100%" } : { x: "100%" }}
            transition={{ type: "spring", damping: 30, stiffness: 350 }}
            className={`fixed z-[61] bg-background border-l border-border shadow-2xl flex flex-col ${
              isMobile
                ? "inset-x-0 bottom-0 top-0 rounded-none"
                : "top-0 right-0 bottom-0 rounded-none"
            }`}
            style={isMobile ? undefined : { width: `min(${width}px, 90vw)` }}
          >
            {/* Header */}
            <div className="flex items-center gap-2.5 px-5 py-3 border-b border-border/60 flex-shrink-0 relative">
              <div className="absolute bottom-0 left-0 right-0 h-px bg-gradient-to-r from-transparent via-[var(--em-primary-alpha-25)] to-transparent" />
              {icon && (
                <div
                  className="flex h-8 w-8 items-center justify-center rounded-lg flex-shrink-0"
                  style={{ backgroundColor: "var(--em-primary-alpha-10)" }}
                >
                  {icon}
                </div>
              )}
              <h2 className="text-base font-bold tracking-tight flex-1">{title}</h2>
              <Button
                variant="ghost"
                size="icon"
                className="h-8 w-8 rounded-lg flex-shrink-0"
                onClick={onClose}
              >
                <X className="h-4 w-4" />
              </Button>
            </div>
            {/* Content */}
            <div className="flex-1 min-h-0 overflow-y-auto">
              {children}
            </div>
          </motion.div>
        </>
      )}
    </AnimatePresence>
  );
}
