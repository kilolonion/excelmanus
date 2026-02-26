import { useReducedMotion } from "framer-motion";

// ── 统一动效 Token 系统 ──
export const duration = {
  instant: 0.075,
  fast: 0.15,
  normal: 0.25,
  slow: 0.35,
} as const;

export const easing = {
  default: [0.25, 0.1, 0.25, 1] as const,
  spring: { type: "spring" as const, stiffness: 300, damping: 30 },
  bounce: { type: "spring" as const, stiffness: 400, damping: 25 },
  smooth: [0.4, 0, 0.2, 1] as const,
  elastic: [0.68, -0.55, 0.265, 1.55] as const,
};

export const hoverTransition = { duration: duration.fast, ease: "easeOut" } as const;
export const enterTransition = { duration: duration.normal, ease: "easeOut" } as const;

// 更流畅的侧边栏动画
export const sidebarTransition = {
  type: "spring" as const,
  stiffness: 280,
  damping: 30,
  mass: 0.8,
};

// 内容渐入动画的延迟配置
export const sidebarContentVariants = {
  closed: {
    opacity: 0,
    x: -20,
    transition: {
      duration: 0.15,
      ease: [0.4, 0, 1, 1] as const,
    }
  },
  open: {
    opacity: 1,
    x: 0,
    transition: {
      duration: 0.3,
      delay: 0.1,
      ease: easing.smooth,
    }
  }
};

export const listItemVariants = {
  initial: { opacity: 0, y: -8 },
  animate: { opacity: 1, y: 0, transition: enterTransition },
  exit: { opacity: 0, x: -20, transition: { duration: duration.fast } },
};

export const fileItemVariants = {
  initial: { opacity: 0, y: -6 },
  animate: { opacity: 1, y: 0, transition: enterTransition },
};

export const messageEnterVariants = {
  initial: { opacity: 0, y: 12 },
  animate: { opacity: 1, y: 0, transition: { duration: duration.normal, ease: "easeOut" as const } },
};

export const panelSlideVariants = {
  initial: { x: "100%", opacity: 0.5 },
  animate: { x: 0, opacity: 1, transition: { duration: duration.slow, ease: easing.default } },
  exit: { x: "100%", opacity: 0, transition: { duration: duration.normal, ease: easing.default } },
};

export const panelSlideVariantsMedium = {
  initial: { x: "100%", opacity: 0.5 },
  animate: { x: 0, opacity: 1, transition: { duration: duration.slow, ease: easing.default } },
  exit: { x: "100%", opacity: 0, transition: { duration: duration.normal, ease: easing.default } },
};

export const panelSlideVariantsMobile = {
  initial: { y: "100%", opacity: 0.5 },
  animate: { y: 0, opacity: 1, transition: { duration: duration.slow, ease: easing.default } },
  exit: { y: "100%", opacity: 0, transition: { duration: duration.normal, ease: easing.default } },
};

export function useMotionSafe() {
  const shouldReduce = useReducedMotion();
  return {
    shouldReduce,
    safeTransition: shouldReduce ? { duration: 0 } : undefined,
  };
}
