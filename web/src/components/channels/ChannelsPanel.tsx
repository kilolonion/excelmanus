"use client";

import { useState, useCallback, useEffect, useRef } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  Radio,
  AlertCircle,
  CheckCircle2,
  Info,
  Settings2,
  BookOpen,
} from "lucide-react";
import { useUIStore } from "@/stores/ui-store";
import { useAuthStore } from "@/stores/auth-store";
import { useAuthConfigStore } from "@/stores/auth-config-store";
import { SlidePanel } from "@/components/ui/slide-panel";
import {
  ChannelOverviewTab,
  ChannelSettingsTab,
  ChannelReferenceTab,
} from "@/components/settings/ChannelsTab";
import {
  fetchChannelsStatus,
  type ChannelStatusInfo,
} from "@/lib/api";

// ── Toast ──────────────────────────────────────────────────

function PanelToast({
  message,
  type,
  onClose,
}: {
  message: string;
  type: "success" | "error" | "info";
  onClose: () => void;
}) {
  useEffect(() => {
    const t = setTimeout(onClose, 4000);
    return () => clearTimeout(t);
  }, [onClose]);

  const colors = {
    success: "bg-green-500/15 text-green-700 dark:text-green-400 border-green-500/30",
    error: "bg-red-500/15 text-red-700 dark:text-red-400 border-red-500/30",
    info: "bg-blue-500/15 text-blue-700 dark:text-blue-400 border-blue-500/30",
  };
  const icons = {
    success: <CheckCircle2 className="h-3.5 w-3.5 shrink-0" />,
    error: <AlertCircle className="h-3.5 w-3.5 shrink-0" />,
    info: <Info className="h-3.5 w-3.5 shrink-0" />,
  };

  return (
    <motion.div
      initial={{ opacity: 0, y: -10 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -10 }}
      className={`flex items-center gap-2 px-3 py-2 rounded-md border text-xs ${colors[type]}`}
    >
      {icons[type]}
      <span className="flex-1">{message}</span>
      <button onClick={onClose} className="text-current opacity-60 hover:opacity-100 cursor-pointer">×</button>
    </motion.div>
  );
}

// ── Tab Definitions ────────────────────────────────────────

interface TabDef {
  id: string;
  label: string;
  icon: typeof Radio;
}

const TABS: TabDef[] = [
  { id: "channels", label: "渠道", icon: Radio },
  { id: "settings", label: "设置", icon: Settings2 },
  { id: "reference", label: "参考", icon: BookOpen },
];

// ── Tab Bar ────────────────────────────────────────────────

