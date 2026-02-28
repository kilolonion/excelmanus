"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { createPortal } from "react-dom";
import { useRouter } from "next/navigation";
import { Circle, LogOut, ArrowRightLeft, ChevronUp, LogIn, X, Clock, Users, HardDrive, Settings, UserCircle } from "lucide-react";
import { apiGet, proxyAvatarUrl } from "@/lib/api";
import { useIsMobile } from "@/hooks/use-mobile";
import { useAuthStore } from "@/stores/auth-store";
import { useAuthConfigStore } from "@/stores/auth-config-store";
import { useUIStore } from "@/stores/ui-store";
import { useRecentAccountsStore, type RecentAccount } from "@/stores/recent-accounts-store";
import { logout, fetchMyWorkspaceUsage, type WorkspaceUsage } from "@/lib/auth-api";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
  DropdownMenuGroup,
} from "@/components/ui/dropdown-menu";


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
  const isMobile = useIsMobile();
  const [openTooltipId, setOpenTooltipId] = useState<string | null>(null);
  const authEnabled = useAuthConfigStore((s) => s.authEnabled);
  const user = useAuthStore((s) => s.user);
  const showFullBadge = !isMobile && authEnabled && !!user;

  // 移动端：点击 tooltip 区域外关闭当前打开的 tooltip
  useEffect(() => {
    if (!isMobile || !openTooltipId) return;
    const handler = (e: PointerEvent) => {
      const target = e.target as HTMLElement;
      if (target.closest('[data-slot="tooltip-trigger"]') || target.closest('[data-slot="tooltip-content"]')) return;
      setOpenTooltipId(null);
    };
    document.addEventListener("pointerdown", handler);
    return () => document.removeEventListener("pointerdown", handler);
  }, [isMobile, openTooltipId]);

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
      {/* Desktop: full user badge row */}
      {showFullBadge && (
        <>
          <div
            className="h-px flex-shrink-0"
            style={{
              background:
                "linear-gradient(to right, transparent, var(--border), transparent)",
            }}
          />
          <div className="px-2 py-1.5 flex-shrink-0">
            <UserBadge />
          </div>
        </>
      )}

      {/* 渐变分隔线 */}
      <div
        className="h-px flex-shrink-0"
        style={{
          background:
            "linear-gradient(to right, transparent, var(--border), transparent)",
        }}
      />

      <div className="px-3 py-2 flex items-center justify-between flex-shrink-0">
        {/* Left: connection status + version + compact metrics */}
        <TooltipProvider delayDuration={300}>
          <Tooltip
            open={isMobile ? openTooltipId === "conn" : undefined}
            onOpenChange={isMobile ? () => {} : undefined}
          >
            <TooltipTrigger asChild>
              <span
                className={`flex items-center gap-1.5 text-[11px] text-muted-foreground cursor-default rounded-sm${reconnectFlash ? " animate-pulse-green" : ""}`}
                style={reconnectFlash ? { color: "var(--em-primary)" } : undefined}
                tabIndex={0}
                onClick={isMobile ? () => setOpenTooltipId((prev) => prev === "conn" ? null : "conn") : undefined}
              >
                <Circle className={dotClasses} />
                {health?.version ? (
                  <span className="truncate max-w-[56px]">v{health.version}</span>
                ) : (
                  <span>{dotLabel}</span>
                )}
                {health && (
                  <span className="text-muted-foreground/50 hidden sm:inline">
                    · {health.tools.length}T · {health.skillpacks.length}S
                  </span>
                )}
              </span>
            </TooltipTrigger>
            <TooltipContent side="top" className="text-xs">
              <span style={{ color: "var(--em-primary)" }}>{dotLabel}</span>
              {health && (
                <>
                  <br />模型: {health.model}
                  <br />工具: {health.tools.length} · 技能包: {health.skillpacks.length} · 会话: {health.active_sessions}
                </>
              )}
            </TooltipContent>
          </Tooltip>
        </TooltipProvider>

        {/* Right: settings gear (desktop) + user badge (compact, only when full badge not shown) */}
        <div className="flex items-center gap-1 flex-shrink-0">
          {!isMobile && (
            <button
              onClick={() => useUIStore.getState().openSettings("model")}
              className="h-7 w-7 inline-flex items-center justify-center rounded-md text-muted-foreground hover:text-foreground hover:bg-accent transition-colors"
              title="设置"
            >
              <Settings className="h-4 w-4" />
            </button>
          )}
          {!showFullBadge && <UserBadge compact />}
        </div>
      </div>
    </>
  );
}

