"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";

export function HistoryPaneOverlay({ children }: { children: ReactNode }) {
  const hostRef = useRef<HTMLDivElement>(null);
  const [top, setTop] = useState(36);

  useEffect(() => {
    const root = hostRef.current?.parentElement;
    if (!root) return;
    const measure = () => {
      const tablist = root.querySelector<HTMLElement>(
        "[data-univer-container] [role='tablist']",
      );
      if (tablist) {
        const parentRect = root.getBoundingClientRect();
        const tabRect = tablist.getBoundingClientRect();
        setTop(Math.max(0, Math.round(tabRect.bottom - parentRect.top)));
        return;
      }
      const header = root.querySelector("[data-univer-container] header");
      if (header instanceof HTMLElement) setTop(header.offsetHeight);
    };
    measure();
    const ro = new ResizeObserver(measure);
    const header = root.querySelector("[data-univer-container] header");
    if (header) ro.observe(header);
    const tablist = root.querySelector("[data-univer-container] [role='tablist']");
    if (tablist) ro.observe(tablist);
    return () => ro.disconnect();
  }, []);

  return (
    <div
      ref={hostRef}
      className="absolute inset-x-0 bottom-0 z-20 bg-background border-t border-border"
      style={{ top }}
    >
      {children}
    </div>
  );
}
