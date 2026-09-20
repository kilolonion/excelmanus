import { useState, useEffect } from "react";

export interface GuideViewport {
  left: number;
  top: number;
  width: number;
  height: number;
}

function readViewport(): GuideViewport {
  if (typeof window === "undefined") return { left: 0, top: 0, width: 1024, height: 768 };
  const viewport = window.visualViewport;
  const style = getComputedStyle(document.documentElement);
  const inset = (name: string) => Number.parseFloat(style.getPropertyValue(name)) || 0;
  const left = inset("--sal");
  const right = inset("--sar");
  const top = inset("--sat");
  const bottom = inset("--sab");
  return {
    left: (viewport?.offsetLeft ?? 0) + left,
    top: (viewport?.offsetTop ?? 0) + top,
    width: Math.max(1, (viewport?.width ?? window.innerWidth) - left - right),
    height: Math.max(1, (viewport?.height ?? window.innerHeight) - top - bottom),
  };
}

/** Shared geometry for the wizard and coach cards, including the soft keyboard. */
export function useGuideViewport(): GuideViewport {
  const [viewport, setViewport] = useState(readViewport);
  useEffect(() => {
    let frame = 0;
    const update = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => setViewport(readViewport()));
    };
    update();
    window.addEventListener("resize", update);
    window.visualViewport?.addEventListener("resize", update);
    window.visualViewport?.addEventListener("scroll", update);
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener("resize", update);
      window.visualViewport?.removeEventListener("resize", update);
      window.visualViewport?.removeEventListener("scroll", update);
    };
  }, []);
  return viewport;
}

export function findTourTarget(target: string): HTMLElement | null {
  if (!target) return null;
  const selector = target.startsWith("[") ? target : `[data-coach-id="${target}"]`;
  // Settings has both mobile and desktop tab buttons in the DOM. Never select
  // the hidden first match, and allow inputs to be targets themselves.
  return Array.from(document.querySelectorAll<HTMLElement>(selector)).find((el) => {
    const rect = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    return rect.width >= 2 && rect.height >= 2 && style.visibility !== "hidden";
  }) ?? null;
}

function visibleRect(el: HTMLElement): DOMRect | null {
  const r = el.getBoundingClientRect();
  const v = readViewport();
  let left = Math.max(r.left, v.left);
  let top = Math.max(r.top, v.top);
  let right = Math.min(r.right, v.left + v.width);
  let bottom = Math.min(r.bottom, v.top + v.height);
  for (let parent = el.parentElement; parent; parent = parent.parentElement) {
    const style = getComputedStyle(parent);
    const bounds = parent.getBoundingClientRect();
    if (/(auto|scroll|hidden|clip)/.test(style.overflowX)) {
      left = Math.max(left, bounds.left);
      right = Math.min(right, bounds.right);
    }
    if (/(auto|scroll|hidden|clip)/.test(style.overflowY)) {
      top = Math.max(top, bounds.top);
      bottom = Math.min(bottom, bounds.bottom);
    }
  }
  if (right - left < 2 || bottom - top < 2) return null;
  return DOMRect.fromRect({ x: left, y: top, width: right - left, height: bottom - top });
}

/** Track actual visible bounds during panel transitions, scrolling and rotation. */
export function useTargetRect(target: string, expandTarget?: string): DOMRect | null {
  const [state, setState] = useState<{ target: string; rect: DOMRect | null }>({ target: "", rect: null });
  useEffect(() => {
    if (!target) return;
    let frame = 0;
    let previous = "";
    let scrolled = false;
    const track = () => {
      const el = findTourTarget(target);
      if (el && !scrolled) {
        el.scrollIntoView({ block: "nearest", inline: "nearest", behavior: "instant" });
        scrolled = true;
      }
      let rect = el ? visibleRect(el) : null;
      const expanded = expandTarget ? findTourTarget(expandTarget) : null;
      const extra = expanded ? visibleRect(expanded) : null;
      if (rect && extra) {
        const x = Math.min(rect.x, extra.x);
        const y = Math.min(rect.y, extra.y);
        rect = DOMRect.fromRect({ x, y, width: Math.max(rect.right, extra.right) - x, height: Math.max(rect.bottom, extra.bottom) - y });
      }
      const key = rect ? [rect.x, rect.y, rect.width, rect.height].map(Math.round).join(",") : "null";
      if (key !== previous) {
        previous = key;
        setState({ target, rect });
      }
      frame = requestAnimationFrame(track);
    };
    frame = requestAnimationFrame(track);
    return () => cancelAnimationFrame(frame);
  }, [target, expandTarget]);
  return state.target === target ? state.rect : null;
}
