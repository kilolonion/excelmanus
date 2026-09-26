"use client";

import type { CSSProperties, ReactNode } from "react";

export function SettingsPageLayout({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  return <div className={`em-settings-page ${className}`.trim()}>{children}</div>;
}

export function SettingsPagePanel({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  return <section className={`em-settings-panel ${className}`.trim()}>{children}</section>;
}

/** Fit cards to their available content width, including inside the settings sidebar. */
export function SettingsCardGrid({
  children,
  minCardWidth,
  label,
  className = "",
}: {
  children: ReactNode;
  minCardWidth: number;
  label: string;
  className?: string;
}) {
  return (
    <section
      aria-label={label}
      className={`em-settings-card-grid ${className}`.trim()}
      style={{ "--em-settings-card-min": `${minCardWidth}px` } as CSSProperties}
    >
      {children}
    </section>
  );
}

export interface SettingsSubnavItem {
  key: string;
  label: string;
  icon: ReactNode;
  description?: string;
  coachId?: string;
}

export function SettingsPageSubnav({
  label,
  showHeader = true,
  items,
  activeKey,
  onChange,
  note,
  aside,
  className = "",
  coachId,
}: {
  label: string;
  showHeader?: boolean;
  items: SettingsSubnavItem[];
  activeKey: string;
  onChange: (key: string) => void;
  note?: ReactNode;
  aside?: ReactNode;
  className?: string;
  coachId?: string;
}) {
  return (
    <nav className={`em-settings-subnav ${className}`.trim()} aria-label={label} role="tablist" data-coach-id={coachId}>
      {showHeader ? (
        <div className="em-settings-subnav-head">
          <div className="em-settings-subnav-head-copy">
            <p className="em-settings-subnav-label">{label}</p>
            {note ? <div className="em-settings-subnav-note">{note}</div> : null}
          </div>
          {aside ? <div className="em-settings-subnav-aside">{aside}</div> : null}
        </div>
      ) : null}
      <div className="em-settings-subnav-items">
        {items.map((item) => {
          const active = item.key === activeKey;
          return (
            <button
              key={item.key}
              type="button"
              role="tab"
              aria-selected={active}
              data-coach-id={item.coachId}
              className={`em-settings-subnav-item ${active ? "is-active" : ""}`}
              onClick={() => onChange(item.key)}
            >
              <span className="em-settings-subnav-icon">{item.icon}</span>
              <span className="min-w-0 flex-1 text-left">
                <span className="block text-xs font-semibold">{item.label}</span>
                {item.description ? <span className="mt-0.5 block truncate text-[10px] text-muted-foreground">{item.description}</span> : null}
              </span>
            </button>
          );
        })}
      </div>
    </nav>
  );
}
