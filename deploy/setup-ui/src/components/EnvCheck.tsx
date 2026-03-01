import { ENV_ITEMS, type EnvCheck as EnvCheckType, type EnvDetails } from "../types";
import { IconCheck, IconCross, IconSpinner, IconSearch, IconRecheck, IconForward } from "./Icons";

interface Props {
  checks: EnvCheckType;
  details: EnvDetails;
  onRecheck: () => void;
  onNext: () => void;
}

export default function EnvCheck({ checks, details, onRecheck, onNext }: Props) {
  const allDone = ENV_ITEMS.every(
    (e) => checks[e.id] === 1 || checks[e.id] === 2
  );
  const allOk = ENV_ITEMS.every((e) => checks[e.id] === 1);

  return (
    <div className="animate-panel-in">
      {/* Card */}
      <div className="glass-card overflow-hidden rounded-[14px]">
        {/* Card header */}
        <div className="flex items-center gap-2.5 border-b border-em-border/50 px-5 py-3.5">
          <div className="flex h-6 w-6 items-center justify-center rounded-md bg-brand/[.08] text-brand">
            <IconSearch size={13} />
          </div>
          <span className="text-[11px] font-bold uppercase tracking-[1.8px] text-em-t2">
            环境检测
          </span>
          {allDone && (
            <span className={`ml-auto rounded-full px-2.5 py-[2px] text-[10px] font-bold ${
              allOk
                ? "bg-brand/[.08] text-brand"
                : "bg-em-red/[.06] text-em-red"
            }`}>
              {allOk ? "全部通过" : "存在问题"}
            </span>
          )}
        </div>

        {/* Check items */}
        <div className="px-5 py-1">
          {ENV_ITEMS.map((e, i) => {
            const st = checks[e.id] ?? 0;
            const detail = details[e.id] ?? "检测中...";
            return (
              <div
                key={e.id}
                className={`env-row flex items-center py-3.5 stagger-${i + 1} ${
                  i < ENV_ITEMS.length - 1 ? "border-b border-em-border/30" : ""
                }`}
              >
                {/* Status icon */}
                <div
                  className={`mr-4 flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-xl transition-all duration-500 ${
                    st === 1
                      ? "bg-brand/[.06] shadow-[0_0_0_1px_rgba(33,115,70,.08)]"
                      : st === 2
                      ? "bg-em-red/[.05] shadow-[0_0_0_1px_rgba(209,52,56,.08)]"
                      : "bg-em-gold/[.05] shadow-[0_0_0_1px_rgba(229,161,0,.08)]"
                  }`}
                >
                  {st === 1 ? <IconCheck className="text-brand" /> : st === 2 ? <IconCross className="text-em-red" /> : <IconSpinner className="text-em-gold" />}
                </div>
                {/* Info */}
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-[13px] font-semibold text-em-t1">{e.name}</span>
                    {st === 1 && detail !== "检测中..." && (
                      <span className="rounded bg-em-bg px-1.5 py-[1px] text-[10px] font-medium text-em-t3 font-mono">
                        {detail.replace(/^.*?(\d[\d.]+).*$/, "$1") || detail}
                      </span>
                    )}
                  </div>
                  <div className="mt-0.5 text-[11px] text-em-t3 truncate">
                    {st === 2 ? (
                      <>
                        未找到 —{" "}
                        <a
                          href={e.downloadUrl}
                          target="_blank"
                          rel="noreferrer"
                          className="text-em-cyan underline decoration-em-cyan/30 underline-offset-2 transition-colors hover:text-em-cyan hover:decoration-em-cyan/60"
                        >
                          点此下载安装
                        </a>
                      </>
                    ) : (
                      detail
                    )}
                  </div>
                </div>
                {/* Right indicator */}
                <div className={`ml-2 h-2 w-2 flex-shrink-0 rounded-full transition-all duration-700 ${
                  st === 1 ? "bg-brand shadow-[0_0_6px_rgba(33,115,70,.3)]"
                  : st === 2 ? "bg-em-red shadow-[0_0_6px_rgba(209,52,56,.2)]"
                  : "bg-em-gold/50 animate-pulse"
                }`} />
              </div>
            );
          })}
        </div>
      </div>

      {/* Buttons */}
      <div className="mt-5 flex justify-end gap-3">
        {allDone && (
          <button onClick={onRecheck} className="btn-secondary flex items-center gap-1.5 px-5 py-2.5 text-sm">
            <IconRecheck size={13} /> 重新检测
          </button>
        )}
        <button
          onClick={onNext}
          disabled={!allOk}
          className="btn-primary px-7 py-2.5 text-sm disabled:cursor-not-allowed disabled:opacity-30 disabled:shadow-none disabled:hover:translate-y-0"
        >
          <span className="flex items-center gap-1.5">开始部署 <IconForward size={14} /></span>
        </button>
      </div>
    </div>
  );
}
