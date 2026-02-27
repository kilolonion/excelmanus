"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import { useRouter } from "next/navigation";
import { Circle, LogOut, ArrowRightLeft, ChevronUp, LogIn, X, Clock, Users, HardDrive, Settings } from "lucide-react";
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

        {/* Right: user badge (includes settings entry) */}
        <div className="flex items-center gap-1 flex-shrink-0">
          <UserBadge compact />
        </div>
      </div>
    </>
  );
}

function Avatar({ src, name, size = 5 }: { src?: string | null; name: string; size?: number }) {
  const [failed, setFailed] = useState(false);
  const initial = name[0]?.toUpperCase() || "U";
  const px = size * 4;
  const textSize = size <= 5 ? "text-[10px]" : "text-sm";
  const proxiedSrc = proxyAvatarUrl(src);

  if (proxiedSrc && !failed) {
    return (
      <img
        src={proxiedSrc}
        alt=""
        className="rounded-full flex-shrink-0"
        style={{ width: px, height: px }}
        referrerPolicy="no-referrer"
        onError={() => setFailed(true)}
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
  const color = isOver ? "bg-red-500" : pct > 80 ? "bg-amber-500" : "bg-[var(--em-primary)]";

  return (
    <div className="px-3 py-1.5">
      <div className="flex items-center gap-1.5 text-[11px] text-muted-foreground mb-1">
        <HardDrive className="h-3 w-3" />
        <span>工作空间</span>
        <span className="ml-auto">
          {usage.size_mb.toFixed(1)} / {usage.max_size_mb} MB · {usage.file_count} / {usage.max_files} 文件
        </span>
      </div>
      <div className="h-1.5 rounded-full bg-muted overflow-hidden">
        <div
          className={`h-full rounded-full transition-all duration-500 ${color}`}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
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

  useEffect(() => {
    if (!authEnabled || !user) return;
    fetchMyWorkspaceUsage().then(setWsUsage).catch(() => {});
  }, [authEnabled, user]);

  // compact mode without auth or user: plain settings gear
  if (compact && (!authEnabled || !user)) {
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

  const handleSwitchTo = (account: RecentAccount) => {
    logout();
    router.push(`/login?email=${encodeURIComponent(account.email)}`);
  };

  const handleSwitchNew = () => {
    logout();
    router.push("/login");
  };

  const handleLogout = () => {
    logout();
    router.push("/login");
  };

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        {compact ? (
          <button className="h-7 w-7 inline-flex items-center justify-center rounded-md text-muted-foreground hover:text-foreground hover:bg-accent transition-colors">
            <Settings className="h-4 w-4" />
          </button>
        ) : (
          <button className="flex items-center gap-2 w-full rounded-lg px-2 py-1.5 text-left hover:bg-muted/60 transition-colors outline-none group">
            <Avatar src={user.avatarUrl} name={user.displayName || user.email} size={7} />
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium truncate leading-tight">{displayLabel}</p>
              <p className="text-[11px] text-muted-foreground truncate leading-tight">{user.email}</p>
            </div>
            <ChevronUp className="h-3.5 w-3.5 text-muted-foreground opacity-0 group-hover:opacity-100 transition-opacity flex-shrink-0" />
          </button>
        )}
      </DropdownMenuTrigger>
      <DropdownMenuContent side="top" align="start" className="w-64 mb-1">
        <DropdownMenuLabel className="font-normal">
          <div className="flex items-center gap-2.5">
            <Avatar src={user.avatarUrl} name={user.displayName || user.email} size={8} />
            <div className="min-w-0">
              <p className="text-sm font-medium truncate">{user.displayName || user.email.split("@")[0]}</p>
              <p className="text-xs text-muted-foreground truncate">{user.email}</p>
            </div>
          </div>
        </DropdownMenuLabel>

        <DropdownMenuSeparator />

        {otherAccounts.length > 0 && (
          <>
            <DropdownMenuLabel className="text-[11px] text-muted-foreground font-normal flex items-center gap-1.5 py-1">
              <Clock className="h-3 w-3" />
              最近使用的账号
            </DropdownMenuLabel>
            <DropdownMenuGroup>
              {otherAccounts.map((account) => (
                <DropdownMenuItem
                  key={account.email}
                  onClick={() => handleSwitchTo(account)}
                  className="cursor-pointer py-2"
                >
                  <div className="flex items-center gap-2.5 flex-1 min-w-0">
                    <Avatar src={account.avatarUrl} name={account.displayName || account.email} />
                    <div className="min-w-0 flex-1">
                      <p className="text-sm truncate">{account.displayName || account.email.split("@")[0]}</p>
                      <p className="text-[11px] text-muted-foreground truncate">{account.email}</p>
                    </div>
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        removeAccount(account.email);
                      }}
                      className="p-0.5 rounded hover:bg-muted-foreground/20 text-muted-foreground opacity-0 group-hover:opacity-100 hover:opacity-100 transition-opacity flex-shrink-0 touch-show"
                    >
                      <X className="h-3 w-3" />
                    </button>
                  </div>
                </DropdownMenuItem>
              ))}
            </DropdownMenuGroup>
            <DropdownMenuSeparator />
          </>
        )}

        <WorkspaceIndicator usage={wsUsage} />
        <DropdownMenuSeparator />

        {user.role === "admin" && (
          <>
            <DropdownMenuItem
              onClick={() => router.push("/admin")}
              className="gap-2 cursor-pointer"
            >
              <Users className="h-4 w-4" />
              用户管理
            </DropdownMenuItem>
            <DropdownMenuSeparator />
          </>
        )}

        <DropdownMenuItem onClick={handleSwitchNew} className="gap-2 cursor-pointer">
          <ArrowRightLeft className="h-4 w-4" />
          {otherAccounts.length > 0 ? "使用其他账号登录" : "切换账号"}
        </DropdownMenuItem>

        <DropdownMenuSeparator />

        <DropdownMenuItem
          onClick={() => openSettings("model")}
          className="gap-2 cursor-pointer"
        >
          <Settings className="h-4 w-4" />
          设置
        </DropdownMenuItem>

        <DropdownMenuSeparator />

        <DropdownMenuItem
          onClick={handleLogout}
          className="gap-2 cursor-pointer text-destructive focus:text-destructive"
        >
          <LogOut className="h-4 w-4" />
          退出登录
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
