"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import dynamic from "next/dynamic";
import { useRouter } from "next/navigation";
import { Circle, Wrench, Package, Activity, LogOut, User } from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
import { apiGet } from "@/lib/api";
import { useAuthStore } from "@/stores/auth-store";
import { logout } from "@/lib/auth-api";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";

const SettingsDialog = dynamic(
  () => import("@/components/settings/SettingsDialog").then((m) => ({ default: m.SettingsDialog })),
  { ssr: false, loading: () => <div className="h-7 w-7" /> }
);

interface HealthData {
  status: string;
  version: string;
  model: string;
  tools: string[];
  skillpacks: string[];
  active_sessions: number;
}

export function StatusFooter() {
  const [health, setHealth] = useState<HealthData | null>(null);
  const [connected, setConnected] = useState<boolean | null>(null);
  const [reconnectFlash, setReconnectFlash] = useState(false);
  const prevConnected = useRef<boolean | null>(null);

  const poll = useCallback(async () => {
    try {
      const data = await apiGet<HealthData>("/health");
      setHealth(data);
      setConnected(true);
    } catch {
      setConnected(false);
    }
  }, []);

  useEffect(() => {
    poll();
    const id = setInterval(poll, 15000);
    return () => clearInterval(id);
  }, [poll]);

  // 连接恢复闪烁动画
  useEffect(() => {
    if (prevConnected.current === false && connected === true) {
      setReconnectFlash(true);
      const timer = setTimeout(() => setReconnectFlash(false), 500);
      return () => clearTimeout(timer);
    }
    prevConnected.current = connected;
  }, [connected]);

  const dotColor =
    connected === null
      ? "text-muted-foreground"
      : connected
        ? "text-green-500"
        : "text-red-500";

  const dotLabel =
    connected === null
      ? "检查中…"
      : connected
        ? "已连接"
        : "未连接";

  // 已连接时添加脉冲动画类
  const dotClasses = `h-2 w-2 fill-current ${dotColor}${connected === true ? " animate-pulse-green" : ""}`;

  return (
    <>
      {/* 渐变分隔线 */}
      <div
        className="h-px"
        style={{
          background:
            "linear-gradient(to right, transparent, var(--border), transparent)",
        }}
      />

      <div className="px-3 py-2 min-h-[87px] flex items-center justify-between gap-2">
        <TooltipProvider delayDuration={300}>
          <div className="flex items-center gap-2.5 text-xs text-muted-foreground min-w-0">
            {/* 连接状态 */}
            <Tooltip>
              <TooltipTrigger asChild>
                <span
                  className={`flex items-center gap-1 cursor-default min-h-[32px] min-w-[32px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--em-primary)] focus-visible:ring-offset-1 rounded-sm${reconnectFlash ? " animate-pulse-green" : ""}`}
                  style={reconnectFlash ? { color: "var(--em-primary)" } : undefined}
                  tabIndex={0}
                >
                  <Circle className={dotClasses} />
                  {health?.version ? (
                    <span className="truncate max-w-[60px]">
                      v{health.version}
                    </span>
                  ) : (
                    <span>{dotLabel}</span>
                  )}
                </span>
              </TooltipTrigger>
              <TooltipContent side="top" className="text-xs">
                <span style={{ color: "var(--em-primary)" }}>{dotLabel}</span>
                {health && (
                  <>
                    <br />
                    模型: {health.model}
                  </>
                )}
              </TooltipContent>
            </Tooltip>

            {health && (
              <>
                {/* 工具数 */}
                <Tooltip>
                  <TooltipTrigger asChild>
                    <span className="flex items-center gap-0.5 cursor-default min-h-[32px] min-w-[32px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--em-primary)] focus-visible:ring-offset-1 rounded-sm" tabIndex={0}>
                      <Wrench
                        className="h-3 w-3"
                        style={{ color: "var(--em-primary)" }}
                      />
                      <AnimatePresence mode="wait">
                        <motion.span
                          key={health.tools.length}
                          initial={{ opacity: 0, y: -4 }}
                          animate={{ opacity: 1, y: 0 }}
                          exit={{ opacity: 0, y: 4 }}
                          transition={{ duration: 0.2 }}
                        >
                          {health.tools.length}
                        </motion.span>
                      </AnimatePresence>
                    </span>
                  </TooltipTrigger>
                  <TooltipContent side="top" className="text-xs">
                    <span style={{ color: "var(--em-primary)" }}>
                      已注册工具:
                    </span>{" "}
                    {health.tools.length}
                  </TooltipContent>
                </Tooltip>

                {/* 技能包数 */}
                <Tooltip>
                  <TooltipTrigger asChild>
                    <span className="flex items-center gap-0.5 cursor-default min-h-[32px] min-w-[32px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--em-primary)] focus-visible:ring-offset-1 rounded-sm" tabIndex={0}>
                      <Package
                        className="h-3 w-3"
                        style={{ color: "var(--em-primary)" }}
                      />
                      <AnimatePresence mode="wait">
                        <motion.span
                          key={health.skillpacks.length}
                          initial={{ opacity: 0, y: -4 }}
                          animate={{ opacity: 1, y: 0 }}
                          exit={{ opacity: 0, y: 4 }}
                          transition={{ duration: 0.2 }}
                        >
                          {health.skillpacks.length}
                        </motion.span>
                      </AnimatePresence>
                    </span>
                  </TooltipTrigger>
                  <TooltipContent side="top" className="text-xs">
                    <span style={{ color: "var(--em-primary)" }}>
                      已加载技能包:
                    </span>{" "}
                    {health.skillpacks.length}
                  </TooltipContent>
                </Tooltip>

                {/* 活跃会话 */}
                <Tooltip>
                  <TooltipTrigger asChild>
                    <span className="flex items-center gap-0.5 cursor-default min-h-[32px] min-w-[32px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--em-primary)] focus-visible:ring-offset-1 rounded-sm" tabIndex={0}>
                      <Activity
                        className="h-3 w-3"
                        style={{ color: "var(--em-primary)" }}
                      />
                      <AnimatePresence mode="wait">
                        <motion.span
                          key={health.active_sessions}
                          initial={{ opacity: 0, y: -4 }}
                          animate={{ opacity: 1, y: 0 }}
                          exit={{ opacity: 0, y: 4 }}
                          transition={{ duration: 0.2 }}
                        >
                          {health.active_sessions}
                        </motion.span>
                      </AnimatePresence>
                    </span>
                  </TooltipTrigger>
                  <TooltipContent side="top" className="text-xs">
                    <span style={{ color: "var(--em-primary)" }}>
                      活跃会话:
                    </span>{" "}
                    {health.active_sessions}
                  </TooltipContent>
                </Tooltip>
              </>
            )}
          </div>
        </TooltipProvider>

        <div className="flex items-center gap-1.5">
          {/* User avatar & logout (only when authenticated) */}
          <UserBadge />
          {/* 设置按钮：hover 旋转 90deg */}
          <div className="transition-transform duration-300 ease-out hover:rotate-90">
            <SettingsDialog />
          </div>
        </div>
      </div>
    </>
  );
}

function UserBadge() {
  const router = useRouter();
  const user = useAuthStore((s) => s.user);
  if (!user) return null;

  const initial = (user.displayName || user.email)[0]?.toUpperCase() || "U";

  return (
    <TooltipProvider delayDuration={300}>
      <Tooltip>
        <TooltipTrigger asChild>
          <button
            onClick={() => {
              logout();
              router.push("/login");
            }}
            className="flex items-center gap-1 rounded-full px-1.5 py-1 text-xs text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
          >
            {user.avatarUrl ? (
              <img src={user.avatarUrl} alt="" className="h-5 w-5 rounded-full" />
            ) : (
              <span
                className="h-5 w-5 rounded-full flex items-center justify-center text-[10px] font-medium text-white"
                style={{ backgroundColor: "var(--em-primary)" }}
              >
                {initial}
              </span>
            )}
            <LogOut className="h-3 w-3 ml-0.5 opacity-60" />
          </button>
        </TooltipTrigger>
        <TooltipContent side="top" className="text-xs">
          {user.displayName || user.email} · 点击退出
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}
