import { useState, useEffect } from "react";
import { api } from "../api";
import type { UpdateInfo } from "../types";
import { IconZap, IconParty, IconUpdate, IconRefresh, IconPencil } from "./Icons";

interface Props {
  onCancel: () => void;
  onStarted: () => void;
  progress: number;
  running: boolean;
  fePort: string;
}

type Phase = "checking" | "update-found" | "updating" | "starting";

export default function QuickStartOverlay({
  onCancel,
  onStarted,
  progress,
  running,
  fePort,
}: Props) {
  const [phase, setPhase] = useState<Phase>("checking");
  const [updateInfo, setUpdateInfo] = useState<UpdateInfo | null>(null);

  // Check for updates on mount
  useEffect(() => {
    const timeout = setTimeout(() => skipUpdate(), 10000);
    api
      .checkUpdateQuick()
      .then((d) => {
        clearTimeout(timeout);
        if (d.has_update) {
          setUpdateInfo(d);
          setPhase("update-found");
        } else {
          skipUpdate();
        }
      })
      .catch(() => {
        clearTimeout(timeout);
        skipUpdate();
      });
    return () => clearTimeout(timeout);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Detect running → redirect
  useEffect(() => {
    if (running && (phase === "starting" || phase === "updating")) {
      window.location.href = `http://localhost:${fePort}`;
    }
  }, [running, phase, fePort]);

  function skipUpdate() {
    setPhase("starting");
    api.quickStart().catch(() => {});
  }

  async function doUpdate() {
    setPhase("updating");
    try {
      const d = await api.applyUpdate();
      if (d.success) {
        setPhase("starting");
        api.quickStart().catch(() => {});
      } else {
        // Update failed, just start
        setTimeout(skipUpdate, 2000);
      }
    } catch {
      setTimeout(skipUpdate, 2000);
    }
  }

  const IconPhase =
    phase === "checking"
      ? IconZap
      : phase === "update-found"
      ? IconParty
      : phase === "updating"
      ? IconUpdate
      : IconZap;

  const title =
    phase === "checking"
      ? "正在检查更新..."
      : phase === "update-found"
      ? "发现新版本！"
      : phase === "updating"
      ? "正在更新..."
      : "快速启动中...";

  const sub =
    phase === "checking"
      ? "检测到已部署的项目，正在检查是否有新版本"
      : phase === "update-found"
      ? ""
      : phase === "updating"
      ? "下载并安装新版本，完成后自动启动"
      : "正在启动服务，请稍候";

  const barWidth =
    phase === "checking"
      ? 30
      : phase === "update-found"
      ? 50
      : phase === "updating"
      ? 40
      : Math.max(progress, 85);

  return (
    <div className="fixed inset-0 z-50 flex flex-col items-center justify-center gap-4 bg-gradient-to-br from-[#f5f7f8] via-[#eef2f0] to-[#f0f5f3] p-10">
      <div className="animate-success-pop text-brand">
        <IconPhase size={52} />
      </div>
      <div className="bg-gradient-to-br from-brand to-brand-light bg-clip-text text-xl font-bold text-transparent">
        {title}
      </div>
      {sub && <div className="text-[13px] text-em-t3">{sub}</div>}

      {/* Progress bar */}
      <div className="mt-2 h-1.5 w-60 overflow-hidden rounded-full bg-em-border">
        <div
          className="progress-shimmer relative h-full rounded-full bg-gradient-to-r from-brand to-brand-light transition-[width] duration-500"
          style={{ width: `${barWidth}%` }}
        />
      </div>

      {/* Update prompt */}
      {phase === "update-found" && updateInfo && (
        <div className="mt-2 max-w-[380px] rounded-em border border-em-border bg-white p-4 text-center shadow-[0_2px_8px_rgba(0,0,0,.06)]">
          <div className="mb-1 flex items-center justify-center gap-1.5 text-[15px] font-bold">
            <IconParty size={18} /> 发现新版本！
          </div>
          <div className="mb-3 text-[13px] text-em-t2">
            <strong>
              {updateInfo.current} → {updateInfo.latest}
            </strong>
            （{updateInfo.behind} 个新提交）
          </div>
          <div className="flex justify-center gap-2.5">
            <button
              onClick={doUpdate}
              className="relative overflow-hidden rounded-em-sm bg-brand px-5 py-2 text-sm font-semibold text-white shadow-sm transition-all hover:bg-brand-dark active:scale-[.97]"
            >
              <span className="pointer-events-none absolute inset-0 bg-gradient-to-br from-white/[.12] to-transparent" />
              <span className="flex items-center gap-1.5"><IconRefresh size={13} /> 立即更新</span>
            </button>
            <button
              onClick={skipUpdate}
              className="rounded-em-sm border border-brand/[.18] bg-em-bg px-5 py-2 text-sm font-semibold text-brand transition-all hover:bg-brand/[.06] active:scale-[.97]"
            >
              跳过，直接启动
            </button>
          </div>
        </div>
      )}

      {/* Cancel → full wizard */}
      <div className="mt-5">
        <button
          onClick={onCancel}
          className="rounded-em-sm border border-brand/[.18] bg-em-bg px-5 py-2 text-sm font-semibold text-brand transition-all hover:bg-brand/[.06] active:scale-[.97]"
        >
          <span className="flex items-center gap-1.5"><IconPencil size={13} /> 进入完整设置向导</span>
        </button>
      </div>
    </div>
  );
}