function Avatar({ src, name, size = 5 }: { src?: string | null; name: string; size?: number }) {
  const [failedSrc, setFailedSrc] = useState<string | null>(null);
  const accessToken = useAuthStore((s) => s.accessToken);
  const initial = name[0]?.toUpperCase() || "U";
  const px = size * 4;
  const textSize = size <= 5 ? "text-[10px]" : "text-sm";

  const proxiedSrc = (() => {
    const base = proxyAvatarUrl(src);
    if (!base || !accessToken) return base;
    if (base.includes("/avatar-file")) {
      const sep = base.includes("?") ? "&" : "?";
      return `${base}${sep}token=${accessToken}`;
    }
    return base;
  })();

  if (proxiedSrc && failedSrc !== src) {
    return (
      <img
        src={proxiedSrc}
        alt=""
        className="rounded-full flex-shrink-0"
        style={{ width: px, height: px }}
        referrerPolicy="no-referrer"
        onError={() => setFailedSrc(src ?? null)}
      />
    );
  }
  return (
    <span
      className={`rounded-full flex items-center justify-center ${textSize} font-medium text-white flex-shrink-0`}
      style={{ width: px, height: px, backgroundColor: "var(--em-primary)" }}
    >
      {initial}
    </span>
  );
}

function WorkspaceIndicator({ usage }: { usage: WorkspaceUsage | null }) {
  if (!usage) return null;
  const pct = usage.max_size_mb > 0
    ? Math.min((usage.size_mb / usage.max_size_mb) * 100, 100)
    : 0;
  const isOver = usage.over_size || usage.over_files;
  const barColor = isOver ? "bg-red-500" : pct > 80 ? "bg-amber-500" : "";
  const barStyle = !isOver && pct <= 80 ? { backgroundColor: "var(--em-primary)" } : undefined;

  return (
    <div className="px-4 py-2.5">
      <div className="flex items-center gap-1.5 text-[11px] text-muted-foreground mb-1.5">
        <HardDrive className="h-3 w-3 flex-shrink-0" />
        <span className="font-medium">工作空间</span>
        <span className="ml-auto tabular-nums">
          {usage.size_mb.toFixed(1)} / {usage.max_size_mb} MB
        </span>
      </div>
      <div className="h-[5px] rounded-full bg-muted overflow-hidden">
        <motion.div
          className={`h-full rounded-full ${barColor}`}
          style={barStyle}
          initial={{ width: 0 }}
          animate={{ width: `${pct}%` }}
          transition={{ duration: 0.8, ease: "easeOut" }}
        />
      </div>
      <p className="text-[10px] text-muted-foreground/50 mt-1 tabular-nums">
        {usage.file_count} / {usage.max_files} 文件
      </p>
    </div>
  );
}

function MenuButton({ icon: Icon, label, onClick, destructive = false }: {
  icon: typeof Settings;
  label: string;
  onClick: () => void;
  destructive?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      className={`flex items-center gap-2.5 w-full px-2.5 py-2 rounded-lg text-[13px] transition-colors cursor-pointer ${
        destructive
          ? "text-destructive hover:bg-destructive/8"
          : "hover:bg-muted/70"
      }`}
    >
      <Icon className={`h-4 w-4 flex-shrink-0 ${destructive ? "" : "text-muted-foreground"}`} />
      <span>{label}</span>
    </button>
  );
}

