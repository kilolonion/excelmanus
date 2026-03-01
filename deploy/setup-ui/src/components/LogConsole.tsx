import { useEffect, useRef, useState } from "react";
import type { LogEntry } from "../types";
import { IconLog, IconChevron } from "./Icons";

interface Props {
  logs: LogEntry[];
}

const levelColor: Record<string, string> = {
  ok: "text-brand",
  err: "text-em-red",
  warn: "text-em-gold",
  hl: "text-em-cyan",
  info: "text-em-t2",
};

const levelDot: Record<string, string> = {
  ok: "bg-brand",
  err: "bg-em-red",
  warn: "bg-em-gold",
  hl: "bg-em-cyan",
  info: "bg-em-t3",
};

export default function LogConsole({ logs }: Props) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (open && ref.current) {
      ref.current.scrollTop = ref.current.scrollHeight;
    }
  }, [logs, open]);

  return (
    <div>
      <button
        onClick={() => setOpen((o) => !o)}
        className="group flex items-center gap-1.5 rounded-md px-2 py-1 text-[11px] font-medium text-em-t3 transition-colors hover:bg-brand/[.04] hover:text-em-cyan"
      >
        <IconLog className="opacity-60 group-hover:opacity-100 transition-opacity" />
        <span>{open ? "收起日志" : "展开详细日志"}</span>
        <span className="rounded bg-em-border/40 px-1.5 py-[1px] text-[9px] tabular-nums font-mono text-em-t3">
          {logs.length}
        </span>
        <IconChevron className={`ml-auto transition-transform duration-200 ${open ? "rotate-180" : ""}`} />
      </button>

      {open && (
        <div
          ref={ref}
          className="log-console mt-2 max-h-[220px] overflow-y-auto rounded-lg border border-em-border/30 bg-[#f8f9fa] px-0 py-1.5 font-mono text-[10.5px] leading-[1.7]"
        >
          {logs.map((l, i) => (
            <div
              key={l.idx}
              className={`flex items-start gap-2 px-3 py-[1px] transition-colors hover:bg-brand/[.02] ${
                levelColor[l.level] ?? "text-em-t2"
              }`}
            >
              <span className="mt-[5px] flex-shrink-0">
                <span className={`inline-block h-[5px] w-[5px] rounded-full ${levelDot[l.level] ?? "bg-em-t3"}`} />
              </span>
              <span className="whitespace-pre-wrap break-all">{l.text}</span>
            </div>
          ))}
          {logs.length === 0 && (
            <div className="px-3 py-3 text-center text-em-t3">等待日志...</div>
          )}
        </div>
      )}
    </div>
  );
}
