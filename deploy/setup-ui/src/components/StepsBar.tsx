import { IconSearch, IconRocket, IconCheckSvg } from "./Icons";

interface Props {
  current: number;
}

const steps = [
  { label: "环境检测", Icon: IconSearch },
  { label: "启动部署", Icon: IconRocket },
];

export default function StepsBar({ current }: Props) {
  return (
    <div className="flex items-center justify-center gap-0 px-5 pb-2 pt-6">
      {steps.map((s, i) => {
        const num = i + 1;
        const isDone = num < current;
        const isActive = num === current;
        return (
          <div key={num} className="flex items-center gap-0">
            {i > 0 && (
              <div className="relative mx-1 h-[3px] w-[72px] overflow-hidden rounded-full bg-em-border/60">
                <div
                  className={`absolute inset-y-0 left-0 rounded-full bg-gradient-to-r from-brand to-brand-light transition-all duration-700 ease-out ${
                    isDone ? "w-full" : "w-0"
                  }`}
                />
              </div>
            )}
            <div className="flex flex-col items-center">
              <div className="relative">
                {isActive && (
                  <div className="absolute -inset-1.5 animate-pulse-ring rounded-full border-2 border-brand/25" />
                )}
                <div
                  className={`relative flex h-[38px] w-[38px] items-center justify-center rounded-full transition-all duration-500 ${
                    isDone
                      ? "bg-gradient-to-br from-brand-light to-brand text-white shadow-[0_2px_10px_rgba(51,168,103,.25)]"
                      : isActive
                      ? "bg-gradient-to-br from-brand to-[#107c41] text-white shadow-[0_3px_16px_rgba(33,115,70,.35),0_0_0_4px_rgba(33,115,70,.08)]"
                      : "border-2 border-em-border-2 bg-white text-em-t3 shadow-[0_1px_3px_rgba(0,0,0,.05)]"
                  }`}
                >
                  {isDone ? <IconCheckSvg size={16} /> : <s.Icon size={16} />}
                </div>
              </div>
              <div
                className={`mt-2 text-[11px] font-semibold transition-colors duration-300 ${
                  isActive
                    ? "text-brand"
                    : isDone
                    ? "text-brand-light"
                    : "text-em-t3"
                }`}
              >
                {s.label}
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}