function UserBadge({ compact = false }: { compact?: boolean }) {
  const router = useRouter();
  const user = useAuthStore((s) => s.user);
  const authEnabled = useAuthConfigStore((s) => s.authEnabled);
  const openSettings = useUIStore((s) => s.openSettings);
  const recentAccounts = useRecentAccountsStore((s) => s.accounts);
  const removeAccount = useRecentAccountsStore((s) => s.removeAccount);
  const [wsUsage, setWsUsage] = useState<WorkspaceUsage | null>(null);
  const isMobile = useIsMobile();
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!authEnabled || !user) return;
    fetchMyWorkspaceUsage().then(setWsUsage).catch(() => {});
  }, [authEnabled, user]);

  // compact mode without auth or user: on desktop, settings button is in footer; on mobile, show gear
  if (compact && (!authEnabled || !user)) {
    if (!isMobile) return null;
    return (
      <button
        onClick={() => openSettings("model")}
        className="h-7 w-7 inline-flex items-center justify-center rounded-md text-muted-foreground hover:text-foreground hover:bg-accent transition-colors"
      >
        <Settings className="h-4 w-4" />
      </button>
    );
  }

  if (!authEnabled) return null;

  if (!user) {
    return (
      <button
        onClick={() => router.push("/login")}
        className="flex items-center gap-2 w-full rounded-lg px-2 py-1.5 text-sm text-muted-foreground hover:text-foreground hover:bg-muted/60 transition-colors"
      >
        <LogIn className="h-4 w-4 flex-shrink-0" />
        <span>登录</span>
      </button>
    );
  }

  const displayLabel = user.displayName || user.email.split("@")[0];
  const otherAccounts = recentAccounts.filter((a) => a.email !== user.email);
  const close = () => setOpen(false);

  const handleSwitchTo = (account: RecentAccount) => { close(); logout(); router.push(`/login?email=${encodeURIComponent(account.email)}`); };
  const handleSwitchNew = () => { close(); logout(); router.push("/login"); };
  const handleLogout = () => { close(); logout(); router.push("/login"); };
  const handleNav = (path: string) => { close(); router.push(path); };

  const gradientDivider = (
    <div className="h-px mx-3" style={{ background: "linear-gradient(to right, transparent, var(--border), transparent)" }} />
  );

  /* ── Shared menu content ── */
  const menuContent = (
    <>
      {/* ── Profile Card ── */}
      <div className="relative overflow-hidden">
        <div
          className="absolute inset-0"
          style={{
            background: `radial-gradient(ellipse at 20% -20%, var(--em-primary-alpha-15) 0%, transparent 70%),
                          radial-gradient(ellipse at 90% 120%, var(--em-primary-alpha-10) 0%, transparent 50%)`,
          }}
        />
        <div className="relative px-4 pt-4 pb-3.5 flex items-center gap-3">
          <div className="relative flex-shrink-0">
            <div
              className="rounded-full p-[2px]"
              style={{ background: "linear-gradient(135deg, var(--em-primary), var(--em-primary-light))" }}
            >
              <div className="rounded-full p-[1.5px]" style={{ backgroundColor: "var(--popover)" }}>
                <Avatar src={user.avatarUrl} name={user.displayName || user.email} size={9} />
              </div>
            </div>
            <span
              className="absolute -bottom-0.5 -right-0.5 h-3 w-3 rounded-full border-[2px]"
              style={{ backgroundColor: "#22c55e", borderColor: "var(--popover)" }}
            />
          </div>
          <div className="min-w-0 flex-1">
            <p className="text-[13px] font-semibold truncate leading-snug">
              {user.displayName || user.email.split("@")[0]}
            </p>
            <p className="text-[11px] text-muted-foreground truncate mt-0.5 leading-snug">{user.email}</p>
          </div>
        </div>
      </div>

      {gradientDivider}

      {/* ── Recent Accounts ── */}
      {otherAccounts.length > 0 && (
        <div className="px-1.5 pt-1.5 pb-0.5">
          <p className="px-2.5 pb-1 pt-0.5 text-[10px] font-semibold uppercase tracking-widest text-muted-foreground/60 flex items-center gap-1.5">
            <Clock className="h-2.5 w-2.5" />
            最近账号
          </p>
          {otherAccounts.map((account) => (
            <button
              key={account.email}
              onClick={() => handleSwitchTo(account)}
              className="flex items-center gap-2.5 w-full px-2.5 py-1.5 rounded-lg hover:bg-muted/60 transition-colors group/acct cursor-pointer"
            >
              <Avatar src={account.avatarUrl} name={account.displayName || account.email} size={6} />
              <div className="min-w-0 flex-1 text-left">
                <p className="text-[13px] truncate">{account.displayName || account.email.split("@")[0]}</p>
                <p className="text-[10px] text-muted-foreground truncate">{account.email}</p>
              </div>
              <span
                onClick={(e: React.MouseEvent) => { e.stopPropagation(); removeAccount(account.email); }}
                className="p-1 rounded-md hover:bg-muted text-muted-foreground/40 hover:text-muted-foreground opacity-0 group-hover/acct:opacity-100 transition-all flex-shrink-0 cursor-pointer"
              >
                <X className="h-3 w-3" />
              </span>
            </button>
          ))}
          {gradientDivider}
        </div>
      )}

      {/* ── Workspace Usage ── */}
      <WorkspaceIndicator usage={wsUsage} />
      {wsUsage && gradientDivider}

      {/* ── Navigation ── */}
      <div className="px-1.5 py-1">
        {user.role === "admin" && (
          <MenuButton icon={Users} label="用户管理" onClick={() => { close(); useUIStore.getState().openAdmin(); }} />
        )}
        <MenuButton icon={UserCircle} label="个人中心" onClick={() => { close(); useUIStore.getState().openProfile(); }} />
        {isMobile && (
          <MenuButton icon={Settings} label="设置" onClick={() => { close(); openSettings("model"); }} />
        )}
        <MenuButton icon={ArrowRightLeft} label={otherAccounts.length > 0 ? "使用其他账号" : "切换账号"} onClick={handleSwitchNew} />
      </div>

      {gradientDivider}

      {/* ── Logout ── */}
      <div className="px-1.5 py-1">
        <MenuButton icon={LogOut} label="退出登录" onClick={handleLogout} destructive />
      </div>
    </>
  );

  /* ── Mobile: Bottom Sheet Drawer ── */
  if (isMobile) {
    const drawer = (
      <AnimatePresence>
        {open && (
          <>
            <motion.div
              key="userbadge-backdrop"
              className="fixed inset-0 z-[100] bg-black/40 backdrop-blur-[2px]"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.2 }}
              onClick={close}
            />
            <motion.div
              key="userbadge-sheet"
              className="fixed bottom-0 left-0 right-0 z-[101] bg-popover text-popover-foreground rounded-t-2xl overflow-hidden"
              style={{ boxShadow: "0 -8px 30px rgba(0,0,0,0.12)" }}
              initial={{ y: "100%" }}
              animate={{ y: 0 }}
              exit={{ y: "100%" }}
              transition={{ type: "spring", damping: 30, stiffness: 350 }}
              drag="y"
              dragConstraints={{ top: 0, bottom: 0 }}
              dragElastic={{ top: 0, bottom: 0.6 }}
              onDragEnd={(_, info) => {
                if (info.offset.y > 80 || info.velocity.y > 400) close();
              }}
            >
              {/* Drag handle */}
              <div className="flex justify-center pt-3 pb-1 cursor-grab active:cursor-grabbing">
                <div className="w-9 h-1 rounded-full bg-muted-foreground/20" />
              </div>
              {menuContent}
              <div style={{ height: "max(12px, env(safe-area-inset-bottom, 12px))" }} />
            </motion.div>
          </>
        )}
      </AnimatePresence>
    );

    return (
      <>
        <button
          onClick={() => setOpen(true)}
          className="h-7 w-7 inline-flex items-center justify-center rounded-md text-muted-foreground hover:text-foreground hover:bg-accent transition-colors"
        >
          <Settings className="h-4 w-4" />
        </button>
        {typeof document !== "undefined" && createPortal(drawer, document.body)}
      </>
    );
  }

  /* ── Desktop: Enhanced Dropdown ── */
  return (
    <DropdownMenu open={open} onOpenChange={setOpen}>
      <DropdownMenuTrigger asChild>
        {compact ? (
          <button className="h-7 w-7 inline-flex items-center justify-center rounded-md text-muted-foreground hover:text-foreground hover:bg-accent transition-colors">
            <Settings className="h-4 w-4" />
          </button>
        ) : (
          <button className="flex items-center gap-2.5 w-full rounded-xl px-2 py-1.5 text-left hover:bg-muted/50 transition-all outline-none group">
            <div className="relative flex-shrink-0">
              <Avatar src={user.avatarUrl} name={user.displayName || user.email} size={7} />
              <span
                className="absolute -bottom-px -right-px h-2 w-2 rounded-full border-[1.5px]"
                style={{ backgroundColor: "#22c55e", borderColor: "var(--em-sidebar-bg)" }}
              />
            </div>
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium truncate leading-tight">{displayLabel}</p>
              <p className="text-[11px] text-muted-foreground truncate leading-tight">{user.email}</p>
            </div>
            <ChevronUp className="h-3.5 w-3.5 text-muted-foreground opacity-0 group-hover:opacity-100 transition-opacity flex-shrink-0" />
          </button>
        )}
      </DropdownMenuTrigger>
      <DropdownMenuContent
        side="top"
        align="start"
        className="w-[var(--radix-dropdown-menu-trigger-width)] min-w-[200px] max-w-[280px] p-0 rounded-xl shadow-xl border-border/50 overflow-hidden mb-1"
      >
        {menuContent}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