function TabBar({
  activeTab,
  onTabChange,
  runningCount,
}: {
  activeTab: string;
  onTabChange: (id: string) => void;
  runningCount: number;
}) {
  return (
    <div className="flex items-center gap-0.5 sm:gap-1 px-3 sm:px-4 pt-3 pb-1">
      {TABS.map((tab) => {
        const isActive = activeTab === tab.id;
        const Icon = tab.icon;
        return (
          <button
            key={tab.id}
            type="button"
            onClick={() => onTabChange(tab.id)}
            className={`relative flex-1 sm:flex-none flex items-center justify-center sm:justify-start gap-1.5 px-2.5 sm:px-3.5 py-2.5 sm:py-2 rounded-lg text-xs font-medium transition-colors cursor-pointer ${
              isActive
                ? "text-foreground"
                : "text-muted-foreground hover:text-foreground hover:bg-muted/50"
            }`}
          >
            {isActive && (
              <motion.div
                layoutId="channel-tab-indicator"
                className="absolute inset-0 rounded-lg"
                style={{ backgroundColor: "var(--em-primary-alpha-10)" }}
                transition={{ type: "spring", damping: 25, stiffness: 300 }}
              />
            )}
            <Icon
              className="h-3.5 w-3.5 relative z-10"
              style={isActive ? { color: "var(--em-primary)" } : undefined}
            />
            <span className="relative z-10">{tab.label}</span>
            {tab.id === "channels" && runningCount > 0 && (
              <span
                className="relative z-10 ml-0.5 h-4 min-w-4 px-1 rounded-full text-[10px] font-bold flex items-center justify-center text-white"
                style={{ backgroundColor: "var(--em-primary)" }}
              >
                {runningCount}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}

// ── Main Panel ─────────────────────────────────────────────

export function ChannelsPanel() {
  const channelsOpen = useUIStore((s) => s.channelsOpen);
  const closeChannels = useUIStore((s) => s.closeChannels);
  const authEnabled = useAuthConfigStore((s) => s.authEnabled);
  const user = useAuthStore((s) => s.user);

  const [activeTab, setActiveTab] = useState("channels");
  const [status, setStatus] = useState<ChannelStatusInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<{ msg: string; type: "success" | "error" | "info" } | null>(null);

  const refresh = useCallback(() => {
    setError(null);
    setLoading(true);
    fetchChannelsStatus()
      .then((next) => {
        setStatus(next);
        setError(null);
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  // Refresh on panel open
  const prevOpenRef = useRef(false);
  useEffect(() => {
    if (channelsOpen && !prevOpenRef.current) {
      refresh();
    }
    prevOpenRef.current = channelsOpen;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [channelsOpen]);

  const handleToast = useCallback((msg: string, type: "success" | "error" | "info") => {
    setToast({ msg, type });
  }, []);

  const runningCount = status?.channels?.length || 0;
  const showBind = authEnabled && !!user;

  return (
    <SlidePanel
      open={channelsOpen}
      onClose={closeChannels}
      title="渠道中心"
      icon={<Radio className="h-4 w-4" style={{ color: "var(--em-primary)" }} />}
      width={500}
    >
      {channelsOpen && (
        <div className="flex flex-col h-full">
          {/* Toast — floating at top */}
          <AnimatePresence>
            {toast && (
              <div className="absolute top-14 left-4 right-4 z-50 pointer-events-auto">
                <PanelToast
                  message={toast.msg}
                  type={toast.type}
                  onClose={() => setToast(null)}
                />
              </div>
            )}
          </AnimatePresence>

          {/* Tab Bar */}
          <TabBar
            activeTab={activeTab}
            onTabChange={setActiveTab}
            runningCount={runningCount}
          />

          {/* Divider */}
          <div className="mx-3 sm:mx-4 mt-1">
            <div className="h-px bg-gradient-to-r from-transparent via-border to-transparent" />
          </div>

          {/* Tab Content */}
          <div className="flex-1 min-h-0 overflow-y-auto">
            <AnimatePresence mode="wait">
              {activeTab === "channels" && (
                <motion.div
                  key="tab-channels"
                  initial={{ opacity: 0, x: -12 }}
                  animate={{ opacity: 1, x: 0 }}
                  exit={{ opacity: 0, x: 12 }}
                  transition={{ duration: 0.2, ease: "easeOut" }}
                  className="p-3 sm:p-4 space-y-4 sm:space-y-5"
                >
                  <ChannelOverviewTab
                    status={status}
                    loading={loading}
                    error={error}
                    onRefresh={refresh}
                    onToast={handleToast}
                    showBind={showBind || undefined}
                  />
                </motion.div>
              )}
              {activeTab === "settings" && (
                <motion.div
                  key="tab-settings"
                  initial={{ opacity: 0, x: -12 }}
                  animate={{ opacity: 1, x: 0 }}
                  exit={{ opacity: 0, x: 12 }}
                  transition={{ duration: 0.2, ease: "easeOut" }}
                  className="p-3 sm:p-4 space-y-3 sm:space-y-4"
                >
                  <ChannelSettingsTab
                    status={status}
                    loading={loading}
                    onRefresh={refresh}
                    onToast={handleToast}
                  />
                </motion.div>
              )}
              {activeTab === "reference" && (
                <motion.div
                  key="tab-reference"
                  initial={{ opacity: 0, x: -12 }}
                  animate={{ opacity: 1, x: 0 }}
                  exit={{ opacity: 0, x: 12 }}
                  transition={{ duration: 0.2, ease: "easeOut" }}
                  className="p-3 sm:p-4 space-y-3 sm:space-y-4"
                >
                  <ChannelReferenceTab status={status} onRefresh={refresh} onToast={handleToast} />
                </motion.div>
              )}
            </AnimatePresence>
          </div>
        </div>
      )}
    </SlidePanel>
  );
}
