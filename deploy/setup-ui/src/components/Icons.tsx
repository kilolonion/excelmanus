import { LOGO_DATA_URI } from "../logo-base64";

interface IconProps {
  className?: string;
  size?: number;
}

/** ExcelManus icon — from deploy/icon.ico (64x64 PNG frame, base64 embedded) */
export function LogoIcon({ className, size = 40 }: IconProps) {
  return (
    <img
      src={LOGO_DATA_URI}
      alt="ExcelManus"
      width={size}
      height={size}
      className={className}
      draggable={false}
      style={{ imageRendering: "auto" }}
    />
  );
}

/** "ExcelManus" wordmark — "Excel" dark + "Manus" brand green */
export function LogoText({ className }: { className?: string }) {
  return (
    <span className={className}>
      <span className="text-em-t1">Excel</span>
      <span className="text-brand">Manus</span>
    </span>
  );
}

// ─── Step Icons ───

export function IconSearch({ className, size = 18 }: IconProps) {
  return (
    <svg viewBox="0 0 18 18" width={size} height={size} fill="none" className={className}>
      <circle cx="7.5" cy="7.5" r="5.5" stroke="currentColor" strokeWidth="1.8" />
      <path d="M11.5 11.5L16 16" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
    </svg>
  );
}

export function IconRocket({ className, size = 18 }: IconProps) {
  return (
    <svg viewBox="0 0 18 18" width={size} height={size} fill="none" className={className}>
      <path d="M9 2C7 6 6 9 6 12L9 10L12 12C12 9 11 6 9 2Z" fill="currentColor" fillOpacity="0.15" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round" />
      <path d="M6 12C4.5 12.5 3.5 14 3 15L6 14" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" />
      <path d="M12 12C13.5 12.5 14.5 14 15 15L12 14" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" />
      <circle cx="9" cy="8" r="1.2" fill="currentColor" />
    </svg>
  );
}

// ─── Status Icons ───

