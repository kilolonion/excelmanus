import { LogoIcon, LogoText } from "./Icons";

export default function Header() {
  return (
    <header className="sticky top-0 z-10 flex h-[56px] items-center gap-4 border-b border-em-border/50 bg-white/[.72] px-7 backdrop-blur-[24px] backdrop-saturate-[200%]">
      {/* Accent gradient line at bottom */}
      <div className="pointer-events-none absolute bottom-[-1px] left-0 right-0 h-[1px] bg-gradient-to-r from-brand/40 via-brand-light/20 to-transparent" />

      <div className="animate-logo-breathe drop-shadow-[0_2px_6px_rgba(33,115,70,.18)]">
        <LogoIcon size={36} />
      </div>

      <div className="flex items-center gap-3">
        <LogoText className="text-[18px] font-extrabold tracking-tight" />
        <span className="rounded-full bg-brand/[.08] px-2.5 py-[2px] text-[10px] font-bold tracking-wide text-brand">
          v2.0
        </span>
      </div>

      <div className="ml-auto flex items-center gap-1.5 opacity-30">
        <div className="h-1.5 w-1.5 rounded-full bg-brand" />
        <div className="h-1.5 w-1.5 rounded-full bg-brand" />
        <div className="h-1.5 w-1.5 rounded-full bg-brand" />
      </div>
    </header>
  );
}
