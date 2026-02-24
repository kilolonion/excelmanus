import { useReducedMotion } from "framer-motion";

// ── Unified Motion Token System ──
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
};

export const hoverTransition = { duration: duration.fast, ease: "easeOut" } as const;
export const enterTransition = { duration: duration.normal, ease: "easeOut" } as const;
export const sidebarTransition = {
  duration: duration.normal,
  ease: easing.default,
} as const;

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
