import { useState, useEffect, useCallback } from "react";
import type { LogEntry } from "../types";
import { api } from "../api";
import LogConsole from "./LogConsole";
import {
  IconRocket, IconPlay, IconParty, IconGlobe, IconFolder,
  IconRefresh, IconStop, IconBack, IconLightbulb, IconUpdate,
} from "./Icons";

interface Props {
  progress: number;
  running: boolean;
  logs: LogEntry[];
  fePort: string;
  onBack: () => void;
}

const STAGE_NAMES: Record<number, string> = {
  0: "正在准备...",
  14: "正在下载源码...",
  28: "正在下载源码...",
  42: "正在安装后端依赖...",
  57: "正在安装后端依赖...",
  71: "正在安装并构建前端...",
  85: "正在启动服务...",
  100: "部署完成！",
};

function closestStage(p: number): string {
  let best = "正在部署...";
  for (const k of Object.keys(STAGE_NAMES)) {
    if (parseInt(k) <= p) best = STAGE_NAMES[parseInt(k)];
  }
  return best;
}

type View = "pre" | "deploying" | "success";

export default function DeployPanel({
  progress,
  running,
  logs,
  fePort,
  onBack,
}: Props) {
  const [view, setView] = useState<View>("pre");
  const [countdown, setCountdown] = useState(3);
  const [redirectActive, setRedirectActive] = useState(false);
  const [msg, setMsg] = useState<{ type: "ok" | "fail"; text: string } | null>(
    null
  );

  const doOpen = useCallback(() => {
    window.location.href = `http://localhost:${fePort}`;
  }, [fePort]);

  // Start deploy
  const startDeploy = async () => {
    setView("deploying");
    api.deploy().catch(() => {});
  };

  // Detect running → success
  useEffect(() => {
    if (running && view === "deploying") {
      setView("success");
      setCountdown(3);
      setRedirectActive(true);
    }
  }, [running, view]);

  // Countdown redirect
  useEffect(() => {
    if (!redirectActive) return;
    if (countdown <= 0) {
      doOpen();
      return;
    }
    const t = setTimeout(() => setCountdown((c) => c - 1), 1000);
    return () => clearTimeout(t);
  }, [countdown, redirectActive, doOpen]);

  const stopServices = async () => {
    setRedirectActive(false);
    try {
      await api.stop();
    } catch {}
    setView("pre");
  };

  const checkUpdate = async () => {
    setMsg(null);
    try {
      const d = await api.checkUpdate();
      if (d.has_update) {
        setMsg({
          type: "ok",
          text: `🎉 发现新版本: ${d.current} → ${d.latest} (${d.behind} 个新提交)`,
        });
      } else {
        setMsg({ type: "ok", text: `✅ 已是最新版本 (${d.current})` });
      }
    } catch (e) {
      setMsg({ type: "fail", text: `❌ 检查更新失败: ${e}` });
    }
  };

  const applyUpdate = async () => {
    setMsg({ type: "ok", text: "⏳ 正在更新，请稍候..." });
    try {
      const d = await api.applyUpdate();
      if (d.success) {
        setMsg({
          type: "ok",
          text: `✅ 更新成功！${d.old_version} → ${d.new_version}\n请重启服务以应用更新。`,
        });
      } else {
        setMsg({ type: "fail", text: `❌ 更新失败: ${d.error ?? "未知错误"}` });
      }
    } catch (e) {
      setMsg({ type: "fail", text: `❌ 更新失败: ${e}` });
    }
  };

  const createShortcut = async () => {
    try {
      const d = await api.createShortcut();
      if (d.path) {
        setMsg({ type: "ok", text: `✅ 桌面快捷方式已创建: ${d.path}` });
      } else {
        setMsg({
          type: "fail",
          text: `❌ 创建失败: ${d.error ?? "未知错误"}`,
        });
      }
    } catch (e) {
      setMsg({ type: "fail", text: `❌ 创建失败: ${e}` });
    }
  };

  return (
    <div className="animate-panel-in">
      <div className="glass-card overflow-hidden rounded-[14px]">
        <div className="p-5">
          {/* Pre-deploy */}
          {view === "pre" && (
            <div className="py-6 text-center">
              {/* Rocket with glow */}
              <div className="relative mx-auto mb-4 flex h-20 w-20 items-center justify-center text-brand">
                <div className="absolute inset-0 animate-pulse rounded-full bg-brand/[.06]" />
                <IconRocket size={48} className="relative drop-shadow-[0_4px_12px_rgba(33,115,70,.15)]" />
              </div>
              <div className="mb-1.5 text-xl font-extrabold text-em-t1">
                一切就绪，准备部署！
              </div>
              <div className="mx-auto mb-6 max-w-[280px] text-[13px] leading-relaxed text-em-t3">
                点击下方按钮，自动完成<br />源码下载、依赖安装与服务启动
              </div>
              <button onClick={startDeploy} className="btn-primary flex w-full items-center justify-center gap-2 py-3.5 text-[15px]">
                <IconPlay size={14} /> 开始部署
              </button>
            </div>
          )}

          {/* Deploying */}
          {view === "deploying" && (
            <div>
              <div className="mb-1 flex items-center justify-between">
                <span className="text-[13px] font-semibold text-em-t1">
                  {closestStage(progress)}
                </span>
                <span className="rounded-full bg-brand/[.06] px-2.5 py-[2px] text-[11px] font-bold tabular-nums text-brand">
                  {progress}%
                </span>
              </div>
              <div className="mb-4 text-[11px] text-em-t3">
                请稍候，首次部署预计需要 5-15 分钟
              </div>
              {/* Progress bar with glow */}
              <div className="mb-5 h-2 overflow-hidden rounded-full bg-em-border/50">
                <div
                  className="progress-shimmer progress-glow relative h-full rounded-full bg-gradient-to-r from-brand via-brand-light to-brand transition-[width] duration-500 ease-out"
                  style={{ width: `${progress}%` }}
                />
              </div>
              <LogConsole logs={logs} />
            </div>
          )}

          {/* Success */}
          {view === "success" && (
            <div className="py-6 text-center">
              {/* Success icon with confetti particles */}
              <div className="confetti-container mx-auto mb-2 h-16 w-16">
                <div className="confetti-particle animate-confetti-1 bg-brand" style={{ marginLeft: "-20px", marginTop: "-10px" }} />
                <div className="confetti-particle animate-confetti-2 bg-brand-light" style={{ marginLeft: "15px", marginTop: "-15px" }} />
                <div className="confetti-particle animate-confetti-3 bg-em-gold" style={{ marginLeft: "0", marginTop: "5px" }} />
                <div className="confetti-particle animate-confetti-1 bg-em-cyan" style={{ marginLeft: "-15px", marginTop: "10px" }} />
                <div className="confetti-particle animate-confetti-2 bg-brand" style={{ marginLeft: "20px", marginTop: "5px" }} />
                <div className="relative z-10 animate-success-pop">
                  <IconParty size={56} />
                </div>
              </div>

              <div className="mb-1.5 bg-gradient-to-r from-brand to-brand-light bg-clip-text text-[24px] font-extrabold text-transparent animate-fade-up">
                部署成功！
              </div>
              <div className="mb-6 text-[13px] text-em-t3 animate-fade-up-delayed opacity-0">
                服务已启动，即将跳转到 ExcelManus
              </div>

              {redirectActive && (
                <div className="mb-5 animate-fade-up opacity-0 [animation-delay:.2s]">
                  <div className="inline-flex items-center gap-2 rounded-full bg-brand/[.06] px-4 py-1.5 text-[12px] text-em-t2">
                    <div className="h-1.5 w-1.5 animate-pulse rounded-full bg-brand" />
                    <span className="tabular-nums font-semibold">{countdown}</span> 秒后自动跳转
                    <button
                      onClick={() => setRedirectActive(false)}
                      className="ml-1 text-em-t3 underline decoration-em-t3/30 underline-offset-2 hover:text-em-t1"
                    >
                      取消
                    </button>
                  </div>
                </div>
              )}

              <button onClick={doOpen} className="btn-primary flex w-full items-center justify-center gap-2 py-3.5 text-[15px]">
                <IconGlobe size={16} /> 立即打开 ExcelManus
              </button>

              <div className="mt-4 flex flex-wrap justify-center gap-2">
                <button onClick={createShortcut} className="btn-secondary flex items-center gap-1.5 px-4 py-2 text-[12px]">
                  <IconFolder size={13} /> 创建快捷方式
                </button>
                <button onClick={checkUpdate} className="btn-secondary flex items-center gap-1.5 px-4 py-2 text-[12px]">
                  <IconRefresh size={13} /> 检查更新
                </button>
                <button
                  onClick={stopServices}
                  className="flex items-center gap-1.5 rounded-lg bg-em-red/90 px-4 py-2 text-[12px] font-semibold text-white shadow-[0_2px_8px_rgba(209,52,56,.15)] transition-all hover:bg-em-red active:scale-[.97]"
                >
                  <IconStop size={12} /> 停止服务
                </button>
              </div>

              {msg && (
                <div
                  className={`mt-4 rounded-xl border p-3 text-left text-[12px] font-medium leading-relaxed ${
                    msg.type === "ok"
                      ? "border-brand/10 bg-brand/[.04] text-brand-dark"
                      : "border-em-red/10 bg-em-red/[.04] text-em-red"
                  }`}
                >
                  {msg.text}
                  {msg.type === "ok" && msg.text.includes("发现新版本") && (
                    <button onClick={applyUpdate} className="btn-primary mt-2 flex items-center gap-1.5 px-4 py-1.5 text-[11px]">
                      <IconUpdate size={12} /> 立即更新
                    </button>
                  )}
                </div>
              )}

              <div className="mt-3 flex items-center justify-center gap-1 text-[10px] text-em-t4">
                <IconLightbulb size={11} className="opacity-50" /> 可通过系统托盘图标随时返回管理面板
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Back button */}
      {view === "pre" && (
        <div className="mt-5 flex justify-end">
          <button onClick={onBack} className="btn-secondary flex items-center gap-1.5 px-6 py-2.5 text-sm">
            <IconBack size={14} /> 上一步
          </button>
        </div>
      )}
    </div>
  );
}
