"use client";

import { useState, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Radio, Link2, Check, AlertCircle } from "lucide-react";
import { useUIStore } from "@/stores/ui-store";
import { useAuthStore } from "@/stores/auth-store";
import { useAuthConfigStore } from "@/stores/auth-config-store";
import { SlidePanel } from "@/components/ui/slide-panel";
import { ChannelsTab } from "@/components/settings/ChannelsTab";
import { ChannelBindSection } from "./ChannelBindSection";

function BindToast({
  message,
  type,
  onClose,
}: {
  message: string;
  type: "success" | "error";
  onClose: () => void;
}) {
  return (
    <motion.div
      initial={{ opacity: 0, y: -10 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -10 }}
      className={`flex items-center gap-2 px-3 py-2 rounded-md border text-xs mx-4 mt-3 ${
        type === "success"
          ? "bg-green-500/15 text-green-700 dark:text-green-400 border-green-500/30"
          : "bg-red-500/15 text-red-700 dark:text-red-400 border-red-500/30"
      }`}
    >
      {type === "success" ? (
        <Check className="h-3.5 w-3.5 shrink-0" />
      ) : (
        <AlertCircle className="h-3.5 w-3.5 shrink-0" />
      )}
      <span className="flex-1">{message}</span>
      <button onClick={onClose} className="text-current opacity-60 hover:opacity-100 cursor-pointer">×</button>
    </motion.div>
  );
}

export function ChannelsPanel() {
  const channelsOpen = useUIStore((s) => s.channelsOpen);
  const closeChannels = useUIStore((s) => s.closeChannels);
  const authEnabled = useAuthConfigStore((s) => s.authEnabled);
  const user = useAuthStore((s) => s.user);

  const [bindToast, setBindToast] = useState<{ message: string; type: "success" | "error" } | null>(null);
  const showBindToast = useCallback(
    (message: string, type: "success" | "error") => {
      setBindToast({ message, type });
      setTimeout(() => setBindToast(null), 3000);
    },
    [],
  );

  return (
    <SlidePanel
      open={channelsOpen}
      onClose={closeChannels}
      title="渠道配置"
      icon={<Radio className="h-4 w-4" style={{ color: "var(--em-primary)" }} />}
      width={480}
    >
      {channelsOpen && (
        <>
          <ChannelsTab />

          {/* ── 渠道绑定（仅 auth 启用且已登录时显示） ── */}
          {authEnabled && user && (
            <>
              <div className="mx-4 my-5">
                <div className="h-px bg-gradient-to-r from-transparent via-border to-transparent" />
              </div>
              <div className="px-4 pb-5">
                <div className="flex items-center gap-2 mb-4">
                  <div
                    className="h-8 w-8 rounded-lg flex items-center justify-center"
                    style={{ backgroundColor: "var(--em-primary-alpha-10)" }}
                  >
                    <Link2 className="h-4 w-4" style={{ color: "var(--em-primary)" }} />
                  </div>
                  <h3 className="text-sm font-semibold">渠道绑定</h3>
                </div>
                <ChannelBindSection showToast={showBindToast} />
              </div>
            </>
          )}

          {/* Bind toast */}
          <AnimatePresence>
            {bindToast && (
              <div className="sticky bottom-0 pb-3">
                <BindToast
                  message={bindToast.message}
                  type={bindToast.type}
                  onClose={() => setBindToast(null)}
                />
              </div>
            )}
          </AnimatePresence>
        </>
      )}
    </SlidePanel>
  );
}