export function IconCheck({ className, size = 18 }: IconProps) {
  return (
    <svg viewBox="0 0 18 18" width={size} height={size} fill="none" className={className}>
      <circle cx="9" cy="9" r="8" fill="currentColor" fillOpacity="0.1" />
      <path d="M5.5 9.5L7.5 11.5L12.5 6.5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export function IconCross({ className, size = 18 }: IconProps) {
  return (
    <svg viewBox="0 0 18 18" width={size} height={size} fill="none" className={className}>
      <circle cx="9" cy="9" r="8" fill="currentColor" fillOpacity="0.08" />
      <path d="M6 6L12 12M12 6L6 12" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
}

export function IconSpinner({ className, size = 18 }: IconProps) {
  return (
    <svg viewBox="0 0 18 18" width={size} height={size} fill="none" className={`animate-spin ${className ?? ""}`}>
      <circle cx="9" cy="9" r="7" stroke="#e5e7eb" strokeWidth="2.5" />
      <path d="M16 9a7 7 0 0 0-7-7" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" />
    </svg>
  );
}

export function IconCheckSvg({ className, size = 16 }: IconProps) {
  return (
    <svg viewBox="0 0 16 16" width={size} height={size} fill="none" className={className}>
      <path
        d="M3.5 8.5L6.5 11.5L12.5 4.5"
        stroke="white"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

// ─── Action Icons ───

export function IconPlay({ className, size = 16 }: IconProps) {
  return (
    <svg viewBox="0 0 16 16" width={size} height={size} fill="none" className={className}>
      <path d="M4 2.5L13 8L4 13.5V2.5Z" fill="currentColor" />
    </svg>
  );
}

export function IconGlobe({ className, size = 16 }: IconProps) {
  return (
    <svg viewBox="0 0 16 16" width={size} height={size} fill="none" className={className}>
      <circle cx="8" cy="8" r="6.5" stroke="currentColor" strokeWidth="1.3" />
      <ellipse cx="8" cy="8" rx="3" ry="6.5" stroke="currentColor" strokeWidth="1.3" />
      <path d="M1.5 8H14.5" stroke="currentColor" strokeWidth="1.2" />
      <path d="M2.5 4.5H13.5" stroke="currentColor" strokeWidth="1" />
      <path d="M2.5 11.5H13.5" stroke="currentColor" strokeWidth="1" />
    </svg>
  );
}

export function IconFolder({ className, size = 16 }: IconProps) {
  return (
    <svg viewBox="0 0 16 16" width={size} height={size} fill="none" className={className}>
      <path d="M2 4.5C2 3.67 2.67 3 3.5 3H6L7.5 4.5H12.5C13.33 4.5 14 5.17 14 6V11.5C14 12.33 13.33 13 12.5 13H3.5C2.67 13 2 12.33 2 11.5V4.5Z" stroke="currentColor" strokeWidth="1.3" />
    </svg>
  );
}

export function IconRefresh({ className, size = 16 }: IconProps) {
  return (
    <svg viewBox="0 0 16 16" width={size} height={size} fill="none" className={className}>
      <path d="M13.5 8A5.5 5.5 0 1 1 8 2.5" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
      <path d="M10 2.5L8 5H12L10 2.5Z" fill="currentColor" />
    </svg>
  );
}

export function IconStop({ className, size = 16 }: IconProps) {
  return (
    <svg viewBox="0 0 16 16" width={size} height={size} fill="none" className={className}>
      <rect x="3.5" y="3.5" width="9" height="9" rx="1.5" fill="currentColor" />
    </svg>
  );
}

export function IconBack({ className, size = 16 }: IconProps) {
  return (
    <svg viewBox="0 0 16 16" width={size} height={size} fill="none" className={className}>
      <path d="M10 3L5 8L10 13" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export function IconForward({ className, size = 16 }: IconProps) {
  return (
    <svg viewBox="0 0 16 16" width={size} height={size} fill="none" className={className}>
      <path d="M6 3L11 8L6 13" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export function IconParty({ className, size = 48 }: IconProps) {
  return (
    <svg viewBox="0 0 48 48" width={size} height={size} fill="none" className={className}>
      <path d="M12 36L18 14L34 30L12 36Z" fill="#217346" fillOpacity="0.1" stroke="#217346" strokeWidth="2" strokeLinejoin="round" />
      <circle cx="28" cy="12" r="2.5" fill="#33a867" />
      <circle cx="36" cy="20" r="1.8" fill="#e5a100" />
      <circle cx="34" cy="10" r="1.5" fill="#0078d4" />
      <path d="M22 8L23 4M26 6L28 3M32 6L35 4" stroke="#33a867" strokeWidth="1.5" strokeLinecap="round" />
      <path d="M38 14L42 13M40 18L43 19" stroke="#e5a100" strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  );
}

export function IconLightbulb({ className, size = 14 }: IconProps) {
  return (
    <svg viewBox="0 0 14 14" width={size} height={size} fill="none" className={className}>
      <path d="M7 1.5C4.5 1.5 3 3.5 3 5.5C3 7 4 8 4.5 8.5V10.5C4.5 11 5 11.5 5.5 11.5H8.5C9 11.5 9.5 11 9.5 10.5V8.5C10 8 11 7 11 5.5C11 3.5 9.5 1.5 7 1.5Z" stroke="currentColor" strokeWidth="1.2" />
      <path d="M5.5 10H8.5" stroke="currentColor" strokeWidth="1" strokeLinecap="round" />
    </svg>
  );
}

export function IconLog({ className, size = 12 }: IconProps) {
  return (
    <svg viewBox="0 0 12 12" width={size} height={size} fill="none" className={className}>
      <rect x="1" y="1" width="10" height="10" rx="2" stroke="currentColor" strokeWidth="1.2" />
      <path d="M3.5 4.5H8.5M3.5 6.5H7M3.5 8.5H6" stroke="currentColor" strokeWidth="1" strokeLinecap="round" />
    </svg>
  );
}

export function IconChevron({ className, size = 10 }: IconProps) {
  return (
    <svg viewBox="0 0 10 10" width={size} height={size} fill="none" className={className}>
      <path d="M2.5 3.5L5 6.5L7.5 3.5" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export function IconDiamond({ className, size = 12 }: IconProps) {
  return (
    <svg viewBox="0 0 12 12" width={size} height={size} fill="none" className={className}>
      <path d="M6 1L11 6L6 11L1 6Z" fill="currentColor" />
    </svg>
  );
}

export function IconRecheck({ className, size = 14 }: IconProps) {
  return (
    <svg viewBox="0 0 14 14" width={size} height={size} fill="none" className={className}>
      <path d="M12 7A5 5 0 1 1 7 2" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
      <path d="M9 2L7 4.5H11L9 2Z" fill="currentColor" />
    </svg>
  );
}

export function IconZap({ className, size = 16 }: IconProps) {
  return (
    <svg viewBox="0 0 16 16" width={size} height={size} fill="none" className={className}>
      <path d="M9 1.5L3.5 9H7.5L6.5 14.5L12.5 7H8.5L9 1.5Z" fill="currentColor" fillOpacity="0.15" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" />
    </svg>
  );
}

export function IconUpdate({ className, size = 16 }: IconProps) {
  return (
    <svg viewBox="0 0 16 16" width={size} height={size} fill="none" className={className}>
      <path d="M2 8a6 6 0 0 1 10.5-4" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
      <path d="M14 8a6 6 0 0 1-10.5 4" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
      <path d="M12 2.5L13 4.5L11 5" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M4 13.5L3 11.5L5 11" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export function IconPencil({ className, size = 14 }: IconProps) {
  return (
    <svg viewBox="0 0 14 14" width={size} height={size} fill="none" className={className}>
      <path d="M2 10.5V12H3.5L10.5 5L9 3.5L2 10.5Z" stroke="currentColor" strokeWidth="1.2" strokeLinejoin="round" />
      <path d="M9 3.5L10.5 2L12 3.5L10.5 5L9 3.5Z" fill="currentColor" fillOpacity="0.2" stroke="currentColor" strokeWidth="1.2" strokeLinejoin="round" />
    </svg>
  );
}
