import { useEffect, useState } from "react";

// 响应式断点定义
const MOBILE_BREAKPOINT = 768;    // 手机端
const TABLET_BREAKPOINT = 1024;   // 平板端
const DESKTOP_BREAKPOINT = 1280;  // 桌面端（三栏布局最小宽度）

/** SSR-safe: returns false on server, synchronous check on client */
function getIsMobile() {
  if (typeof window === "undefined") return false;
  return window.innerWidth < MOBILE_BREAKPOINT;
}

/** 检查是否为平板尺寸 */
function getIsTablet() {
  if (typeof window === "undefined") return false;
  return window.innerWidth >= MOBILE_BREAKPOINT && window.innerWidth < TABLET_BREAKPOINT;
}

/** 检查是否支持三栏布局（桌面端） */
function getIsDesktop() {
  if (typeof window === "undefined") return false;
  return window.innerWidth >= DESKTOP_BREAKPOINT;
}

/** 检查是否为中等屏幕（平板到小桌面） */
function getIsMediumScreen() {
  if (typeof window === "undefined") return false;
  return window.innerWidth >= TABLET_BREAKPOINT && window.innerWidth < DESKTOP_BREAKPOINT;
}

export function useIsMobile() {
  const [isMobile, setIsMobile] = useState(getIsMobile);

  useEffect(() => {
    const mql = window.matchMedia(`(max-width: ${MOBILE_BREAKPOINT - 1}px)`);
    const onChange = () => setIsMobile(mql.matches);
    onChange();
    mql.addEventListener("change", onChange);
    return () => mql.removeEventListener("change", onChange);
  }, []);

  return isMobile;
}

export function useIsTablet() {
  const [isTablet, setIsTablet] = useState(getIsTablet);

  useEffect(() => {
    const mql = window.matchMedia(`(min-width: ${MOBILE_BREAKPOINT}px) and (max-width: ${TABLET_BREAKPOINT - 1}px)`);
    const onChange = () => setIsTablet(mql.matches);
    onChange();
    mql.addEventListener("change", onChange);
    return () => mql.removeEventListener("change", onChange);
  }, []);

  return isTablet;
}

export function useIsDesktop() {
  const [isDesktop, setIsDesktop] = useState(getIsDesktop);

  useEffect(() => {
    const mql = window.matchMedia(`(min-width: ${DESKTOP_BREAKPOINT}px)`);
    const onChange = () => setIsDesktop(mql.matches);
    onChange();
    mql.addEventListener("change", onChange);
    return () => mql.removeEventListener("change", onChange);
  }, []);

  return isDesktop;
}

export function useIsMediumScreen() {
  const [isMediumScreen, setIsMediumScreen] = useState(getIsMediumScreen);

  useEffect(() => {
    const mql = window.matchMedia(`(min-width: ${TABLET_BREAKPOINT}px) and (max-width: ${DESKTOP_BREAKPOINT - 1}px)`);
    const onChange = () => setIsMediumScreen(mql.matches);
    onChange();
    mql.addEventListener("change", onChange);
    return () => mql.removeEventListener("change", onChange);
  }, []);

  return isMediumScreen;
}

export { getIsMobile, getIsTablet, getIsDesktop, getIsMediumScreen };
