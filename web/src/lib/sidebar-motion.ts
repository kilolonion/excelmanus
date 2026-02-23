import { useReducedMotion } from "framer-motion";

// 统一过渡配置
export const hoverTransition = { duration: 0.15, ease: "easeOut" } as const;
export const enterTransition = { duration: 0.2, ease: "easeOut" } as const;
export const sidebarTransition = {
  duration: 0.25,
  ease: [0.25, 0.1, 0.25, 1],
} as const;

// 列表项入场/退出动画 variants
export const listItemVariants = {
  initial: { opacity: 0, y: -8 },
  animate: { opacity: 1, y: 0, transition: enterTransition },
  exit: { opacity: 0, x: -20, transition: { duration: 0.15 } },
};

// 文件项入场动画 variants
export const fileItemVariants = {
  initial: { opacity: 0, y: -6 },
  animate: { opacity: 1, y: 0, transition: enterTransition },
};

// 条件性返回动画 props（尊重 reduced-motion 偏好）
export function useMotionSafe() {
  const shouldReduce = useReducedMotion();
  return {
    shouldReduce,
    safeTransition: shouldReduce ? { duration: 0 } : undefined,
  };
}
